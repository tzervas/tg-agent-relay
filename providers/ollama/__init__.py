"""Ollama / llama.cpp provider — routing via [backends.*]; usage stub only.

Delivery is **not** a full runtime integration. Telegram → model inject
goes through multi-backend ``delivery = \"cmd\"`` (see docs/PROVIDERS.md
and docs/ROUTING.md). Hooks are N/A for these CLIs today.
"""

from __future__ import annotations

from providers.base import DeliveryPreset, Provider, register
from providers.ollama.usage import collect_usage, default_usage_dir

_PRESETS = (
    DeliveryPreset(
        backend_type="ollama",
        delivery="cmd",
        model="llama3.2",
        prefixes=("@ollama", "/ollama", "ollama:"),
        tag="ollama",
        cmd=(
            "bash",
            "-lc",
            'ollama run "${RELAY_MODEL:-llama3.2}" "$RELAY_TEXT" '
            '| RELAY_BACKEND=ollama "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="Ollama CLI. Model server must already be running.",
    ),
    DeliveryPreset(
        backend_type="llamacpp",
        delivery="cmd",
        model="",
        prefixes=("@llama", "/llama", "llamacpp:"),
        tag="llamacpp",
        cmd=(
            "bash",
            "-lc",
            'curl -sS http://127.0.0.1:8080/completion -H "Content-Type: application/json" '
            '-d "$(jq -n --arg p "$RELAY_TEXT" \'{prompt:$p,n_predict:256}\')" '
            "| jq -r '.content // empty' "
            '| RELAY_BACKEND=llamacpp "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="llama.cpp server OpenAI-ish /completion endpoint on :8080.",
    ),
)

PROVIDER = register(
    Provider(
        id="ollama",
        display_name="Ollama / llama.cpp",
        config_namespace="ollama",
        backend_id="ollama",
        hook_events=[],
        usage_source="ollama",
        usage_default_dir=default_usage_dir(),
        usage_collect=collect_usage,
        # Common local model id prefixes (Ollama tags + llama.cpp GGUF names).
        model_prefixes=("llama", "mistral", "qwen", "phi", "gemma", "codellama"),
        provider_label="ollama",
        backend_types=("ollama", "llamacpp"),
        delivery_presets=_PRESETS,
        description="Self-hosted Ollama + llama.cpp delivery backends.",
    )
)
