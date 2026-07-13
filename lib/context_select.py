#!/usr/bin/env python3
"""lib/context_select.py - Exclusive hybrid context selection for agents.

Each manifest entry pairs the SAME content as text and (optionally) image.
Selection is EXCLUSIVE by capability so vision models do not double-dip:

  vision=True  → image path + short caption only (text path omitted)
  vision=False → text path only (image omitted)

CLI:
  python3 lib/context_select.py --manifest docs/context/manifest.json --vision
  python3 lib/context_select.py --manifest docs/context/manifest.json --no-vision
  python3 lib/context_select.py --list-ids

Prints one JSON object to stdout: {"mode": "visual"|"text", "items": [...]}
Never raises for missing optional images — those entries fall back to text
with a "fallback": "text_missing_image" note (still exclusive, never both).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError("manifest must be an object with an 'entries' array")
    return data


def select_context(
    manifest: dict,
    *,
    vision: bool,
    repo_root: Path | None = None,
) -> dict:
    """Return exclusive context items for the given capability.

    Never includes both text and image for the same entry id.
    """
    root = repo_root or Path.cwd()
    items: list[dict] = []
    mode = "visual" if vision else "text"

    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("id") or "")
        title = str(entry.get("title") or eid)
        text_rel = entry.get("text")
        image_rel = entry.get("image")
        caption = str(entry.get("caption") or "")

        if vision and image_rel:
            img_path = root / str(image_rel)
            if img_path.is_file():
                items.append(
                    {
                        "id": eid,
                        "title": title,
                        "modality": "image",
                        "path": str(image_rel),
                        "caption": caption,
                        # Explicit: text twin MUST NOT be loaded
                        "do_not_load": [str(text_rel)] if text_rel else [],
                    }
                )
                continue
            # Vision preferred but image missing → exclusive text fallback
            if text_rel:
                items.append(
                    {
                        "id": eid,
                        "title": title,
                        "modality": "text",
                        "path": str(text_rel),
                        "fallback": "text_missing_image",
                        "do_not_load": [str(image_rel)],
                    }
                )
            continue

        # Non-vision path: text only
        if text_rel:
            items.append(
                {
                    "id": eid,
                    "title": title,
                    "modality": "text",
                    "path": str(text_rel),
                    "do_not_load": [str(image_rel)] if image_rel else [],
                }
            )

    return {
        "mode": mode,
        "selection_rule": "exclusive_by_capability",
        "note": (
            "Load ONLY the listed paths. Paths in each item's do_not_load "
            "are the twin modality of the same content — never load them."
        ),
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--manifest",
        default="docs/context/manifest.json",
        help="Path to hybrid context manifest JSON",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--vision", action="store_true", help="Vision-capable model (images only)")
    g.add_argument("--no-vision", action="store_true", help="Text-only model (default)")
    p.add_argument("--list-ids", action="store_true", help="List entry ids and exit")
    p.add_argument("--repo-root", default=".", help="Repo root for path existence checks")
    args = p.parse_args(argv)

    root = Path(args.repo_root).resolve()
    man_path = Path(args.manifest)
    if not man_path.is_file():
        man_path = root / args.manifest
    try:
        manifest = load_manifest(man_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    if args.list_ids:
        ids = [e.get("id") for e in manifest.get("entries") or [] if isinstance(e, dict)]
        print(json.dumps({"ids": ids}, indent=2))
        return 0

    vision = bool(args.vision) and not args.no_vision
    out = select_context(manifest, vision=vision, repo_root=root)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
