# Agent project rules — TG Agent Relay

## Hybrid context (exclusive visual / text)

Session context for models lives under `docs/context/` + `docs/assets/context/`.
It is **not** auto-injected into Telegram messages; agents load it on demand.

### Selection rule — no double-dip

| Capability | Load | Do not load |
|---|---|---|
| Vision / benefits from images | PNG + short caption for that `id` | Twin text path for that `id` |
| Text-only / no vision benefit | Text path for that `id` | Twin image path for that `id` |

**Never load both modalities for the same manifest `id`.**

### When to prefer which modality

- **Prefer visual panels** for architecture and routing (spatial / flow structure):
  `docs/assets/context/architecture.png`, `docs/assets/context/routing.png`
- **Prefer text** for commands, config keys, flags, and security invariants that
  must be copy-paste accurate (`docs/COMMANDS.md`, `relay.toml.example`, …)
- Dense config/reference topics may be text-only (no `image` in the manifest)

### How to load

```bash
# Multimodal session — images + captions only
python3 lib/context_select.py --vision --repo-root .

# Text-only session — text paths only
python3 lib/context_select.py --no-vision --repo-root .
```

Respect each item’s `do_not_load` list. Details: [`docs/context/README.md`](docs/context/README.md).

### Refresh visual panels

```bash
python3 lib/context_render.py --repo-root .
# → docs/assets/context/{architecture,routing}.png
# Requires matplotlib; without it, text fallback still works (exclusive).
```
