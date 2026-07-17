"""Offline tests for inbound media path sanitization + kind detection."""

from __future__ import annotations

from pathlib import Path

from tg_agent_relay.media_inbound import (
    extract_media_attachment,
    format_media_line,
    ingest_message_media,
    media_storage_dir,
    mime_allowed,
    sanitize_filename,
)


def test_extract_photo_kind() -> None:
    msg = {
        "photo": [{"file_id": "small"}, {"file_id": "big", "file_size": 100}],
        "caption": "hi",
    }
    att = extract_media_attachment(msg)
    assert att is not None
    assert att.kind == "photo"
    assert att.file_id == "big"
    assert att.caption == "hi"


def test_sanitize_filename_blocks_traversal() -> None:
    assert ".." not in sanitize_filename("../../etc/passwd", "photo")
    assert sanitize_filename("", "voice") == "file.voice"


def test_media_storage_dir_safe() -> None:
    p = media_storage_dir(Path("/bridge"), "-1001", 42)
    assert str(p).endswith(".media/-1001/42")


def test_format_media_line() -> None:
    line = format_media_line(
        kind="photo",
        path=Path("/bridge/.media/1/2/photo.jpg"),
        mime="image/jpeg",
        size=10,
        caption="cap",
    )
    assert line.startswith("[telegram:media]")
    assert "kind=photo" in line
    assert "caption=cap" in line
    assert "/bridge/.media/1/2/photo.jpg" in line


def test_ingest_with_mock_download(tmp_path: Path) -> None:
    dest_holder: list[Path] = []

    def fake_dl(token: str, fid: str, dest: Path, max_b: int):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 3)
        dest_holder.append(dest)
        return dest, "image/jpeg", 3

    msg = {"photo": [{"file_id": "x"}]}
    line = ingest_message_media(
        msg,
        bridge_dir=tmp_path,
        token="TOKEN",
        update_id=7,
        chat_id="99",
        download_fn=fake_dl,
    )
    assert line is not None
    assert "[telegram:media]" in line
    assert dest_holder[0].stat().st_mode & 0o777 == 0o600


def test_mime_allowed_document() -> None:
    assert mime_allowed("image/png", "document")
    assert not mime_allowed("application/zip", "document")
