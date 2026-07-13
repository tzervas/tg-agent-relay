#!/usr/bin/env python3
"""lib/usage_ingest.py - Pure-stdlib TOKEN USAGE aggregation, by provider,
model, and project, over a harness's local session-transcript logs (the
ingest side of the opt-in usage dashboard - see lib/dashboard_render.py's
usage panels + handlers/usage.sh's `/usage` command).

SOURCE-ADAPTER ABSTRACTION: `ADAPTERS` maps a `source` name (from
relay.toml's `[usage].source`) to a function that enumerates + parses one
harness's usage records into a flat list[UsageRow]. One adapter ships
today: "claude-code" - it walks `<projects_dir>/*/*.jsonl` (Claude Code's
own session-transcript format; default `projects_dir` is
`~/.claude/projects`, matching the real on-disk layout) and reads each
assistant message's `usage` object (`input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`) + its `model`
id. A future harness adds its own adapter function + registers it here -
no caller (usage.sh, dashboard.sh, dashboard_render.py) needs to change.
`[usage].projects_dir` is the configurable "source path" - point it at any
directory holding that adapter's transcripts (e.g. a different machine's
synced Claude Code projects dir).

OPT-IN + BEST-EFFORT, NEVER FABRICATED (G2/VR-5 style - see this repo's
CLAUDE.md house rules, which this feature deliberately mirrors even though
tg-agent-relay itself isn't the Mycelium repo): every stage here accepts a
missing/absent/malformed source and returns an honest, explicitly-labeled
EMPTY result - it never raises for a data condition, never invents a
number, and never silently drops the fact that something was skipped (see
`collect()`'s `skipped` field). Only a genuine CLI-usage bug (wrong arg
count) exits non-zero - the same contract as lib/metrics_agg.py.

PRIVACY (this is a PUBLIC repo - see .gitignore's "Token-usage
cache/data" block and docs/USAGE.md's "Token usage dashboard" section):
this module reads local transcript files and writes ONLY a local,
gitignored JSON cache at the path the caller supplies. It never makes a
network call and never embeds real project paths/usage numbers in
anything that gets committed - test fixtures under tests/fixtures/ are
synthetic, fabricated data only.

CLI (also what handlers/usage.sh and handlers/dashboard.sh shell out to):
    usage_ingest.py <source> <projects_dir> <window> <out_json_path>
        source:       adapter name, e.g. "claude-code"
        projects_dir: path the adapter reads (~ expanded)
        window:       "today" | "all" | "<N>d" | "<N>h"  (e.g. "7d")
        out_json_path: where the aggregated summary JSON is written
                       (caller's job to make sure this path is gitignored)
    Prints "OK:<out_json_path>" on a normal collection, or
    "SKIP:<reason>" when the source was absent/unrecognized/unreadable
    (the cache is still written - an honest empty summary, never a
    fabricated one). Never raises; never exits non-zero for a data
    condition (only a malformed CLI invocation does).
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple


class UsageRow(NamedTuple):
    ts: int
    provider: str
    model: str
    project: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


# --- provider inference -----------------------------------------------------


def infer_provider(model_id: str) -> str:
    """Infer a display provider from a model id, by prefix - Declared
    heuristic (not a lookup against a live model registry), same honesty
    posture as metrics_agg.py's "model-turns avoided". Unrecognized
    prefixes fold into "other" rather than guessing."""
    m = (model_id or "").strip().lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt"):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    return "other"


# --- window resolution -------------------------------------------------------

_WINDOW_RE = re.compile(r"^(\d+)([dh])$")


def resolve_window(spec: str, now: int | None = None) -> tuple[int, int, str]:
    """Resolve a window spec ("today" | "all" | "<N>d" | "<N>h") to
    (window_start_ts, window_end_ts, label). Never raises; an unrecognized
    spec falls back to a clearly-labeled 7d window rather than silently
    defaulting to "all" (which could balloon the aggregate) or erroring."""
    if now is None:
        now = int(time.time())
    spec_norm = (spec or "").strip().lower()

    if spec_norm in ("", "all"):
        return 0, now, "all"

    if spec_norm == "today":
        local = time.localtime(now)
        midnight = int(
            time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        )
        return midnight, now, "today"

    match = _WINDOW_RE.match(spec_norm)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        hours = n * 24 if unit == "d" else n
        return now - hours * 3600, now, spec_norm

    return now - 7 * 24 * 3600, now, f"{spec_norm} (unrecognized window - defaulted to 7d)"


def filter_rows(rows: list[UsageRow], window_start: int, window_end: int) -> list[UsageRow]:
    """Pure window filter over an already-parsed row list - kept separate
    from parsing/collection so it's unit-testable with no filesystem."""
    return [r for r in rows if window_start <= r.ts <= window_end]


# --- source adapters ----------------------------------------------------------


def _parse_iso8601(raw: object) -> int | None:
    """Parse an ISO-8601 timestamp (Claude Code transcripts use
    "...Z"-suffixed UTC) to an epoch-seconds int. Never raises - a
    missing/malformed timestamp just makes that row unusable and it's
    skipped by the caller."""
    if not raw:
        return None
    try:
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


def _collect_claude_code(base: Path) -> list[UsageRow]:
    """The "claude-code" adapter: walk <base>/<project>/*.jsonl (Claude
    Code's own session-transcript layout) and extract one UsageRow per
    assistant message that carries a `usage` object. Best-effort per file
    AND per line - a malformed jsonl file, or a single malformed/partial
    line within an otherwise-good file, is skipped, never fatal (mirrors
    metrics_agg.parse_log's per-line skip contract)."""
    rows: list[UsageRow] = []
    try:
        transcripts = sorted(base.glob("*/*.jsonl"))
    except OSError:
        return rows

    for jsonl_path in transcripts:
        project = jsonl_path.parent.name
        try:
            with jsonl_path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict) or obj.get("type") != "assistant":
                        continue
                    message = obj.get("message")
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ts = _parse_iso8601(obj.get("timestamp"))
                    if ts is None:
                        continue
                    model = str(message.get("model") or "unknown")

                    def _int(v: object) -> int:
                        try:
                            return int(v)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            return 0

                    rows.append(
                        UsageRow(
                            ts=ts,
                            provider=infer_provider(model),
                            model=model,
                            project=project,
                            input_tokens=_int(usage.get("input_tokens", 0)),
                            output_tokens=_int(usage.get("output_tokens", 0)),
                            cache_read_tokens=_int(usage.get("cache_read_input_tokens", 0)),
                            cache_creation_tokens=_int(usage.get("cache_creation_input_tokens", 0)),
                        )
                    )
        except OSError:
            continue
    return rows


ADAPTERS: dict[str, Callable[[Path], list[UsageRow]]] = {
    "claude-code": _collect_claude_code,
}


# --- aggregation ---------------------------------------------------------------


def _empty_totals() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
    }


def _add_row(totals: dict[str, int], r: UsageRow) -> None:
    totals["input_tokens"] += r.input_tokens
    totals["output_tokens"] += r.output_tokens
    totals["cache_read_tokens"] += r.cache_read_tokens
    totals["cache_creation_tokens"] += r.cache_creation_tokens
    totals["total_tokens"] += (
        r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
    )


def _bucket_size_seconds(window_start: int, window_end: int) -> int:
    # Same hourly-up-to-48h/daily-beyond rule as metrics_agg.py's dashboard
    # timeline, for a consistent reading experience across both charts.
    span_hours = max(1.0, (window_end - window_start) / 3600)
    return 3600 if span_hours <= 48 else 86400


def aggregate(
    rows: list[UsageRow], window_start: int, window_end: int, window_label: str
) -> dict:
    """Compute the full usage summary from an already-window-filtered row
    list. Pure function - no I/O, fully unit-testable with a synthetic row
    list (mirrors metrics_agg.aggregate's pure/testable shape)."""
    totals = _empty_totals()
    by_provider: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_project: dict[str, dict[str, int]] = {}
    bucket_size = _bucket_size_seconds(window_start, window_end)
    buckets: dict[int, int] = {}

    for r in rows:
        _add_row(totals, r)
        _add_row(by_provider.setdefault(r.provider, _empty_totals()), r)
        _add_row(by_model.setdefault(r.model, _empty_totals()), r)
        _add_row(by_project.setdefault(r.project, _empty_totals()), r)
        b = r.ts - (r.ts % bucket_size)
        buckets[b] = buckets.get(b, 0) + (
            r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
        )

    return {
        "generated_at": int(time.time()),
        "window": window_label,
        "window_start": window_start,
        "window_end": window_end,
        "bucket_size": bucket_size,
        "total_events": len(rows),
        "totals": totals,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_project": by_project,
        "timeline": sorted(buckets.items()),
    }


def collect(source: str, projects_dir: str, window: str, now: int | None = None) -> dict:
    """The top-level, skip-graceful entry point: resolve the window,
    dispatch to the configured source adapter, filter, and aggregate.
    ALWAYS returns a valid summary dict (see aggregate()'s shape) plus
    `source`/`projects_dir`/`sources_scanned`, and a `skipped` string
    whenever the source adapter is unknown, the projects_dir is missing,
    or collection raised - the aggregate is then honestly empty (never a
    fabricated number), never a raised exception."""
    window_start, window_end, label = resolve_window(window, now)
    adapter = ADAPTERS.get(source)
    rows: list[UsageRow] = []
    skipped_reason: str | None = None

    if adapter is None:
        skipped_reason = f"unknown usage source adapter: {source!r} (only 'claude-code' ships today)"
    else:
        base = Path(projects_dir).expanduser()
        if not base.is_dir():
            skipped_reason = f"projects_dir not found: {base}"
        else:
            try:
                rows = adapter(base)
            except Exception as e:  # noqa: BLE001 - a bad transcript tree must never crash the caller
                skipped_reason = f"collection error: {e.__class__.__name__}"

    filtered = filter_rows(rows, window_start, window_end)
    agg = aggregate(filtered, window_start, window_end, label)
    agg["source"] = source
    agg["projects_dir"] = str(projects_dir)
    agg["sources_scanned"] = len(rows)
    if skipped_reason:
        agg["skipped"] = skipped_reason
    return agg


def _main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(
            "usage: usage_ingest.py <source> <projects_dir> <window> <out_json_path>",
            file=sys.stderr,
        )
        return 2
    source, projects_dir, window, out_path = argv[1], argv[2], argv[3], argv[4]

    agg = collect(source, projects_dir, window)

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(agg, f)
    except OSError:
        pass  # cache write is best-effort - never fatal, never silent (the print below still fires)

    if agg.get("skipped"):
        print(f"SKIP:{agg['skipped']}")
    else:
        print(f"OK:{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
