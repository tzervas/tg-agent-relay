"""Ollama / llama.cpp provider — routing via [backends.*]; usage stub only.

Delivery is **not** a full runtime integration. Telegram → model inject
goes through multi-backend ``delivery = \"cmd\"`` (see docs/PROVIDERS.md
and docs/ROUTING.md). Hooks are N/A for these CLIs today.
"""

from __future__ import annotations

from providers.base import Provider, register
from providers.ollama.usage import collect_usage, default_usage_dir

PROVIDER = register(
    Provider(
        id="ollama",
        display_name="Ollama",
        config_namespace="ollama",
        backend_id="ollama",
        hook_events=[],
        usage_source="ollama",
        usage_default_dir=default_usage_dir(),
        usage_collect=collect_usage,
        # Common local model id prefixes (Ollama tags + llama.cpp GGUF names).
        model_prefixes=("llama", "mistral", "qwen", "phi", "gemma", "codellama"),
        provider_label="ollama",
    )
)
