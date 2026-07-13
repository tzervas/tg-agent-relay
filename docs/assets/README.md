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
- `usage-example.png` / `usage-example.txt` — a representative `/usage`
  render (image + text-fallback paths), generated from the **synthetic,
  fabricated fixture tree** at `tests/fixtures/usage-synthetic/` (fake
  project names, fake model ids, fake token counts — see
  `lib/usage_ingest.py`'s tests). Never real token-usage data — this
  feature is opt-in and its real cache is gitignored by design (see
  `docs/USAGE.md`'s "Token usage dashboard" privacy note).
- `code-highlight-example.html` — a representative `[code_highlight]
  mode = "html-doc"` render (`pygments`' `HtmlFormatter(noclasses=True)`,
  `monokai` theme, line numbers on), generated from a **synthetic,
  hand-written `.myc` snippet** (not real production code — this repo's
  Mycelium native-lexer showcase) via `lib/code_highlight.py`'s
  `MyceliumLexer`. Download and open it in a browser to see the real
  per-token color (this is exactly the self-contained document
  `sendDocument` delivers — all CSS inlined, no external stylesheet).
  `code-highlight-example.txt` shows BOTH tiers side by side for the same
  snippet: the always-on inline `<pre><code class="language-rust">` box
  (the `myc_inline_lang` alias, Tier 1) and a description of the Tier-2
  document alongside it.

Regenerate with `lib/dashboard_render.py` / `lib/metrics_agg.py` against
any `.metrics.log`, `lib/usage_ingest.py` / `lib/dashboard_render.py
--usage-only` against any usage-summary JSON, or `lib/code_highlight.py
<lang> <out.html>` (code on stdin) for a code-highlight example (see each
module's docstring for the CLI).
