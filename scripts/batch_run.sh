#!/bin/bash
# Usage: ./batch_run.sh <input_file> [model] [system_prompt]
# All-in-one: submit + poll + download results
# Example:
#   export OLLAMA_ADMIN="http://localhost:8585"
#   export OLLAMA_API_KEY="CHANGE_ME"
#   ./batch_run.sh prompts.txt qwen2.5:7b "You are a translator"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT="${1:?Usage: $0 <input_file> [model] [system_prompt]}"
MODEL="${2:-qwen2.5:7b}"
SYSTEM="${3:-}"

# Submit
BATCH_ID=$("$SCRIPT_DIR/batch_submit.sh" "$INPUT" "$MODEL" "$SYSTEM" | tail -1)

# Poll & download
"$SCRIPT_DIR/batch_poll.sh" "$BATCH_ID"
