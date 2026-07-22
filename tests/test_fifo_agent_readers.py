"""Unit tests for lib/fifo_agent_readers.py (orphan / agent-reader detection)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from fifo_agent_readers import (  # noqa: E402
    count_agent_readers,
    fifo_has_agent_reader,
    fifo_path_in_cmdline,
    is_agent_reader_cmdline,
    parse_fdinfo_flags,
)


@pytest.mark.parametrize(
    "cmdline,expected",
    [
        ("bash /bridge/adapters/backend-fifo-reader.sh /tmp/fleet.fifo", True),
        (b"bash\0/bridge/adapters/backend-fifo-reader.sh\0/tmp/x.fifo\0", True),
        ("/usr/bin/bash -c tgar-session@fleet.service", True),
        ("bash -c fifo-keepalive: label=backend-fleet", False),
        ("bash -c fifo-ka-root_fleet.fifo sleep 3600", False),
        ("tg-poll.sh", False),
        ("", False),
        (b"", False),
    ],
)
def test_is_agent_reader_cmdline(cmdline: bytes | str, expected: bool) -> None:
    assert is_agent_reader_cmdline(cmdline) is expected


@pytest.mark.parametrize(
    "cmdline,fifo,expected",
    [
        (
            "bash /x/adapters/backend-fifo-reader.sh /tmp/sessions/fleet.fifo",
            "/tmp/sessions/fleet.fifo",
            True,
        ),
        (
            "bash /x/adapters/backend-fifo-reader.sh /tmp/sessions/cabal.fifo",
            "/tmp/sessions/fleet.fifo",
            False,
        ),
        (
            b"bash\0backend-fifo-reader.sh\0/tmp/sessions/fleet.fifo\0",
            "/tmp/sessions/fleet.fifo",
            True,
        ),
        (
            "backend-fifo-reader.sh fleet.fifo",
            "/tmp/sessions/fleet.fifo",
            False,  # basename-only must not match (too loose)
        ),
        (
            # /proc form: entire bash -c script is ONE argv after -c
            b"bash\0-c\0echo backend-fifo-reader.sh /tmp/sessions/fleet.fifo\0",
            "/tmp/sessions/fleet.fifo",
            False,  # path embedded in -c blob, not its own argv element
        ),
    ],
)
def test_fifo_path_in_cmdline(
    cmdline: bytes | str, fifo: str, expected: bool
) -> None:
    assert fifo_path_in_cmdline(cmdline, fifo) is expected


def test_count_agent_readers_fake_proc(tmp_path: Path) -> None:
    """Walk a synthetic /proc tree and count only matching agent readers."""
    fifo = tmp_path / "fleet.fifo"
    fifo.write_text("")  # plain file is fine for path matching

    def write_cmd(pid: str, parts: list[str]) -> None:
        d = tmp_path / "proc" / pid
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(p.encode() for p in parts) + b"\0")

    write_cmd(
        "100",
        ["bash", str(REPO / "adapters/backend-fifo-reader.sh"), str(fifo)],
    )
    write_cmd(
        "101",
        ["bash", "-c", "fifo-keepalive: label=x", "exec", "3<>" + str(fifo)],
    )
    write_cmd(
        "102",
        ["bash", str(REPO / "adapters/backend-fifo-reader.sh"), "/other/cabal.fifo"],
    )
    write_cmd("103", ["tg-poll.sh"])

    pids = count_agent_readers(fifo, proc_root=tmp_path / "proc")
    assert pids == [100]
    # cmdline-only tree (no fd/) still counts as agent reader via fallback
    assert fifo_has_agent_reader(fifo, proc_root=tmp_path / "proc") is True


def test_fifo_has_agent_reader_fd_scan(tmp_path: Path) -> None:
    """ /proc/*/fd + fdinfo: agent reader yes; keepalive / O_WRONLY no. """
    fifo = tmp_path / "cabal.fifo"
    os.mkfifo(fifo)
    proc = tmp_path / "proc"

    def add_pid(
        pid: str,
        cmdline: bytes,
        *,
        flags: str = "00",
        link_fifo: Path | None = None,
    ) -> None:
        pdir = proc / pid
        (pdir / "fd").mkdir(parents=True)
        (pdir / "fdinfo").mkdir(parents=True)
        target = link_fifo if link_fifo is not None else fifo
        (pdir / "fd" / "3").symlink_to(str(target.resolve()))
        (pdir / "fdinfo" / "3").write_text(f"pos:\t0\nflags:\t{flags}\n")
        (pdir / "cmdline").write_bytes(cmdline)

    add_pid(
        "10",
        b"bash\0-c\0fifo-keepalive: label=backend-cabal\0",
        flags="02",
    )
    assert fifo_has_agent_reader(fifo, proc_root=proc) is False

    add_pid(
        "11",
        b"bash\0"
        + str(REPO / "adapters/backend-fifo-reader.sh").encode()
        + b"\0"
        + str(fifo).encode()
        + b"\0",
        flags="00",
    )
    assert fifo_has_agent_reader(fifo, proc_root=proc) is True


def test_parse_fdinfo_flags_accmode() -> None:
    assert (parse_fdinfo_flags("flags:\t02000000\n") or 0) & 0o3 == 0
    assert (parse_fdinfo_flags("flags:\t01\n") or 0) & 0o3 == 0o1
    assert (parse_fdinfo_flags("flags:\t02\n") or 0) & 0o3 == 0o2


def test_doctor_inbound_script_help() -> None:
    """doctor-inbound.sh --help exits 0 and mentions default_backend."""
    import subprocess

    script = REPO / "scripts" / "doctor-inbound.sh"
    assert script.is_file()
    proc = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "default_backend" in out
    assert "agent reader" in out.lower() or "agent_readers" in out or "Monitor" in out


def test_doctor_inbound_orphan_exit(tmp_path: Path) -> None:
    """With default_backend=fleet fifo and no agent reader → exit 1."""
    import shutil
    import subprocess

    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "adapters").mkdir()
    (bridge / "adapters" / "backend-fifo-reader.sh").write_text("#!/bin/bash\n")
    (bridge / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "doctor-inbound.sh", bridge / "scripts" / "doctor-inbound.sh")
    (bridge / "scripts" / "doctor-inbound.sh").chmod(0o755)
    # Use real config loader + reader helper from the repo
    lib = bridge / "lib"
    lib.mkdir()
    for name in ("relay-config.sh", "python.sh", "toml_to_json.py", "sessions.py", "fifo_agent_readers.py"):
        src = REPO / "lib" / name
        if src.is_file():
            shutil.copy(src, lib / name)
    fifo = bridge / "fleet.fifo"
    os.mkfifo(fifo)
    cabal_fifo = bridge / "cabal.fifo"
    os.mkfifo(cabal_fifo)

    (bridge / "relay.toml").write_text(
        f"""
[routing]
default_backend = "fleet"

[backends.fleet]
type = "grok"
delivery = "fifo"
fifo = "{fifo}"
tag = "fleet"
prefixes = ["@fleet"]

[backends.cabal]
type = "grok"
delivery = "fifo"
fifo = "{cabal_fifo}"
tag = "cabal"
prefixes = ["@cabal"]
"""
    )

    # Use repo package so poll.fifo_has_agent_reader is available; no live
    # agent reader holds these temp FIFOs → doctor must exit 1 (orphan).
    proc = subprocess.run(
        ["bash", str(bridge / "scripts" / "doctor-inbound.sh"), "--bridge-dir", str(bridge)],
        cwd=str(bridge),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "default_backend=fleet" in combined
    assert "ORPHAN" in combined or "no agent reader" in combined
    assert "backend-fifo-reader.sh" in combined
