#!/usr/bin/env python3
"""toml_to_json.py - Convert a relay.toml file to JSON on stdout.

Used by lib/relay-config.sh via `python3` (stdlib `tomllib`, Python 3.11+;
no third-party dependency, matching this repo's zero-framework style). This
is the ONLY place TOML parsing happens - every shell script queries the
resulting JSON with `jq`, which is already a dependency of the whole repo.

Never-silent-but-never-fatal: any failure (missing file, unparsable TOML,
too-old Python without tomllib) prints "{}" and exits 0, so a caller that
forgot to check the exit code still gets a safely-empty config rather than
a script-with-a-typo'd relay.toml wedging the whole bridge. The caller
(cfg_get in relay-config.sh) then falls through to ITS OWN default, which
is how every script stays behavior-identical to before relay.toml existed.
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("{}")
        return 0

    path = sys.argv[1]

    try:
        import tomllib
    except ImportError:
        # Python < 3.11: no stdlib TOML parser available. Never crash the
        # caller over this - config-fallback (env vars / hardcoded
        # defaults) is the documented behavior with no relay.toml support.
        print("{}", file=sys.stdout)
        return 0

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        print(json.dumps(data))
    except (OSError, tomllib.TOMLDecodeError):
        print("{}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
