"""OpenAI / ChatGPT / OpenAI-compatible self-hosted provider.

Two integration shapes (same Provider id ``openai``):

1. **Delivery backend** — Telegram → ``openai`` CLI / HTTP Chat Completions
   (ChatGPT API, Azure OpenAI, OpenRouter, vLLM, LM Studio, LiteLLM, LocalAI, …).
2. **Usage** — honest stub until local logs exist; model prefixes map gpt-* / o*
   to ``provider_label=openai`` for dashboards.

There is **no** Claude/Grok-style global hook stream for ChatGPT web today.
Optional future: Codex CLI / Cursor OpenAI hooks can add ``format_hook`` here
without changing routing.
"""

from __future__ import annotations

from providers.base import (
    CAP_OPENAI_COMPAT,
    DeliveryPreset,
    Provider,
    register,
)
from providers.openai.usage import collect_usage, default_usage_dir

# Model-id prefixes (OpenAI + common Azure/OpenRouter rewrites).
_MODEL_PREFIXES = (
    "gpt-",
    "gpt",
    "o1",
    "o3",
    "o4",
    "chatgpt",
    "text-davinci",
    "text-embedding",
    "davinci",
    "chatgpt-4o",
    "openai/",  # openrouter-style
)

_PRESETS = (
    DeliveryPreset(
        backend_type="openai",
        delivery="cmd",
        model="gpt-4o-mini",
        prefixes=("@openai", "@chatgpt", "/openai", "openai:", "chatgpt:"),
        tag="openai",
        cmd=(
            "bash",
            "-lc",
            # Official OpenAI CLI when installed; fails clearly if missing.
            'openai api chat.completions.create -m "${RELAY_MODEL:-gpt-4o-mini}" '
            '-g user "$RELAY_TEXT" 2>/dev/null '
            '| RELAY_BACKEND=openai "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw '
            "|| printf '%s\\n' \"[openai] CLI missing or failed — install openai CLI / set OPENAI_API_KEY\"",
        ),
        notes="ChatGPT API via openai CLI. Requires OPENAI_API_KEY in the poll environment.",
    ),
    DeliveryPreset(
        backend_type="openai-compat",
        delivery="cmd",
        model="local-model",
        prefixes=("@oai", "@compat", "/oai", "oai:"),
        tag="oai-compat",
        cmd=(
            "bash",
            "-lc",
            # OpenAI-compatible HTTP (vLLM / LM Studio / LiteLLM / LocalAI / OpenRouter).
            # OPENAI_BASE_URL default = local OpenAI-compatible server.
            'curl -sS "${OPENAI_BASE_URL:-http://127.0.0.1:1234/v1}/chat/completions" '
            '-H "Content-Type: application/json" '
            '-H "Authorization: Bearer ${OPENAI_API_KEY:-lm-studio}" '
            '-d "$(jq -n --arg m "${RELAY_MODEL:-local-model}" --arg p "$RELAY_TEXT" '
            '\'{model:$m,messages:[{role:\\"user\\",content:$p}],stream:false}\')" '
            "| jq -r '.choices[0].message.content // empty' "
            '| RELAY_BACKEND=openai-compat "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="Any OpenAI Chat Completions–compatible endpoint (self-hosted or proxy).",
    ),
    DeliveryPreset(
        backend_type="openrouter",
        delivery="cmd",
        model="openai/gpt-4o-mini",
        prefixes=("@openrouter", "/openrouter", "or:"),
        tag="openrouter",
        cmd=(
            "bash",
            "-lc",
            "curl -sS https://openrouter.ai/api/v1/chat/completions "
            '-H "Content-Type: application/json" '
            '-H "Authorization: Bearer ${OPENROUTER_API_KEY}" '
            '-d "$(jq -n --arg m "${RELAY_MODEL:-openai/gpt-4o-mini}" --arg p "$RELAY_TEXT" '
            '\'{model:$m,messages:[{role:\\"user\\",content:$p}]}\')" '
            "| jq -r '.choices[0].message.content // empty' "
            '| RELAY_BACKEND=openrouter "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="OpenRouter multi-model proxy (OpenAI-compatible).",
    ),
    DeliveryPreset(
        backend_type="lmstudio",
        delivery="cmd",
        model="local-model",
        prefixes=("@lmstudio", "@lms", "/lmstudio"),
        tag="lmstudio",
        cmd=(
            "bash",
            "-lc",
            'curl -sS "${LMSTUDIO_BASE_URL:-http://127.0.0.1:1234/v1}/chat/completions" '
            '-H "Content-Type: application/json" '
            '-d "$(jq -n --arg m "${RELAY_MODEL:-local-model}" --arg p "$RELAY_TEXT" '
            '\'{model:$m,messages:[{role:\\"user\\",content:$p}]}\')" '
            "| jq -r '.choices[0].message.content // empty' "
            '| RELAY_BACKEND=lmstudio "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="LM Studio local server (OpenAI-compatible on :1234 by default).",
    ),
    DeliveryPreset(
        backend_type="vllm",
        delivery="cmd",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
        prefixes=("@vllm", "/vllm"),
        tag="vllm",
        cmd=(
            "bash",
            "-lc",
            'curl -sS "${VLLM_BASE_URL:-http://127.0.0.1:8000/v1}/chat/completions" '
            '-H "Content-Type: application/json" '
            '-d "$(jq -n --arg m "$RELAY_MODEL" --arg p "$RELAY_TEXT" '
            '\'{model:$m,messages:[{role:\\"user\\",content:$p}]}\')" '
            "| jq -r '.choices[0].message.content // empty' "
            '| RELAY_BACKEND=vllm "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="vLLM OpenAI-compatible server.",
    ),
)

PROVIDER = register(
    Provider(
        id="openai",
        display_name="OpenAI / ChatGPT / OpenAI-compatible",
        config_namespace="openai",
        backend_id="openai",
        hook_events=[],  # ChatGPT web has no relay hooks; Codex/Cursor may add later
        usage_source="openai",
        usage_default_dir=default_usage_dir(),
        usage_collect=collect_usage,
        model_prefixes=_MODEL_PREFIXES,
        provider_label="openai",
        capabilities=frozenset({CAP_OPENAI_COMPAT}),
        backend_types=(
            "openai",
            "chatgpt",
            "openai-compat",
            "openrouter",
            "lmstudio",
            "vllm",
            "litellm",
            "localai",
            "azure-openai",
        ),
        delivery_presets=_PRESETS,
        description=(
            "ChatGPT API + any OpenAI Chat Completions–compatible self-hosted "
            "or proxy endpoint (vLLM, LM Studio, LiteLLM, OpenRouter, …)."
        ),
    )
)
