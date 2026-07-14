#!/bin/bash
# Build and run a Podman self-hosted GitHub Actions runner for this repo.
#
# Allocates a reasonable slice of the workstation (default 5 CPUs, 8GiB RAM)
# so Actions jobs are fast without starving the host.
#
# Usage:
#   # One-time: create a registration token (expires ~1h)
#   export RUNNER_TOKEN="$(gh api -X POST repos/tzervas/tg-agent-relay/actions/runners/registration-token --jq .token)"
#
#   bash scripts/self-hosted-runner/run-runner.sh          # build + start
#   bash scripts/self-hosted-runner/run-runner.sh --stop
#   bash scripts/self-hosted-runner/run-runner.sh --logs
#   bash scripts/self-hosted-runner/run-runner.sh --status
#
# Env overrides:
#   REPO_URL          default https://github.com/tzervas/tg-agent-relay
#   RUNNER_NAME       default tg-agent-relay-podman
#   RUNNER_CPUS       default 5
#   RUNNER_MEMORY     default 8g
#   RUNNER_LABELS     default self-hosted,linux,x64,podman
#   CONTAINER_NAME    default gha-runner-tg-agent-relay
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

REPO_URL="${REPO_URL:-https://github.com/tzervas/tg-agent-relay}"
RUNNER_NAME="${RUNNER_NAME:-tg-agent-relay-podman}"
RUNNER_CPUS="${RUNNER_CPUS:-5}"
RUNNER_MEMORY="${RUNNER_MEMORY:-8g}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,x64,podman}"
CONTAINER_NAME="${CONTAINER_NAME:-gha-runner-tg-agent-relay}"
IMAGE_NAME="${IMAGE_NAME:-localhost/tg-agent-relay-gha-runner:latest}"
VOLUME_NAME="${VOLUME_NAME:-tg-agent-relay-gha-runner-data}"

CMD="${1:-start}"

need_podman() {
    command -v podman >/dev/null 2>&1 || {
        printf 'run-runner.sh: podman is required\n' >&2
        exit 1
    }
}

build_image() {
    printf 'Building %s …\n' "$IMAGE_NAME"
    podman build -t "$IMAGE_NAME" -f Containerfile .
}

start_runner() {
    need_podman
    if [[ -z "${RUNNER_TOKEN:-}" ]]; then
        if command -v gh >/dev/null 2>&1; then
            printf 'Fetching registration token via gh …\n'
            RUNNER_TOKEN="$(gh api -X POST repos/tzervas/tg-agent-relay/actions/runners/registration-token --jq .token)"
            export RUNNER_TOKEN
        else
            printf 'Set RUNNER_TOKEN (registration token) or install gh.\n' >&2
            exit 1
        fi
    fi

    build_image
    podman volume exists "$VOLUME_NAME" 2>/dev/null || podman volume create "$VOLUME_NAME" >/dev/null

    if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
        state="$(podman inspect -f '{{.State.Status}}' "$CONTAINER_NAME")"
        if [[ "$state" == "running" ]]; then
            printf 'Container %s already running\n' "$CONTAINER_NAME"
            podman ps --filter "name=${CONTAINER_NAME}"
            return 0
        fi
        printf 'Removing stopped container %s …\n' "$CONTAINER_NAME"
        podman rm -f "$CONTAINER_NAME" >/dev/null
    fi

    printf 'Starting runner: cpus=%s memory=%s labels=%s\n' "$RUNNER_CPUS" "$RUNNER_MEMORY" "$RUNNER_LABELS"
    # Persist /opt/actions-runner so .runner registration survives container recreate
    # (volume is populated from the image on first use via entrypoint seed).
    podman run -d \
        --name "$CONTAINER_NAME" \
        --replace \
        --cpus "$RUNNER_CPUS" \
        --memory "$RUNNER_MEMORY" \
        --memory-swap "$RUNNER_MEMORY" \
        --pids-limit 4096 \
        --restart unless-stopped \
        -e REPO_URL="$REPO_URL" \
        -e RUNNER_TOKEN="$RUNNER_TOKEN" \
        -e RUNNER_NAME="$RUNNER_NAME" \
        -e RUNNER_LABELS="$RUNNER_LABELS" \
        -v "${VOLUME_NAME}:/opt/actions-runner:Z" \
        "$IMAGE_NAME"

    printf 'Started. Check: podman logs -f %s\n' "$CONTAINER_NAME"
    printf 'GitHub: repo Settings → Actions → Runners (labels: self-hosted,linux,x64,podman)\n'
}

stop_runner() {
    need_podman
    if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
        podman stop "$CONTAINER_NAME" >/dev/null || true
        podman rm -f "$CONTAINER_NAME" >/dev/null || true
        printf 'Stopped and removed %s\n' "$CONTAINER_NAME"
    else
        printf 'No container named %s\n' "$CONTAINER_NAME"
    fi
}

case "$CMD" in
    start | --start | "") start_runner ;;
    stop | --stop) stop_runner ;;
    logs | --logs) podman logs -f "$CONTAINER_NAME" ;;
    status | --status)
        podman ps -a --filter "name=${CONTAINER_NAME}"
        if command -v gh >/dev/null 2>&1; then
            gh api repos/tzervas/tg-agent-relay/actions/runners --jq '.runners[]|{name,status,labels:[.labels[].name]}' 2>/dev/null || true
        fi
        ;;
    build | --build) need_podman; build_image ;;
    -h | --help)
        sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        printf 'unknown command: %s (start|stop|logs|status|build)\n' "$CMD" >&2
        exit 2
        ;;
esac
