# Hybrid agent context (exclusive visual / text)

These files are **for models/agents**, not for Telegram phone formatting.

Each topic in [`manifest.json`](manifest.json) has:

| Field | Role |
|---|---|
| `text` | Full prose / bullet context (fallback) |
| `image` | Same content as a compact diagram/legend PNG |
| `caption` | Short string to pair with the image (not a second copy of the text file) |

## Exclusive selection — no double-dip

```text
vision-capable model  →  load IMAGE + caption only
                         IGNORE the text twin for that id

no vision / no benefit →  load TEXT only
                         IGNORE the image twin for that id
```

**Never load both modalities for the same `id`.** That would burn tokens
twice on the same facts and can confuse ranking.

Use the selector (stdlib Python):

```bash
# Vision model / multimodal agent session
python3 lib/context_select.py --vision --repo-root .

# Text-only model
python3 lib/context_select.py --no-vision --repo-root .
```

Output JSON lists exactly which paths to open and a `do_not_load` list for
the twin modality of each entry.

## Generating / refreshing images

If `docs/assets/context/*.png` is missing, vision selection falls back to
text for that entry only (still exclusive). Regenerate panels with:

```bash
python3 lib/context_render.py --repo-root .
```

(Requires matplotlib for PNG output; without it, only text context is used.)

## Experiment log

See [`../assets/visual-context-experiment.md`](../assets/visual-context-experiment.md)
for when visual panels help vs when text is enough.
