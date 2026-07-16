#!/usr/bin/env python3
"""lib/usage_ingest.py - Pure-stdlib TOKEN USAGE aggregation, by provider,
model, and project, over a harness's local session-transcript logs (the
ingest side of the opt-in usage dashboard - see lib/dashboard_render.py's
usage panels + handlers/usage.sh's `/usage` command).

SOURCE-ADAPTER ABSTRACTION (issue #31): `ADAPTERS` maps a `source` name
(from relay.toml's `[usage].source`) to a function that enumerates + parses
one harness's usage records into a flat list[UsageRow].

**Registry is the sole registration path.** ADAPTERS is populated from the
providers registry (`Provider.usage_source` + `Provider.usage_collect` on
each entry in `providers/*`). A new harness adds usage by registering a
Provider with `usage_collect` — no hard-coded ADAPTERS entries and no
caller (usage.sh, dashboard.sh, dashboard_render.py) needs to change.

**Local `_collect_*` are FALLBACK ONLY.** `_collect_claude_code` /
`_collect_grok` fill the historical source keys only when the providers
package fails to import (or a source has no collector). When providers are
importable they always win. Call `refresh_usage_adapters()` after dynamic
Provider registration so new collectors appear in collect().

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
        window:       "today" | "all" | "lifetime" | "<N>h|d|w|m|y"
        out_json_path: where the aggregated summary JSON is written
                       (caller's job to make sure this path is gitignored)
    source may also be "grok", "multi", or "auto" (merge claude-code+grok).
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
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple


class UsageRow(NamedTuple):
    ts: int
    provider: str
    model: str
    project: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    adapter: str = ""  # usage source key (claude-code, grok, …) set in collect()


# --- provider inference -----------------------------------------------------


def infer_provider(model_id: str) -> str:
    """Infer a display provider from a model id.

    Prefer registered provider extensions (providers/*) when importable;
    fall back to built-in prefix heuristics. Unrecognized → \"other\".
    """
    m = (model_id or "").strip().lower()
    # Extension registry (Grok/Claude/Ollama/…)
    try:
        repo = Path(__file__).resolve().parents[1]
        import sys

        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from providers.base import infer_provider_label  # type: ignore

        label = infer_provider_label(model_id or "")
        if label:
            return label
    except Exception:
        pass
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("grok"):
        return "xai"
    if m.startswith("llama") or m.startswith("mistral") or m.startswith("qwen"):
        return "ollama"
    return "other"


def display_model(model_id: str) -> str:
    """Short, human-readable model label for charts. Aggregation always
    keeps the raw model id; this is render-only."""
    m = (model_id or "").strip()
    if not m or m == "<synthetic>":
        return m or "unknown"
    low = m.lower()
    # claude-opus-4-8 / claude-sonnet-5 / claude-fable-5 / claude-haiku-4-5-20251001
    if low.startswith("claude-"):
        rest = m[7:]
        # drop trailing date yyyymmdd
        rest = re.sub(r"-\d{8}$", "", rest)
        parts = rest.split("-")
        if parts:
            family = parts[0].capitalize()
            ver = ".".join(parts[1:]) if len(parts) > 1 else ""
            return f"{family} {ver}".strip() if ver else family
    if low.startswith("grok"):
        return m  # already short enough (grok-4.5, grok-build-0.1)
    if len(m) > 22:
        return m[:21] + "…"
    return m


# --- window resolution -------------------------------------------------------

# N + unit: h=hours, d=days, w=weeks, m=months(~30d), y=years(~365d)
_WINDOW_RE = re.compile(r"^(\d+)([hdwmy])$")


def resolve_window(spec: str, now: int | None = None) -> tuple[int, int, str]:
    """Resolve a window spec to (window_start_ts, window_end_ts, label).

    Accepted:
      today | all | lifetime | <N>h | <N>d | <N>w | <N>m | <N>y
    `lifetime` is an alias for `all` labeled as local retained history
    (not provider billing lifetime). Unrecognized specs fall back to a
    clearly-labeled 7d window."""
    if now is None:
        now = int(time.time())
    spec_norm = (spec or "").strip().lower()

    if spec_norm in ("", "all"):
        return 0, now, "all"
    if spec_norm == "lifetime":
        # Honest label: local retained transcripts only, not subscription billing.
        return 0, now, "lifetime (local retained)"

    if spec_norm == "today":
        local = time.localtime(now)
        midnight = int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))
        return midnight, now, "today"

    match = _WINDOW_RE.match(spec_norm)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        if unit == "h":
            seconds = n * 3600
        elif unit == "d":
            seconds = n * 86400
        elif unit == "w":
            seconds = n * 7 * 86400
        elif unit == "m":
            seconds = n * 30 * 86400  # approximate calendar month
        else:  # y
            seconds = n * 365 * 86400  # approximate calendar year
        return now - seconds, now, spec_norm

    return now - 7 * 24 * 3600, now, f"{spec_norm} (unrecognized window - defaulted to 7d)"


def row_total_tokens(r: UsageRow) -> int:
    return (
        r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
    )


ALLOTMENT_PERIODS = frozenset({"daily", "weekly", "monthly"})


def period_window_bounds(period: str, now: int | None = None) -> tuple[int, int]:
    """Local-calendar bounds for quota periods (daily / weekly / monthly).

    weekly starts Monday 00:00 local; monthly starts day-of-month 1 00:00 local.
    """
    if now is None:
        now = int(time.time())
    period_norm = (period or "").strip().lower()
    local = time.localtime(now)
    if period_norm == "daily":
        start = int(
            time.mktime(
                (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
            )
        )
        return start, now
    if period_norm == "weekly":
        # Monday-based week (tm_wday: 0=Mon … 6=Sun)
        days_since_mon = local.tm_wday
        midnight_today = int(
            time.mktime(
                (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
            )
        )
        start = midnight_today - days_since_mon * 86400
        return start, now
    if period_norm == "monthly":
        start = int(
            time.mktime((local.tm_year, local.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        )
        return start, now
    return 0, now


def parse_allotments(raw: object) -> dict[str, dict[str, int | None]]:
    """Normalize relay.toml ``[usage.allotments]`` to {subject: {period: cap}}.

    Supports nested tables (``[usage.allotments.claude-code] daily = …``) and
    dotted keys (``claude-code.weekly = …``). Omitted periods or caps mean no
    quota line for that slot. ``0`` means unlimited (no bar).
    """
    out: dict[str, dict[str, int | None]] = {}
    if not isinstance(raw, dict):
        return out

    def _set_cap(subject: str, period: str, cap_raw: object) -> None:
        subject = (subject or "").strip()
        period = (period or "").strip().lower()
        if not subject or period not in ALLOTMENT_PERIODS:
            return
        if cap_raw is None:
            return
        try:
            cap = int(cap_raw)
        except (TypeError, ValueError):
            return
        if cap <= 0:
            out.setdefault(subject, {})[period] = None  # unlimited — no bar
        else:
            out.setdefault(subject, {})[period] = cap

    for key, val in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, dict):
            for period, cap_raw in val.items():
                if isinstance(period, str):
                    _set_cap(key, period, cap_raw)
            continue
        if "." in key:
            subject, _, period = key.partition(".")
            _set_cap(subject, period, val)
    return out


def usage_in_period(
    rows: list[UsageRow],
    window_start: int,
    window_end: int,
    *,
    adapter: str | None = None,
) -> int:
    """Sum token mass for rows in [window_start, window_end].

    ``adapter`` None or ``total`` counts every row; otherwise match
    ``UsageRow.adapter`` (usage source key).
    """
    total = 0
    want_all = adapter is None or adapter == "total"
    for r in rows:
        if not (window_start <= r.ts <= window_end):
            continue
        if want_all or r.adapter == adapter:
            total += row_total_tokens(r)
    return total


def allotment_usage_snapshot(
    rows: list[UsageRow],
    allotments: dict[str, dict[str, int | None]],
    now: int | None = None,
) -> dict[str, dict[str, dict[str, int | float | None]]]:
    """Per configured subject+period: used tokens, cap, percent (or None if unlimited)."""
    if now is None:
        now = int(time.time())
    snapshot: dict[str, dict[str, dict[str, int | float | None]]] = {}
    for subject, periods in allotments.items():
        for period, cap in periods.items():
            if cap is None:
                continue  # unlimited / omitted cap — no quota row
            ws, we = period_window_bounds(period, now)
            used = usage_in_period(rows, ws, we, adapter=subject)
            pct: float | None = None
            if cap > 0:
                pct = min(100.0, round(100.0 * used / cap, 1))
            snapshot.setdefault(subject, {})[period] = {
                "used": used,
                "cap": cap,
                "percent": pct,
            }
    return snapshot


def quota_progress_bar(percent: float | None, width: int = 10) -> str:
    """Text bar for Telegram; empty when percent is None."""
    if percent is None:
        return ""
    filled = int(round(width * min(100.0, max(0.0, percent)) / 100.0))
    filled = min(width, max(0, filled))
    return "[" + ("█" * filled) + ("░" * (width - filled)) + "]"


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
    except (ValueError, TypeError) as _exc:
        return None


def _int_field(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError) as _exc:
        return 0


def _collect_claude_code(base: Path) -> list[UsageRow]:
    """FALLBACK collector for source "claude-code".

    Prefer ``providers.claude.usage.collect_usage`` via the registry
    (see ``refresh_usage_adapters``). This local copy is used only when
    the providers package cannot be imported at registration time.

    Recursively walks <base>/**/*.jsonl (Claude Code session-transcript
    layout, including nested session/subagent directories). Project slug
    is the first path component under `base`. Best-effort per file AND
    per line.
    """
    rows: list[UsageRow] = []
    try:
        transcripts = sorted(base.rglob("*.jsonl"))
    except OSError:
        return rows

    for jsonl_path in transcripts:
        try:
            rel = jsonl_path.relative_to(base)
        except ValueError:
            continue
        # First component under projects_dir is the project directory.
        project = rel.parts[0] if rel.parts else jsonl_path.parent.name
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
                    if model == "<synthetic>":
                        continue

                    rows.append(
                        UsageRow(
                            ts=ts,
                            provider=infer_provider(model),
                            model=model,
                            project=project,
                            input_tokens=_int_field(usage.get("input_tokens", 0)),
                            output_tokens=_int_field(usage.get("output_tokens", 0)),
                            cache_read_tokens=_int_field(usage.get("cache_read_input_tokens", 0)),
                            cache_creation_tokens=_int_field(
                                usage.get("cache_creation_input_tokens", 0)
                            ),
                        )
                    )
        except OSError:
            continue
    return rows


def _collect_grok(base: Path) -> list[UsageRow]:
    """FALLBACK collector for source "grok" (Declared / best-effort).

    Prefer ``providers.grok.usage.collect_usage`` via the registry
    (see ``refresh_usage_adapters``). This local copy is used only when
    the providers package cannot be imported at registration time.

    Grok does not persist Claude-style input/output token billing on
    assistant messages. We approximate using the peak
    `params._meta.totalTokens` seen in each session's updates.jsonl
    (running context total), attributed to the session's primary model
    from summary.json / signals.json. Rows carry total_tokens-only mass
    in input_tokens (output/cache zero) so aggregate totals remain
    non-zero; callers should treat this as a context-peak proxy, not
    API billing. See module docs / UI labels.
    """
    rows: list[UsageRow] = []
    try:
        session_dirs = [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        return rows

    for workspace_dir in session_dirs:
        # Workspace folders are URL-encoded paths; use a short slug.
        project = workspace_dir.name
        if len(project) > 48:
            project = project[:45] + "…"
        try:
            sid_dirs = [p for p in workspace_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for sess in sid_dirs:
            model = "grok"
            ts = int(sess.stat().st_mtime) if sess.exists() else int(time.time())
            summary_path = sess / "summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(summary, dict):
                        model = str(
                            summary.get("current_model_id")
                            or (summary.get("info") or {}).get("model")
                            or model
                        )
                        for key in ("updated_at", "last_active_at", "created_at"):
                            t = _parse_iso8601(summary.get(key))
                            if t is not None:
                                ts = t
                                break
                except (OSError, ValueError, TypeError) as _exc:
                    pass
            signals_path = sess / "signals.json"
            if signals_path.is_file():
                try:
                    signals = json.loads(signals_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(signals, dict):
                        model = str(signals.get("primaryModelId") or model)
                        models_used = signals.get("modelsUsed")
                        if isinstance(models_used, list) and models_used and model == "grok":
                            model = str(models_used[0])
                except (OSError, ValueError, TypeError) as _exc:
                    pass

            peak = 0
            updates_path = sess / "updates.jsonl"
            if updates_path.is_file():
                try:
                    with updates_path.open(encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if "totalTokens" not in line:
                                continue
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            meta = (obj.get("params") or {}).get("_meta") or {}
                            if isinstance(meta, dict) and "totalTokens" in meta:
                                peak = max(peak, _int_field(meta.get("totalTokens")))
                except OSError:
                    pass

            if peak <= 0:
                continue
            rows.append(
                UsageRow(
                    ts=ts,
                    provider=infer_provider(model),
                    model=model,
                    project=project,
                    input_tokens=peak,  # proxy: context peak as total mass
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                )
            )
    return rows


def _collect_multi(base: Path) -> list[UsageRow]:
    """Unused path placeholder — multi is handled in collect()."""
    return []


# Sole public adapter map. Populated by refresh_usage_adapters() from
# providers/* (usage_source + usage_collect). Local _collect_* functions
# are FALLBACK ONLY when the registry is unavailable. Keys "claude-code"
# and "grok" stay stable for relay.toml / multi|auto.
ADAPTERS: dict[str, Callable[[Path], list[UsageRow]]] = {}

# Historical source names that get a local fallback if the providers
# package cannot be imported. New harnesses must NOT be added here —
# register Provider.usage_collect instead.
_FALLBACK_COLLECTORS: dict[str, Callable[[Path], list[UsageRow]]] = {
    "claude-code": _collect_claude_code,
    "grok": _collect_grok,
}


def refresh_usage_adapters() -> None:
    """Populate ADAPTERS solely from the providers registry, then fallbacks.

    For each registered Provider with both ``usage_source`` and
    ``usage_collect``, install that collector under ADAPTERS[usage_source].
    Registry always wins for a given source name.

    Local ``_FALLBACK_COLLECTORS`` fill only sources still missing after the
    registry pass (typically when ``import providers`` fails entirely).
    Prefer zero hard-coded registration when providers load successfully.

    Safe to call again after dynamic Provider.register(...) so a new
    harness's collector appears in collect() without restarting the process.
    """
    ADAPTERS.clear()
    try:
        repo = Path(__file__).resolve().parents[1]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import providers  # noqa: F401
        from providers.base import list_providers

        for p in list_providers():
            if p.usage_source and p.usage_collect:
                # Registry wins — sole registration path when importable.
                ADAPTERS[p.usage_source] = p.usage_collect  # type: ignore[assignment]
    except Exception:
        # providers unavailable: fall through to local fallbacks only.
        pass

    # FALLBACK ONLY: historical sources if registry did not supply them.
    for name, collector in _FALLBACK_COLLECTORS.items():
        if name not in ADAPTERS:
            ADAPTERS[name] = collector


# Backward-compat private name used by older tests / callers.
_register_provider_usage_adapters = refresh_usage_adapters

refresh_usage_adapters()


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


def aggregate(rows: list[UsageRow], window_start: int, window_end: int, window_label: str) -> dict:
    """Compute the full usage summary from an already-window-filtered row
    list. Pure function - no I/O, fully unit-testable with a synthetic row
    list (mirrors metrics_agg.aggregate's pure/testable shape)."""
    totals = _empty_totals()
    by_provider: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_project: dict[str, dict[str, int]] = {}
    bucket_size = _bucket_size_seconds(window_start, window_end)
    buckets: dict[int, int] = {}
    buckets_by_provider: dict[int, dict[str, int]] = {}
    buckets_by_model: dict[int, dict[str, int]] = {}

    by_source: dict[str, dict[str, int]] = {}

    for r in rows:
        _add_row(totals, r)
        _add_row(by_provider.setdefault(r.provider, _empty_totals()), r)
        _add_row(by_model.setdefault(r.model, _empty_totals()), r)
        _add_row(by_project.setdefault(r.project, _empty_totals()), r)
        if r.adapter:
            _add_row(by_source.setdefault(r.adapter, _empty_totals()), r)
        b = r.ts - (r.ts % bucket_size)
        tok = row_total_tokens(r)
        buckets[b] = buckets.get(b, 0) + tok
        bp = buckets_by_provider.setdefault(b, {})
        bp[r.provider] = bp.get(r.provider, 0) + tok
        bm = buckets_by_model.setdefault(b, {})
        bm[r.model] = bm.get(r.model, 0) + tok

    return {
        "generated_at": int(time.time()),
        "window": window_label,
        "window_start": window_start,
        "window_end": window_end,
        "bucket_size": bucket_size,
        "total_events": len(rows),
        "totals": totals,
        "by_provider": by_provider,
        "by_source": by_source,
        "by_model": by_model,
        "by_project": by_project,
        "timeline": sorted(buckets.items()),
        "timeline_by_provider": sorted(buckets_by_provider.items()),
        "timeline_by_model": sorted(buckets_by_model.items()),
    }


def _default_dir_for_source(source: str) -> str:
    home = str(Path.home())
    if source == "grok":
        return f"{home}/.grok/sessions"
    return f"{home}/.claude/projects"


def collect(
    source: str,
    projects_dir: str,
    window: str,
    now: int | None = None,
    allotments: dict[str, dict[str, int | None]] | None = None,
) -> dict:
    """The top-level, skip-graceful entry point: resolve the window,
    dispatch to the configured source adapter, filter, and aggregate.
    ALWAYS returns a valid summary dict (see aggregate()'s shape) plus
    `source`/`projects_dir`/`sources_scanned`, and a `skipped` string
    whenever the source adapter is unknown, the projects_dir is missing,
    or collection raised - the aggregate is then honestly empty (never a
    fabricated number), never a raised exception.

    Special sources:
      multi / auto — merge claude-code + grok from their default dirs
        (projects_dir is ignored; each adapter uses its own default).
    """
    window_start, window_end, label = resolve_window(window, now)
    rows: list[UsageRow] = []
    skipped_reason: str | None = None
    notes: list[str] = []

    sources: list[tuple[str, str]]
    if source in ("multi", "auto"):
        sources = [
            ("claude-code", _default_dir_for_source("claude-code")),
            ("grok", _default_dir_for_source("grok")),
        ]
    else:
        sources = [(source, projects_dir)]

    for src_name, src_dir in sources:
        adapter = ADAPTERS.get(src_name)
        if adapter is None:
            notes.append(f"unknown usage source adapter: {src_name!r}")
            continue
        base = Path(src_dir).expanduser()
        if not base.is_dir():
            notes.append(f"{src_name}: projects_dir not found: {base}")
            continue
        try:
            part = adapter(base)
            for r in part:
                rows.append(r._replace(adapter=src_name))
            if src_name == "grok" and part:
                notes.append("grok: token_basis=context_peak_proxy (not API billing)")
        except Exception as e:
            notes.append(f"{src_name}: collection error: {e.__class__.__name__}")

    if not rows and notes:
        skipped_reason = "; ".join(notes)

    filtered = filter_rows(rows, window_start, window_end)
    agg = aggregate(filtered, window_start, window_end, label)
    if allotments:
        agg["periods"] = allotment_usage_snapshot(rows, allotments, now)
    agg["source"] = source
    agg["projects_dir"] = str(projects_dir)
    agg["sources_scanned"] = len(rows)
    if skipped_reason:
        agg["skipped"] = skipped_reason
    elif notes:
        agg["notes"] = notes
    return agg


def _compact_tokens(n: object) -> str:
    try:
        v = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}k"
    return str(v)


def render_telegram_usage_text(
    usage_agg: dict,
    *,
    show_providers: bool = True,
    show_models: bool = True,
) -> str:
    """Rich text for `/usage` (providers, harness sources, optional quota lines)."""
    if not usage_agg:
        return (
            "📈 Token usage — unavailable\n"
            "(usage tracking is disabled, or no source is configured — see relay.toml [usage])"
        )
    totals = usage_agg.get("totals", {})
    win = usage_agg.get("window", "?")
    lines = [
        f"📈 Token usage — {win}",
        "═" * 32,
        "",
        f"total tokens:   {_compact_tokens(totals.get('total_tokens', 0))}",
        f"  input:        {_compact_tokens(totals.get('input_tokens', 0))}",
        f"  output:       {_compact_tokens(totals.get('output_tokens', 0))}",
        f"  cache read:   {_compact_tokens(totals.get('cache_read_tokens', 0))}",
        f"  cache create: {_compact_tokens(totals.get('cache_creation_tokens', 0))}",
        "",
    ]

    by_source = usage_agg.get("by_source") or {}
    if by_source:
        lines.append("By harness (source):")
        for src in sorted(by_source.keys()):
            t = by_source[src].get("total_tokens", 0)
            lines.append(f"  {src}: {_compact_tokens(t)}")
        lines.append("")

    if show_providers:
        by_prov = usage_agg.get("by_provider") or {}
        if by_prov:
            lines.append("By provider:")
            for p in sorted(by_prov.keys(), key=lambda k: -by_prov[k].get("total_tokens", 0)):
                lines.append(f"  {p}: {_compact_tokens(by_prov[p].get('total_tokens', 0))}")
            lines.append("")
        else:
            lines += ["By provider:", "  (no data in this window)", ""]
    else:
        lines += ["By provider:", "  (display disabled — [usage].providers = false)", ""]

    if show_models:
        by_mod = usage_agg.get("by_model") or {}
        if by_mod:
            lines.append("By model (top):")
            top = sorted(
                by_mod.items(), key=lambda kv: -kv[1].get("total_tokens", 0)
            )[:8]
            for m, d in top:
                lines.append(f"  {display_model(m)}: {_compact_tokens(d.get('total_tokens', 0))}")
            lines.append("")
    else:
        lines += ["By model:", "  (display disabled — [usage].models = false)", ""]

    periods = usage_agg.get("periods") or {}
    if periods:
        lines.append("Quotas (configured periods):")
        for subject in sorted(periods.keys()):
            for period in ("daily", "weekly", "monthly"):
                slot = periods[subject].get(period)
                if not slot:
                    continue
                used = int(slot.get("used", 0))
                cap = int(slot.get("cap", 0))
                pct = slot.get("percent")
                bar = quota_progress_bar(float(pct) if pct is not None else None)
                pct_s = f" {pct}%" if pct is not None else ""
                lines.append(
                    f"  {subject} {period}: {_compact_tokens(used)} / {_compact_tokens(cap)}"
                    f"{pct_s} {bar}".rstrip()
                )
        lines.append("")

    if usage_agg.get("skipped"):
        lines.append(f"note: {usage_agg['skipped']}")
    elif usage_agg.get("notes"):
        for note in usage_agg["notes"]:
            lines.append(f"note: {note}")
    elif usage_agg.get("total_events", 0) == 0:
        lines.append("(no usage data recorded yet in this window)")

    return "\n".join(lines)


def _load_allotments_arg(path: str) -> dict[str, dict[str, int | None]]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return parse_allotments(raw)


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--telegram-text":
        if len(argv) < 3:
            print("usage: usage_ingest.py --telegram-text <usage_json_path>", file=sys.stderr)
            return 2
        cache_path = argv[2]
        show_providers = "--no-providers" not in argv
        show_models = "--no-models" not in argv
        try:
            with open(cache_path, encoding="utf-8") as f:
                agg = json.load(f)
        except (OSError, ValueError, TypeError):
            agg = {}
        print(render_telegram_usage_text(agg, show_providers=show_providers, show_models=show_models))
        return 0

    args = [a for a in argv[1:] if a not in ("--no-providers", "--no-models")]
    allotments: dict[str, dict[str, int | None]] | None = None
    if "--allotments" in args:
        idx = args.index("--allotments")
        if idx + 1 >= len(args):
            print("usage: ... --allotments <allotments.json>", file=sys.stderr)
            return 2
        allotments = _load_allotments_arg(args[idx + 1])
        del args[idx : idx + 2]

    if len(args) < 4:
        print(
            "usage: usage_ingest.py <source> <projects_dir> <window> <out_json_path> "
            "[--allotments <allotments.json>]",
            file=sys.stderr,
        )
        return 2
    source, projects_dir, window, out_path = args[0], args[1], args[2], args[3]

    agg = collect(source, projects_dir, window, allotments=allotments)

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
