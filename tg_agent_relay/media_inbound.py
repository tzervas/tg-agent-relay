"""Secure inbound Telegram media download + agent stream lines (offline-testable)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DownloadFn = Callable[[str, str, Path, int], tuple[Path | None, str, int] | None]

_SAFE_CHAT = re.compile(r"[^0-9A-Za-z_-]+")
_DEFAULT_MAX_IMAGE = 10 * 1024 * 1024
_DEFAULT_MAX_VIDEO = 50 * 1024 * 1024
_DEFAULT_MAX_AUDIO = 20 * 1024 * 1024

ALLOWED_MIME_PREFIXES = ("image/", "video/", "audio/")
ALLOWED_MIME_EXACT = frozenset(
    {
        "application/octet-stream",  # Telegram sometimes omits type on voice
    }
)


@dataclass(frozen=True)
class MediaAttachment:
    kind: str  # photo | video | voice | audio | document
    file_id: str
    mime: str
    size: int
    caption: str


def _safe_chat_id(chat_id: str) -> str:
    return _SAFE_CHAT.sub("_", str(chat_id))


def media_limits_from_cfg(cfg: dict[str, Any] | None) -> dict[str, int]:
    media = (cfg or {}).get("media") if isinstance((cfg or {}).get("media"), dict) else {}
    if not isinstance(media, dict):
        media = {}
    return {
        "image": int(media.get("max_image_bytes") or _DEFAULT_MAX_IMAGE),
        "video": int(media.get("max_video_bytes") or _DEFAULT_MAX_VIDEO),
        "audio": int(media.get("max_audio_bytes") or _DEFAULT_MAX_AUDIO),
    }


def max_bytes_for_kind(kind: str, mime: str, limits: dict[str, int]) -> int:
    if kind == "photo" or mime.startswith("image/"):
        return limits["image"]
    if kind in ("voice", "audio") or mime.startswith("audio/"):
        return limits["audio"]
    return limits["video"]


def mime_allowed(mime: str, kind: str) -> bool:
    m = (mime or "").strip().lower()
    if kind in ("photo", "voice"):
        return True
    if m.startswith(ALLOWED_MIME_PREFIXES):
        return True
    if kind == "document" and not m:
        return False
    return m in ALLOWED_MIME_EXACT


def extract_media_attachment(msg: dict[str, Any]) -> MediaAttachment | None:
    """Pick largest photo or single video/voice/audio/document attachment."""
    if not isinstance(msg, dict):
        return None
    caption = str(msg.get("caption") or "")

    photos = msg.get("photo")
    if isinstance(photos, list) and photos:
        best = photos[-1]
        if isinstance(best, dict) and best.get("file_id"):
            return MediaAttachment(
                kind="photo",
                file_id=str(best["file_id"]),
                mime="image/jpeg",
                size=int(best.get("file_size") or 0),
                caption=caption,
            )

    for key, kind, default_mime in (
        ("video", "video", "video/mp4"),
        ("voice", "voice", "audio/ogg"),
        ("audio", "audio", "audio/mpeg"),
    ):
        obj = msg.get(key)
        if isinstance(obj, dict) and obj.get("file_id"):
            mime = str(obj.get("mime_type") or default_mime)
            return MediaAttachment(
                kind=kind,
                file_id=str(obj["file_id"]),
                mime=mime,
                size=int(obj.get("file_size") or 0),
                caption=caption,
            )

    doc = msg.get("document")
    if isinstance(doc, dict) and doc.get("file_id"):
        mime = str(doc.get("mime_type") or "")
        if mime_allowed(mime, "document"):
            return MediaAttachment(
                kind="document",
                file_id=str(doc["file_id"]),
                mime=mime,
                size=int(doc.get("file_size") or 0),
                caption=caption,
            )
    return None


def media_storage_dir(bridge_dir: Path, chat_id: str, update_id: int) -> Path:
    safe = _safe_chat_id(chat_id)
    return bridge_dir / ".media" / safe / str(update_id)


def sanitize_filename(name: str, kind: str) -> str:
    base = Path(name).name
    if ".." in base or base.startswith("/"):
        base = f"file.{kind}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    if not base or base in (".", ".."):
        base = f"file.{kind}"
    return base


def format_media_line(
    *,
    kind: str,
    path: Path,
    mime: str,
    size: int,
    caption: str = "",
) -> str:
    cap = (caption or "").replace("\n", " ").strip()
    cap_part = f" caption={cap}" if cap else ""
    return f"[telegram:media] kind={kind} path={path.resolve()} mime={mime} size={size}{cap_part}"


def _telegram_get_file_path(token: str, file_id: str) -> tuple[str, int]:
    q = urllib.parse.urlencode({"file_id": file_id})
    url = f"https://api.telegram.org/bot{token}/getFile?{q}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not data.get("ok"):
        raise OSError("getFile not ok")
    result = data.get("result") or {}
    fpath = str(result.get("file_path") or "")
    fsize = int(result.get("file_size") or 0)
    if not fpath or ".." in fpath:
        raise OSError("invalid file_path")
    return fpath, fsize


def download_telegram_file(
    token: str,
    file_id: str,
    dest: Path,
    *,
    max_bytes: int,
) -> tuple[Path, int, str]:
    """Download via getFile + file API. Returns (path, size, mime_guess)."""
    fpath, declared = _telegram_get_file_path(token, file_id)
    if declared and declared > max_bytes:
        raise OSError("file too large")
    url = f"https://api.telegram.org/file/bot{token}/{fpath}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    req = urllib.request.Request(url, method="GET")
    total = 0
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                dest.unlink(missing_ok=True)
                raise OSError("file too large")
            out.write(chunk)
    os.chmod(dest, 0o600)
    ext = Path(fpath).suffix or ""
    mime = "application/octet-stream"
    if ext.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext.lower() == ".png":
        mime = "image/png"
    elif ext.lower() in (".mp4", ".mov"):
        mime = "video/mp4"
    elif ext.lower() in (".ogg", ".oga"):
        mime = "audio/ogg"
    elif ext.lower() == ".mp3":
        mime = "audio/mpeg"
    return dest, total, mime


def ingest_message_media(
    msg: dict[str, Any],
    *,
    bridge_dir: Path,
    token: str,
    update_id: int,
    chat_id: str,
    cfg: dict[str, Any] | None = None,
    download_fn: DownloadFn | None = None,
) -> str | None:
    """Download allowed media; return agent stream line or None."""
    att = extract_media_attachment(msg)
    if att is None:
        return None
    if not mime_allowed(att.mime, att.kind):
        return None

    limits = media_limits_from_cfg(cfg)
    max_b = max_bytes_for_kind(att.kind, att.mime, limits)
    if att.size and att.size > max_b:
        return None

    store_dir = media_storage_dir(bridge_dir, chat_id, update_id)
    fname = sanitize_filename(att.file_id, att.kind)
    if att.kind == "photo":
        fname = "photo.jpg"
    dest = store_dir / fname

    try:
        if download_fn is None:
            path, size, mime = download_telegram_file(token, att.file_id, dest, max_bytes=max_b)
        else:
            got = download_fn(token, att.file_id, dest, max_b)
            if not got:
                return None
            path, mime, size = got
            if path is None:
                return None
            mime = mime or att.mime
            size = size or att.size
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as _exc:
        return None

    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return format_media_line(
        kind=att.kind,
        path=path,
        mime=mime or att.mime,
        size=size,
        caption=att.caption,
    )


def buffer_parts_for_update(
    msg: dict[str, Any],
    *,
    bridge_dir: Path,
    token: str,
    update_id: int,
    chat_id: str,
    cfg: dict[str, Any] | None = None,
    download_fn: DownloadFn | None = None,
) -> list[str]:
    """Text and/or media lines to append to inbound buffer."""
    parts: list[str] = []
    text = msg.get("text")
    if text is not None and str(text).strip():
        parts.append(str(text))
    media_line = ingest_message_media(
        msg,
        bridge_dir=bridge_dir,
        token=token,
        update_id=update_id,
        chat_id=chat_id,
        cfg=cfg,
        download_fn=download_fn,
    )
    if media_line:
        parts.append(media_line)
    cap = msg.get("caption")
    if cap and (text is None or not str(text).strip()):
        # caption-only: still surfaced via media line; optional standalone caption skip
        pass
    return parts
