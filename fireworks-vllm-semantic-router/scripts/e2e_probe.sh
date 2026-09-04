#!/usr/bin/env bash
# One live round-trip through the routed listener. model=MoM so the recipe can shift.
# Pinning a Fireworks model ID bypasses routing — do not do that here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CHAT_URL="${ROUTER_CHAT_URL:-http://127.0.0.1:18080/inference/v1/chat/completions}"
REPLAY_URL="${ROUTER_REPLAY_URL:-http://127.0.0.1:8080/v1/router_replay}"
MODEL="${ROUTER_PROBE_MODEL:-MoM}"
PROMPT="${1:-Rename the helper variable foo to user_id in this one-line function.}"

if [[ -z "${FIREWORKS_API_KEY:-}" ]]; then
  echo "FIREWORKS_API_KEY is not set. The router cannot complete a real Fireworks call." >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 2
fi

payload="$(jq -n --arg model "$MODEL" --arg prompt "$PROMPT" \
  '{model: $model, messages: [{role: "user", content: $prompt}], max_tokens: 64}')"

echo "==> POST $CHAT_URL model=$MODEL"
http_code="$(curl -sS -o /tmp/e2e-chat.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${FIREWORKS_API_KEY}" \
  -d "$payload" \
  "$CHAT_URL")"

echo "HTTP $http_code"
if [[ "$http_code" != "200" ]]; then
  echo "Chat request failed. Body:" >&2
  cat /tmp/e2e-chat.json >&2
  exit 1
fi

completion="$(jq -r '.choices[0].message.content // empty' /tmp/e2e-chat.json)"
usage="$(jq -c '.usage // {}' /tmp/e2e-chat.json)"
routed="$(jq -r '.model // empty' /tmp/e2e-chat.json)"
echo "routed_model=$routed"
echo "completion_chars=${#completion}"
echo "usage=$usage"

if [[ -z "$completion" ]]; then
  echo "Empty completion — not a successful round-trip." >&2
  exit 1
fi

echo "==> GET $REPLAY_URL?limit=1"
curl -sS "${REPLAY_URL}?limit=1" -o /tmp/e2e-replay.json
jq '{count: (.items | length? // .data | length? // 1), preview: .}' /tmp/e2e-replay.json | head -c 4000
echo
echo "OK: live completion returned and replay endpoint responded."
