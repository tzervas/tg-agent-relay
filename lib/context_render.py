#!/usr/bin/env python3
"""lib/context_render.py - Generate compact PNG panels for hybrid agent context.

Writes docs/assets/context/{architecture,routing}.png using the same dark
palette as dashboard_render.py. Skip-graceful if matplotlib is missing.

  python3 lib/context_render.py --repo-root .
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BG = "#1a1a19"
INK = "#ffffff"
MUTED = "#c3c2b7"
DIM = "#898781"
ACCENT = "#3987e5"
AQUA = "#199e70"
VIOLET = "#9085e9"


def _render_architecture(out: Path) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError:
        return False

    fig, ax = plt.subplots(figsize=(10.5, 5.2), dpi=160)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.axis("off")
    ax.set_title(
        "TG Agent Relay — architecture",
        color=INK,
        fontsize=14,
        fontweight="bold",
        loc="left",
        pad=12,
    )

    def box(x, y, w, h, label, color=ACCENT):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.02,rounding_size=0.15",
                facecolor="#2c2c2a",
                edgecolor=color,
                linewidth=1.5,
            )
        )
        ax.text(
            x + w / 2, y + h / 2, label, ha="center", va="center", color=INK, fontsize=9, wrap=True
        )

    box(0.3, 3.2, 2.2, 1.2, "Agent / hooks\n(Claude · Grok · …)", ACCENT)
    box(3.0, 3.2, 2.4, 1.2, "relay-notify\n+ adapters", AQUA)
    box(5.9, 3.2, 2.0, 1.2, "tg-send\nformat · TTS", VIOLET)
    box(8.2, 3.2, 1.5, 1.2, "Telegram\nphone", "#d95926")
    ax.annotate(
        "", xy=(2.95, 3.8), xytext=(2.55, 3.8), arrowprops=dict(arrowstyle="->", color=MUTED)
    )
    ax.annotate(
        "", xy=(5.85, 3.8), xytext=(5.45, 3.8), arrowprops=dict(arrowstyle="->", color=MUTED)
    )
    ax.annotate(
        "", xy=(8.15, 3.8), xytext=(7.95, 3.8), arrowprops=dict(arrowstyle="->", color=MUTED)
    )

    box(8.2, 0.6, 1.5, 1.2, "Phone\ninbound", "#d95926")
    box(5.5, 0.6, 2.2, 1.2, "tg-poll\nallowlist · reassembly", VIOLET)
    box(2.8, 0.6, 2.2, 1.2, "handlers\n/dashboard /usage\n/project", AQUA)
    box(0.3, 0.6, 2.0, 1.2, "Agent Monitor\nor FIFO/cmd", ACCENT)
    ax.annotate(
        "", xy=(5.45, 1.2), xytext=(8.15, 1.2), arrowprops=dict(arrowstyle="->", color=MUTED)
    )
    ax.annotate(
        "", xy=(2.75, 1.2), xytext=(5.45, 1.2), arrowprops=dict(arrowstyle="->", color=MUTED)
    )
    ax.annotate(
        "", xy=(2.35, 0.9), xytext=(2.75, 0.9), arrowprops=dict(arrowstyle="->", color=MUTED)
    )

    ax.text(0.3, 2.5, "Outbound status: 0 model tokens", color=DIM, fontsize=8)
    ax.text(
        0.3,
        0.15,
        "Inbound: relay-handled = 0 tokens; forward to agent = billed",
        color=DIM,
        fontsize=8,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return out.is_file()


def _render_routing(out: Path) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    fig, ax = plt.subplots(figsize=(10.5, 5.5), dpi=160)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.set_title(
        "Project rooms + multi-backend routing",
        color=INK,
        fontsize=14,
        fontweight="bold",
        loc="left",
        pad=8,
    )

    lines = [
        "Telegram room (group OR forum topic)  →  sticky PROJECT",
        "    backend optional: sticky OR @claude / @grok / @ollama prefix",
        "",
        "Pattern A — forum topics:  one group, thread_id per repo",
        "Pattern B — groups:        one chat_id per repo",
        "",
        "Bind:  /project bind <slug>  →  .chats.d/bindings.json (overlay)",
        "Hooks: cwd → project_from_cwd → RELAY_PROJECT → reverse-lookup room",
        "",
        "Security: ALLOWED_USER_ID only; listed chats only; no open groups",
        "Default with no routing config: single DM (legacy, unchanged)",
    ]
    y = 0.92
    for line in lines:
        ax.text(
            0.04,
            y,
            line,
            transform=ax.transAxes,
            color=MUTED
            if line.startswith(" ")
            or line.startswith("Pattern")
            or line.startswith("Bind")
            or line.startswith("Hooks")
            or line.startswith("Security")
            or line.startswith("Default")
            else INK,
            fontsize=11,
            family="monospace",
        )
        y -= 0.08

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return out.is_file()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    args = p.parse_args(argv)
    root = Path(args.repo_root).resolve()
    out_dir = root / "docs" / "assets" / "context"
    ok_a = _render_architecture(out_dir / "architecture.png")
    ok_r = _render_routing(out_dir / "routing.png")
    if not ok_a and not ok_r:
        print("SKIP: matplotlib unavailable — text-only context still works", file=sys.stderr)
        return 0
    print(f"OK architecture={ok_a} routing={ok_r} -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
