#!/usr/bin/env bash
# Prove the three Fireworks model IDs are live. No local router, no Docker.
# Three tiny completions, max_tokens=8. Expected spend: well under $0.02.
set -euo pipefail

if [[ -z "${FIREWORKS_API_KEY:-}" ]]; then
  echo "Set FIREWORKS_API_KEY first." >&2
  exit 2
fi
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

URL="https://api.fireworks.ai/inference/v1/chat/completions"

smoke() {
  local name="$1" id="$2"
  local payload
  payload="$(jq -n --arg model "$id" \
    '{model: $model, messages: [{role: "user", content: "Reply with the single word pong."}], max_tokens: 8}')"
  local code
  code="$(curl -sS -o /tmp/fw-smoke.json -w '%{http_code}' \
    -H "Authorization: Bearer ${FIREWORKS_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "$URL")"
  local text pin pout
  text="$(jq -r '.choices[0].message.content // .error.message // empty' /tmp/fw-smoke.json | tr '\n' ' ')"
  pin="$(jq -r '.usage.prompt_tokens // 0' /tmp/fw-smoke.json)"
  pout="$(jq -r '.usage.completion_tokens // 0' /tmp/fw-smoke.json)"
  printf '%-18s http=%s tok=%s/%s  %s\n' "$name" "$code" "$pin" "$pout" "$text"
  [[ "$code" == "200" ]]
}

echo "Direct Fireworks smoke (no router). max_tokens=8."
smoke "minimax-m3" "accounts/fireworks/models/minimax-m3"
smoke "glm-5p2" "accounts/fireworks/models/glm-5p2"
smoke "kimi-k2p7-code" "accounts/fireworks/models/kimi-k2p7-code"
echo "If all three returned HTTP 200, the IDs are live. Routing still needs vllm-sr serve + ./scripts/founder_four.sh"
