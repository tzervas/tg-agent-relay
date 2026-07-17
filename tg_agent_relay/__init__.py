"""TG Agent Relay — Python package (Python 3.14 preferred).

This package is the migration target for shell runtime. Existing `lib/*.py`
and `providers/` remain the live implementation; modules here re-export or
wrap them behind stable interfaces for swarm agents to extend.

Stable public surface for agents:
  - tg_agent_relay.config   load relay.toml + overlays
  - tg_agent_relay.routing  project/backend route resolve
  - tg_agent_relay.metrics  emit_metric TSV
  - tg_agent_relay.tts      strip_formatting + spoken_mode short/full chunk
  - tg_agent_relay.hooks    provider_hook dispatch
  - tg_agent_relay.protocols Protocol types for send/format/usage

Shell scripts call into this package via `lib/python.sh` / entry points.
"""

from __future__ import annotations

__version__ = "0.10.0"
