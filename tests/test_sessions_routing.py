"""Unit tests: dynamic session registry + @handle routing (v0.7)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def bridge_tmp(tmp_path: Path) -> Path:
    return tmp_path


def _write_session(sessions_d: Path, handle: str, fifo: Path) -> None:
    sessions_d.mkdir(parents=True, exist_ok=True)
    rec = {
        "handle": handle,
        "fifo": str(fifo),
        "tag": handle,
        "type": "grok",
        "delivery": "fifo",
        "prefixes": [f"@{handle}", f"/{handle}", f"{handle}:"],
        "project": "",
        "pid": 1,
        "registered_at": "2026-01-01T00:00:00+00:00",
    }
    (sessions_d / f"{handle}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_resolve_cabal_vs_fleet_different_fifos(bridge_tmp: Path) -> None:
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    sessions_d = bridge_tmp / ".sessions.d"
    fifo_cabal = bridge_tmp / "sessions" / "cabal.fifo"
    fifo_fleet = bridge_tmp / "sessions" / "fleet.fifo"
    _write_session(sessions_d, "cabal", fifo_cabal)
    _write_session(sessions_d, "fleet", fifo_fleet)

    cfg = {"sessions": {"dir": str(sessions_d)}}
    r_cabal = routing.resolve(cfg, "1", "", "@cabal ship it")
    r_fleet = routing.resolve(cfg, "1", "", "@fleet deploy")

    assert r_cabal.backend == "cabal"
    assert r_fleet.backend == "fleet"
    assert r_cabal.text == "ship it"
    assert r_fleet.text == "deploy"
    assert r_cabal.match_kind == "prefix"
    assert r_fleet.match_kind == "prefix"

    merged = routing.merged_backends(cfg)
    assert merged["cabal"]["fifo"] == str(fifo_cabal)
    assert merged["fleet"]["fifo"] == str(fifo_fleet)


def test_longest_prefix_cabal_vs_cabal2(bridge_tmp: Path) -> None:
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    sessions_d = bridge_tmp / ".sessions.d"
    _write_session(sessions_d, "cabal", bridge_tmp / "cabal.fifo")
    _write_session(sessions_d, "cabal2", bridge_tmp / "cabal2.fifo")

    cfg = {"sessions": {"dir": str(sessions_d)}}
    hit = routing.strip_prefix(cfg, "@cabal2 go")
    assert hit is not None
    assert hit[0] == "cabal2"
    assert hit[2] == "go"

    hit2 = routing.strip_prefix(cfg, "@cabal go")
    assert hit2 is not None
    assert hit2[0] == "cabal"
    assert hit2[2] == "go"


def test_strip_prefix_removes_handle(bridge_tmp: Path) -> None:
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    sessions_d = bridge_tmp / ".sessions.d"
    _write_session(sessions_d, "cabal", bridge_tmp / "cabal.fifo")
    cfg = {"sessions": {"dir": str(sessions_d)}}
    hit = routing.strip_prefix(cfg, "@cabal hello world")
    assert hit == ("cabal", "", "hello world")


def test_session_overrides_static_backend(bridge_tmp: Path) -> None:
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    sessions_d = bridge_tmp / ".sessions.d"
    fifo_dyn = bridge_tmp / "dyn.fifo"
    _write_session(sessions_d, "grok", fifo_dyn)
    cfg = {
        "sessions": {"dir": str(sessions_d)},
        "backends": {
            "grok": {
                "fifo": str(bridge_tmp / "static.fifo"),
                "prefixes": ["@grok"],
                "delivery": "fifo",
            }
        },
    }
    merged = routing.merged_backends(cfg)
    assert merged["grok"]["fifo"] == str(fifo_dyn)


def test_register_session_creates_fifo_and_json(bridge_tmp: Path) -> None:
    reg = REPO / "scripts" / "register-session.sh"
    sessions_d = bridge_tmp / ".sessions.d"
    fifo = bridge_tmp / "sessions" / "alpha.fifo"
    proc = subprocess.run(
        [
            str(reg),
            "--handle",
            "alpha",
            "--fifo",
            str(fifo),
            "--sessions-dir",
            str(sessions_d),
            "--pid",
            "99999",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert fifo.is_fifo() or fifo.exists()
    jpath = sessions_d / "alpha.json"
    assert jpath.is_file()
    data = json.loads(jpath.read_text(encoding="utf-8"))
    assert data["handle"] == "alpha"
    assert data["fifo"] == str(fifo)
    assert "@alpha" in data["prefixes"]


def test_env_sessions_dir_is_honored(bridge_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """$RELAY_SESSIONS_DIR is the second documented source — it must work alone.

    No cfg["sessions"]["dir"], no _bridge_dir: only the environment.
    """
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    sessions_d = bridge_tmp / "env-sessions.d"
    _write_session(sessions_d, "envhandle", bridge_tmp / "envhandle.fifo")
    monkeypatch.setenv("RELAY_SESSIONS_DIR", str(sessions_d))

    assert routing.strip_prefix({}, "@envhandle go") == ("envhandle", "", "go")


def test_no_sessions_source_does_not_read_home_registry(
    bridge_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cfg that says nothing about sessions must not inherit $HOME's registry.

    sessions_dir_from_cfg() falls all the way through to
    ~/.claude/telegram-bridge/.sessions.d when nothing else is set. That default
    is right for the relay process itself, which IS that bridge — but routing
    resolves configs that are not the live bridge (a CI checkout, another
    relay.toml, `python lib/routing.py resolve` in a scratch dir). Those must see
    static [backends.*] only, never whatever handles happen to be registered on
    the host.
    """
    sys.path.insert(0, str(REPO))
    from tg_agent_relay import routing

    fake_home = bridge_tmp / "home"
    home_sessions = fake_home / ".claude" / "telegram-bridge" / ".sessions.d"
    _write_session(home_sessions, "hostonly", fake_home / "hostonly.fifo")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("RELAY_SESSIONS_DIR", raising=False)

    cfg = {"backends": {"claude": {"tag": "claude", "prefixes": ["@claude"]}}}
    assert routing.strip_prefix(cfg, "@hostonly go") is None
    assert routing.strip_prefix(cfg, "@claude go") == ("claude", "", "go")
