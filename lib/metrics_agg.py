#!/usr/bin/env python3
"""lib/metrics_agg.py - Pure aggregation over .metrics.log, plus the
matplotlib-free text renderers (the fallback path AND the /stats reply).

No third-party dependency (stdlib only) so it always runs, matching this
repo's zero-framework style (see lib/toml_to_json.py). matplotlib-DEPENDENT
rendering lives separately in lib/dashboard_render.py, which imports this
module for its numbers - so the aggregation is unit-testable with no image
library installed, and the same numbers back both the image and text paths
(one source of truth, never two divergent computations).

Log format (see lib/relay-common.sh's emit_metric): one TSV line per event,
"<epoch>\t<source>\t<event>\t<detail>". A malformed/short line is skipped,
never fatal - the log is best-effort/append-only and a single bad line must
never take the whole dashboard down (G2, never-silent-but-never-fatal, same
contract as toml_to_json.py).

CLI (also the fallback text-dashboard path a handler shells out to):
    metrics_agg.py <log_path> <window_hours> <mode>
        mode: "dashboard" (full text dashboard) | "stats" (key numbers only)
    Prints the rendered text to stdout. Never raises - a missing/unreadable
    log renders an honest "no data yet" dashboard rather than erroring.
"""
from __future__ import annotations

import sys
import time
from typing import NamedTuple


class Row(NamedTuple):
    ts: int
    source: str
    event: str
    detail: str


def parse_log(path: str) -> list[Row]:
    """Read every well-formed line of the metrics log. Never raises: a
    missing file or an unreadable line is skipped, not fatal."""
    rows: list[Row] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                ts_raw, source, event = parts[0], parts[1], parts[2]
                detail = parts[3] if len(parts) > 3 else ""
                try:
                    ts = int(ts_raw)
                except ValueError:
                    continue
                rows.append(Row(ts, source, event, detail))
    except OSError:
        return []
    return rows


def filter_window(rows: list[Row], window_hours: float, now: int | None = None) -> tuple[list[Row], int, int]:
    """Rows within the last window_hours of `now` (default: current time).
    Returns (filtered_rows, window_start_ts, window_end_ts)."""
    if now is None:
        now = int(time.time())
    window_start = now - int(window_hours * 3600)
    filtered = [r for r in rows if window_start <= r.ts <= now]
    return filtered, window_start, now


def _bucket_size_seconds(window_hours: float) -> int:
    # Hourly buckets up to 48h; daily buckets beyond that - keeps the
    # volume-over-time chart readable instead of hundreds of thin bars.
    return 3600 if window_hours <= 48 else 86400


def aggregate(rows: list[Row], window_hours: float, window_start: int, window_end: int) -> dict:
    """Compute every stat the dashboard/stats/help panels need, from an
    already-window-filtered row list. Pure function - no I/O, fully
    unit-testable with a synthetic row list."""
    messages_in = 0
    pages_sent = 0
    sends = 0
    commands_by_name: dict[str, int] = {}
    commands_by_mode = {"forward": 0, "relay": 0}
    hooks: dict[str, int] = {}
    hooks_disabled: dict[str, int] = {}
    poll_errors = 0
    generic_sends = 0

    bucket_size = _bucket_size_seconds(window_hours)
    buckets: dict[int, dict[str, int]] = {}

    def bucket_of(ts: int) -> int:
        return ts - (ts % bucket_size)

    def bump(kind: str, ts: int) -> None:
        b = bucket_of(ts)
        slot = buckets.setdefault(b, {"in": 0, "out": 0})
        slot[kind] += 1

    for r in rows:
        if r.source == "tg-poll" and r.event == "message_flushed":
            messages_in += 1
            bump("in", r.ts)
        elif r.source == "tg-poll" and r.event in ("command_forwarded", "command_relay_handled"):
            messages_in += 1
            bump("in", r.ts)
            name = r.detail or "(unnamed)"
            commands_by_name[name] = commands_by_name.get(name, 0) + 1
            mode = "relay" if r.event == "command_relay_handled" else "forward"
            commands_by_mode[mode] += 1
        elif r.source == "tg-poll" and r.event == "poll_error":
            poll_errors += 1
        elif r.source == "tg-send" and r.event == "send":
            sends += 1
            bump("out", r.ts)
            # detail is "pages=N"
            if r.detail.startswith("pages="):
                try:
                    pages_sent += int(r.detail.split("=", 1)[1])
                except ValueError:
                    pages_sent += 1
            else:
                pages_sent += 1
        elif r.source == "relay-notify" and r.event == "generic_send":
            generic_sends += 1
        elif r.source == "hook":
            if r.event.endswith("_disabled"):
                ev = r.event[: -len("_disabled")]
                hooks_disabled[ev] = hooks_disabled.get(ev, 0) + 1
            else:
                hooks[r.event] = hooks.get(r.event, 0) + 1

    hook_fires = sum(hooks.values())
    relay_handled = commands_by_mode.get("relay", 0)
    # "model-turns avoided" (Declared heuristic, not a measured token count -
    # see docs/handlers README + CLAUDE.md VR-5): every ENABLED hook ping is
    # an agent-signal delivered to the user with zero model turn spent
    # composing it, and every relay-handled command is a full user request
    # answered with zero model turn at all. Disabled-hook and forwarded
    # commands are excluded - those either never fired or DID cost a turn.
    model_turns_avoided = hook_fires + relay_handled

    timeline = sorted(
        (b, counts["in"], counts["out"]) for b, counts in buckets.items()
    )

    return {
        "window_hours": window_hours,
        "window_start": window_start,
        "window_end": window_end,
        "bucket_size": bucket_size,
        "total_events": len(rows),
        "messages_in": messages_in,
        "messages_out": sends,
        "pages_sent": pages_sent,
        "generic_sends": generic_sends,
        "commands_by_name": commands_by_name,
        "commands_by_mode": commands_by_mode,
        "hooks": hooks,
        "hooks_disabled": hooks_disabled,
        "poll_errors": poll_errors,
        "hook_fires": hook_fires,
        "model_turns_avoided": model_turns_avoided,
        "timeline": timeline,
    }


def _fmt_hours(h: float) -> str:
    if h == int(h):
        return f"{int(h)}h"
    return f"{h:g}h"


def render_text_stats(agg: dict) -> str:
    """Lightweight /stats reply - the key numbers only, no bars."""
    win = _fmt_hours(agg["window_hours"])
    lines = [
        f"📊 Relay stats — last {win}",
        "",
        f"messages in:        {agg['messages_in']}",
        f"messages out:        {agg['messages_out']} ({agg['pages_sent']} pages)",
        f"commands used:       {sum(agg['commands_by_name'].values())}",
        f"  forwarded:         {agg['commands_by_mode'].get('forward', 0)}",
        f"  relay-handled:     {agg['commands_by_mode'].get('relay', 0)}",
        f"hook events:         {agg['hook_fires']}",
        f"poll errors:         {agg['poll_errors']}",
        f"model-turns avoided: {agg['model_turns_avoided']} *",
        "",
        "* Declared estimate: enabled-hook pings + relay-handled commands -",
        "  see handlers/README.md. Not a measured token count.",
    ]
    if agg["total_events"] == 0:
        lines.append("")
        lines.append("(no metrics recorded yet in this window)")
    return "\n".join(lines)


def _bar(count: int, max_count: int, width: int = 18) -> str:
    if max_count <= 0:
        return ""
    filled = max(1, round((count / max_count) * width)) if count > 0 else 0
    return "█" * filled + "░" * (width - filled)


def _bar_section(title: str, counts: dict[str, int], limit: int = 8) -> list[str]:
    lines = [title]
    if not counts:
        lines.append("  (none)")
        return lines
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    shown, rest = items[:limit], items[limit:]
    max_count = max(c for _, c in items)
    name_width = min(16, max(len(n) for n, _ in shown))
    for name, count in shown:
        lines.append(f"  {name[:name_width]:<{name_width}}  {_bar(count, max_count)}  {count}")
    if rest:
        lines.append(f"  … +{len(rest)} more")
    return lines


def render_text_dashboard(agg: dict) -> str:
    """Full multi-panel unicode/text dashboard - the graceful fallback when
    matplotlib is unavailable, and what an image-render failure degrades to
    (never a hard failure - see handlers/dashboard.sh)."""
    win = _fmt_hours(agg["window_hours"])
    lines = [
        f"📊 Relay Dashboard — last {win}",
        "═" * 32,
        "",
        f"messages in:        {agg['messages_in']}",
        f"messages out:       {agg['messages_out']}  ({agg['pages_sent']} pages)",
        f"model-turns avoided: {agg['model_turns_avoided']} *",
        f"poll errors:        {agg['poll_errors']}",
        "",
    ]
    lines += _bar_section("Hook events by type:", agg["hooks"])
    lines.append("")
    lines += _bar_section(
        "Commands used:",
        agg["commands_by_name"],
    )
    lines.append("")
    lines.append("* model-turns avoided is a Declared estimate (enabled-hook")
    lines.append("  pings + relay-handled commands), not a measured token count.")
    if agg["total_events"] == 0:
        lines.append("")
        lines.append("(no metrics recorded yet in this window)")
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: metrics_agg.py <log_path> <window_hours> <mode:dashboard|stats>", file=sys.stderr)
        return 2
    log_path, window_hours_raw, mode = argv[1], argv[2], argv[3]
    try:
        window_hours = float(window_hours_raw)
    except ValueError:
        window_hours = 24.0
    rows = parse_log(log_path)
    filtered, w_start, w_end = filter_window(rows, window_hours)
    agg = aggregate(filtered, window_hours, w_start, w_end)
    if mode == "stats":
        print(render_text_stats(agg))
    else:
        print(render_text_dashboard(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
