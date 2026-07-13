# Visual context experiment (agent/models only)

## Question

Do compact PNGs of static architecture/routing help multimodal agents more
than the same facts as markdown — and if so, how do we avoid loading both?

## Selection rule (product requirement)

**Exclusive by capability — no double-dip:**

| Model | Load | Do not load |
|---|---|---|
| Vision-capable / benefits from images | `image` + short `caption` per manifest id | Twin `text` path for that id |
| No vision / no benefit | `text` path only | Twin `image` path for that id |

Implemented by `lib/context_select.py` + `docs/context/manifest.json`.

## Method (lightweight)

1. Generate panels: `python3 lib/context_render.py --repo-root .`
2. Ask the same routing question twice in comparable sessions:
   - A: text-only `docs/context/routing.txt`
   - B: image-only `docs/assets/context/routing.png` + caption (no text twin)
3. Score: correct project-room vs backend-prefix distinction; token weight of context.

## Preliminary guidance (until you run a live bake-off)

- **Prefer visual** for spatial/flow structure (architecture diagram, room patterns).
- **Prefer text** for exact command flags, TOML keys, and security invariants that must be copy-paste accurate.
- Manifest can mix: architecture/routing as visual-primary; leave dense config reference text-only (omit `image` key) so vision models still get text for those ids only when no image exists — selector already falls back without double-loading.

## How agents should consume

```bash
# Multimodal session
python3 lib/context_select.py --vision | jq -r '.items[] | "\(.path)\t\(.caption // "")"'

# Text-only session  
python3 lib/context_select.py --no-vision | jq -r '.items[].path'
```

Respect each item’s `do_not_load` list.
