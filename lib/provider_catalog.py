#!/usr/bin/env python3
"""Emit provider catalogs for installers / docs / shell.

provider_catalog.py list
provider_catalog.py events grok
provider_catalog.py events claude
provider_catalog.py events grok --enabled-only   # default-on events
provider_catalog.py events claude --names-only   # install-hooks.sh
provider_catalog.py usage-sources
provider_catalog.py presets openai               # delivery presets for a provider
provider_catalog.py backend-type vllm            # resolve type → provider id
provider_catalog.py capabilities
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
    from providers.base import (
        get_provider,
        list_providers,
        provider_for_backend_type,
    )

    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("events")
    e.add_argument("provider_id")
    e.add_argument("--enabled-only", action="store_true")
    e.add_argument("--names-only", action="store_true")
    sub.add_parser("usage-sources")
    sub.add_parser("capabilities")
    pr = sub.add_parser("presets")
    pr.add_argument("provider_id")
    bt = sub.add_parser("backend-type")
    bt.add_argument("type_name", help="e.g. openai-compat, vllm, ollama")
    args = p.parse_args(argv)

    if args.cmd == "list":
        rows = [
            {
                "id": prov.id,
                "display_name": prov.display_name,
                "config_namespace": prov.config_namespace,
                "backend_id": prov.backend_id,
                "hook_events": len(prov.hook_events),
                "usage_source": prov.usage_source,
                "provider_label": prov.provider_label,
                "capabilities": sorted(prov.capabilities),
                "backend_types": list(prov.backend_types),
                "description": prov.description,
            }
            for prov in list_providers()
        ]
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "events":
        prov = get_provider(args.provider_id)
        if not prov:
            print(f"unknown provider: {args.provider_id}", file=sys.stderr)
            return 1
        events = prov.hook_events
        if args.enabled_only:
            events = [ev for ev in events if ev.default_enabled]
        if args.names_only:
            for ev in events:
                print(ev.name)
            return 0
        print(
            json.dumps(
                [
                    {
                        "name": ev.name,
                        "default_enabled": ev.default_enabled,
                        "default_prefix": ev.default_prefix,
                        "description": ev.description,
                        "placeholders": list(ev.placeholders),
                    }
                    for ev in events
                ],
                indent=2,
            )
        )
        return 0

    if args.cmd == "usage-sources":
        rows = [
            {
                "usage_source": prov.usage_source,
                "provider_id": prov.id,
                "default_dir": prov.usage_default_dir,
            }
            for prov in list_providers()
            if prov.usage_source
        ]
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "capabilities":
        rows = {prov.id: sorted(prov.capabilities) for prov in list_providers()}
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "presets":
        prov = get_provider(args.provider_id)
        if not prov:
            print(f"unknown provider: {args.provider_id}", file=sys.stderr)
            return 1
        rows = [
            {
                "backend_type": d.backend_type,
                "delivery": d.delivery,
                "model": d.model,
                "prefixes": list(d.prefixes),
                "tag": d.tag,
                "cmd": list(d.cmd),
                "notes": d.notes,
            }
            for d in prov.delivery_presets
        ]
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "backend-type":
        prov = provider_for_backend_type(args.type_name)
        if not prov:
            print(json.dumps({"type": args.type_name, "provider_id": None}))
            return 1
        print(
            json.dumps(
                {
                    "type": args.type_name,
                    "provider_id": prov.id,
                    "backend_id": prov.backend_id,
                    "capabilities": sorted(prov.capabilities),
                },
                indent=2,
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
