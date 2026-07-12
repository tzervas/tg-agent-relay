# docs/assets/

Sample artifacts for a future README showcase (not built here — see
`ROADMAP.md`).

- `dashboard-example.png` — a representative `/dashboard` render (the
  matplotlib image path), generated from a synthetic `.metrics.log` with a
  realistic 24h spread across all four panels (header stats,
  volume-over-time, hook-event breakdown, command usage). Real live traffic
  in this repo's own bridge is currently too sparse to make a good example
  image, so this is illustrative, not a screenshot of production data.
- `dashboard-example.txt` — the same synthetic data through the unicode/text
  fallback path (`lib/metrics_agg.py`'s `render_text_dashboard`), for when
  matplotlib is unavailable.

Regenerate either with `lib/dashboard_render.py` / `lib/metrics_agg.py`
against any `.metrics.log` (see their module docstrings for the CLI).
