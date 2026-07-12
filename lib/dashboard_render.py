#!/usr/bin/env python3
"""lib/dashboard_render.py - matplotlib multi-panel dashboard IMAGE, with a
graceful text fallback baked into the same CLI contract.

Palette/theme: the dark-mode slice of the repo's validated data-viz palette
(categorical hues + dark chart chrome - see the /dataviz skill's
references/palette.md). Dark-friendly, mobile-legible font sizes, thin
marks, no dual-axis, recessive gridlines, a fixed categorical hue order -
the house non-negotiables, not eyeballed.

Contract (handlers/dashboard.sh is the only caller):
    dashboard_render.py <metrics_log_path> <window_hours> <out_png_path>
Prints exactly one of:
    IMAGE:<out_png_path>      - PNG written successfully, send via sendPhoto
    TEXT\n<dashboard text>     - matplotlib missing/failed; send via sendMessage
NEVER raises and NEVER exits non-zero for a rendering problem: any exception
during import/render is caught and degrades to the TEXT path, because a
dashboard that fails to draw must still answer the user (see
handlers/README.md's never-silent contract). A genuinely bad CLI usage
(wrong arg count) is the only non-zero exit - that is a caller bug, not a
runtime/data condition.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_agg  # noqa: E402

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


def _render_image(agg: dict, out_path: str) -> bool:
    """Best-effort matplotlib render. Returns True iff out_path was written.
    Any exception (missing lib, backend issue, font issue, ...) propagates
    to the caller, which treats it identically to "matplotlib absent"."""
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

    # Mobile-portrait aspect: tall enough for 4 stacked panels, narrow
    # enough to read at Telegram's inline preview width.
    fig = plt.figure(figsize=(7.2, 10.5), dpi=200)
    gs = fig.add_gridspec(
        4, 1, height_ratios=[1.1, 2.0, 1.8, 1.8], hspace=0.6, left=0.24, right=0.94, top=0.95, bottom=0.06
    )

    # No emoji in the title: matplotlib's default sans font (DejaVu Sans)
    # has no color-emoji glyphs and renders one as a missing-glyph box -
    # text sends (which DO render emoji fine via Telegram's own font) keep
    # the emoji; the image title stays plain ASCII/unicode-punctuation only.
    win_label = metrics_agg._fmt_hours(agg["window_hours"])
    fig.suptitle(f"Relay Dashboard — last {win_label}", fontsize=16, fontweight="bold", color=INK_PRIMARY, y=0.985)

    # --- Panel 1: header stat row (text tiles, no chart chrome) ---
    ax0 = fig.add_subplot(gs[0])
    ax0.axis("off")
    stats = [
        ("in", agg["messages_in"]),
        ("out", f"{agg['messages_out']} ({agg['pages_sent']}pg)"),
        ("turns avoided", agg["model_turns_avoided"]),
        ("poll errors", agg["poll_errors"]),
    ]
    n = len(stats)
    for i, (label, value) in enumerate(stats):
        x = (i + 0.5) / n
        ax0.text(x, 0.62, str(value), ha="center", va="center", fontsize=17, fontweight="bold", color=INK_PRIMARY, transform=ax0.transAxes)
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


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: dashboard_render.py <log_path> <window_hours> <out_png_path>", file=sys.stderr)
        return 2
    log_path, window_hours_raw, out_path = argv[1], argv[2], argv[3]
    try:
        window_hours = float(window_hours_raw)
    except ValueError:
        window_hours = 24.0

    rows = metrics_agg.parse_log(log_path)
    filtered, w_start, w_end = metrics_agg.filter_window(rows, window_hours)
    agg = metrics_agg.aggregate(filtered, window_hours, w_start, w_end)

    try:
        ok = _render_image(agg, out_path)
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
