#!/bin/bash
# Usage: ./batch_submit.sh <input_file> [model] [system_prompt]
# Input: 한 줄에 하나씩 프롬프트가 담긴 텍스트 파일
# Example:
#   export OLLAMA_ADMIN="http://localhost:8585"
#   export OLLAMA_API_KEY="CHANGE_ME"
#   ./batch_submit.sh prompts.txt qwen2.5:7b "You are a translator"

set -euo pipefail

ADMIN="${OLLAMA_ADMIN:-http://localhost:8585}"
API_KEY="${OLLAMA_API_KEY:-}"
INPUT="${1:?Usage: $0 <input_file> [model] [system_prompt]}"
MODEL="${2:-qwen2.5:7b}"
SYSTEM="${3:-}"

AUTH_HEADER=""
if [ -n "$API_KEY" ]; then
  AUTH_HEADER="-H"
  AUTH_VALUE="Authorization: Bearer $API_KEY"
fi

if [ ! -f "$INPUT" ]; then
  echo "Error: file not found: $INPUT" >&2
  exit 1
fi

JOBS=$(jq -R --arg model "$MODEL" '{"prompt": ., "model": $model}' "$INPUT" | jq -s '.')

PAYLOAD=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM" \
  --argjson jobs "$JOBS" \
  '{model: $model, jobs: $jobs} + (if $system != "" then {system: $system} else {} end)')

if [ -n "$API_KEY" ]; then
  RESULT=$(curl -s "$ADMIN/api/queue/batch" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $API_KEY" \
    -d "$PAYLOAD")
else
  RESULT=$(curl -s "$ADMIN/api/queue/batch" \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD")
fi

BATCH_ID=$(echo "$RESULT" | jq -r '.batch_id')
COUNT=$(echo "$RESULT" | jq -r '.count')

if [ "$BATCH_ID" = "null" ]; then
  echo "Error: $(echo "$RESULT" | jq -r '.error // "unknown"')" >&2
  exit 1
fi

echo "Batch submitted: $BATCH_ID ($COUNT jobs)"
echo "$BATCH_ID"
