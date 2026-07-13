#!/usr/bin/env python3
"""tests/test_routing_tables.py - Table-driven tests for RouteResult.as_pipe().

Stable pipe format (docs/AGENT_INTERFACES.md):
  backend|project|text|match_kind
  match_kind ∈ chat | prefix | default | none | legacy

Also checks resolve() → RouteResult.as_pipe() for a few in-memory configs.
NO network. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_routing_tables.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay import routing
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
]

for name, cfg, chat_id, thread_id, text, want_pipe in RESOLVE_PIPE_CASES:
    got = routing.resolve(cfg, chat_id, thread_id, text).as_pipe()
    eq(f"resolve→as_pipe: {name}", want_pipe, got)

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
