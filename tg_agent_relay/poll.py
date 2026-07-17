"""Inbound long-poll — Python port of tg-poll.sh (issue #27).

getUpdates long-poll with:
  - strict ALLOWED_USER_ID allowlist (+ ALLOWED_CHAT_ID / [[chats]] acceptance)
  - per-chat(+thread) durable reassembly buffers (RS-delimited)
  - command classify + dispatch (forward | relay)
  - route via tg_agent_relay.routing.resolve
  - deliver_to_backend: stdout | fifo | cmd

Join API (testable):
  classify_command, command_field, dispatch_command
  chat_is_accepted, buffer_paths, join_buffer_parts
  append_message, flush_buffer, flush_stale_buffers
  process_update, process_result, get_updates
  poll_loop (injectable HTTP + sleep + clock)

CLI:
  python -m tg_agent_relay.poll
  python -m tg_agent_relay.cli poll   (via main_poll entry)
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from tg_agent_relay.config import cfg_get, load_config
from tg_agent_relay.media_inbound import buffer_parts_for_update
from tg_agent_relay.metrics import emit_metric
from tg_agent_relay.routing import (
    has_routing_config,
    inbound_tag,
    project_worktree,
    resolve,
    strip_prefix,
)
from tg_agent_relay.send import load_env

# Record separator — never appears in normal Telegram text.
RS = "\x1e"
JOINER = " ⏎ "
_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]+")

EmitFn = Callable[[str], None]
SleepFn = Callable[[float], None]
NowFn = Callable[[], int]
GetUpdatesFn = Callable[[str, int, int, float], dict[str, Any] | None]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_id(value: str) -> str:
    return _SAFE_RE.sub("_", str(value))


def reassemble_window(cfg: dict[str, Any] | None = None) -> int:
    """Seconds of quiet before a buffered burst flushes (default 4)."""
    env = os.environ.get("TG_REASSEMBLE_WINDOW", "").strip()
    if env.isdigit():
        return int(env)
    if cfg is not None:
        raw = cfg_get(cfg, "general.reassemble_window", 4)
        try:
            n = int(raw)  # type: ignore[arg-type]
            if n >= 0:
                return n
        except (TypeError, ValueError) as _exc:
            pass
    return 4


def buffer_paths(
    bridge_dir: Path | str,
    chat_id: str = "",
    thread_id: str = "",
    *,
    multi_chat: bool = False,
) -> tuple[Path, Path]:
    """Return (buffer_file, buffer_ts_file) for this room.

    Multi-chat isolation when multi_chat and chat_id are set; else legacy
    single-buffer paths (.tg-buffer / .tg-buffer-ts).
    """
    root = Path(bridge_dir)
    if not multi_chat or not chat_id:
        return root / ".tg-buffer", root / ".tg-buffer-ts"
    safe = _safe_id(chat_id)
    if thread_id:
        safe = f"{safe}_t{_safe_id(thread_id)}"
    return root / f".tg-buffer.{safe}", root / f".tg-buffer-ts.{safe}"


def chat_is_accepted(
    cfg: dict[str, Any],
    chat_id: str,
    *,
    allowed_chat_id: str = "",
) -> bool:
    """True if this chat may receive bot traffic (shell chat_is_accepted).

    Always accepts legacy ALLOWED_CHAT_ID; when [[chats]] configured, also
    accepts those chat_ids. With neither set, accepts any chat (setup /
    discovery still keys off user id only).
    """
    cid = str(chat_id)
    if allowed_chat_id and cid == str(allowed_chat_id):
        return True
    if has_routing_config(cfg):
        chats = cfg.get("chats") or []
        if isinstance(chats, list):
            for row in chats:
                if isinstance(row, dict) and str(row.get("chat_id", "")) == cid:
                    return True
    # No routing / no ALLOWED_CHAT_ID yet: accept any chat from allowlisted user.
    return not bool(allowed_chat_id)


def classify_command(cfg: dict[str, Any], text: str) -> str:
    """Return commands.<name> key if text matches slash/keyword, else "".

    Backward-compat: empty when no [commands.*] section.
    Match rules (shell parity):
      slash: text == slash OR text starts with "slash "
      keyword: text == keyword OR starts with "keyword " OR "keyword:"
    """
    commands = cfg.get("commands")
    if not isinstance(commands, dict) or not commands:
        return ""
    for name, entry in commands.items():
        if not isinstance(entry, dict):
            continue
        slash = str(entry.get("slash") or "")
        keyword = str(entry.get("keyword") or "")
        if slash and (text == slash or text.startswith(slash + " ")):
            return str(name)
        if keyword and (
            text == keyword or text.startswith(keyword + " ") or text.startswith(keyword + ":")
        ):
            return str(name)
    return ""


def resolve_command_match(cfg: dict[str, Any], text: str) -> tuple[str, str]:
    """Classify command on text; if no match, retry after @handle strip.

    Returns (command_name, text_for_handler). Empty name means not a command.
    """
    name = classify_command(cfg, text)
    if name:
        return name, text
    if has_routing_config(cfg):
        hit = strip_prefix(cfg, text)
        if hit:
            stripped = hit[2]
            name2 = classify_command(cfg, stripped)
            if name2:
                return name2, stripped
    return "", ""


def command_field(cfg: dict[str, Any], name: str, field: str, default: str = "") -> str:
    """One relay.toml [commands.<name>].<field> value, or default."""
    commands = cfg.get("commands")
    if not isinstance(commands, dict):
        return default
    entry = commands.get(name)
    if not isinstance(entry, dict):
        return default
    val = entry.get(field)
    if val is None or val == "":
        return default
    return str(val)


def join_buffer_parts(raw: str) -> str:
    """Join RS-delimited buffer parts with JOINER; flatten internal newlines."""
    if not raw:
        return ""
    # Split on RS; trailing RS yields empty trailing part — drop empties only
    # at ends that are pure artifacts of append format "text\\x1e".
    parts = raw.split(RS)
    # Drop trailing empty from final RS terminator
    while parts and parts[-1] == "":
        parts.pop()
    out_parts: list[str] = []
    for part in parts:
        flat = part.replace("\n", " ").replace("\r", " ")
        out_parts.append(flat)
    return JOINER.join(out_parts)


def append_message(
    bridge_dir: Path | str,
    text: str,
    chat_id: str = "",
    thread_id: str = "",
    *,
    multi_chat: bool = False,
    now: int | None = None,
) -> None:
    """Append text to durable buffer BEFORE offset advance (crash-safe)."""
    buf, ts = buffer_paths(bridge_dir, chat_id, thread_id, multi_chat=multi_chat)
    root = Path(bridge_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        with buf.open("a", encoding="utf-8") as f:
            f.write(text + RS)
        ts.write_text(str(now if now is not None else int(time.time())), encoding="utf-8")
        meta = Path(str(buf) + ".meta")
        meta.write_text(f"{chat_id}|{thread_id}\n", encoding="utf-8")
    except OSError as _exc:
        pass


def _read_meta(buf: Path) -> tuple[str, str]:
    meta = Path(str(buf) + ".meta")
    if not meta.is_file():
        return "", ""
    try:
        raw = meta.read_text(encoding="utf-8").strip()
    except OSError as _exc:
        return "", ""
    if "|" not in raw:
        return raw, ""
    chat_id, _, thread_id = raw.partition("|")
    return chat_id, thread_id


def _clear_buffer(buf: Path, ts: Path) -> None:
    try:
        if buf.is_file():
            buf.write_text("", encoding="utf-8")
        if ts.is_file():
            ts.unlink()
        meta = Path(str(buf) + ".meta")
        if meta.is_file():
            meta.unlink()
    except OSError as _exc:
        pass


def _any_nonempty_buffer(bridge_dir: Path) -> bool:
    legacy = bridge_dir / ".tg-buffer"
    if legacy.is_file() and legacy.stat().st_size > 0:
        return True
    try:
        for p in bridge_dir.glob(".tg-buffer.*"):
            if p.name.endswith(".meta"):
                continue
            if ".tg-buffer-ts" in p.name:
                continue
            if p.is_file() and p.stat().st_size > 0:
                return True
    except OSError as _exc:
        return False
    return False


def _run_handler(
    bridge_dir: Path,
    handler: str,
    text: str,
    *,
    chat_id: str = "",
    thread_id: str = "",
    detach: bool = True,
) -> None:
    """Launch relay-mode handler (absolute or relative to bridge_dir)."""
    if not handler:
        return
    path = Path(handler)
    if not path.is_absolute():
        path = bridge_dir / handler
    if not path.is_file() or not os.access(path, os.X_OK):
        return
    env = os.environ.copy()
    env["RELAY_CHAT_ID"] = chat_id or env.get("RELAY_CHAT_ID", "")
    env["RELAY_THREAD_ID"] = thread_id or env.get("RELAY_THREAD_ID", "")
    try:
        if detach:
            subprocess.Popen(
                [str(path), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=str(bridge_dir),
            )
        else:
            subprocess.run(
                [str(path), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=str(bridge_dir),
                check=False,
                timeout=30,
            )
    except (OSError, subprocess.SubprocessError) as _exc:
        pass


def dispatch_command(
    cfg: dict[str, Any],
    name: str,
    text: str,
    *,
    bridge_dir: Path | str | None = None,
    chat_id: str = "",
    thread_id: str = "",
    detach_handlers: bool = True,
) -> list[str]:
    """Route a classified command (forward tag line or relay handler).

    Returns stdout lines (empty for mode=relay).
    """
    root = Path(bridge_dir) if bridge_dir else _repo_root()
    mode = command_field(cfg, name, "mode", "forward")
    if mode == "relay":
        emit_metric("tg-poll", "command_relay_handled", name, bridge_dir=root)
        handler = command_field(cfg, name, "handler", "")
        _run_handler(
            root,
            handler,
            text,
            chat_id=chat_id,
            thread_id=thread_id,
            detach=detach_handlers,
        )
        return []
    tag = command_field(cfg, name, "tag", name)
    emit_metric("tg-poll", "command_forwarded", name, bridge_dir=root)
    return [f"[telegram:cmd:{tag}] {text}"]


def deliver_to_backend(
    cfg: dict[str, Any],
    backend: str,
    project: str,
    text: str,
    chat_id: str = "",
    thread_id: str = "",
    *,
    bridge_dir: Path | str | None = None,
    detach_cmd: bool = True,
) -> list[str]:
    """Route flushed text to stdout | fifo | cmd. Returns stdout lines."""
    root = Path(bridge_dir) if bridge_dir else _repo_root()
    if not backend:
        emit_metric("tg-poll", "message_flushed", "legacy", bridge_dir=root)
        return [f"[telegram] {text}"]

    backends = cfg.get("backends") if isinstance(cfg.get("backends"), dict) else {}
    bcfg = backends.get(backend) if isinstance(backends, dict) else None  # type: ignore[assignment]
    if not isinstance(bcfg, dict):
        bcfg = {}
    delivery = str(bcfg.get("delivery") or "stdout")
    tag = inbound_tag(backend, project)
    filter_backend = os.environ.get("TG_POLL_BACKEND", "")

    if delivery == "stdout" and filter_backend and filter_backend != backend:
        emit_metric(
            "tg-poll",
            "message_filtered",
            f"backend={backend} want={filter_backend}",
            bridge_dir=root,
        )
        return []

    if delivery == "fifo":
        fifo = str(bcfg.get("fifo") or "")
        fifo = os.path.expanduser(fifo)
        if not fifo:
            emit_metric(
                "tg-poll",
                "deliver_skip",
                f"backend={backend} reason=no_fifo",
                bridge_dir=root,
            )
            return []
        try:
            fifo_path = Path(fifo)
            fifo_path.parent.mkdir(parents=True, exist_ok=True)
            if not fifo_path.exists():
                os.mkfifo(fifo_path)  # type: ignore[arg-type]
        except OSError as _exc:
            pass
        line = f"{tag} {text}\n"
        try:
            # Best-effort non-blocking-ish write (may fail without a reader).
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            emit_metric(
                "tg-poll",
                "message_delivered",
                f"backend={backend} mode=fifo",
                bridge_dir=root,
            )
        except OSError as _exc:
            emit_metric(
                "tg-poll",
                "deliver_skip",
                f"backend={backend} reason=fifo_timeout",
                bridge_dir=root,
            )
        return []

    if delivery == "cmd":
        cmd = bcfg.get("cmd")
        if cmd is None or cmd == "" or cmd == []:
            emit_metric(
                "tg-poll",
                "deliver_skip",
                f"backend={backend} reason=no_cmd",
                bridge_dir=root,
            )
            return []
        cwd = project_worktree(cfg, project, backend)
        env = os.environ.copy()
        env["RELAY_TEXT"] = text
        env["RELAY_BACKEND"] = backend
        env["RELAY_PROJECT"] = project
        env["RELAY_CHAT_ID"] = chat_id
        env["RELAY_THREAD_ID"] = thread_id
        env["RELAY_CWD"] = cwd
        env["RELAY_MODEL"] = str(bcfg.get("model") or "")
        run_cwd = cwd if cwd and Path(cwd).is_dir() else None
        try:
            if isinstance(cmd, list):
                argv = [str(x) for x in cmd]
                popen_args: Sequence[str] | str = argv
                shell = False
            else:
                popen_args = str(cmd)
                shell = True
            if detach_cmd:
                subprocess.Popen(
                    popen_args,
                    shell=shell,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    cwd=run_cwd,
                    stdin=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    popen_args,
                    shell=shell,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    cwd=run_cwd,
                    stdin=subprocess.DEVNULL,
                    check=False,
                    timeout=30,
                )
            emit_metric(
                "tg-poll",
                "message_delivered",
                f"backend={backend} mode=cmd",
                bridge_dir=root,
            )
        except (OSError, subprocess.SubprocessError) as _exc:
            emit_metric(
                "tg-poll",
                "deliver_skip",
                f"backend={backend} reason=cmd_fail",
                bridge_dir=root,
            )
        return []

    # stdout (default)
    emit_metric("tg-poll", "message_flushed", f"backend={backend}", bridge_dir=root)
    return [f"{tag} {text}"]


def _notify_route_none(bridge_dir: Path, chat_id: str, thread_id: str) -> None:
    """Background help nudge when require_prefix blocks (shell parity)."""
    notify = bridge_dir / "relay-notify.sh"
    if not notify.is_file():
        return
    env = os.environ.copy()
    env["RELAY_CHAT_ID"] = chat_id
    env["RELAY_THREAD_ID"] = thread_id
    msg = (
        "No backend matched. Prefix with @claude / @grok / @ollama, "
        "or configure [routing].default_backend."
    )
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.Popen(
            [str(notify), "--raw", msg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(bridge_dir),
        )


def flush_buffer(
    bridge_dir: Path | str,
    cfg: dict[str, Any],
    chat_id: str = "",
    thread_id: str = "",
    *,
    multi_chat: bool | None = None,
    detach: bool = True,
) -> list[str]:
    """Emit buffered burst for this room. At most one stdout line (or relay 0)."""
    root = Path(bridge_dir)
    if multi_chat is None:
        multi_chat = has_routing_config(cfg)
    buf, ts = buffer_paths(root, chat_id, thread_id, multi_chat=multi_chat)
    if not buf.is_file() or buf.stat().st_size == 0:
        return []
    if not chat_id:
        chat_id, thread_id = _read_meta(buf)
    try:
        raw = buf.read_text(encoding="utf-8")
    except OSError as _exc:
        return []
    out = join_buffer_parts(raw)
    if not out:
        _clear_buffer(buf, ts)
        return []

    lines: list[str] = []
    work_cfg = cfg if cfg.get("_bridge_dir") else {**cfg, "_bridge_dir": str(root)}
    name, cmd_text = resolve_command_match(work_cfg, out)
    if name:
        os.environ["RELAY_CHAT_ID"] = chat_id
        os.environ["RELAY_THREAD_ID"] = thread_id
        lines = dispatch_command(
            cfg,
            name,
            cmd_text,
            bridge_dir=root,
            chat_id=chat_id,
            thread_id=thread_id,
            detach_handlers=detach,
        )
    elif has_routing_config(cfg):
        result = resolve(cfg, chat_id, thread_id, out)
        stripped = result.text
        if not stripped and out and result.match_kind != "none":
            stripped = out
        if result.match_kind == "none":
            emit_metric("tg-poll", "route_none", "", bridge_dir=root)
            os.environ["RELAY_CHAT_ID"] = chat_id
            os.environ["RELAY_THREAD_ID"] = thread_id
            _notify_route_none(root, chat_id, thread_id)
        else:
            lines = deliver_to_backend(
                cfg,
                result.backend,
                result.project,
                stripped or out,
                chat_id,
                thread_id,
                bridge_dir=root,
                detach_cmd=detach,
            )
    else:
        emit_metric("tg-poll", "message_flushed", "", bridge_dir=root)
        lines = [f"[telegram] {out}"]

    _clear_buffer(buf, ts)
    return lines


def flush_stale_buffers(
    bridge_dir: Path | str,
    cfg: dict[str, Any],
    *,
    window: int | None = None,
    now: int | None = None,
    detach: bool = True,
) -> list[str]:
    """Quiet-window flush across legacy + per-chat buffer timestamp files."""
    root = Path(bridge_dir)
    if window is None:
        window = reassemble_window(cfg)
    now_ts = now if now is not None else int(time.time())
    lines: list[str] = []
    ts_files: list[Path] = []
    legacy_ts = root / ".tg-buffer-ts"
    if legacy_ts.is_file():
        ts_files.append(legacy_ts)
    with contextlib.suppress(OSError, PermissionError):
        ts_files.extend(sorted(root.glob(".tg-buffer-ts.*")))

    for tsf in ts_files:
        try:
            last_raw = tsf.read_text(encoding="utf-8").strip()
            last = int(last_raw) if last_raw.isdigit() else 0
        except (OSError, ValueError) as _exc:
            last = 0
        if now_ts - last < window:
            continue
        if tsf.name == ".tg-buffer-ts":
            lines.extend(flush_buffer(root, cfg, multi_chat=False, detach=detach))
        else:
            # .tg-buffer-ts.<safe> -> .tg-buffer.<safe>
            buf = root / tsf.name.replace(".tg-buffer-ts.", ".tg-buffer.", 1)
            chat_id, thread_id = _read_meta(buf)
            lines.extend(
                flush_buffer(
                    root,
                    cfg,
                    chat_id,
                    thread_id,
                    multi_chat=True,
                    detach=detach,
                )
            )
    return lines


def read_offset(bridge_dir: Path | str) -> int:
    path = Path(bridge_dir) / ".offset"
    if not path.is_file():
        return 0
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdigit() else 0
    except (OSError, ValueError) as _exc:
        return 0


def write_offset(bridge_dir: Path | str, offset: int) -> None:
    path = Path(bridge_dir) / ".offset"
    with contextlib.suppress(OSError, PermissionError):
        path.write_text(f"{offset}\n", encoding="utf-8")


def _message_obj(update: dict[str, Any]) -> dict[str, Any]:
    msg = update.get("message")
    return msg if isinstance(msg, dict) else {}


def _message_fields(update: dict[str, Any]) -> tuple[str, str, str, str]:
    """Extract from_id, text, chat_id, thread_id from an update object."""
    msg = _message_obj(update)
    if not msg:
        return "", "", "", ""
    from_obj = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    chat_obj = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    from_id = str(from_obj.get("id", "")) if from_obj else ""
    text = msg.get("text")
    text_s = str(text) if text is not None else ""
    chat_id = str(chat_obj.get("id", "")) if chat_obj else ""
    thread = msg.get("message_thread_id")
    thread_id = str(thread) if thread is not None else ""
    return from_id, text_s, chat_id, thread_id


def process_update(
    update: dict[str, Any],
    *,
    bridge_dir: Path | str,
    cfg: dict[str, Any],
    allowed_user_id: str,
    allowed_chat_id: str = "",
    bot_token: str = "",
    now: int | None = None,
) -> list[str]:
    """Handle one Telegram update. Advances offset. May buffer (not flush).

    Returns immediate stdout lines (setup discovery only; normal messages
    wait for reassembly flush).
    """
    root = Path(bridge_dir)
    update_id = update.get("update_id")
    try:
        uid = int(update_id)  # type: ignore[arg-type]
    except (TypeError, ValueError) as _exc:
        return []

    from_id, text, chat_id, thread_id = _message_fields(update)
    msg = _message_obj(update)
    lines: list[str] = []

    buffer_parts: list[str] = []
    if msg and bot_token:
        buffer_parts = buffer_parts_for_update(
            msg,
            bridge_dir=root,
            token=bot_token,
            update_id=uid,
            chat_id=chat_id,
            cfg=cfg,
        )
    elif text:
        buffer_parts = [text]

    if not from_id or not buffer_parts:
        write_offset(root, uid + 1)
        return lines

    if not allowed_user_id:
        lines.append(f"[telegram-setup] your user_id is {from_id}")
        write_offset(root, uid + 1)
        return lines

    if from_id == str(allowed_user_id):
        if not chat_is_accepted(cfg, chat_id, allowed_chat_id=allowed_chat_id):
            write_offset(root, uid + 1)
            return lines
        multi = has_routing_config(cfg)
        for part in buffer_parts:
            append_message(
                root,
                part,
                chat_id,
                thread_id,
                multi_chat=multi,
                now=now,
            )
    # else: unrecognized sender — silently ignored (allowlist boundary).

    write_offset(root, uid + 1)
    return lines


def process_result(
    result: Sequence[dict[str, Any]],
    *,
    bridge_dir: Path | str,
    cfg: dict[str, Any],
    allowed_user_id: str,
    allowed_chat_id: str = "",
    bot_token: str = "",
    now: int | None = None,
) -> list[str]:
    """Process getUpdates result array. Returns immediate stdout lines."""
    lines: list[str] = []
    for update in result:
        if not isinstance(update, dict):
            continue
        lines.extend(
            process_update(
                update,
                bridge_dir=bridge_dir,
                cfg=cfg,
                allowed_user_id=allowed_user_id,
                allowed_chat_id=allowed_chat_id,
                bot_token=bot_token,
                now=now,
            )
        )
    return lines


def get_updates(
    token: str,
    offset: int,
    timeout: int = 50,
    *,
    curl_maxtime: float = 60.0,
) -> dict[str, Any] | None:
    """HTTP getUpdates. Returns parsed JSON or None on failure (never raises)."""
    if not token:
        return None
    q = urllib.parse.urlencode({"timeout": timeout, "offset": offset})
    url = f"https://api.telegram.org/bot{token}/getUpdates?{q}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=curl_maxtime) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as _exc:
        return None


def _emit_lines(lines: Sequence[str], out: TextIO, emit: EmitFn | None) -> None:
    for line in lines:
        if emit is not None:
            emit(line)
        else:
            print(line, file=out, flush=True)


def poll_once(
    *,
    bridge_dir: Path | str,
    cfg: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    get_updates_fn: GetUpdatesFn | None = None,
    now_fn: NowFn | None = None,
    sleep_fn: SleepFn | None = None,
    out: TextIO | None = None,
    emit: EmitFn | None = None,
    detach: bool = True,
) -> str:
    """One iteration of the poll loop. Returns status token for tests.

    status ∈ ok | no_env | no_token | poll_error | empty
    """
    root = Path(bridge_dir)
    out_f = out if out is not None else __import__("sys").stdout
    sleep = sleep_fn or time.sleep
    now = now_fn or (lambda: int(time.time()))
    gu = get_updates_fn or get_updates

    env_file = root / ".env"
    if not env_file.is_file() and env is None:
        sleep(15)
        return "no_env"

    env_map = env if env is not None else load_env(root)
    token = env_map.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
    allowed_user = env_map.get("ALLOWED_USER_ID") or os.environ.get("ALLOWED_USER_ID") or ""
    allowed_chat = env_map.get("ALLOWED_CHAT_ID") or os.environ.get("ALLOWED_CHAT_ID") or ""

    if cfg is None:
        cfg = load_config(root / "relay.toml", bridge_dir=root)

    if not token:
        sleep(15)
        return "no_token"

    # Quiet-window flush before long poll.
    flushed = flush_stale_buffers(root, cfg, now=now(), detach=detach)
    _emit_lines(flushed, out_f, emit)

    offset = read_offset(root)
    if _any_nonempty_buffer(root):
        poll_timeout, curl_max = 1, 5.0
    else:
        poll_timeout, curl_max = 50, 60.0

    resp = gu(token, offset, poll_timeout, curl_maxtime=curl_max)
    if resp is None:
        emit_metric("tg-poll", "poll_error", "curl_fail", bridge_dir=root)
        sleep(2)
        return "poll_error"

    if not resp.get("ok"):
        emit_metric("tg-poll", "poll_error", "api_not_ok", bridge_dir=root)
        sleep(2)
        return "poll_error"

    result = resp.get("result") or []
    if not isinstance(result, list):
        return "empty"

    immediate = process_result(
        result,
        bridge_dir=root,
        cfg=cfg,
        allowed_user_id=allowed_user,
        allowed_chat_id=allowed_chat,
        bot_token=token,
        now=now(),
    )
    _emit_lines(immediate, out_f, emit)
    return "ok" if result else "empty"


def poll_loop(
    *,
    bridge_dir: Path | str | None = None,
    max_iterations: int | None = None,
    get_updates_fn: GetUpdatesFn | None = None,
    now_fn: NowFn | None = None,
    sleep_fn: SleepFn | None = None,
    out: TextIO | None = None,
    emit: EmitFn | None = None,
    cfg: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    detach: bool = True,
) -> int:
    """Long-poll loop. max_iterations limits runs (tests). Returns 0."""
    root = Path(bridge_dir) if bridge_dir else _repo_root()
    n = 0
    while True:
        poll_once(
            bridge_dir=root,
            cfg=cfg,
            env=env,
            get_updates_fn=get_updates_fn,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
            out=out,
            emit=emit,
            detach=detach,
        )
        n += 1
        if max_iterations is not None and n >= max_iterations:
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run the poll loop (bridge dir = repo root or CWD with .env)."""
    import argparse

    p = argparse.ArgumentParser(prog="tg-relay-poll", description="Telegram inbound long-poll")
    p.add_argument(
        "--bridge-dir",
        default="",
        help="Bridge directory (default: package repo root)",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N iterations (0 = forever; tests use this)",
    )
    args = p.parse_args(argv)
    root = Path(args.bridge_dir) if args.bridge_dir else _repo_root()
    max_iter = args.max_iterations if args.max_iterations > 0 else None
    try:
        return poll_loop(bridge_dir=root, max_iterations=max_iter)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
