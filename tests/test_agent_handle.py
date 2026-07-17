"""Unit tests for @repo-branch agent handle construction."""

from __future__ import annotations

from tg_agent_relay.agent_handle import (
    backend_id_from_handle,
    branch_short,
    build_handle,
    build_handle_from_env,
    is_reserved_handle,
    parse_leading_handle,
    repo_short,
    strip_orchestrator_prefix,
)


def test_repo_short() -> None:
    assert repo_short("tzervas/TG-Agent-Relay") == "tgagentrelay"
    assert repo_short("tg-agent-relay") == "tgagentrelay"
    assert len(repo_short("a" * 40)) == 16


def test_branch_short() -> None:
    assert branch_short("feat/agent-handles-bidirectional") == "agent-handles-bidire"
    assert branch_short("main") == "main"
    assert branch_short("fix/foo_bar") == "foo-bar"


def test_build_handle_examples() -> None:
    assert (
        build_handle(repo="tzervas/tg-agent-relay", branch="feat/agent-handles-bidirectional")
        == "@tgagentrelay-agent-handles-bidire"
    )
    assert build_handle(repo="foo", branch="main") == "@foo-main"


def test_reserved_and_parse() -> None:
    assert is_reserved_handle("@cabal")
    assert is_reserved_handle("orchestrator")
    assert not is_reserved_handle("@tgagentrelay-main")
    hit = parse_leading_handle("@tgagentrelay-main ship it")
    assert hit == ("@tgagentrelay-main", "ship it")
    orch = strip_orchestrator_prefix("@cabal /config")
    assert orch == ("cabal", "/config")


def test_orchestrator_backend_resolution() -> None:
    from tg_agent_relay import routing
    from tg_agent_relay.agent_handle import orchestrator_backend_id

    cfg = {
        "routing": {"orchestrator_backend": "cabal", "require_prefix": True},
        "backends": {"cabal": {"prefixes": ["@cabal"]}},
    }
    assert orchestrator_backend_id(cfg, "fleet") == "cabal"
    r = routing.resolve(cfg, "1", "", "plain message")
    assert r.backend == "cabal"
    assert r.match_kind == "orchestrator"


def test_env_and_backend_id() -> None:
    env = {"RELAY_REPO": "o/r", "RELAY_BRANCH": "feat/x"}
    assert build_handle_from_env(env) == "@r-x"
    assert backend_id_from_handle("@cabal") == "cabal"
    assert backend_id_from_handle("@tgagentrelay-main") == "tgagentrelay-main"
