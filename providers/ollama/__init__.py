"""Ollama provider stub — routing/delivery via [backends.ollama]; hooks TBD."""
from __future__ import annotations

from providers.base import Provider, register

PROVIDER = register(
    Provider(
        id="ollama",
        display_name="Ollama",
        config_namespace="ollama",
        backend_id="ollama",
        hook_events=[],
        usage_source=None,
        model_prefixes=("llama", "mistral", "qwen", "phi", "gemma", "codellama"),
        provider_label="ollama",
    )
)
