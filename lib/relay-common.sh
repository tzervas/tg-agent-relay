#!/bin/bash
# lib/relay-common.sh - Shared helpers used by every script (tg-send.sh,
# tg-poll.sh, relay-notify.sh, adapters/). Sourced, never executed directly
# (no shebang execution path).
#
# Extracted from the original hook-notify.sh (which had these inlined) so
# the generic entry point and every adapter share ONE implementation
# instead of copy-pasting it - fixing a bug here fixes it everywhere.
set -u

# Collapse a field's whitespace/newlines into a single-line, readable
# summary and trim leading/trailing space.
oneline() {
    local text="$1"
    text="$(printf '%s' "$text" | tr '\n\r\t' ' ' | tr -s ' ')"
    text="${text#"${text%%[![:space:]]*}"}"
    text="${text%"${text##*[![:space:]]}"}"
    printf '%s' "$text"
}

# cap_if_huge <text> <max_chars> <page_size>
#
# Bound only an extreme outlier (many pages' worth of tg-send.sh's own
# pagination). Never a silent/mid-word cut: truncates at a char boundary
# and appends a marker stating how many pages were dropped, rather than
# fabricating a shortened message with no sign anything is missing.
cap_if_huge() {
    local text="$1" max_chars="$2" page_size="$3"
    if (( ${#text} <= max_chars )); then
        printf '%s' "$text"
        return
    fi
    local kept="${text:0:max_chars}"
    local omitted_chars=$(( ${#text} - max_chars ))
    local omitted_pages=$(( (omitted_chars + page_size - 1) / page_size ))
    printf '%s\n\n[+%s more pages omitted]' "$kept" "$omitted_pages"
}

# render_template <template> [<name1> <value1> [<name2> <value2> ...]]
#
# Minimal `{name}` interpolation shared by adapters/claude-code.sh (per-event
# `[claude_code.<Event>].format`) and relay-notify.sh (`[generic].format`) -
# ONE implementation so both config surfaces behave identically. Every
# "{name}" literal in <template> whose <name> was passed in is replaced with
# its <value>; a "{name}" that was NOT passed in is left LITERAL in the
# output. This is deliberate, not a bug: a typo'd placeholder in a
# hand-written relay.toml `format` string stays visibly wrong on the phone
# instead of silently rendering as an empty gap - the same
# never-silent-failure posture as the rest of this repo (see
# lib/relay-common.sh's other helpers).
#
# Example: render_template '{prefix} {agent} finished' prefix "✅" agent "build"
#   -> "✅ build finished"
render_template() {
    local tmpl="$1"
    shift
    while [[ $# -ge 2 ]]; do
        tmpl="${tmpl//\{$1\}/$2}"
        shift 2
    done
    printf '%s' "$tmpl"
}

# emit_metric <source> <event> [detail]
#
# Extension SEAM for a later metrics dashboard - not built here, see
# ROADMAP.md ("Next"). Appends one TSV line
# (epoch\tsource\tevent\tdetail) to the metrics log and returns
# immediately. NEVER blocks or fails its caller: a metrics write that
# can't happen (missing dir, disk full, log path unwritable, whatever) is
# silently skipped - exactly like this repo's other "never disrupt the
# thing being observed" guarantees (see adapters/claude-code.sh's header).
# Every caller already has $BRIDGE_DIR set before sourcing this file; the
# log path is overridable via $RELAY_METRICS_LOG (e.g. to point multiple
# bridge instances at one shared log, or /dev/null to fully disable).
emit_metric() {
    local source="$1" event="$2" detail="${3:-}"
    local log="${RELAY_METRICS_LOG:-${BRIDGE_DIR:-.}/.metrics.log}"
    # The whole append is grouped under one redirect so a failure to even
    # OPEN $log (missing dir, read-only fs, ...) is swallowed too - not
    # just a failure of the printf itself.
    { printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$source" "$event" "$detail" >> "$log"; } 2>/dev/null || true
}
