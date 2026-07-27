"""Shared Protocol / TypedDict contracts for swarm agents.

Implement these interfaces when porting shell modules. Do not invent
parallel shapes — consumers (send/poll/handlers) depend on these names.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RouteResult:
    """Inbound route resolution result (mirrors lib/routing.sh pipe format)."""

    backend: str
    project: str
    text: str
    match_kind: str  # chat | prefix | default | orchestrator | none | legacy

    def as_pipe(self) -> str:
        return f"{self.backend}|{self.project}|{self.text}|{self.match_kind}"


@dataclass(frozen=True)
class FormatResult:
    """Outbound message after structured formatting."""

    text: str
    parse_mode: str  # "HTML" or ""


@dataclass(frozen=True)
class SendRequest:
    """One outbound send (text and/or media)."""

    text: str
    chat_id: str
    thread_id: str = ""
    parse_mode: str = ""
    source: str = ""  # "hook" | ""
    backend: str = ""
    project: str = ""


@runtime_checkable
class ConfigLoader(Protocol):
    def load(self, path: Path | None = None) -> dict[str, Any]:
        """Load relay.toml (+ overlays) into a JSON-compatible dict."""
        ...

    def get(self, dotted: str, default: Any = None) -> Any: ...


@runtime_checkable
class Router(Protocol):
    def resolve(self, chat_id: str, thread_id: str, text: str) -> RouteResult: ...

    def project_from_cwd(self, cwd: str) -> str: ...

    def lookup_chat(self, backend: str, project: str) -> tuple[str, str]:
        """Return (chat_id, thread_id)."""
        ...


@runtime_checkable
class Formatter(Protocol):
    def format_message(self, text: str) -> FormatResult: ...


@runtime_checkable
class Sender(Protocol):
    def send(self, req: SendRequest) -> None: ...


@runtime_checkable
class UsageCollector(Protocol):
    def __call__(self, base: Path) -> list[Any]: ...


# Metric emitter signature used by shell emit_metric and Python ports
MetricEmitter = Callable[[str, str, str], None]
