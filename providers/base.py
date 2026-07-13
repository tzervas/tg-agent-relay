"""Base types for platform/provider extensions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class HookEvent:
    """One harness hook event this provider understands."""

    name: str
    default_enabled: bool = False
    default_prefix: str = "ℹ️"
    description: str = ""
    # Placeholders available in [provider.Event].format templates
    placeholders: tuple[str, ...] = ("prefix", "event")


class UsageCollector(Protocol):
    def __call__(self, base: Path) -> list[Any]:
        """Return list of usage rows (compatible with usage_ingest.UsageRow)."""
        ...


@dataclass
class Provider:
    """A platform/harness extension (Grok, Claude Code, Ollama, …)."""

    id: str
    display_name: str
    # Config namespace in relay.toml: [grok.Stop], [claude_code.Stop], …
    config_namespace: str
    # Backend id used by multi-backend routing (RELAY_BACKEND)
    backend_id: str
    hook_events: list[HookEvent] = field(default_factory=list)
    # Normalize raw event name from payload/env → canonical HookEvent.name
    normalize_event: Callable[[str], str] | None = None
    # Build summary line from payload dict + resolved event + config overrides
    format_hook: Callable[[dict[str, Any], str, dict[str, str]], str] | None = None
    # Usage adapter name registered in usage_ingest (e.g. "grok")
    usage_source: str | None = None
    usage_default_dir: str | None = None  # e.g. ~/.grok/sessions
    usage_collect: UsageCollector | None = None
    # Model id prefixes this provider claims for infer_provider
    model_prefixes: tuple[str, ...] = ()
    provider_label: str = "other"  # anthropic / xai / ollama / …

    def event_by_name(self, name: str) -> HookEvent | None:
        for e in self.hook_events:
            if e.name == name:
                return e
        return None

    def default_enabled(self, event: str) -> bool:
        e = self.event_by_name(event)
        return e.default_enabled if e else True

    def default_prefix(self, event: str) -> str:
        e = self.event_by_name(event)
        return e.default_prefix if e else "ℹ️"


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    _REGISTRY[provider.id] = provider
    return provider


def get_provider(provider_id: str) -> Provider | None:
    return _REGISTRY.get(provider_id)


def list_providers() -> list[Provider]:
    return list(_REGISTRY.values())


def providers_by_usage_source() -> dict[str, Provider]:
    return {p.usage_source: p for p in _REGISTRY.values() if p.usage_source}


def infer_provider_label(model_id: str) -> str | None:
    """Return a registered provider_label if a model prefix matches, else None."""
    m = (model_id or "").strip().lower()
    if not m:
        return None
    # Longer prefixes first
    hits: list[tuple[int, str]] = []
    for p in _REGISTRY.values():
        for pref in p.model_prefixes:
            pl = pref.lower()
            if m.startswith(pl):
                hits.append((len(pl), p.provider_label))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]
