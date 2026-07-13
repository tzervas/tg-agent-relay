#!/usr/bin/env python3
"""Emit provider catalogs for installers / docs / shell.

provider_catalog.py list
provider_catalog.py events grok
provider_catalog.py events claude
provider_catalog.py events grok --enabled-only   # default-on events
provider_catalog.py events claude --names-only   # install-hooks.sh
provider_catalog.py usage-sources
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import providers  # noqa: F401
    from providers.base import get_provider, list_providers

    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("events")
    e.add_argument("provider_id")
    e.add_argument("--enabled-only", action="store_true")
    e.add_argument("--names-only", action="store_true")
    sub.add_parser("usage-sources")
    args = p.parse_args(argv)

    if args.cmd == "list":
        rows = [
            {
                "id": pr.id,
                "display_name": pr.display_name,
                "config_namespace": pr.config_namespace,
                "backend_id": pr.backend_id,
                "hook_events": len(pr.hook_events),
                "usage_source": pr.usage_source,
                "provider_label": pr.provider_label,
            }
            for pr in list_providers()
        ]
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "events":
        pr = get_provider(args.provider_id)
        if not pr:
            print(f"unknown provider: {args.provider_id}", file=sys.stderr)
            return 1
        events = pr.hook_events
        if args.enabled_only:
            events = [e for e in events if e.default_enabled]
        if args.names_only:
            for e in events:
                print(e.name)
            return 0
        print(
            json.dumps(
                [
                    {
                        "name": e.name,
                        "default_enabled": e.default_enabled,
                        "default_prefix": e.default_prefix,
                        "description": e.description,
                        "placeholders": list(e.placeholders),
                    }
                    for e in events
                ],
                indent=2,
            )
        )
        return 0

    if args.cmd == "usage-sources":
        rows = [
            {
                "usage_source": pr.usage_source,
                "provider_id": pr.id,
                "default_dir": pr.usage_default_dir,
            }
            for pr in list_providers()
            if pr.usage_source
        ]
        print(json.dumps(rows, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
