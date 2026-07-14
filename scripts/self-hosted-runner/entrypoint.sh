#!/bin/bash
# Configure (once) and run the GitHub Actions runner.
# When /opt/actions-runner is an empty volume, seed from the image copy.
set -euo pipefail

SEED="${RUNNER_SEED:-/opt/actions-runner-seed}"
HOME_DIR="${RUNNER_HOME:-/opt/actions-runner}"

if [[ ! -x "${HOME_DIR}/run.sh" ]]; then
    if [[ -x "${SEED}/run.sh" ]]; then
        printf 'entrypoint: seeding runner from image …\n'
        # Volume mount may be empty; copy baked runner bits
        cp -a "${SEED}/." "${HOME_DIR}/"
    else
        printf 'entrypoint: runner binaries missing in %s\n' "$HOME_DIR" >&2
        exit 1
    fi
fi

cd "$HOME_DIR"

REPO_URL="${REPO_URL:-}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-tg-agent-relay-podman}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,x64,podman}"
RUNNER_GROUP="${RUNNER_GROUP:-Default}"
WORK_DIR="${RUNNER_WORK_DIR:-_work}"

if [[ -z "$REPO_URL" ]]; then
    printf 'entrypoint: REPO_URL is required\n' >&2
    exit 1
fi

if [[ ! -f .runner ]]; then
    if [[ -z "$RUNNER_TOKEN" ]]; then
        printf 'entrypoint: RUNNER_TOKEN required for first registration\n' >&2
        exit 1
    fi
    ./config.sh --unattended \
        --url "$REPO_URL" \
        --token "$RUNNER_TOKEN" \
        --name "$RUNNER_NAME" \
        --labels "$RUNNER_LABELS" \
        --work "$WORK_DIR" \
        --runnergroup "$RUNNER_GROUP" \
        --replace
fi

./run.sh
