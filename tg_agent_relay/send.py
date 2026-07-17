"""Outbound Telegram send — Python port of tg-send.sh (issue #26).

Live path may still be tg-send.sh until callers switch; this module is the
full send core: pagination, flock ordering, format, TTS strip/voice,
sendMessage (+ Photo/Voice helpers), never-silent HTML→plain retry.

Implement against SendRequest / Sender in protocols.py. Unit tests must
mock urllib (no live Telegram).
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from tg_agent_relay.config import cfg_get, load_config
from tg_agent_relay.format_api import format_message
from tg_agent_relay.metrics import emit_metric
from tg_agent_relay.protocols import SendRequest
from tg_agent_relay.tts import plan_voice_send, prepare_spoken, strip_formatting

# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def load_env(bridge_dir: Path | str | None = None) -> dict[str, str]:
    """Parse KEY=VALUE from .env (no export/shell)."""
    root = Path(bridge_dir) if bridge_dir else Path(__file__).resolve().parents[1]
    path = root / ".env"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'").strip('"')
    except OSError:
        pass
    return out


def _repo_root(bridge_dir: Path | str | None = None) -> Path:
    return Path(bridge_dir) if bridge_dir else Path(__file__).resolve().parents[1]


def _env_or(env: dict[str, str], key: str, default: str = "") -> str:
    """Prefer process env, then .env file map, then default."""
    v = os.environ.get(key)
    if v is not None and v != "":
        return v
    return env.get(key) or default


def _as_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError) as _exc:
        return default


def _as_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError) as _exc:
        return default


def _as_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


# ---------------------------------------------------------------------------
# Pagination (parity with tg-send.sh)
# ---------------------------------------------------------------------------


def paginate(text: str, page_size: int = 3500) -> list[str]:
    """Split *text* into pages on line boundaries; hard-split overlong lines.

    A message within *page_size* is a single unmodified page (no ``[k/n]``
    prefix — callers add that). Empty text → ``[]``.
    """
    if not text:
        return []
    if page_size <= 0:
        return [text]
    if len(text) <= page_size:
        return [text]

    pages: list[str] = []
    cur = ""
    # Preserve trailing empty lines like bash `while read`.
    lines = text.split("\n")
    for line in lines:
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) <= page_size:
            cur = cand
            continue
        if cur:
            pages.append(cur)
            cur = ""
        if len(line) <= page_size:
            cur = line
        else:
            rest = line
            while len(rest) > page_size:
                pages.append(rest[:page_size])
                rest = rest[page_size:]
            cur = rest
    if cur:
        pages.append(cur)
    return pages if pages else [text]


# ---------------------------------------------------------------------------
# Dedup (10s window on full pre-split message)
# ---------------------------------------------------------------------------


def last_sent_path(bridge_dir: Path | str) -> Path:
    return Path(bridge_dir) / ".last-sent"


def is_duplicate(
    bridge_dir: Path | str, msg: str, *, now: int | None = None, window_s: int = 10
) -> bool:
    """True if identical *msg* was recorded within *window_s* seconds."""
    path = last_sent_path(bridge_dir)
    if not path.is_file() or not msg:
        return False
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError) as _exc:
        return False
    ts_s, _, last_text = line.partition("|")
    try:
        last_ts = int(ts_s)
    except ValueError:
        return False
    ts = int(time.time()) if now is None else now
    return last_text == msg and (ts - last_ts) < window_s


def record_last_sent(bridge_dir: Path | str, msg: str, *, now: int | None = None) -> None:
    ts = int(time.time()) if now is None else now
    with suppress(OSError):
        last_sent_path(bridge_dir).write_text(f"{ts}|{msg}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Flock serialization (best-effort)
# ---------------------------------------------------------------------------


@contextmanager
def send_lock(
    bridge_dir: Path | str,
    *,
    interval_ms: int = 350,
    lock_file: Path | str | None = None,
) -> Iterator[bool]:
    """Exclusive flock on ``.tg-send.lock`` for the whole send + interval.

    Yields True if the lock was acquired, False if flock is unavailable /
    failed (skip-graceful unserialized send). Holds *interval_ms* after the
    body before releasing, matching tg-send.sh.
    """
    root = Path(bridge_dir)
    path = Path(lock_file) if lock_file else root / ".tg-send.lock"
    env_lock = os.environ.get("TG_SEND_LOCK_FILE")
    if env_lock and lock_file is None:
        path = Path(env_lock)

    fd = None
    have = False
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]

    if fcntl is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = open(path, "a+", encoding="utf-8")  # noqa: SIM115
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
            have = True
        except OSError as _exc:
            if fd is not None:
                with suppress(OSError):
                    fd.close()
                fd = None
            emit_metric(
                "queue",
                "flock_unavailable",
                "serialized send ordering skipped - flock failed",
                bridge_dir=root,
            )
    else:
        emit_metric(
            "queue",
            "flock_unavailable",
            "serialized send ordering skipped - install util-linux flock / fcntl",
            bridge_dir=root,
        )

    try:
        yield have
    finally:
        if have and interval_ms > 0:
            with suppress(Exception):
                time.sleep(interval_ms / 1000.0)
        if fd is not None:
            try:
                import fcntl as _fcntl

                _fcntl.flock(fd.fileno(), _fcntl.LOCK_UN)
            except Exception:
                pass
            with suppress(OSError):
                fd.close()


# ---------------------------------------------------------------------------
# HTTP helpers (urllib only — mockable in tests)
# ---------------------------------------------------------------------------


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _response_ok(raw: str) -> bool:
    return '"ok":true' in raw.replace(" ", "")


def _urlopen_read(req: urllib.request.Request, *, timeout: float) -> str:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "",
    thread_id: str = "",
    reply_markup: str = "",
    timeout: float = 10.0,
) -> bool:
    """POST sendMessage. Returns True if ok:true. No raise on network fail."""
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if thread_id:
        data["message_thread_id"] = thread_id
    if reply_markup:
        data["reply_markup"] = reply_markup
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        _api_url(token, "sendMessage"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        raw = _urlopen_read(req, timeout=timeout)
        return _response_ok(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as _exc:
        return False


def send_photo(
    token: str,
    chat_id: str,
    photo_path: Path | str,
    *,
    caption: str = "",
    thread_id: str = "",
    reply_markup: str = "",
    timeout: float = 30.0,
) -> bool:
    """POST sendPhoto (multipart). Returns True if ok:true."""
    return _send_multipart(
        token,
        "sendPhoto",
        chat_id,
        field="photo",
        file_path=photo_path,
        caption=caption,
        thread_id=thread_id,
        reply_markup=reply_markup,
        timeout=timeout,
    )


def send_voice(
    token: str,
    chat_id: str,
    voice_path: Path | str,
    *,
    thread_id: str = "",
    timeout: float = 30.0,
) -> bool:
    """POST sendVoice (multipart OGG/OPUS). Returns True if ok:true."""
    return _send_multipart(
        token,
        "sendVoice",
        chat_id,
        field="voice",
        file_path=voice_path,
        thread_id=thread_id,
        timeout=timeout,
    )


def send_audio(
    token: str,
    chat_id: str,
    audio_path: Path | str,
    *,
    thread_id: str = "",
    timeout: float = 30.0,
) -> bool:
    """POST sendAudio (multipart; WAV fallback when ffmpeg absent)."""
    return _send_multipart(
        token,
        "sendAudio",
        chat_id,
        field="audio",
        file_path=audio_path,
        thread_id=thread_id,
        timeout=timeout,
    )


def send_document(
    token: str,
    chat_id: str,
    document_path: Path | str,
    *,
    caption: str = "",
    thread_id: str = "",
    timeout: float = 30.0,
) -> bool:
    """POST sendDocument (multipart). Returns True if ok:true."""
    return _send_multipart(
        token,
        "sendDocument",
        chat_id,
        field="document",
        file_path=document_path,
        caption=caption,
        thread_id=thread_id,
        timeout=timeout,
    )


def _send_multipart(
    token: str,
    method: str,
    chat_id: str,
    *,
    field: str,
    file_path: Path | str,
    caption: str = "",
    thread_id: str = "",
    reply_markup: str = "",
    timeout: float = 30.0,
) -> bool:
    path = Path(file_path)
    if not path.is_file():
        return False
    try:
        file_bytes = path.read_bytes()
    except OSError:
        return False
    filename = path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----RelayBoundary{os.getpid()}{int(time.time() * 1000)}"
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)
    if thread_id:
        add_field("message_thread_id", str(thread_id))
    if reply_markup:
        add_field("reply_markup", reply_markup)
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        _api_url(token, method),
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        raw = _urlopen_read(req, timeout=timeout)
        return _response_ok(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as _exc:
        return False


# ---------------------------------------------------------------------------
# Format + never-silent HTML → plain retry
# ---------------------------------------------------------------------------


def build_page_payloads(
    pages: list[str],
    *,
    config: dict[str, Any] | None = None,
    pre_parse_mode: str = "",
) -> list[tuple[str, str, str]]:
    """Return list of (send_text, parse_mode, plain_text) per page.

    *plain_text* is the pre-format ``[k/n]\\npage`` shape used for the
    never-silent fallback. When *pre_parse_mode* is set, pages are sent
    as-is (caller already formatted).
    """
    total = len(pages)
    out: list[tuple[str, str, str]] = []
    for idx, page in enumerate(pages, start=1):
        plain = f"[{idx}/{total}]\n{page}" if total > 1 else page

        if pre_parse_mode:
            if total > 1 and pre_parse_mode.upper() == "HTML":
                send_text = f"<b>[{idx}/{total}]</b>\n{page}"
            else:
                send_text = plain if total > 1 else page
            out.append((send_text, pre_parse_mode, plain))
            continue

        fmt = format_message(page, config=config)
        if fmt.parse_mode:
            send_text = f"<b>[{idx}/{total}]</b>\n{fmt.text}" if total > 1 else fmt.text
            out.append((send_text, fmt.parse_mode, plain))
        else:
            out.append((plain, "", plain))
    return out


def send_text_never_silent(
    token: str,
    chat_id: str,
    send_text: str,
    parse_mode: str,
    plain_text: str,
    *,
    thread_id: str = "",
    reply_markup: str = "",
    bridge_dir: Path | str | None = None,
    page_label: str = "",
) -> bool:
    """sendMessage; on HTML failure, retry once as plain (never-silent)."""
    ok = send_message(
        token,
        chat_id,
        send_text,
        parse_mode=parse_mode,
        thread_id=thread_id,
        reply_markup=reply_markup,
    )
    if ok:
        return True
    if parse_mode:
        emit_metric(
            "format",
            "send_fallback",
            f"page={page_label or '?'} formatted send failed, retrying as plain text",
            bridge_dir=bridge_dir,
        )
        return send_message(
            token,
            chat_id,
            plain_text,
            parse_mode="",
            thread_id=thread_id,
        )
    return False


# ---------------------------------------------------------------------------
# TTS: strip / truncate / chunk / optional local voice
# ---------------------------------------------------------------------------


def truncate_words(text: str, max_chars: int) -> str:
    """Word-boundary truncate for spoken_mode=short. max<=0 → no truncate."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split spoken prose into chunks ≤ max_chars on whitespace (shell parity).

    max_chars <= 0 → single unbounded chunk. Empty text → [].
    """
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    words = text.split()
    chunks: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}" if cur else word
        if len(cand) <= max_chars:
            cur = cand
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(word) <= max_chars:
            cur = word
        else:
            rest = word
            while len(rest) > max_chars:
                chunks.append(rest[:max_chars])
                rest = rest[max_chars:]
            cur = rest
    if cur:
        chunks.append(cur)
    return chunks if chunks else [text]


def spoken_transcript(
    text: str,
    *,
    config: dict[str, Any] | None = None,
) -> str:
    """Strip formatting to clean spoken prose (tts.strip_formatting).

    Never blanks non-empty input: strip failure → original text.
    """
    if not text:
        return ""
    speak_code = _as_bool(cfg_get(config or {}, "tts.speak_code", False), False)
    code_ref = str(
        cfg_get(config or {}, "tts.voice_code_ref", "ref. the message for the code")
        or "ref. the message for the code"
    )
    link_ref = str(
        cfg_get(config or {}, "tts.voice_link_ref", "ref. the message for the link")
        or "ref. the message for the link"
    )
    collapse = _as_bool(cfg_get(config or {}, "tts.collapse_adjacent_refs", True), True)
    try:
        out = strip_formatting(
            text,
            speak_code=speak_code,
            code_ref=code_ref,
            link_ref=link_ref,
            collapse_refs=collapse,
        )
    except TypeError:
        # Older strip_formatting may only take text.
        try:
            out = strip_formatting(text)
        except Exception:
            return text
    except Exception:
        return text
    if out or not text:
        return out
    return text


def select_tts_engine(config: dict[str, Any] | None = None) -> str | None:
    """Return ``piper`` / ``espeak`` or None if no local engine available."""
    cfg = config or {}
    configured = str(cfg_get(cfg, "tts.engine", "auto") or "auto").lower()
    voice_model = str(cfg_get(cfg, "tts.voice_model", "") or "")
    try_piper = configured in ("auto", "piper", "")
    try_espeak = configured in ("auto", "espeak", "")
    if configured == "piper":
        try_espeak = False
    elif configured == "espeak":
        try_piper = False

    if try_piper and shutil.which("piper") and voice_model and Path(voice_model).is_file():
        return "piper"
    if try_espeak and shutil.which("espeak-ng"):
        return "espeak"
    return None


def synthesize_wav(
    engine: str,
    text: str,
    out_wav: Path,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    """Run piper/espeak-ng → WAV. Returns True if *out_wav* is non-empty."""
    cfg = config or {}
    try:
        if engine == "piper":
            voice_model = str(cfg_get(cfg, "tts.voice_model", "") or "")
            if not voice_model:
                return False
            args = ["piper", "--model", voice_model, "--output_file", str(out_wav)]
            length_scale = cfg_get(cfg, "tts.length_scale", "")
            if length_scale not in ("", None):
                try:
                    float(length_scale)
                    args.extend(["--length-scale", str(length_scale)])
                except (TypeError, ValueError) as _exc:
                    pass
            subprocess.run(
                args,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
        elif engine == "espeak":
            subprocess.run(
                ["espeak-ng", "-w", str(out_wav), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
        else:
            return False
    except (OSError, subprocess.SubprocessError, TimeoutError) as _exc:
        return False
    try:
        return out_wav.is_file() and out_wav.stat().st_size > 0
    except OSError:
        return False


def tts_send_voice(
    token: str,
    chat_id: str,
    text: str,
    *,
    thread_id: str = "",
    config: dict[str, Any] | None = None,
    bridge_dir: Path | str | None = None,
) -> bool:
    """Local TTS → sendVoice/sendAudio. Skip-graceful; never blocks text."""
    if not text or not token or not chat_id:
        return False
    engine = select_tts_engine(config)
    if not engine:
        emit_metric(
            "tts",
            "skip",
            "no local TTS engine available (install piper+voice_model or espeak-ng)",
            bridge_dir=bridge_dir,
        )
        return False

    tmp_wav: Path | None = None
    tmp_ogg: Path | None = None
    try:
        fd, wav_name = tempfile.mkstemp(prefix="relay-tts-", suffix=".wav")
        os.close(fd)
        tmp_wav = Path(wav_name)
        if not synthesize_wav(engine, text, tmp_wav, config=config):
            emit_metric(
                "tts",
                "skip",
                f"engine={engine} synthesis produced no audio",
                bridge_dir=bridge_dir,
            )
            return False

        send_path = tmp_wav
        method = "sendAudio"
        if shutil.which("ffmpeg"):
            tmp_ogg = tmp_wav.with_suffix(".ogg")
            try:
                rc = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(tmp_wav),
                        "-c:a",
                        "libopus",
                        "-b:a",
                        "32k",
                        str(tmp_ogg),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=120,
                )
                if rc.returncode == 0 and tmp_ogg.is_file() and tmp_ogg.stat().st_size > 0:
                    send_path = tmp_ogg
                    method = "sendVoice"
                else:
                    emit_metric(
                        "tts",
                        "skip",
                        f"engine={engine} ffmpeg conversion to opus failed",
                        bridge_dir=bridge_dir,
                    )
                    return False
            except (OSError, subprocess.SubprocessError, TimeoutError) as _exc:
                emit_metric(
                    "tts",
                    "skip",
                    f"engine={engine} ffmpeg conversion to opus failed",
                    bridge_dir=bridge_dir,
                )
                return False

        if method == "sendVoice":
            ok = send_voice(token, chat_id, send_path, thread_id=thread_id)
        else:
            ok = send_audio(token, chat_id, send_path, thread_id=thread_id)
        if ok:
            emit_metric(
                "tts",
                "sent",
                f"engine={engine} method={method}",
                bridge_dir=bridge_dir,
            )
            return True
        emit_metric(
            "tts",
            "skip",
            f"engine={engine} {method} request failed",
            bridge_dir=bridge_dir,
        )
        return False
    finally:
        for p in (tmp_wav, tmp_ogg):
            if p is not None:
                with suppress(OSError):
                    p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# EnvSender — full send pipeline
# ---------------------------------------------------------------------------


VoiceSender = Callable[..., bool]


class EnvSender:
    """Sender protocol: paginate, flock, format, TTS, never-silent sendMessage."""

    def __init__(
        self,
        bridge_dir: Path | str | None = None,
        *,
        config: dict[str, Any] | None = None,
        voice_sender: VoiceSender | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.bridge_dir = _repo_root(bridge_dir)
        env = load_env(self.bridge_dir)
        self._file_env = env
        self.token = _env_or(env, "BOT_TOKEN")
        self.default_chat = _env_or(env, "ALLOWED_CHAT_ID")
        if config is not None:
            self.config = config
        else:
            self.config = load_config(bridge_dir=self.bridge_dir)
        self._voice_sender: VoiceSender = voice_sender or tts_send_voice
        self._sleep = sleep_fn or time.sleep

    def _page_size(self) -> int:
        env_v = os.environ.get("TG_PAGE_SIZE")
        if env_v is not None and env_v != "":
            return _as_int(env_v, 3500)
        return _as_int(cfg_get(self.config, "general.page_size", 3500), 3500)

    def _page_delay(self) -> float:
        env_v = os.environ.get("TG_PAGE_DELAY")
        if env_v is not None and env_v != "":
            return _as_float(env_v, 0.4)
        return _as_float(cfg_get(self.config, "general.page_delay", 0.4), 0.4)

    def _send_interval_ms(self) -> int:
        env_v = os.environ.get("TG_SEND_INTERVAL_MS")
        if env_v is not None and env_v != "":
            return _as_int(env_v, 350)
        return _as_int(cfg_get(self.config, "general.send_interval_ms", 350), 350)

    def send(self, req: SendRequest) -> None:
        """Full outbound pipeline. Silent no-op without token/chat/text."""
        msg = req.text or ""
        if not msg:
            return

        chat = req.chat_id or os.environ.get("RELAY_CHAT_ID") or self.default_chat
        thread = req.thread_id or os.environ.get("RELAY_THREAD_ID") or ""
        token = self.token
        if not token or not chat:
            return

        source = req.source or os.environ.get("TG_SEND_SOURCE") or ""
        backend = req.backend or os.environ.get("RELAY_BACKEND") or ""
        is_hook = source == "hook"

        interval_ms = self._send_interval_ms()
        with send_lock(self.bridge_dir, interval_ms=interval_ms) as _locked:
            if is_duplicate(self.bridge_dir, msg):
                return

            page_size = self._page_size()
            pages = paginate(msg, page_size)
            if not pages:
                return
            total = len(pages)

            voice_sent = self._maybe_send_voice(
                token=token,
                chat=chat,
                thread=thread,
                msg=msg,
                total_pages=total,
                is_hook=is_hook,
            )

            tts_mode = str(cfg_get(self.config, "tts.mode", "off") or "off").lower()
            if tts_mode not in ("text+voice", "voice-only"):
                tts_mode = "off"

            # Hook pings always send text; voice-only only skips text when voice succeeded.
            send_text_pages = is_hook or tts_mode != "voice-only" or not voice_sent
            reply_markup = os.environ.get("RELAY_REPLY_MARKUP_JSON") or ""
            if send_text_pages:
                payloads = build_page_payloads(
                    pages,
                    config=self.config,
                    pre_parse_mode=req.parse_mode or "",
                )
                delay = self._page_delay()
                for i, (send_text, parse_mode, plain) in enumerate(payloads, start=1):
                    markup = reply_markup if i == 1 and reply_markup else ""
                    send_text_never_silent(
                        token,
                        chat,
                        send_text,
                        parse_mode,
                        plain,
                        thread_id=thread,
                        reply_markup=markup,
                        bridge_dir=self.bridge_dir,
                        page_label=f"{i}/{total}",
                    )
                    # Host-highlighted code docs (html-doc mode) after each page
                    with suppress(Exception):
                        from tg_agent_relay.highlight_docs import (
                            build_code_doc_jobs,
                            send_code_doc_jobs,
                        )

                        jobs = build_code_doc_jobs(
                            pages[i - 1],
                            config=self.config,
                            bridge_dir=self.bridge_dir,
                        )
                        if jobs:
                            send_code_doc_jobs(
                                token,
                                chat,
                                jobs,
                                thread_id=thread,
                            )
                    if i < total and delay > 0:
                        with suppress(Exception):
                            self._sleep(delay)

            record_last_sent(self.bridge_dir, msg)
            detail = f"pages={total}"
            if backend:
                detail += f" backend={backend}"
            if source:
                detail += f" source={source}"
            emit_metric(
                "tg-send-py",
                "send",
                detail,
                bridge_dir=self.bridge_dir,
            )

    def _maybe_send_voice(
        self,
        *,
        token: str,
        chat: str,
        thread: str,
        msg: str,
        total_pages: int,
        is_hook: bool,
    ) -> bool:
        tts_mode = str(cfg_get(self.config, "tts.mode", "off") or "off").lower()
        if tts_mode not in ("text+voice", "voice-only"):
            return False

        hook_voice = _as_bool(cfg_get(self.config, "tts.hook_voice", True), True)
        spoken_mode_cfg = str(cfg_get(self.config, "tts.spoken_mode", "short") or "short")
        spoken_max = _as_int(cfg_get(self.config, "tts.spoken_max_chars", 600), 600)
        clip_max = cfg_get(self.config, "tts.clip_max_chars", None)
        if clip_max in ("", None):
            clip_max = cfg_get(self.config, "tts.hook_voice_max_chars", 1500)
        clip_max_i = _as_int(clip_max, 1500)
        tts_max = _as_int(cfg_get(self.config, "tts.max_chars", 600), 600)
        hook_event = os.environ.get("RELAY_HOOK_EVENT") or ""

        vp = plan_voice_send(
            msg,
            tts_mode=tts_mode,
            is_hook=is_hook,
            hook_voice=hook_voice,
            total_pages=total_pages,
            tts_max_chars=tts_max,
            spoken_mode_cfg=spoken_mode_cfg,
            spoken_max_chars=spoken_max,
            clip_max_chars=clip_max_i,
            hook_event=hook_event or None,
        )
        if not vp.eligible or not msg:
            return False

        spoken_mode = vp.spoken_mode
        clips = prepare_spoken(
            msg,
            spoken_mode=spoken_mode,
            spoken_max_chars=spoken_max,
            clip_max_chars=clip_max_i,
            config=self.config,
        )
        voice_text = " ".join(clips.clips)
        if not voice_text:
            return False
        if clips.truncated:
            emit_metric(
                "tts",
                "hook_voice_truncated",
                f"spoken_chars={clips.spoken_chars} max={spoken_max} mode=short",
                bridge_dir=self.bridge_dir,
            )
        chunk_max = vp.chunk_max
        chunks = list(clips.clips)
        if len(chunks) > 1:
            emit_metric(
                "tts",
                "hook_voice_chunked",
                f"chunks={len(chunks)} spoken_chars={len(voice_text)} chunk_max={chunk_max}",
                bridge_dir=self.bridge_dir,
            )

        any_sent = False
        for chunk in chunks:
            try:
                ok = self._voice_sender(
                    token,
                    chat,
                    chunk,
                    thread_id=thread,
                    config=self.config,
                    bridge_dir=self.bridge_dir,
                )
            except TypeError:
                # Simpler test stubs: (token, chat, text) only
                try:
                    ok = self._voice_sender(token, chat, chunk)
                except Exception:
                    ok = False
            except Exception:
                ok = False
            if ok:
                any_sent = True
        return any_sent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def send_text(
    text: str,
    *,
    bridge_dir: Path | str | None = None,
    chat_id: str = "",
    thread_id: str = "",
    source: str = "",
    backend: str = "",
    parse_mode: str = "",
    config: dict[str, Any] | None = None,
) -> None:
    """Convenience: build SendRequest and EnvSender.send."""
    EnvSender(bridge_dir, config=config).send(
        SendRequest(
            text=text,
            chat_id=chat_id,
            thread_id=thread_id,
            parse_mode=parse_mode,
            source=source,
            backend=backend,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``tg-relay-send [message…]`` or stdin. Always exit 0 on no-op."""
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="tg-relay-send",
        description="Outbound Telegram send (Python port of tg-send.sh)",
    )
    p.add_argument("message", nargs="*", help="Message text (else stdin)")
    p.add_argument("--bridge-dir", default="", help="Bridge root (default: package parent)")
    p.add_argument(
        "--chat-id", default="", help="Override chat id (else RELAY_CHAT_ID / ALLOWED_CHAT_ID)"
    )
    p.add_argument("--thread-id", default="", help="Forum thread id")
    p.add_argument("--source", default="", help="hook | empty (else TG_SEND_SOURCE)")
    p.add_argument("--backend", default="", help="Backend tag (else RELAY_BACKEND)")
    p.add_argument(
        "--config-json",
        default="",
        help="Optional JSON config path (else relay.toml under bridge-dir)",
    )
    args = p.parse_args(argv)

    text = " ".join(args.message) if args.message else sys.stdin.read()
    if not text:
        return 0

    bridge = Path(args.bridge_dir) if args.bridge_dir else None
    cfg: dict[str, Any] | None = None
    if args.config_json:
        try:
            cfg = json.loads(Path(args.config_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as _exc:
            cfg = {}

    source = args.source or os.environ.get("TG_SEND_SOURCE") or ""
    backend = args.backend or os.environ.get("RELAY_BACKEND") or ""
    chat = args.chat_id or os.environ.get("RELAY_CHAT_ID") or ""
    thread = args.thread_id or os.environ.get("RELAY_THREAD_ID") or ""

    EnvSender(bridge, config=cfg).send(
        SendRequest(
            text=text,
            chat_id=chat,
            thread_id=thread,
            source=source,
            backend=backend,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
