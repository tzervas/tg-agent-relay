"""Durable inbound spool — inbound lines survive when no agent reader is attached.

Why this exists
---------------
``deliver_to_backend`` writes inbound Telegram lines into a backend FIFO. But
``scripts/ensure-inbound.sh`` holds every FIFO open ``RDWR`` (a *keepalive*) so
writers never ``ENXIO``. The side effect is that a write **always succeeds into
the 64K kernel pipe buffer even when no agent Monitor is attached** — the line
is not delivered into the agent, it just rots in the buffer until some future
reader attaches (then dumps as one burst), or until the buffer fills and every
subsequent write fails and is dropped for good.

Detecting that (``message_orphaned``) is not enough: detection without recovery
still loses the message. This module is the recovery layer. When no agent
reader is attested, the line is spooled to disk instead of being written into
the void, and ``adapters/backend-fifo-reader.sh`` replays the spool — in order
— the moment a session actually attaches.

On-disk layout
--------------
``<bridge>/.run/spool/<backend>/<stamp>.msg``

``stamp`` is ``<time_ns:019d>-<pid:07d>-<counter:04d>``, so a plain lexical
sort of the directory is chronological order. Files are written ``.tmp`` then
atomically renamed in, so a partially written file is never drained.

Drain protocol
--------------
A drainer *claims* a file by ``os.rename`` to ``<name>.claimed.<pid>``. Rename
is atomic, so if two drainers race, exactly one wins and the loser sees
``FileNotFoundError`` and moves on — a line is never delivered twice. If a
drainer dies mid-flight the claim is left behind; :func:`reclaim_stale` returns
those to pending after ``RELAY_SPOOL_CLAIM_TTL`` seconds (default 300).

Bounding
--------
The spool is capped at ``RELAY_SPOOL_MAX`` lines per backend (default 1000).
On overflow the *oldest* lines are dropped, and each drop emits a
``spool_overflow`` metric — a bounded queue that truncates silently would
recreate the very failure this module exists to fix.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from tg_agent_relay.metrics import emit_metric

__all__ = [
    "DEFAULT_CLAIM_TTL",
    "DEFAULT_SPOOL_MAX",
    "drain",
    "pending_count",
    "pending_paths",
    "reclaim_stale",
    "spool_dir",
    "spool_message",
]

DEFAULT_SPOOL_MAX = 1000
DEFAULT_CLAIM_TTL = 300

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
_counter = 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_backend(backend: str) -> str:
    """Sanitize a backend id for use as a directory name.

    Backend ids come from ``relay.toml``, but they also appear in routed
    inbound tags, so never let one escape the spool root via ``..`` or ``/``.
    """
    cleaned = "".join(c if c in _SAFE else "_" for c in str(backend))
    cleaned = cleaned.strip(".") or "_"
    return cleaned


def spool_dir(backend: str, bridge_dir: Path | str | None = None) -> Path:
    """Directory holding pending lines for *backend*."""
    root = Path(bridge_dir) if bridge_dir else _repo_root()
    return root / ".run" / "spool" / _safe_backend(backend)


def _spool_max() -> int:
    raw = os.environ.get("RELAY_SPOOL_MAX", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SPOOL_MAX
    return val if val > 0 else DEFAULT_SPOOL_MAX


def _claim_ttl() -> int:
    raw = os.environ.get("RELAY_SPOOL_CLAIM_TTL", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CLAIM_TTL
    return val if val > 0 else DEFAULT_CLAIM_TTL


def pending_paths(backend: str, bridge_dir: Path | str | None = None) -> list[Path]:
    """Pending spool files for *backend*, oldest first."""
    d = spool_dir(backend, bridge_dir)
    try:
        return sorted(p for p in d.iterdir() if p.suffix == ".msg" and p.is_file())
    except OSError:
        return []


def pending_count(backend: str, bridge_dir: Path | str | None = None) -> int:
    """Number of lines waiting for an agent reader."""
    return len(pending_paths(backend, bridge_dir))


def _enforce_cap(backend: str, bridge_dir: Path | str | None) -> None:
    """Drop oldest lines beyond the cap, loudly."""
    cap = _spool_max()
    pending = pending_paths(backend, bridge_dir)
    excess = len(pending) - cap
    if excess <= 0:
        return
    for victim in pending[:excess]:
        try:
            victim.unlink()
        except OSError:
            continue
        emit_metric(
            "spool",
            "spool_overflow",
            f"backend={backend} dropped={victim.name} cap={cap}",
            bridge_dir=bridge_dir,
        )


def spool_message(
    backend: str,
    line: str,
    bridge_dir: Path | str | None = None,
    *,
    reason: str = "",
) -> Path | None:
    """Persist one inbound *line* for *backend*. Returns the spool path, or None.

    *line* is stored exactly as it would have been written to the FIFO (the
    trailing newline is not stored; :func:`drain` re-adds it). Returning None
    means the spool itself was unwritable — the caller must not report the
    message as delivered.
    """
    global _counter
    d = spool_dir(backend, bridge_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        emit_metric(
            "spool",
            "spool_error",
            f"backend={backend} reason=mkdir_failed",
            bridge_dir=bridge_dir,
        )
        return None

    _counter = (_counter + 1) % 10000
    stamp = f"{time.time_ns():019d}-{os.getpid():07d}-{_counter:04d}"
    final = d / f"{stamp}.msg"
    tmp = d / f"{stamp}.tmp"
    payload = line[:-1] if line.endswith("\n") else line

    try:
        # Write-then-rename so a drainer never sees a half-written line.
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, final)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        emit_metric(
            "spool",
            "spool_error",
            f"backend={backend} reason=write_failed",
            bridge_dir=bridge_dir,
        )
        return None

    detail = f"backend={backend} pending={pending_count(backend, bridge_dir)}"
    if reason:
        detail += f" reason={reason}"
    emit_metric("spool", "message_spooled", detail, bridge_dir=bridge_dir)
    _enforce_cap(backend, bridge_dir)
    return final


def reclaim_stale(
    backend: str,
    bridge_dir: Path | str | None = None,
    *,
    ttl: int | None = None,
    now: float | None = None,
) -> int:
    """Return claims abandoned by dead drainers to pending. Returns count."""
    d = spool_dir(backend, bridge_dir)
    cutoff = (now if now is not None else time.time()) - (
        ttl if ttl is not None else _claim_ttl()
    )
    recovered = 0
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    for path in entries:
        if ".claimed." not in path.name:
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            original = path.name.split(".claimed.", 1)[0]
            os.rename(path, d / original)
            recovered += 1
        except OSError:
            continue
    if recovered:
        emit_metric(
            "spool",
            "spool_reclaimed",
            f"backend={backend} count={recovered}",
            bridge_dir=bridge_dir,
        )
    return recovered


def drain(
    backend: str,
    emit,
    bridge_dir: Path | str | None = None,
    *,
    limit: int = 0,
    reclaim: bool = True,
) -> int:
    """Replay spooled lines for *backend* in order. Returns lines emitted.

    *emit* is called once per line (without a trailing newline). A line is
    unlinked only *after* ``emit`` returns, so a crash mid-emit leaves the claim
    behind for :func:`reclaim_stale` rather than losing the message. If ``emit``
    raises, the claim is released immediately and the drain stops — the line
    stays pending rather than being dropped.
    """
    if reclaim:
        reclaim_stale(backend, bridge_dir)

    d = spool_dir(backend, bridge_dir)
    sent = 0
    for path in pending_paths(backend, bridge_dir):
        if limit and sent >= limit:
            break
        claimed = d / f"{path.name}.claimed.{os.getpid()}"
        try:
            # Atomic claim: the loser of a race gets FileNotFoundError.
            os.rename(path, claimed)
        except OSError:
            continue
        try:
            text = claimed.read_text(encoding="utf-8")
        except OSError:
            with contextlib.suppress(OSError):
                claimed.unlink()
            continue
        try:
            emit(text)
        except Exception:
            # Put it back — an emit failure must not consume the message.
            with contextlib.suppress(OSError):
                os.rename(claimed, path)
            raise
        with contextlib.suppress(OSError):
            claimed.unlink()
        sent += 1

    if sent:
        emit_metric(
            "spool",
            "spool_drained",
            f"backend={backend} count={sent}",
            bridge_dir=bridge_dir,
        )
    return sent


def main(argv: list[str] | None = None) -> int:
    """CLI so shell callers share this module's on-disk format.

    ``tg-poll.sh`` and ``backend-fifo-reader.sh`` go through here rather than
    reimplementing the spool layout in bash — one format, one implementation.

      python -m tg_agent_relay.spool put   <backend> [--reason R]  # line on stdin
      python -m tg_agent_relay.spool drain <backend>               # lines to stdout
      python -m tg_agent_relay.spool count <backend>
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(
            "usage: spool.py {put|drain|count} <backend> "
            "[--bridge-dir PATH] [--reason R]",
            file=sys.stderr,
        )
        return 2

    action, backend = args[0], args[1]
    rest = args[2:]
    bridge: str | None = None
    reason = ""
    i = 0
    while i < len(rest):
        if rest[i] == "--bridge-dir" and i + 1 < len(rest):
            bridge = rest[i + 1]
            i += 2
        elif rest[i] == "--reason" and i + 1 < len(rest):
            reason = rest[i + 1]
            i += 2
        else:
            i += 1

    if action == "put":
        line = sys.stdin.read()
        # Exit 1 (not 0) when the spool is unwritable so the caller never
        # reports a message as safely queued when it is not.
        return 0 if spool_message(backend, line, bridge, reason=reason) else 1

    if action == "drain":

        def emit(text: str) -> None:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

        drain(backend, emit, bridge)
        return 0

    if action == "count":
        print(pending_count(backend, bridge))
        return 0

    print(f"spool.py: unknown action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
