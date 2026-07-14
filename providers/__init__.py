"""Provider/consumer extension registry for TG Agent Relay.

Each platform (Grok Build, Claude Code, OpenAI/ChatGPT, Ollama, Gemini, …)
registers a Provider that can supply:

  - hook event catalog + payload → summary formatting (harness platforms)
  - usage transcript collection
  - multi-backend delivery presets (cmd / fifo / stdout)
  - model-id → display/provider inference hints

**Plug-and-play:** add ``providers/<id>/__init__.py`` that calls
``register(Provider(...))``. Discovery imports every subpackage automatically.

Shell adapters remain thin entry points that call into this package.
"""

from __future__ import annotations

# Eager known packages (stable import order for tests) + full discovery.
from providers import adk as _adk  # noqa: F401
from providers import aider as _aider  # noqa: F401
from providers import claude as _claude  # noqa: F401
from providers import gemini as _gemini  # noqa: F401
from providers import generic as _generic  # noqa: F401
from providers import grok as _grok  # noqa: F401
from providers import ollama as _ollama  # noqa: F401
from providers import openai as _openai  # noqa: F401
from providers.base import (
    CAP_DELIVERY,
    CAP_HOOKS,
    CAP_OPENAI_COMPAT,
    CAP_USAGE,
    DeliveryPreset,
    HookEvent,
    Provider,
    discover_and_import,
    get_provider,
    infer_provider_label,
    list_providers,
    provider_for_backend_type,
    providers_by_usage_source,
    providers_with_capability,
    register,
)

# Auto-import any additional providers/<name>/ dropped in by users or plugins.
discover_and_import()

__all__ = [
    "CAP_DELIVERY",
    "CAP_HOOKS",
    "CAP_OPENAI_COMPAT",
    "CAP_USAGE",
    "DeliveryPreset",
    "HookEvent",
    "Provider",
    "discover_and_import",
    "get_provider",
    "infer_provider_label",
    "list_providers",
    "provider_for_backend_type",
    "providers_by_usage_source",
    "providers_with_capability",
    "register",
]
