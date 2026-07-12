#!/usr/bin/env bash
# Waits for the user to drop a BOT_TOKEN into .env (and message the bot),
# then activates the bridge (resolves id + sends the live DM). Exits on success
# so the main Claude session is re-invoked to start the inbound Monitor.
BR="$HOME/.claude/telegram-bridge"
for i in $(seq 1 360); do
  tok=$(grep -E '^BOT_TOKEN=' "$BR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')
  if [ -n "$tok" ]; then
    out=$(bash "$BR/go-live.sh" 2>&1)
    if printf '%s' "$out" | grep -qiE 'live|success|resolved'; then
      printf 'BRIDGE_LIVE :: %s\n' "$out"
      exit 0
    fi
  fi
  sleep 10
done
printf 'BRIDGE_WATCH_TIMEOUT after ~1h with no token+message; re-arm if needed\n'
