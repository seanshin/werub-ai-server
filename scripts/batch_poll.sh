#!/bin/bash
# Usage: ./batch_poll.sh <batch_id> [output_file]
# Example:
#   export OLLAMA_ADMIN="http://localhost:8585"
#   export OLLAMA_API_KEY="CHANGE_ME"
#   ./batch_poll.sh abc12345 results.jsonl

set -euo pipefail

ADMIN="${OLLAMA_ADMIN:-http://localhost:8585}"
API_KEY="${OLLAMA_API_KEY:-}"
BATCH_ID="${1:?Usage: $0 <batch_id> [output_file]}"
OUTPUT="${2:-results_${BATCH_ID:0:8}.jsonl}"
INTERVAL="${POLL_INTERVAL:-5}"

AUTH=()
if [ -n "$API_KEY" ]; then
  AUTH=(-H "Authorization: Bearer $API_KEY")
fi

echo "Polling batch $BATCH_ID ..."

while true; do
  STATUS=$(curl -s "${AUTH[@]}" "$ADMIN/api/queue/batch/$BATCH_ID")
  PENDING=$(echo "$STATUS" | jq '.pending + .running')
  TOTAL=$(echo "$STATUS" | jq '.total')
  COMPLETED=$(echo "$STATUS" | jq '.completed')
  FAILED=$(echo "$STATUS" | jq '.failed')

  echo "[$(date '+%H:%M:%S')] $COMPLETED/$TOTAL done, $FAILED failed, $PENDING remaining"

  if [ "$PENDING" -eq 0 ]; then
    break
  fi

  sleep "$INTERVAL"
done

curl -s "${AUTH[@]}" "$ADMIN/api/queue/batch/$BATCH_ID/results" > "$OUTPUT"
echo "Results saved to: $OUTPUT"
echo "Total: $TOTAL, Completed: $COMPLETED, Failed: $FAILED"
