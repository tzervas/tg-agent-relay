#!/bin/bash
# Install gha-runner-ctl (the controller). This is NOT GitHub's config.sh.
#
#   config.sh  = inside the official runner package; registers the runner with GitHub
#   gha-runner-ctl = our tool; builds/starts the Podman snapshot and calls registration for you
#
# Usage:
#   bash scripts/self-hosted-runner/install-ctl.sh
#   bash scripts/self-hosted-runner/install-ctl.sh --prefix ~/.local
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${HOME}/.local"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) PREFIX="${2:-}"; shift 2 ;;
        -h | --help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

command -v cargo >/dev/null || {
    printf 'install-ctl.sh: cargo/rust required (rustup + MSRV 1.96)\n' >&2
    exit 1
}
command -v podman >/dev/null || {
    printf 'install-ctl.sh: podman required\n' >&2
    exit 1
}

printf 'Building gha-runner-ctl (release)…\n'
(cd "$ROOT" && cargo build -p gha_runner_ctl --release)

BIN_DIR="${PREFIX}/bin"
mkdir -p "$BIN_DIR"
install -m 755 "$ROOT/target/release/gha-runner-ctl" "$BIN_DIR/gha-runner-ctl"
printf 'Installed: %s/gha-runner-ctl\n' "$BIN_DIR"

if ! command -v gha-runner-ctl >/dev/null 2>&1; then
    printf '\nAdd to PATH (current shell):\n  export PATH="%s:\$PATH"\n' "$BIN_DIR"
    printf 'Or append to ~/.bashrc:\n  echo '\''export PATH="%s:$PATH"'\'' >> ~/.bashrc\n' "$BIN_DIR"
else
    printf 'On PATH: %s\n' "$(command -v gha-runner-ctl)"
fi

printf '\nNext:\n'
printf '  gha-runner-ctl prepare   # build Podman image + seed volume (once)\n'
printf '  gha-runner-ctl up        # register + start the one runner\n'
printf '  gha-runner-ctl status\n'
printf '  gha-runner-ctl listen    # optional: auto up/down on demand\n'
printf '\nDo not run scripts/actions-runner/config.sh by hand unless debugging.\n'
