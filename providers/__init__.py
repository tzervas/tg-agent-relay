"""Provider/consumer extension registry for TG Agent Relay.

Each platform (Grok Build, Claude Code, Ollama, …) registers a Provider
that can supply:
  - hook event catalog + payload → summary formatting
  - usage transcript collection
  - optional model-id → display/provider inference hints

Shell adapters remain thin entry points that call into this package.
"""

from __future__ import annotations

# Import concrete providers for side-effect registration.
from providers import claude as _claude  # noqa: F401
from providers import generic as _generic  # noqa: F401
from providers import grok as _grok  # noqa: F401
from providers import ollama as _ollama  # noqa: F401
from providers.base import HookEvent, Provider, get_provider, list_providers, register

__all__ = [
    "HookEvent",
    "Provider",
    "get_provider",
    "list_providers",
    "register",
]
