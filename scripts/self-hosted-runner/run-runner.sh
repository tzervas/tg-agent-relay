#!/bin/bash
# Thin wrapper → gha-runner-ctl (one runner, snapshot, auto-register).
# Prefer: cargo build -p gha_runner_ctl --release
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTL=""
for c in "$ROOT/target/release/gha-runner-ctl" "$ROOT/target/debug/gha-runner-ctl"; do
    if [[ -x "$c" ]]; then
        CTL="$c"
        break
    fi
done

if [[ -z "$CTL" ]]; then
    if command -v cargo >/dev/null 2>&1; then
        printf 'run-runner.sh: building gha-runner-ctl …\n' >&2
        (cd "$ROOT" && cargo build -p gha_runner_ctl --release)
        CTL="$ROOT/target/release/gha-runner-ctl"
    else
        printf 'run-runner.sh: gha-runner-ctl not found; install rust and build:\n' >&2
        printf '  cargo build -p gha_runner_ctl --release\n' >&2
        exit 1
    fi
fi

CMD="${1:-start}"
shift || true

case "$CMD" in
    start | --start | up) exec "$CTL" up "$@" ;;
    stop | --stop | down) exec "$CTL" down "$@" ;;
    prepare | --prepare | build | --build) exec "$CTL" prepare "$@" ;;
    listen | --listen) exec "$CTL" listen "$@" ;;
    logs | --logs)
        exec podman logs -f "${GHA_CONTAINER:-gha-runner-tg-agent-relay}"
        ;;
    status | --status) exec "$CTL" status "$@" ;;
    -h | --help) exec "$CTL" --help ;;
    *)
        printf 'usage: run-runner.sh {prepare|start|stop|listen|status|logs}\n' >&2
        exit 2
        ;;
esac
