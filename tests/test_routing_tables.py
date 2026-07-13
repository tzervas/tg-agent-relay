#!/usr/bin/env python3
"""tests/test_routing_tables.py - Table-driven tests for routing parity (#24).

Stable pipe format (docs/AGENT_INTERFACES.md):
  backend|project|text|match_kind
  match_kind ∈ chat | prefix | default | none | legacy

Covers:
  - RouteResult.as_pipe()
  - resolve() sticky / prefix / default / none / legacy
  - project_from_cwd longest-match
  - lookup_chat preference order
  - .chats.d/bindings.json overlay via config.load_config
  - CLI: python -m tg_agent_relay.cli route / .routing

NO network. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_routing_tables.py
      uv run python tests/test_routing_tables.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay import config, routing
from tg_agent_relay.protocols import RouteResult

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


# --- RouteResult.as_pipe() pure table ---------------------------------------
AS_PIPE_CASES: list[tuple[str, RouteResult, str]] = [
    (
        "legacy empty backend/project",
        RouteResult(backend="", project="", text="hello", match_kind="legacy"),
        "||hello|legacy",
    ),
    (
        "chat sticky full",
        RouteResult(backend="claude", project="mycelium", text="hi", match_kind="chat"),
        "claude|mycelium|hi|chat",
    ),
    (
        "prefix stripped",
        RouteResult(backend="grok", project="", text="status please", match_kind="prefix"),
        "grok||status please|prefix",
    ),
    (
        "default backend",
        RouteResult(backend="claude", project="main", text="plain", match_kind="default"),
        "claude|main|plain|default",
    ),
    (
        "none require_prefix miss",
        RouteResult(backend="", project="", text="@unknown x", match_kind="none"),
        "||@unknown x|none",
    ),
    (
        "empty text still four fields",
        RouteResult(backend="ollama", project="local", text="", match_kind="chat"),
        "ollama|local||chat",
    ),
    (
        "text with spaces preserved",
        RouteResult(backend="grok", project="p", text="a b c", match_kind="prefix"),
        "grok|p|a b c|prefix",
    ),
]

for name, rr, want in AS_PIPE_CASES:
    eq(f"as_pipe: {name}", want, rr.as_pipe())

# Field order is always backend|project|text|match_kind (4 parts)
for name, rr, _want in AS_PIPE_CASES:
    parts = rr.as_pipe().split("|")
    eq(f"as_pipe field count: {name}", 4, len(parts))
    eq(f"as_pipe backend field: {name}", rr.backend, parts[0])
    eq(f"as_pipe project field: {name}", rr.project, parts[1])
    eq(f"as_pipe text field: {name}", rr.text, parts[2])
    eq(f"as_pipe match_kind field: {name}", rr.match_kind, parts[3])

# --- resolve() → as_pipe() integration table --------------------------------
CFG_MULTI = {
    "routing": {"default_backend": "claude"},
    "backends": {
        "claude": {
            "tag": "claude",
            "prefixes": ["@claude", "claude:"],
            "project": "mycelium",
        },
        "grok": {
            "tag": "grok",
            "prefixes": ["@grok"],
            "project": "mycelium",
        },
    },
    "chats": [
        {"chat_id": -1001, "backend": "claude", "project": "mycelium"},
        {"chat_id": -1002, "backend": "grok", "project": "mycelium"},
    ],
}

CFG_PROJECT_ONLY = {
    "routing": {"default_backend": "claude"},
    "backends": {
        "claude": {"prefixes": ["@claude"]},
        "grok": {"prefixes": ["@grok"]},
    },
    "projects": {
        "mycelium": {
            "root": "/tmp/mycelium-proj",
            "default_backend": "claude",
        },
    },
    "chats": [
        {"chat_id": -100999, "thread_id": 3, "project": "mycelium"},
    ],
}

RESOLVE_PIPE_CASES: list[tuple[str, dict, str, str, str, str]] = [
    # name, cfg, chat_id, thread_id, text, expected pipe
    (
        "no backends/chats → legacy",
        {},
        "1",
        "",
        "hello",
        "||hello|legacy",
    ),
    (
        "prefix @grok",
        {
            "backends": {"grok": {"prefixes": ["@grok"]}},
            "routing": {"default_backend": "claude"},
        },
        "1",
        "",
        "@grok do it",
        "grok||do it|prefix",
    ),
    (
        "prefix colon form",
        {
            "backends": {"claude": {"prefixes": ["claude:"], "project": "home"}},
        },
        "1",
        "",
        "claude: ship it",
        "claude|home|ship it|prefix",
    ),
    (
        "prefix exact token only → empty stripped",
        {
            "backends": {"grok": {"prefixes": ["@grok"]}},
        },
        "1",
        "",
        "@grok",
        "grok|||prefix",
    ),
    (
        "longest prefix wins",
        {
            "backends": {
                "short": {"prefixes": ["@g"]},
                "long": {"prefixes": ["@grok"]},
            },
        },
        "1",
        "",
        "@grok yes",
        "long||yes|prefix",
    ),
    (
        "default when no prefix",
        {
            "backends": {
                "claude": {"prefixes": ["@claude"], "project": "home"},
            },
            "routing": {"default_backend": "claude"},
        },
        "1",
        "",
        "just text",
        "claude|home|just text|default",
    ),
    (
        "sticky full backend+project (shell multi-chat case)",
        CFG_MULTI,
        "-1001",
        "",
        "hello",
        "claude|mycelium|hello|chat",
    ),
    (
        "sticky other chat",
        CFG_MULTI,
        "-1002",
        "",
        "hello",
        "grok|mycelium|hello|chat",
    ),
    (
        "prefix outside sticky room",
        CFG_MULTI,
        "999",
        "",
        "@grok review",
        "grok|mycelium|review|prefix",
    ),
    (
        "default outside sticky room",
        CFG_MULTI,
        "999",
        "",
        "plain",
        "claude|mycelium|plain|default",
    ),
    (
        "sticky project-only + @grok keeps project",
        {
            "backends": {"grok": {"prefixes": ["@grok"]}},
            "chats": [{"chat_id": -42, "project": "sticky-proj"}],
        },
        "-42",
        "",
        "@grok ship it",
        "grok|sticky-proj|ship it|chat",
    ),
    (
        "project-only + @grok (thread match)",
        CFG_PROJECT_ONLY,
        "-100999",
        "3",
        "@grok hi",
        "grok|mycelium|hi|chat",
    ),
    (
        "project-only default_backend from project",
        CFG_PROJECT_ONLY,
        "-100999",
        "3",
        "hello",
        "claude|mycelium|hello|chat",
    ),
    (
        "project-only falls back to routing.default_backend",
        {
            "routing": {"default_backend": "grok"},
            "backends": {
                "claude": {"prefixes": ["@claude"]},
                "grok": {"prefixes": ["@grok"]},
            },
            "chats": [{"chat_id": -7, "project": "p1"}],
        },
        "-7",
        "",
        "no prefix",
        "grok|p1|no prefix|chat",
    ),
    (
        "require_prefix miss → none",
        {
            "backends": {"claude": {"prefixes": ["@claude"]}},
            "routing": {"require_prefix": True},
        },
        "1",
        "",
        "no prefix here",
        "||no prefix here|none",
    ),
    (
        "require_prefix string true",
        {
            "backends": {"claude": {"prefixes": ["@claude"]}},
            "routing": {"require_prefix": "true"},
        },
        "1",
        "",
        "plain",
        "||plain|none",
    ),
    (
        "backends only no default no require → legacy",
        {
            "backends": {"claude": {"prefixes": ["@claude"]}},
        },
        "1",
        "",
        "plain",
        "||plain|legacy",
    ),
    (
        "chat-only binding without thread falls back",
        {
            "backends": {"claude": {"prefixes": ["@claude"]}},
            "chats": [{"chat_id": -50, "backend": "claude", "project": "room"}],
        },
        "-50",
        "99",
        "hi",
        "claude|room|hi|chat",
    ),
    (
        "exact thread preferred over chat-only",
        {
            "backends": {"claude": {"prefixes": ["@claude"]}, "grok": {"prefixes": ["@grok"]}},
            "chats": [
                {"chat_id": -60, "backend": "claude", "project": "base"},
                {"chat_id": -60, "thread_id": 5, "backend": "grok", "project": "topic"},
            ],
        },
        "-60",
        "5",
        "hi",
        "grok|topic|hi|chat",
    ),
    (
        "int chat_id in cfg matches string query",
        {
            "backends": {"x": {"prefixes": ["@x"]}},
            "chats": [{"chat_id": -1001, "backend": "x", "project": "p"}],
        },
        "-1001",
        "",
        "z",
        "x|p|z|chat",
    ),
]

for name, cfg, chat_id, thread_id, text, want_pipe in RESOLVE_PIPE_CASES:
    got = routing.resolve(cfg, chat_id, thread_id, text).as_pipe()
    eq(f"resolve→as_pipe: {name}", want_pipe, got)

# --- project_from_cwd -------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    td_path = Path(td)
    root_a = td_path / "proj-a"
    root_b = td_path / "proj-a" / "nested-b"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    (root_b / "src").mkdir()
    wt = td_path / "worktree-a"
    wt.mkdir()

    cfg_cwd = {
        "projects": {
            "proj-a": {"root": str(root_a), "worktrees": {"claude": str(wt)}},
            "nested": {"root": str(root_b)},
        }
    }
    eq(
        "project_from_cwd longest root wins",
        "nested",
        routing.project_from_cwd(cfg_cwd, str(root_b / "src")),
    )
    eq(
        "project_from_cwd outer root",
        "proj-a",
        routing.project_from_cwd(cfg_cwd, str(root_a / "other")),
    )
    eq(
        "project_from_cwd worktree",
        "proj-a",
        routing.project_from_cwd(cfg_cwd, str(wt / "file")),
    )
    eq("project_from_cwd empty cwd", "", routing.project_from_cwd(cfg_cwd, ""))
    eq(
        "project_from_cwd no match",
        "",
        routing.project_from_cwd(cfg_cwd, "/var/empty-no-match-xyz"),
    )

# Shell-equivalent non-existent path (realpath -m style)
eq(
    "project_from_cwd non-existent under configured root",
    "mycelium",
    routing.project_from_cwd(CFG_PROJECT_ONLY, "/tmp/mycelium-proj/src"),
)

# --- lookup_chat preference order -------------------------------------------
CFG_LOOKUP = {
    "chats": [
        {"chat_id": -1, "backend": "claude", "project": "mycelium", "thread_id": 1},
        {"chat_id": -2, "project": "mycelium"},  # project-only room
        {"chat_id": -3, "backend": "grok", "project": "other"},
        {"chat_id": -4, "backend": "claude"},  # backend-only
    ]
}
eq(
    "lookup_chat exact backend+project",
    ("-1", "1"),
    routing.lookup_chat(CFG_LOOKUP, "claude", "mycelium"),
)
eq(
    "lookup_chat project-only room when no exact",
    ("-2", ""),
    routing.lookup_chat(CFG_LOOKUP, "grok", "mycelium"),
)
eq(
    "lookup_chat any project match",
    ("-3", ""),
    routing.lookup_chat(
        {"chats": [{"chat_id": -3, "backend": "x", "project": "only-proj"}]},
        "missing",
        "only-proj",
    ),
)
# When project is empty, first chat with matching backend wins (shell order).
eq(
    "lookup_chat backend-only first match",
    ("-1", "1"),
    routing.lookup_chat(CFG_LOOKUP, "claude", ""),
)
eq(
    "lookup_chat backend-only unique",
    ("-4", ""),
    routing.lookup_chat(
        {"chats": [{"chat_id": -4, "backend": "claude"}]},
        "claude",
        "",
    ),
)
eq(
    "lookup_chat miss",
    ("", ""),
    routing.lookup_chat(CFG_LOOKUP, "unknown", "nope"),
)

# --- strip_prefix / chat_binding helpers ------------------------------------
hit = routing.strip_prefix(
    {"backends": {"grok": {"prefixes": ["@grok"], "project": "p"}}},
    "@grok hi",
)
eq("strip_prefix tuple", ("grok", "p", "hi"), hit)
eq(
    "chat_binding hit",
    True,
    routing.chat_binding(
        {"chats": [{"chat_id": -1, "project": "p"}]},
        "-1",
        "",
    )
    is not None,
)
eq(
    "chat_binding miss",
    None,
    routing.chat_binding({"chats": [{"chat_id": -1}]}, "999", ""),
)

# --- format_tag / inbound_tag (shell helpers) -------------------------------
eq(
    "format_tag bracket with project",
    "[grok · mycelium]",
    routing.format_tag(
        {"backends": {"grok": {"tag": "grok"}}, "routing": {"tag_style": "bracket"}},
        "grok",
        "mycelium",
    ),
)
eq(
    "format_tag empty backend",
    "",
    routing.format_tag({}, "", "x"),
)
eq(
    "inbound_tag both",
    "[telegram:backend:claude:project:mycelium]",
    routing.inbound_tag("claude", "mycelium"),
)
eq("inbound_tag none", "[telegram]", routing.inbound_tag())

# --- overlay .chats.d/bindings.json via config.load_config ------------------
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

[backends.grok]
prefixes = ["@grok"]
tag = "grok"

[[chats]]
chat_id = -100
backend = "claude"
project = "from-toml"

[[chats]]
chat_id = -300
thread_id = 9
project = "keep-static"
""",
        encoding="utf-8",
    )
    # Missing overlay: static only (#34)
    cfg_no_ov = config.load_config(toml, bridge_dir=bridge)
    eq("overlay missing: static chat count", 2, len(cfg_no_ov.get("chats") or []))
    eq(
        "overlay missing: static project",
        "from-toml",
        routing.resolve(cfg_no_ov, "-100", "", "x").project,
    )

    # Corrupt overlay must not drop static rows (#34)
    (chats_d / "bindings.json").write_text("{broken", encoding="utf-8")
    cfg_corrupt = config.load_config(toml, bridge_dir=bridge)
    eq("overlay corrupt: static preserved", 2, len(cfg_corrupt.get("chats") or []))

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
                        "chat_id": -200,
                        "project": "sticky-only",
                    },
                    {
                        "chat_id": -1001234567890,
                        "thread_id": 7,
                        "project": "forum-topic",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    old_overlay = os.environ.get("RELAY_CHATS_OVERLAY")
    try:
        # load_config uses bridge_dir for default overlay path
        cfg_ov = config.load_config(toml, bridge_dir=bridge)
    finally:
        if old_overlay is None:
            os.environ.pop("RELAY_CHATS_OVERLAY", None)
        else:
            os.environ["RELAY_CHATS_OVERLAY"] = old_overlay

    # Overlay replaces same chat_id|thread_id key; other static kept
    r_ov = routing.resolve(cfg_ov, "-100", "", "hello")
    eq("overlay replaces chat binding backend", "grok", r_ov.backend)
    eq("overlay replaces chat binding project", "from-overlay", r_ov.project)
    eq("overlay sticky match_kind chat", "chat", r_ov.match_kind)
    eq(
        "overlay merge keeps unrelated static thread row",
        "keep-static",
        routing.resolve(cfg_ov, "-300", "9", "x").project,
    )

    r_sticky = routing.resolve(cfg_ov, "-200", "", "@grok go")
    eq("overlay adds project-only room backend", "grok", r_sticky.backend)
    eq("overlay adds project-only room project", "sticky-only", r_sticky.project)
    eq("overlay project-only match_kind chat", "chat", r_sticky.match_kind)

    r_forum = routing.resolve(cfg_ov, "-1001234567890", "7", "hi")
    eq("overlay negative chat_id + thread project", "forum-topic", r_forum.project)
    eq("overlay forum match_kind chat", "chat", r_forum.match_kind)

# --- CLI: tg_agent_relay.cli route + module routing -------------------------
with tempfile.TemporaryDirectory() as td:
    cfg_path = Path(td) / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "backends": {"grok": {"prefixes": ["@grok"], "project": "p"}},
                "routing": {"default_backend": "grok"},
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    py = sys.executable

    proc = subprocess.run(
        [
            py,
            "-m",
            "tg_agent_relay.cli",
            "route",
            "--config",
            str(cfg_path),
            "--chat-id",
            "1",
            "--text",
            "@grok hi",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        check=False,
    )
    eq("cli route exit 0", 0, proc.returncode)
    eq("cli route pipe", "grok|p|hi|prefix", proc.stdout.strip())

    proc2 = subprocess.run(
        [
            py,
            "-m",
            "tg_agent_relay.routing",
            "resolve",
            "--config",
            str(cfg_path),
            "--chat-id",
            "1",
            "--text",
            "plain",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        check=False,
    )
    eq("module routing resolve exit 0", 0, proc2.returncode)
    eq("module routing resolve pipe", "grok|p|plain|default", proc2.stdout.strip())

    proc3 = subprocess.run(
        [
            py,
            "-m",
            "tg_agent_relay.routing",
            "lookup-chat",
            "--config",
            str(cfg_path),
            "--backend",
            "grok",
            "--project",
            "p",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        check=False,
    )
    # no chats in this cfg → empty
    eq("module routing lookup-chat empty", "|", proc3.stdout.strip())

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
