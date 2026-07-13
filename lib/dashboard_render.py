#!/usr/bin/env python3
"""lib/dashboard_render.py - matplotlib multi-panel dashboard IMAGE, with a
graceful text fallback baked into the same CLI contract.

Palette/theme: the dark-mode slice of the repo's validated data-viz palette
(categorical hues + dark chart chrome - see the /dataviz skill's
references/palette.md). Dark-friendly, mobile-legible font sizes, thin
marks, no dual-axis, recessive gridlines, a fixed categorical hue order -
the house non-negotiables, not eyeballed.

Contract (handlers/dashboard.sh and handlers/usage.sh are the only
callers):
    dashboard_render.py <metrics_log_path> <window_hours> <out_png_path> [usage_json_path]
        The relay-metrics `/dashboard` image. `usage_json_path` is
        OPTIONAL - handlers/dashboard.sh only passes it when
        relay.toml's `[usage].enabled = true` and a fresh
        lib/usage_ingest.py summary was written. Omitted (or unreadable),
        the dashboard renders EXACTLY as it always has - no usage panels,
        byte-for-byte the pre-usage-feature behavior.
    dashboard_render.py --usage-only <usage_json_path> <out_png_path>
        The dedicated `/usage` command image (handlers/usage.sh) - just
        the token-usage panels, no relay-operational metrics.
Prints exactly one of:
    IMAGE:<out_png_path>      - PNG written successfully, send via sendPhoto
    TEXT\n<dashboard text>     - matplotlib missing/failed; send via sendMessage
NEVER raises and NEVER exits non-zero for a rendering problem: any exception
during import/render is caught and degrades to the TEXT path, because a
dashboard that fails to draw must still answer the user (see
handlers/README.md's never-silent contract). A genuinely bad CLI usage
(wrong arg count) is the only non-zero exit - that is a caller bug, not a
runtime/data condition.

Usage panels are OPT-IN and best-effort: see lib/usage_ingest.py's module
docstring for the source-adapter contract + the privacy posture (local
only, gitignored cache, never transmitted anywhere but this relay's own
allowlisted Telegram chat). A usage summary with `total_events == 0` (or a
`skipped` reason) renders an honest "(none)"/note rather than fabricating
data - never-silent, same posture as every other panel in this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_agg  # noqa: E402

# NOTE: usage panels read an already-aggregated JSON cache (written by
# lib/usage_ingest.py's CLI) via _load_usage_agg() below - this module
# does NOT import usage_ingest or re-collect anything itself, keeping the
# collection (I/O-heavy, harness-specific) and rendering (pure) concerns
# separate, same split as metrics_agg.py/dashboard_render.py already have.

# Dark-mode slice of the validated palette (references/palette.md), fixed
# categorical order - never cycled/reassigned per-render.
BG = "#1a1a19"          # dark chart surface
INK_PRIMARY = "#ffffff"
INK_SECONDARY = "#c3c2b7"
INK_MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
CAT = {
    "blue": "#3987e5",
    "aqua": "#199e70",
    "yellow": "#c98500",
    "green": "#008300",
    "violet": "#9085e9",
    "red": "#e66767",
    "magenta": "#d55181",
    "orange": "#d95926",
}
CAT_ORDER = ["blue", "aqua", "yellow", "green", "violet", "red", "magenta", "orange"]


def _maybe_panel(ax, enabled: bool, title: str, render_fn, *args) -> None:
    """Render a panel, or an honest "(disabled in config)" placeholder in
    the SAME slot when the caller has toggled it off (relay.toml's
    `[usage].providers`/`.models`) - never silently drops the row, never
    fabricates the panel's content either way."""
    if enabled:
        render_fn(ax, *args)
        return
    ax.set_title(title, fontsize=11, color=INK_SECONDARY, loc="left")
    ax.text(0.5, 0.5, "(display disabled — [usage] config)", ha="center", va="center", color=INK_MUTED, transform=ax.transAxes)
    ax.axis("off")


def _render_image(
    agg: dict,
    out_path: str,
    usage_agg: dict | None = None,
    show_providers: bool = True,
    show_models: bool = True,
) -> bool:
    """Best-effort matplotlib render. Returns True iff out_path was written.
    Any exception (missing lib, backend issue, font issue, ...) propagates
    to the caller, which treats it identically to "matplotlib absent".

    `usage_agg` is OPTIONAL (see this module's header) - None (the default,
    and what every call site before the usage feature existed passes)
    renders the original 4-panel relay dashboard, byte-for-byte. Passing a
    usage summary dict (lib/usage_ingest.py's aggregate() shape) appends
    the token-usage panels below the relay panels. `show_providers`/
    `show_models` mirror relay.toml's `[usage].providers`/`.models` display
    toggles (default True; the by-project panel has no toggle)."""
    import matplotlib

    matplotlib.use("Agg")  # headless - no display server in this environment
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    plt.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "savefig.facecolor": BG,
            "text.color": INK_PRIMARY,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_SECONDARY,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "font.size": 11,
            "font.family": "sans-serif",
        }
    )

    show_usage = usage_agg is not None
    usage_has_trend = show_usage and len(usage_agg.get("timeline", [])) >= 2
    # model / provider / project bars, plus an optional trend row.
    extra_rows = (3 + (1 if usage_has_trend else 0)) if show_usage else 0

    height_ratios = [1.1, 2.0, 1.8, 1.8]
    if show_usage:
        height_ratios += [1.7, 1.5, 1.7]
        if usage_has_trend:
            height_ratios += [1.8]

    # Mobile-portrait aspect: tall enough for the stacked panels, narrow
    # enough to read at Telegram's inline preview width. Grows with the
    # usage panels so they stay proportioned, not squeezed.
    fig_height = 10.5 + extra_rows * 1.9
    fig = plt.figure(figsize=(7.2, fig_height), dpi=200)
    gs = fig.add_gridspec(
        4 + extra_rows,
        1,
        height_ratios=height_ratios,
        hspace=0.6,
        left=0.24,
        right=0.94,
        top=0.98 if show_usage else 0.95,
        bottom=0.03 if show_usage else 0.06,
    )

    # No emoji in the title: matplotlib's default sans font (DejaVu Sans)
    # has no color-emoji glyphs and renders one as a missing-glyph box -
    # text sends (which DO render emoji fine via Telegram's own font) keep
    # the emoji; the image title stays plain ASCII/unicode-punctuation only.
    win_label = metrics_agg._fmt_hours(agg["window_hours"])
    fig.suptitle(f"Relay Dashboard — last {win_label}", fontsize=16, fontweight="bold", color=INK_PRIMARY, y=0.99 if show_usage else 0.985)

    # --- Panel 1: header stat row (text tiles, no chart chrome) ---
    ax0 = fig.add_subplot(gs[0])
    ax0.axis("off")
    stats = [
        ("in", agg["messages_in"]),
        ("out", f"{agg['messages_out']} ({agg['pages_sent']}pg)"),
        ("turns avoided", agg["model_turns_avoided"]),
        ("poll errors", agg["poll_errors"]),
    ]
    if show_usage:
        stats.append(("tokens", _fmt_tokens(usage_agg.get("totals", {}).get("total_tokens", 0))))
    n = len(stats)
    for i, (label, value) in enumerate(stats):
        x = (i + 0.5) / n
        ax0.text(x, 0.62, str(value), ha="center", va="center", fontsize=17 if n <= 4 else 14, fontweight="bold", color=INK_PRIMARY, transform=ax0.transAxes)
        ax0.text(x, 0.12, label, ha="center", va="center", fontsize=9.5, color=INK_MUTED, transform=ax0.transAxes)
    ax0.set_xlim(0, 1)
    ax0.set_ylim(0, 1)

    # --- Panel 2: volume over time (line, in vs out) - ONE axis, two series ---
    ax1 = fig.add_subplot(gs[1])
    timeline = agg["timeline"]
    import datetime as _dt

    import matplotlib.dates as mdates

    # ALWAYS bound the x-axis to the actual query window (never to the data
    # extent) - with 0-2 sparse points, autoscaling to data extent lets
    # matplotlib's AutoDateLocator pick a wildly wrong span (observed:
    # years, for a 24h window with one bucket) - see this file's tests.
    win_start_dt = _dt.datetime.fromtimestamp(agg["window_start"])
    win_end_dt = _dt.datetime.fromtimestamp(agg["window_end"])
    if timeline:
        xs = [_dt.datetime.fromtimestamp(b) for b, _, _ in timeline]
        ins = [i for _, i, _ in timeline]
        outs = [o for _, _, o in timeline]
        ax1.plot(xs, ins, color=CAT["blue"], linewidth=2, marker="o", markersize=4, label="in")
        ax1.plot(xs, outs, color=CAT["aqua"], linewidth=2, marker="o", markersize=4, label="out")
        ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    else:
        ax1.text(0.5, 0.5, "no traffic in this window", ha="center", va="center", color=INK_MUTED, transform=ax1.transAxes)
    locator = mdates.AutoDateLocator(maxticks=6, minticks=3)
    ax1.xaxis.set_major_locator(locator)
    ax1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax1.set_xlim(win_start_dt, win_end_dt)
    for label in ax1.get_xticklabels():
        label.set_color(INK_MUTED)
        label.set_fontsize(9)
    ax1.set_title("Volume over time", fontsize=11, color=INK_SECONDARY, loc="left")
    ax1.grid(True, color=GRID, linewidth=0.6, axis="y")
    ax1.spines[["top", "right"]].set_visible(False)
    ax1.spines[["left", "bottom"]].set_color(BASELINE)
    ax1.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    # Zero-based y-axis - a line chart on a truncated axis misrepresents
    # magnitude (the #1 anti-pattern the /dataviz skill flags).
    ax1.set_ylim(bottom=0)

    # --- Panel 3: hook events by type (horizontal bar - single sequential
    # --- hue; the y-axis already names identity, so bar length is the only
    # --- job color needs to do here) ---
    ax2 = fig.add_subplot(gs[2])
    _hbar(ax2, agg["hooks"], "Hook events by type", CAT["blue"])

    # --- Panel 4: command usage (horizontal bar, same treatment) ---
    ax3 = fig.add_subplot(gs[3])
    _hbar(ax3, agg["commands_by_name"], "Commands used", CAT["violet"])

    # --- Optional usage panels (only when the caller opted in - see the
    # --- usage_agg contract in this function's docstring) ---
    if show_usage:
        row = 4
        usage_win = usage_agg.get("window", "?")

        ax_model = fig.add_subplot(gs[row])
        row += 1
        _maybe_panel(
            ax_model,
            show_models,
            f"Tokens by model (usage window: {usage_win})",
            lambda ax: _hbar(
                ax,
                {m: d.get("total_tokens", 0) for m, d in usage_agg.get("by_model", {}).items()},
                f"Tokens by model (usage window: {usage_win})",
                CAT["yellow"],
            ),
        )

        ax_provider = fig.add_subplot(gs[row])
        row += 1
        _maybe_panel(
            ax_provider,
            show_providers,
            "Tokens by provider (share)",
            lambda ax: _usage_share_bar(ax, usage_agg.get("by_provider", {}), usage_agg.get("totals", {}).get("total_tokens", 0)),
        )

        ax_project = fig.add_subplot(gs[row])
        row += 1
        _hbar(
            ax_project,
            {p: d.get("total_tokens", 0) for p, d in usage_agg.get("by_project", {}).items()},
            "Tokens by project",
            CAT["green"],
        )

        if usage_has_trend:
            ax_trend = fig.add_subplot(gs[row])
            row += 1
            _usage_trend(ax_trend, usage_agg)

        if usage_agg.get("skipped"):
            fig.text(0.5, 0.006, f"usage note: {usage_agg['skipped']}", ha="center", fontsize=7.5, color=INK_MUTED)

    fig.savefig(out_path, format="png")
    plt.close(fig)
    return Path(out_path).is_file() and Path(out_path).stat().st_size > 0


def _hbar(ax, counts: dict, title: str, color: str, limit: int = 8) -> None:
    import matplotlib.ticker as mticker

    ax.set_title(title, fontsize=11, color=INK_SECONDARY, loc="left")
    if not counts:
        ax.text(0.5, 0.5, "(none)", ha="center", va="center", color=INK_MUTED, transform=ax.transAxes)
        ax.axis("off")
        return
    items = sorted(counts.items(), key=lambda kv: kv[1])[-limit:]
    names = [n for n, _ in items]
    values = [v for _, v in items]
    y = range(len(names))
    ax.barh(y, values, color=color, height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=9.5, color=INK_SECONDARY)
    for i, v in enumerate(values):
        ax.text(v, i, f"  {v}", va="center", ha="left", fontsize=9, color=INK_PRIMARY)
    ax.grid(True, color=GRID, linewidth=0.6, axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax.set_xlim(0, max(values) * 1.2 if values else 1)


def _usage_share_bar(ax, by_provider: dict, total_tokens: int, limit: int = 6) -> None:
    """Tokens-by-provider SHARE panel. Deliberately a horizontal bar of
    percentages, NOT a pie/donut - a handful of categories compared by
    length reads faster and more accurately than compared by angle/area
    (see the /dataviz skill's anti-patterns: "a donut/pie for comparing
    close values -> a bar, or the numbers"). Same mark-type convention as
    every other categorical panel in this file (_hbar)."""
    import matplotlib.ticker as mticker

    ax.set_title("Tokens by provider (share)", fontsize=11, color=INK_SECONDARY, loc="left")
    if not by_provider or total_tokens <= 0:
        ax.text(0.5, 0.5, "(none)", ha="center", va="center", color=INK_MUTED, transform=ax.transAxes)
        ax.axis("off")
        return
    items = sorted(by_provider.items(), key=lambda kv: kv[1].get("total_tokens", 0))[-limit:]
    names = [n for n, _ in items]
    values = [d.get("total_tokens", 0) for _, d in items]
    pct = [(v / total_tokens * 100) if total_tokens else 0 for v in values]
    y = range(len(names))
    ax.barh(y, pct, color=CAT["orange"], height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=9.5, color=INK_SECONDARY)
    for i, p in enumerate(pct):
        ax.text(p, i, f"  {p:.0f}%", va="center", ha="left", fontsize=9, color=INK_PRIMARY)
    ax.set_xlim(0, 100)
    ax.grid(True, color=GRID, linewidth=0.6, axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))


def _usage_trend(ax, usage_agg: dict) -> None:
    """Token usage over time - the same ONE-axis line-with-markers
    treatment as the relay dashboard's "Volume over time" panel, single
    series (total tokens per bucket). Only called when the caller has
    already checked len(timeline) >= 2 (see "if timestamps allow" in the
    usage feature's design) - a single point can't show a trend."""
    import datetime as _dt

    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker

    ax.set_title(f"Token usage over time (usage window: {usage_agg.get('window', '?')})", fontsize=11, color=INK_SECONDARY, loc="left")
    timeline = usage_agg.get("timeline", [])
    xs = [_dt.datetime.fromtimestamp(b) for b, _ in timeline]
    ys = [v for _, v in timeline]
    ax.plot(xs, ys, color=CAT["blue"], linewidth=2, marker="o", markersize=4)
    locator = mdates.AutoDateLocator(maxticks=6, minticks=3)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    for label in ax.get_xticklabels():
        label.set_color(INK_MUTED)
        label.set_fontsize(9)
    ax.grid(True, color=GRID, linewidth=0.6, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(BASELINE)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax.set_ylim(bottom=0)


def _fmt_tokens(n: object) -> str:
    """Compact token-count formatting (12345 -> "12.3k") for the header
    stat tiles, where raw digit counts get unreadable fast."""
    try:
        n = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _top_key(d: dict) -> str | None:
    """The key with the largest total_tokens in a by_model/by_project
    dict, or None for an empty dict - never fabricates a "top" when there
    is no data."""
    if not d:
        return None
    return max(d.items(), key=lambda kv: kv[1].get("total_tokens", 0))[0]


def _truncate(s: str, limit: int = 13) -> str:
    """Truncate a header-stat-tile value (a model id, a project slug) so
    it fits its tile without colliding with its neighbors - the full,
    untruncated name is always still visible in that panel's own bar
    chart below; this is purely a header-tile space constraint."""
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _load_usage_agg(path: str | None) -> dict | None:
    """Best-effort load of a lib/usage_ingest.py JSON summary. Returns
    None for a missing/unset/unreadable/malformed path - the caller then
    renders NO usage panels (for the /dashboard path) or the honest
    "unavailable" text (for the /usage path), never raises."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _render_usage_image(
    usage_agg: dict, out_path: str, show_providers: bool = True, show_models: bool = True
) -> bool:
    """The dedicated `/usage` command image - header stat tiles + the
    three token-usage bars + an optional trend row. Same palette/theme/
    mark conventions as _render_image; never raises (propagates to the
    caller like _render_image does). `show_providers`/`show_models` mirror
    relay.toml's `[usage].providers`/`.models` display toggles."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "savefig.facecolor": BG,
            "text.color": INK_PRIMARY,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_SECONDARY,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "font.size": 11,
            "font.family": "sans-serif",
        }
    )

    has_trend = len(usage_agg.get("timeline", [])) >= 2
    n_rows = 4 + (1 if has_trend else 0)
    height_ratios = [1.1, 1.8, 1.6, 1.8] + ([1.8] if has_trend else [])
    fig = plt.figure(figsize=(7.2, 10.3 if not has_trend else 12.1), dpi=200)
    gs = fig.add_gridspec(n_rows, 1, height_ratios=height_ratios, hspace=0.65, left=0.24, right=0.94, top=0.95, bottom=0.05)

    win_label = usage_agg.get("window", "?")
    fig.suptitle(f"Token Usage — {win_label}", fontsize=16, fontweight="bold", color=INK_PRIMARY, y=0.985)

    totals = usage_agg.get("totals", {})
    ax0 = fig.add_subplot(gs[0])
    ax0.axis("off")
    # (value, label, is_numeric) - numeric tiles (short, e.g. "12.3k") get
    # a larger bold face; the two name tiles (a model id / project slug,
    # which can run long) get a smaller face AND are truncated so they
    # never collide with their neighbor - the full name is always still
    # visible in that panel's own bar chart below.
    stats = [
        (_fmt_tokens(totals.get("total_tokens", 0)), "total", True),
        (
            f"{_fmt_tokens(totals.get('input_tokens', 0))} / {_fmt_tokens(totals.get('output_tokens', 0))}",
            "in / out",
            True,
        ),
        (_truncate(_top_key(usage_agg.get("by_model", {})) or "—"), "top model", False),
        (_truncate(_top_key(usage_agg.get("by_project", {})) or "—"), "top project", False),
    ]
    n = len(stats)
    for i, (value, label, is_numeric) in enumerate(stats):
        x = (i + 0.5) / n
        ax0.text(x, 0.62, str(value), ha="center", va="center", fontsize=15 if is_numeric else 10.5, fontweight="bold", color=INK_PRIMARY, transform=ax0.transAxes)
        ax0.text(x, 0.12, label, ha="center", va="center", fontsize=9.5, color=INK_MUTED, transform=ax0.transAxes)
    ax0.set_xlim(0, 1)
    ax0.set_ylim(0, 1)

    ax1 = fig.add_subplot(gs[1])
    _maybe_panel(
        ax1,
        show_models,
        "Tokens by model",
        lambda ax: _hbar(ax, {m: d.get("total_tokens", 0) for m, d in usage_agg.get("by_model", {}).items()}, "Tokens by model", CAT["yellow"]),
    )

    ax2 = fig.add_subplot(gs[2])
    _maybe_panel(
        ax2,
        show_providers,
        "Tokens by provider (share)",
        lambda ax: _usage_share_bar(ax, usage_agg.get("by_provider", {}), totals.get("total_tokens", 0)),
    )

    ax3 = fig.add_subplot(gs[3])
    _hbar(ax3, {p: d.get("total_tokens", 0) for p, d in usage_agg.get("by_project", {}).items()}, "Tokens by project", CAT["green"])

    if has_trend:
        ax4 = fig.add_subplot(gs[4])
        _usage_trend(ax4, usage_agg)

    if usage_agg.get("skipped"):
        fig.text(0.5, 0.012, f"note: {usage_agg['skipped']}", ha="center", fontsize=8, color=INK_MUTED)
    elif usage_agg.get("total_events", 0) == 0:
        fig.text(0.5, 0.012, "no usage data recorded yet in this window", ha="center", fontsize=8, color=INK_MUTED)

    fig.savefig(out_path, format="png")
    plt.close(fig)
    return Path(out_path).is_file() and Path(out_path).stat().st_size > 0


def _render_usage_text(usage_agg: dict | None, show_providers: bool = True, show_models: bool = True) -> str:
    """The unicode/text fallback for the `/usage` command - same
    never-fails-to-answer contract as metrics_agg.render_text_dashboard.
    Reuses metrics_agg's _bar_section renderer (one source of truth for
    the bar-list text shape, never a second divergent implementation).
    `show_providers`/`show_models` mirror relay.toml's `[usage].providers`/
    `.models` display toggles - a disabled section is labeled, not
    silently dropped, so the toggle's effect is always visible."""
    if not usage_agg:
        return (
            "📈 Token usage — unavailable\n"
            "(usage tracking is disabled, or no source is configured — see relay.toml's [usage] table)"
        )
    totals = usage_agg.get("totals", {})
    win = usage_agg.get("window", "?")
    lines = [
        f"📈 Token usage — {win}",
        "═" * 32,
        "",
        f"total tokens:   {totals.get('total_tokens', 0)}",
        f"  input:        {totals.get('input_tokens', 0)}",
        f"  output:       {totals.get('output_tokens', 0)}",
        f"  cache read:   {totals.get('cache_read_tokens', 0)}",
        f"  cache create: {totals.get('cache_creation_tokens', 0)}",
        "",
    ]
    if show_providers:
        lines += metrics_agg._bar_section(
            "By provider:", {p: d.get("total_tokens", 0) for p, d in usage_agg.get("by_provider", {}).items()}
        )
    else:
        lines += ["By provider:", "  (display disabled — [usage].providers = false)"]
    lines.append("")
    if show_models:
        lines += metrics_agg._bar_section(
            "By model:", {m: d.get("total_tokens", 0) for m, d in usage_agg.get("by_model", {}).items()}
        )
    else:
        lines += ["By model:", "  (display disabled — [usage].models = false)"]
    lines.append("")
    lines += metrics_agg._bar_section(
        "By project:", {p: d.get("total_tokens", 0) for p, d in usage_agg.get("by_project", {}).items()}
    )
    if usage_agg.get("skipped"):
        lines += ["", f"note: {usage_agg['skipped']}"]
    elif usage_agg.get("total_events", 0) == 0:
        lines += ["", "(no usage data recorded yet in this window)"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    # Optional, order-independent display-toggle flags (relay.toml's
    # [usage].providers/.models -> handlers/usage.sh + dashboard.sh pass
    # these when a toggle is explicitly false; default - flags absent - is
    # both panels shown, unchanged from before the toggles existed).
    show_providers = "--no-providers" not in argv
    show_models = "--no-models" not in argv
    argv = [a for a in argv if a not in ("--no-providers", "--no-models")]

    if len(argv) >= 2 and argv[1] == "--usage-only":
        if len(argv) < 4:
            print("usage: dashboard_render.py --usage-only <usage_json_path> <out_png_path> [--no-providers] [--no-models]", file=sys.stderr)
            return 2
        usage_json_path, out_path = argv[2], argv[3]
        usage_agg = _load_usage_agg(usage_json_path)

        ok = False
        if usage_agg is not None:
            try:
                ok = _render_usage_image(usage_agg, out_path, show_providers=show_providers, show_models=show_models)
            except Exception:  # noqa: BLE001 - ANY render failure degrades to text, never crashes
                ok = False

        if ok:
            print(f"IMAGE:{out_path}")
        else:
            print("TEXT")
            print(_render_usage_text(usage_agg, show_providers=show_providers, show_models=show_models))
        return 0

    if len(argv) < 4:
        print(
            "usage: dashboard_render.py <log_path> <window_hours> <out_png_path> [usage_json_path] [--no-providers] [--no-models]",
            file=sys.stderr,
        )
        return 2
    log_path, window_hours_raw, out_path = argv[1], argv[2], argv[3]
    usage_json_path = argv[4] if len(argv) > 4 else None
    try:
        window_hours = float(window_hours_raw)
    except ValueError:
        window_hours = 24.0

    rows = metrics_agg.parse_log(log_path)
    filtered, w_start, w_end = metrics_agg.filter_window(rows, window_hours)
    agg = metrics_agg.aggregate(filtered, window_hours, w_start, w_end)
    usage_agg = _load_usage_agg(usage_json_path)

    try:
        ok = _render_image(agg, out_path, usage_agg=usage_agg, show_providers=show_providers, show_models=show_models)
    except Exception:  # noqa: BLE001 - ANY render failure degrades to text, never crashes
        ok = False

    if ok:
        print(f"IMAGE:{out_path}")
    else:
        print("TEXT")
        print(metrics_agg.render_text_dashboard(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
