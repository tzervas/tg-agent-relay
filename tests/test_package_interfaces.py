#!/usr/bin/env python3
"""tests/test_package_interfaces.py - Unit tests for tg_agent_relay package APIs.

Covers the stable agent interfaces documented in docs/AGENT_INTERFACES.md:
  __version__, config, routing, hooks, metrics, tts.

NO network calls. Dual-run: pytest entry + standalone PASS/FAIL runner.
Run:  uv run pytest tests/test_package_interfaces.py
      python3 tests/test_package_interfaces.py
Also dual-invoked by tests/run-tests.sh via relay_python.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = 0
    sys.path.insert(0, str(REPO))
    import tg_agent_relay
    from tg_agent_relay import config, hooks, metrics, routing, tts

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

    def true(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            ok(name)
        else:
            fail(name, detail)

    true("__version__ exists and is non-empty", bool(getattr(tg_agent_relay, "__version__", None)))
    true("__version__ is a string", isinstance(tg_agent_relay.__version__, str))
    with tempfile.TemporaryDirectory() as td:
        bridge = Path(td)
        missing = bridge / "relay.toml.does.not.exist.for.tests"
        cfg_missing = config.load_config(missing, bridge_dir=bridge)
        eq("load_config missing file backends empty", {}, cfg_missing.get("backends"))
        true(
            "load_config missing file applies session registry shell",
            cfg_missing.get("_sessions_merged") is True,
        )
    cfg_sample = {
        "routing": {"default_backend": "claude"},
        "backends": {"claude": {"tag": "claude", "prefixes": ["@claude"]}},
    }
    eq(
        "cfg_get nested routing.default_backend",
        "claude",
        config.cfg_get(cfg_sample, "routing.default_backend"),
    )
    eq(
        "cfg_get nested backends.claude.tag",
        "claude",
        config.cfg_get(cfg_sample, "backends.claude.tag"),
    )
    eq(
        "cfg_get missing path returns default",
        "fallback",
        config.cfg_get(cfg_sample, "routing.missing.key", "fallback"),
    )
    eq("cfg_get empty cfg returns default", None, config.cfg_get({}, "a.b"))
    r_legacy = routing.resolve({}, "1", "", "hello there")
    eq("resolve empty cfg match_kind legacy", "legacy", r_legacy.match_kind)
    eq("resolve empty cfg backend empty", "", r_legacy.backend)
    eq("resolve empty cfg project empty", "", r_legacy.project)
    eq("resolve empty cfg text passthrough", "hello there", r_legacy.text)
    eq("resolve empty cfg as_pipe", "||hello there|legacy", r_legacy.as_pipe())
    cfg_sticky = {
        "backends": {
            "grok": {"prefixes": ["@grok"], "tag": "grok"},
            "claude": {"prefixes": ["@claude"], "tag": "claude"},
        },
        "chats": [{"chat_id": -1001, "project": "mycelium"}],
        "routing": {"default_backend": "claude"},
    }
    r_sticky = routing.resolve(cfg_sticky, "-1001", "", "@grok hi team")
    eq("sticky+@grok backend", "grok", r_sticky.backend)
    eq("sticky+@grok project sticky", "mycelium", r_sticky.project)
    eq("sticky+@grok stripped text", "hi team", r_sticky.text)
    eq("sticky+@grok match_kind chat", "chat", r_sticky.match_kind)
    cfg_default = {
        "backends": {
            "claude": {"prefixes": ["@claude"], "project": "main"},
            "grok": {"prefixes": ["@grok"]},
        },
        "routing": {"default_backend": "claude"},
    }
    r_def = routing.resolve(cfg_default, "999", "", "plain message no prefix")
    eq("default_backend backend", "claude", r_def.backend)
    eq("default_backend match_kind", "default", r_def.match_kind)
    eq("default_backend text unchanged", "plain message no prefix", r_def.text)
    eq("default_backend project from backend cfg", "main", r_def.project)
    status_ok, body_ok = hooks.dispatch_hook("grok", {"hookEventName": "stop", "message": "x"})
    eq("dispatch_hook stop status OK", "OK", status_ok)
    true("dispatch_hook stop body non-empty", bool(body_ok), repr(body_ok))
    true("dispatch_hook stop body mentions message", "x" in body_ok, repr(body_ok))
    status_skip, body_skip = hooks.dispatch_hook(
        "grok",
        {"hookEventName": "stop", "message": "x"},
        config={"grok": {"Stop": {"enabled": False}}},
    )
    eq("dispatch_hook Stop disabled status SKIP", "SKIP", status_skip)
    true(
        "dispatch_hook Stop disabled reason mentions disabled",
        "disabled" in body_skip.lower(),
        repr(body_skip),
    )
    with tempfile.TemporaryDirectory() as td:
        bridge = Path(td)
        metrics.emit_metric("pkg-test", "unit_event", "detail-z", bridge_dir=bridge)
        log_path = bridge / ".metrics.log"
        true("emit_metric creates .metrics.log", log_path.is_file(), str(log_path))
        content = log_path.read_text(encoding="utf-8")
        true(
            "emit_metric line has source/event/detail TSV",
            "\tpkg-test\tunit_event\tdetail-z" in content,
            repr(content),
        )
        lines = [ln for ln in content.splitlines() if ln.strip()]
        true("emit_metric wrote exactly one line", len(lines) == 1, repr(lines))
        parts = lines[0].split("\t")
        eq("emit_metric TSV has 4 fields", 4, len(parts))
        true("emit_metric epoch is digits", parts[0].isdigit(), parts[0])
    spoken = tts.strip_formatting("Use `secret_token` and visit https://example.com/docs for more.")
    true("strip_formatting removes backticks", "`" not in spoken, repr(spoken))
    true(
        "strip_formatting removes bare URL",
        "https://example.com" not in spoken and "example.com/docs" not in spoken,
        repr(spoken),
    )
    true(
        "strip_formatting does not voice inline code body",
        "secret_token" not in spoken,
        repr(spoken),
    )
    true(
        "strip_formatting leaves surrounding prose",
        "Use" in spoken and "for more" in spoken,
        repr(spoken),
    )
    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


def test_package_interfaces() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())
