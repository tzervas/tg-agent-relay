#!/usr/bin/env python3
"""Detect agent harness readers on backend FIFOs (not keepalives).

Keepalives from ``scripts/ensure-inbound.sh`` open FIFOs RDWR and sleep so
writers avoid ENXIO. They do **not** consume lines. Agent Monitors run
``adapters/backend-fifo-reader.sh`` (or a systemd ``tgar-session@`` unit).

A FIFO with only keepalives is an *orphan* sink: tg-poll may report
``message_delivered`` while no agent ever sees the line. Poll then emits
``message_orphaned backend=… reason=no_agent_reader``.

Pure helpers are unit-tested offline (inject ``proc_root``); production walks
``/proc/*/fd`` on Linux.
"""

from __future__ import annotations

import os
from pathlib import Path

# Cmdline markers for real agent FIFO readers (Monitor / systemd unit).
AGENT_READER_MARKERS: tuple[str, ...] = ("backend-fifo-reader", "tgar-session@")
# Keepalive holders (ensure-inbound RDWR sleep) are NOT agent readers.
KEEPALIVE_MARKERS: tuple[str, ...] = ("fifo-keepalive", "fifo-ka-")


def cmdline_text(raw: bytes | str) -> str:
    """Normalize /proc cmdline (NUL-separated bytes or str) to a single string."""
    if isinstance(raw, bytes):
        return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
    return raw.replace("\0", " ")


def _argv_parts(raw: bytes | str) -> list[str]:
    """Split cmdline into argv elements.

    Prefer NUL-separated /proc form so a giant ``bash -c '…mention…'`` stays
    one argv element and does not false-positive on embedded path text.
    """
    if isinstance(raw, bytes):
        return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
    if "\0" in raw:
        return [p for p in raw.split("\0") if p]
    return raw.split()


def is_agent_reader_cmdline(cmdline: bytes | str) -> bool:
    """True if argv looks like an agent FIFO reader (not keepalive / writer).

    Agent readers: argv base name ``backend-fifo-reader[.sh]`` (Monitor) or an
    argv element containing ``tgar-session@`` (systemd unit). Keepalive
    holders that only RDWR-open the fifo so writers avoid ENXIO are excluded.

    Matching is per-argv (not a full-string substring search) so long agent
    shells that merely *mention* the reader path in a ``bash -c`` script are
    not counted as Monitors.
    """
    parts = _argv_parts(cmdline)
    if not parts:
        return False
    joined = " ".join(parts).lower()
    if any(m in joined for m in KEEPALIVE_MARKERS):
        return False
    for p in parts:
        # Per-element basename only — never substring-search a giant bash -c blob
        # (operator notes often *mention* backend-fifo-reader / tgar-session@).
        raw_p = p.strip()
        if not raw_p or "\n" in raw_p or len(raw_p) > 240:
            # Long / multi-line argv is almost always `bash -c '…docs…'`, not a unit.
            continue
        base = os.path.basename(raw_p.lower())
        if base in ("backend-fifo-reader.sh", "backend-fifo-reader"):
            return True
        # systemd unit argv: tgar-session@fleet.service or systemd:tgar-session@…
        if base.startswith("tgar-session@") or "tgar-session@" in base:
            return True
    return False


def fifo_path_in_cmdline(cmdline: bytes | str, fifo: str | Path) -> bool:
    """True if *fifo* appears as an argv element (full path, not basename-only).

    Basename-only matching is intentionally avoided: many shells mention
    ``fleet.fifo`` / ``cabal.fifo`` in unrelated ``bash -c`` text.
    """
    parts = _argv_parts(cmdline)
    if not parts:
        return False
    fifo_s = os.path.expanduser(str(fifo))
    try:
        fifo_real = str(Path(fifo_s).resolve())
    except OSError:
        fifo_real = fifo_s
    for p in parts:
        pe = os.path.expanduser(p)
        if pe in (fifo_s, fifo_real):
            return True
        try:
            if Path(pe).resolve() == Path(fifo_real):
                return True
        except OSError:
            continue
    return False


def fd_open_for_read(flags_octal: int) -> bool:
    """True if open flags allow reading (O_RDONLY or O_RDWR), not O_WRONLY-only."""
    acc = flags_octal & 0o3  # O_ACCMODE
    return acc in (0o0, 0o2)  # O_RDONLY, O_RDWR


def parse_fdinfo_flags(fdinfo: str) -> int | None:
    """Parse Linux /proc/pid/fdinfo flags line → int, or None."""
    for line in fdinfo.splitlines():
        if line.startswith("flags:"):
            raw = line.split(":", 1)[1].strip()
            try:
                return int(raw, 8)
            except ValueError:
                try:
                    return int(raw, 0)
                except ValueError:
                    return None
    return None


def _inode_tuple(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _fd_matches_fifo(
    link_target: str,
    fifo_path: Path,
    fifo_ino: tuple[int, int] | None,
    *,
    proc_fd_path: Path | None = None,
) -> bool:
    """Whether a /proc/pid/fd/N symlink target refers to fifo_path."""
    target = link_target.strip()
    if not target:
        return False
    if proc_fd_path is not None and fifo_ino is not None:
        try:
            st = proc_fd_path.stat()
            if (st.st_dev, st.st_ino) == fifo_ino:
                return True
        except OSError:
            pass
    try:
        resolved = Path(os.path.expanduser(target)).resolve()
        if resolved == fifo_path.resolve():
            return True
    except OSError:
        pass
    if target == str(fifo_path) or os.path.expanduser(target) == str(fifo_path):
        return True
    if fifo_ino is not None and target.startswith("pipe:["):
        try:
            ino = int(target[6:-1])
            if ino == fifo_ino[1]:
                return True
        except ValueError:
            pass
    return False


def count_agent_readers(
    fifo: str | Path,
    *,
    proc_root: str | Path = "/proc",
) -> list[int]:
    """Return PIDs of live agent readers for *fifo* via cmdline scan.

    Scans ``proc_root/<pid>/cmdline`` for agent-reader markers that also
    reference *fifo*. Does not count keepalive holders. Useful when fd
    scanning is unavailable; prefer :func:`fifo_has_agent_reader` for honesty
    checks that require the fifo open for read.
    """
    fifo_s = os.path.expanduser(str(fifo))
    root = Path(proc_root)
    pids: list[int] = []
    if not root.is_dir():
        return pids
    try:
        entries = list(root.iterdir())
    except OSError:
        return pids
    for ent in entries:
        name = ent.name
        if not name.isdigit():
            continue
        cmd_path = ent / "cmdline"
        try:
            raw = cmd_path.read_bytes()
        except OSError:
            continue
        if not is_agent_reader_cmdline(raw):
            continue
        if not fifo_path_in_cmdline(raw, fifo_s):
            continue
        pids.append(int(name))
    return sorted(pids)


def fifo_has_agent_reader(
    fifo_path: str | Path,
    *,
    proc_root: Path | str | None = None,
) -> bool:
    """Return True if any process holds *fifo_path* open for read as an agent reader.

    Scans ``/proc/*/fd`` on Linux (injectable via *proc_root* for tests).

    An **agent reader** is a process whose cmdline contains ``backend-fifo-reader``
    or ``tgar-session@``. FIFO keepalives from ``ensure-inbound.sh`` (cmdline
    contains ``fifo-keepalive``) do **not** count — they only prevent writer
    ENXIO and do not deliver lines into the agent TUI.

    When ``/proc`` is unavailable (non-Linux) and *proc_root* is not injected,
    returns True so we do not spam false ``message_orphaned`` metrics. When
    *proc_root* is injected (tests), a missing tree means no reader → False.

    Operators: a successful FIFO write without an agent reader may still
    succeed (kernel buffer accepts bytes) but is not delivery into the agent —
    attach a Monitor with ``adapters/backend-fifo-reader.sh <fifo>``.
    """
    fifo = Path(os.path.expanduser(str(fifo_path)))
    injected = proc_root is not None
    root = Path(proc_root) if proc_root is not None else Path("/proc")
    if not root.is_dir():
        return not injected

    fifo_ino = _inode_tuple(fifo) if fifo.exists() else None

    try:
        pid_dirs = list(root.iterdir())
    except OSError:
        return not injected

    for pid_dir in pid_dirs:
        name = pid_dir.name
        if not name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        if not fd_dir.is_dir():
            # Fall back to cmdline-only match for this pid (partial fake trees).
            cmd_path = pid_dir / "cmdline"
            try:
                raw = cmd_path.read_bytes()
            except OSError:
                continue
            if is_agent_reader_cmdline(raw) and fifo_path_in_cmdline(raw, fifo):
                return True
            continue
        try:
            fd_entries = list(fd_dir.iterdir())
        except OSError:
            continue

        holds_read = False
        for fd_link in fd_entries:
            try:
                target = os.readlink(fd_link)
            except OSError:
                continue
            if not _fd_matches_fifo(target, fifo, fifo_ino, proc_fd_path=fd_link):
                continue
            flags: int | None = None
            fdinfo_path = pid_dir / "fdinfo" / fd_link.name
            try:
                flags = parse_fdinfo_flags(
                    fdinfo_path.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                flags = None
            if flags is not None and not fd_open_for_read(flags):
                continue
            holds_read = True
            break

        if not holds_read:
            continue

        cmdline_path = pid_dir / "cmdline"
        try:
            cmdline = cmdline_path.read_bytes()
        except OSError:
            continue
        if is_agent_reader_cmdline(cmdline):
            return True

    return False


# Underscore alias used by poll.py / tests that prefer private naming.
_fifo_has_agent_reader = fifo_has_agent_reader


def main() -> int:
    """CLI: ``fifo_agent_readers.py <fifo>`` → print count and has_reader."""
    import sys

    if len(sys.argv) < 2:
        print("usage: fifo_agent_readers.py <fifo-path>", file=sys.stderr)
        return 2
    fifo = sys.argv[1]
    pids = count_agent_readers(fifo)
    has = fifo_has_agent_reader(fifo)
    print(f"count={len(pids)}")
    print(f"has_agent_reader={1 if has else 0}")
    if pids:
        print("pids=" + ",".join(str(p) for p in pids))
    # Always exit 0 on successful probe so shell pipelines with set -o pipefail
    # can parse stdout; callers inspect has_agent_reader / count.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
