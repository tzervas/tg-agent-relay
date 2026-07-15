#!/bin/bash
# adapters/backend-fifo-reader.sh - Read a backend's inbound FIFO and print
# lines to stdout (for use as a Monitor / agent event source).
#
# Usage:
#   backend-fifo-reader.sh /path/to/in.fifo
#   backend-fifo-reader.sh   # uses $RELAY_FIFO or dies with usage
#
# Each line is already tagged by tg-poll.sh, e.g.:
#   [telegram:backend:grok:project:mycelium] review the parser
set -u

FIFO="${1:-${RELAY_FIFO:-}}"
if [[ -z "$FIFO" ]]; then
    printf 'usage: backend-fifo-reader.sh <fifo-path>\n' >&2
    exit 2
fi
FIFO="${FIFO/#\~/$HOME}"

if [[ ! -p "$FIFO" ]]; then
    mkdir -p "$(dirname "$FIFO")" 2>/dev/null || true
    mkfifo "$FIFO" 2>/dev/null || true
fi

# Re-open the fifo forever so writer-side closes don't end the reader.
while true; do
    # shellcheck disable=SC2094
    while IFS= read -r line; do
        printf '%s\n' "$line"
    done < "$FIFO"
done
