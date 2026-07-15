"""Optional Google Agent Development Kit (ADK) bridge.

Python ADK (``google-adk`` on PyPI) is the supported surface. Integration is
**soft**: the relay never requires ADK to import or run. When installed:

* ``probe_adk()`` reports version / availability
* delivery backends can shell out to ``adk run`` (see providers/adk)
* ADK agents can attach this relay as an **MCP tool server**
  (``python -m tg_agent_relay.mcp_stub --stdio``) so tools hit the Bot API
  path without a second Telegram stack

Rust: Google's public ADK is Python/Java-first. This repo pins **MSRV 1.96**
for optional Rust hotspots; a future ``crates/adk_bridge`` can land after a
benchmark need — not required for ADK Python.

Security: ADK runs in *your* process/env. The relay still enforces
``BOT_TOKEN`` + allowlist for Telegram. Do not expose ADK web UIs on public
networks with live ``dry_run=false`` MCP tools without auth.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Optional dependency name on PyPI
ADK_DIST_NAMES = ("google-adk", "google_adk")


@dataclass(frozen=True)
class AdkProbe:
    available: bool
    version: str = ""
    import_error: str = ""
    cli_path: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "version": self.version,
            "import_error": self.import_error,
            "cli_path": self.cli_path,
            "notes": self.notes,
            "install_hint": 'uv pip install "google-adk"  # optional; not required by relay core',
            "mcp_attach": (
                "Point ADK MCP tool config at: python -m tg_agent_relay.mcp_stub --stdio"
            ),
        }


def probe_adk() -> AdkProbe:
    """Detect whether Google ADK Python is importable (no hard dependency)."""
    version = ""
    for dist in ADK_DIST_NAMES:
        try:
            version = importlib.metadata.version(dist)
            break
        except importlib.metadata.PackageNotFoundError:
            continue

    import_error = ""
    available = False
    # Prefer google.adk namespace used by the official package
    for mod in ("google.adk", "google_adk"):
        try:
            if importlib.util.find_spec(mod) is not None:
                available = True
                break
        except (ModuleNotFoundError, ValueError) as _exc:
            import_error = str(_exc)

    if not available and not import_error:
        import_error = "google.adk not installed (optional)"

    cli = shutil.which("adk") or ""
    notes = (
        "ADK is optional. Relay core never imports it at startup. "
        "Use providers/adk delivery preset or MCP attach for tool use."
    )
    if available and not version:
        version = "unknown"
    return AdkProbe(
        available=available or bool(version),
        version=version,
        import_error="" if (available or version) else import_error,
        cli_path=cli,
        notes=notes,
    )


def adk_mcp_config_snippet(*, python: str = "python3") -> dict[str, Any]:
    """JSON snippet for wiring this relay as an MCP server for ADK / clients."""
    return {
        "mcpServers": {
            "tg-agent-relay": {
                "command": python,
                "args": ["-m", "tg_agent_relay.mcp_stub", "--stdio"],
                "env": {
                    # Inherit BOT_TOKEN from the host env or set explicitly
                    "RELAY_MCP_DRY_RUN": "true"
                },
            }
        },
        "note": (
            "Set RELAY_MCP_DRY_RUN=false only on trusted hosts with BOT_TOKEN. "
            "Inbound allowlist remains tg-poll; this only enables outbound tools."
        ),
    }


def try_import_adk_agents() -> tuple[bool, str]:
    """Best-effort import of google.adk.agents for diagnostics."""
    try:
        import google.adk  # type: ignore

        return True, getattr(google.adk, "__version__", probe_adk().version or "ok")
    except Exception as _exc:
        return False, f"{type(_exc).__name__}: {_exc}"


def run_adk_cli(
    args: list[str], *, cwd: str | Path | None = None, timeout: float = 120.0
) -> dict[str, Any]:
    """Optional helper: invoke ``adk`` CLI if on PATH (never required)."""
    cli = shutil.which("adk")
    if not cli:
        return {"ok": False, "error": "adk CLI not on PATH (pip install google-adk)"}
    try:
        proc = subprocess.run(
            [cli, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as _exc:
        return {"ok": False, "error": f"{type(_exc).__name__}: {_exc}"}


def register_adk_extensions() -> list[str]:
    """Register ADK-related extension tools on the relay bus (no model required for probe)."""
    from tg_agent_relay.extensions import (
        SRC_ADK,
        ExtensionTool,
        register_extension,
    )

    def _probe(_args: dict[str, Any]) -> dict[str, Any]:
        p = probe_adk()
        d = p.as_dict()
        d["ok"] = True
        return d

    def _mcp_snippet(args: dict[str, Any]) -> dict[str, Any]:
        py = str(args.get("python") or "python3")
        return {"ok": True, "config": adk_mcp_config_snippet(python=py)}

    register_extension(
        ExtensionTool(
            name="relay_adk_probe",
            description="Probe optional Google ADK (google-adk) install; never requires it.",
            handler=_probe,
            input_schema={"type": "object", "properties": {}},
            source=SRC_ADK,
        )
    )
    register_extension(
        ExtensionTool(
            name="relay_adk_mcp_config",
            description="Emit MCP server config JSON so ADK/clients can attach this relay as tools.",
            handler=_mcp_snippet,
            input_schema={
                "type": "object",
                "properties": {
                    "python": {"type": "string", "default": "python3"},
                },
            },
            source=SRC_ADK,
        )
    )
    return ["relay_adk_probe", "relay_adk_mcp_config"]
