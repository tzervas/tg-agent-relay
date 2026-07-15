#!/usr/bin/env python3
"""tests/test_project_bind.py - Overlay merge + project-room bind edge cases (#34).

Covers:
  - config._merge_chats_overlay / load_config merge order
  - missing / corrupt / empty overlay (static [[chats]] preserved)
  - negative chat_id + forum thread_id keys
  - overlay replaces same chat_id|thread_id; keeps other static rows
  - project_from_cwd → lookup_chat reverse path (adapter RELAY_PROJECT contract)

NO network. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_project_bind.py
      uv run python tests/test_project_bind.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay import config, routing

PASS = FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def eq(name: str, exp, act) -> None:
    if exp == act:
        ok(name)
    else:
        fail(name, f"expected {exp!r} got {act!r}")


def _chat_keys(cfg: dict) -> list[str]:
    out = []
    for c in cfg.get("chats") or []:
        if isinstance(c, dict):
            out.append(f"{c.get('chat_id', '')}|{c.get('thread_id', '') or ''}")
    return out


# --- pure merge helper (public via load_config) -----------------------------
with tempfile.TemporaryDirectory() as td:
    bridge = Path(td)
    chats_d = bridge / ".chats.d"
    chats_d.mkdir()
    toml = bridge / "relay.toml"
    toml.write_text(
        """
[routing]
default_backend = "claude"

[backends.claude]
prefixes = ["@claude"]
tag = "claude"
project = "home"

[backends.grok]
prefixes = ["@grok"]
tag = "grok"

[projects.mycelium]
root = "/tmp/mycelium-proj"
default_backend = "claude"

[projects.other]
root = "/tmp/other-proj"

[[chats]]
chat_id = -100
backend = "claude"
project = "from-toml"

[[chats]]
chat_id = -200
thread_id = 1
project = "topic-static"
""",
        encoding="utf-8",
    )

    # Missing overlay → static chats only
    cfg_miss = config.load_config(toml, bridge_dir=bridge)
    eq("missing overlay: keeps 2 static chats", 2, len(cfg_miss.get("chats") or []))
    eq(
        "missing overlay: first static project",
        "from-toml",
        (cfg_miss.get("chats") or [{}])[0].get("project"),
    )

    # Corrupt overlay → same as missing (do not raise, do not wipe static)
    (chats_d / "bindings.json").write_text("{not-json", encoding="utf-8")
    cfg_bad = config.load_config(toml, bridge_dir=bridge)
    eq("corrupt overlay: keeps static chats", 2, len(cfg_bad.get("chats") or []))
    r_bad = routing.resolve(cfg_bad, "-100", "", "hello")
    eq("corrupt overlay: still sticky-routes static", "claude", r_bad.backend)
    eq("corrupt overlay: still sticky project", "from-toml", r_bad.project)

    # Empty chats array overlay → no replacements, static kept
    (chats_d / "bindings.json").write_text(json.dumps({"chats": []}), encoding="utf-8")
    cfg_empty = config.load_config(toml, bridge_dir=bridge)
    eq("empty overlay chats: static intact", 2, len(cfg_empty.get("chats") or []))

    # Overlay with only a list root (legacy tolerance: raw list as chats)
    (chats_d / "bindings.json").write_text(
        json.dumps(
            [
                {
                    "chat_id": -300,
                    "project": "list-root",
                }
            ]
        ),
        encoding="utf-8",
    )
    cfg_list = config.load_config(toml, bridge_dir=bridge)
    keys_list = _chat_keys(cfg_list)
    eq("list-root overlay: static -100 kept", True, "-100|" in keys_list)
    eq("list-root overlay: adds -300", True, "-300|" in keys_list)

    # Full merge order: same key replaced; other static kept; new overlay rows added
    (chats_d / "bindings.json").write_text(
        json.dumps(
            {
                "chats": [
                    {
                        "chat_id": -100,
                        "backend": "grok",
                        "project": "from-overlay",
                    },
                    {
                        "chat_id": -1001234567890,
                        "thread_id": 7,
                        "project": "mycelium",
                    },
                    {
                        "chat_id": -999,
                        "thread_id": None,
                        "project": "group-only",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = config.load_config(toml, bridge_dir=bridge)
    keys = _chat_keys(cfg)
    # -100 replaced (not duplicated)
    eq("merge: -100 appears once", 1, keys.count("-100|"))
    eq("merge: static thread row kept", True, "-200|1" in keys)
    eq("merge: forum topic overlay added", True, "-1001234567890|7" in keys)
    eq("merge: group-level null thread as empty key", True, "-999|" in keys)

    # Resolve through merged config
    r_ov = routing.resolve(cfg, "-100", "", "hello")
    eq("merge replace: backend from overlay", "grok", r_ov.backend)
    eq("merge replace: project from overlay", "from-overlay", r_ov.project)
    eq("merge replace: match_kind chat", "chat", r_ov.match_kind)

    r_topic = routing.resolve(cfg, "-1001234567890", "7", "hello")
    eq("forum topic bind: project sticky", "mycelium", r_topic.project)
    eq("forum topic bind: match_kind chat", "chat", r_topic.match_kind)
    # project-only → default_backend from [projects.mycelium]
    eq("forum topic bind: project default backend", "claude", r_topic.backend)

    r_topic_pref = routing.resolve(cfg, "-1001234567890", "7", "@grok review")
    eq("forum topic + prefix: backend grok", "grok", r_topic_pref.backend)
    eq("forum topic + prefix: sticky project kept", "mycelium", r_topic_pref.project)
    eq("forum topic + prefix: match_kind chat", "chat", r_topic_pref.match_kind)

    r_group = routing.resolve(cfg, "-999", "", "@claude hi")
    eq("group bind + prefix: backend claude", "claude", r_group.backend)
    eq("group bind + prefix: sticky project", "group-only", r_group.project)

    # Different thread on same chat must NOT match topic overlay
    r_wrong_thread = routing.resolve(cfg, "-1001234567890", "99", "hello")
    # falls through to default (no sticky for this thread)
    eq(
        "wrong thread: not sticky chat match",
        True,
        r_wrong_thread.match_kind in ("default", "prefix", "legacy", "none"),
    )
    eq(
        "wrong thread: not mycelium sticky",
        True,
        r_wrong_thread.project != "mycelium" or r_wrong_thread.match_kind != "chat",
    )

    # Negative chat_id type coercion (int in overlay, string query)
    r_neg = routing.resolve(cfg, "-1001234567890", "7", "x")
    eq("negative chat_id string query matches int overlay", "mycelium", r_neg.project)

# --- reverse lookup: project_from_cwd → lookup_chat (adapter contract) -----
with tempfile.TemporaryDirectory() as td:
    td_path = Path(td)
    root = td_path / "mycelium"
    root.mkdir()
    (root / "src").mkdir()

    cfg_rev = {
        "routing": {"default_backend": "claude"},
        "backends": {
            "claude": {"prefixes": ["@claude"], "tag": "claude"},
            "grok": {"prefixes": ["@grok"], "tag": "grok"},
        },
        "projects": {
            "mycelium": {
                "root": str(root),
                "default_backend": "claude",
            }
        },
        "chats": [
            {
                "chat_id": -1001234567890,
                "thread_id": 7,
                "project": "mycelium",
            }
        ],
    }
    proj = routing.project_from_cwd(cfg_rev, str(root / "src"))
    eq("project_from_cwd → mycelium", "mycelium", proj)
    chat_id, thread_id = routing.lookup_chat(cfg_rev, "claude", proj)
    eq("lookup_chat via RELAY_PROJECT: chat_id", "-1001234567890", chat_id)
    eq("lookup_chat via RELAY_PROJECT: thread_id", "7", thread_id)
    # Project-only room still matches when backend differs (hook from grok)
    chat_id2, thread_id2 = routing.lookup_chat(cfg_rev, "grok", proj)
    eq("lookup_chat project-only room for grok backend", "-1001234567890", chat_id2)
    eq("lookup_chat project-only room thread", "7", thread_id2)

    # Group-level bind (no thread)
    cfg_grp = {
        "projects": {"other": {"root": str(td_path / "other")}},
        "chats": [{"chat_id": -50, "project": "other"}],
    }
    (td_path / "other").mkdir()
    p2 = routing.project_from_cwd(cfg_grp, str(td_path / "other" / "file.py"))
    eq("group project_from_cwd", "other", p2)
    cid, tid = routing.lookup_chat(cfg_grp, "claude", p2)
    eq("group reverse lookup chat_id", "-50", cid)
    eq("group reverse lookup empty thread", "", tid)

# --- shell-parity: load_config honors RELAY_CHATS_OVERLAY env -------------
with tempfile.TemporaryDirectory() as td:
    bridge = Path(td)
    alt = bridge / "alt-bindings.json"
    toml = bridge / "relay.toml"
    toml.write_text(
        """
[[chats]]
chat_id = 1
project = "static"
""",
        encoding="utf-8",
    )
    alt.write_text(
        json.dumps({"chats": [{"chat_id": 1, "project": "env-overlay"}]}),
        encoding="utf-8",
    )
    old = os.environ.get("RELAY_CHATS_OVERLAY")
    try:
        os.environ["RELAY_CHATS_OVERLAY"] = str(alt)
        cfg_env = config.load_config(toml, bridge_dir=bridge)
    finally:
        if old is None:
            os.environ.pop("RELAY_CHATS_OVERLAY", None)
        else:
            os.environ["RELAY_CHATS_OVERLAY"] = old
    eq(
        "RELAY_CHATS_OVERLAY env path used",
        "env-overlay",
        (cfg_env.get("chats") or [{}])[0].get("project"),
    )

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
