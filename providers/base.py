"""Base types for platform/provider extensions (plug-and-play registry)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Capability flags — consumers filter on these (install, usage, routing).
CAP_HOOKS = "hooks"  # harness lifecycle events → Telegram
CAP_USAGE = "usage"  # local transcript collector
CAP_DELIVERY = "delivery"  # multi-backend Telegram → agent inject
CAP_OPENAI_COMPAT = "openai_compat"  # speaks OpenAI Chat Completions HTTP shape


@dataclass(frozen=True)
class HookEvent:
    """One harness hook event this provider understands."""

    name: str
    default_enabled: bool = False
    default_prefix: str = "ℹ️"
    description: str = ""
    # Placeholders available in [provider.Event].format templates
    placeholders: tuple[str, ...] = ("prefix", "event")


@dataclass(frozen=True)
class DeliveryPreset:
    """Documented default for a [backends.<id>] row (cmd / fifo / stdout).

    Presets are *hints* for humans and scaffold tools — routing still reads
    relay.toml only. Keep cmd arrays free of secrets; use env vars.
    """

    backend_type: str  # value of [backends.x].type
    delivery: str = "cmd"  # stdout | fifo | cmd
    model: str = ""
    # JSON-serializable cmd list template; may include $RELAY_* for shell -lc
    cmd: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    tag: str = ""
    notes: str = ""


class UsageCollector(Protocol):
    def __call__(self, base: Path) -> list[Any]:
        """Return list of usage rows (compatible with usage_ingest.UsageRow)."""
        ...


@dataclass
class Provider:
    """A platform/harness extension (Grok, Claude Code, OpenAI, Ollama, …)."""

    id: str
    display_name: str
    # Config namespace in relay.toml: [grok.Stop], [claude_code.Stop], [openai], …
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
    provider_label: str = "other"  # anthropic / xai / openai / ollama / …
    # Plug-and-play metadata
    capabilities: frozenset[str] = field(default_factory=frozenset)
    # [backends.*.type] values this provider owns
    backend_types: tuple[str, ...] = ()
    delivery_presets: tuple[DeliveryPreset, ...] = ()
    # Short human blurb for catalog / docs
    description: str = ""

    def __post_init__(self) -> None:
        # Infer capabilities when not set explicitly
        caps: set[str] = set(self.capabilities or ())
        if self.hook_events or self.format_hook:
            caps.add(CAP_HOOKS)
        if self.usage_source and self.usage_collect:
            caps.add(CAP_USAGE)
        if self.backend_types or self.delivery_presets or self.backend_id:
            caps.add(CAP_DELIVERY)
        if CAP_OPENAI_COMPAT in (self.capabilities or ()):
            caps.add(CAP_OPENAI_COMPAT)
        self.capabilities = frozenset(caps)
        if not self.backend_types and self.backend_id:
            self.backend_types = (self.backend_id,)

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

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


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


def providers_with_capability(capability: str) -> list[Provider]:
    return [p for p in _REGISTRY.values() if p.has(capability)]


def provider_for_backend_type(backend_type: str) -> Provider | None:
    """Resolve a [backends.x].type string to a registered Provider."""
    t = (backend_type or "").strip().lower()
    if not t:
        return None
    for p in _REGISTRY.values():
        if t in {b.lower() for b in p.backend_types}:
            return p
        if t == p.backend_id.lower() or t == p.id.lower():
            return p
    return None


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
            if m.startswith(pl) or f"/{pl}" in m or m == pl:
                hits.append((len(pl), p.provider_label))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def discover_and_import(package_dir: Path | None = None) -> list[str]:
    """Import every providers/<name>/ package for side-effect registration.

    New platforms: drop ``providers/<id>/`` with ``__init__.py`` that calls
    ``register(Provider(...))`` — no need to edit this file for discovery
    (``providers/__init__.py`` still may import known packages eagerly).
    """
    root = package_dir or Path(__file__).resolve().parent
    imported: list[str] = []
    if not root.is_dir():
        return imported
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        init = child / "__init__.py"
        if not init.is_file():
            continue
        mod_name = f"providers.{child.name}"
        try:
            __import__(mod_name)
            imported.append(child.name)
        except Exception as _exc:  # never block registry load on one bad plugin
            # Soft-fail: catalog still lists other providers
            continue
    return imported
