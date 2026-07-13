"""CLI entry points — stubs until full send/poll ports land (#26/#27)."""

from __future__ import annotations

import argparse
import json
import sys

from tg_agent_relay import __version__


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tg-relay", description="TG Agent Relay")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("version")
    h = sub.add_parser("hook", help="Format a provider hook payload from stdin")
    h.add_argument("provider", help="Provider id: grok | claude | …")
    h.add_argument("--config-json", default="", help="Optional config overrides JSON path")
    r = sub.add_parser("route", help="Resolve a route (JSON config path)")
    r.add_argument("--config", required=True)
    r.add_argument("--chat-id", default="")
    r.add_argument("--thread-id", default="")
    r.add_argument("--text", default="")
    args = p.parse_args(argv)

    if args.cmd in (None, "version"):
        print(__version__)
        return 0

    if args.cmd == "hook":
        from tg_agent_relay.hooks import dispatch_hook

        cfg = {}
        if args.config_json:
            try:
                cfg = json.loads(Path_read(args.config_json))
            except Exception:
                cfg = {}
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            print("SKIP:invalid json")
            return 0
        status, body = dispatch_hook(args.provider, payload, config=cfg)
        print(f"{status}:{body}")
        return 0

    if args.cmd == "route":
        from tg_agent_relay.config import load_config
        from tg_agent_relay.routing import resolve

        # load from JSON file for agents (pre-converted toml)
        path = args.config
        if path.endswith(".toml"):
            cfg = load_config(path)
        else:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        res = resolve(cfg, args.chat_id, args.thread_id, args.text)
        print(res.as_pipe())
        return 0

    return 2


def Path_read(p: str) -> str:
    from pathlib import Path

    return Path(p).read_text(encoding="utf-8")


def main_send(argv: list[str] | None = None) -> int:
    print("tg-relay-send: not yet ported — use tg-send.sh (issue #26)", file=sys.stderr)
    return 2


def main_poll(argv: list[str] | None = None) -> int:
    from tg_agent_relay.poll import main as poll_main

    return poll_main(argv)


def main_hook(argv: list[str] | None = None) -> int:
    # default to grok if only args after --
    return main(["hook", *(argv or sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
