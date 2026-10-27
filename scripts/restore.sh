#!/bin/bash
# Ollama Admin — 복구 스크립트
# 사용법: ./restore.sh <backup.tar.gz>

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <backup.tar.gz>"
    echo ""
    echo "Available backups:"
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    ls -lht "${PROJECT_DIR}/backups/"*.tar.gz 2>/dev/null || echo "  (none)"
    exit 1
fi

ARCHIVE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TEMP_DIR=$(mktemp -d)

echo "=== Ollama Admin Restore ==="
echo "From: $ARCHIVE"

# 압축 해제
echo "[1/4] Extracting..."
tar xzf "$ARCHIVE" -C "$TEMP_DIR"
RESTORE_DIR=$(ls "$TEMP_DIR")

# 서버 중지
echo "[2/4] Stopping server..."
launchctl unload ~/Library/LaunchAgents/com.ollama-admin.server.plist 2>/dev/null || true
sleep 2

# 복구
echo "[3/4] Restoring..."
if [ -f "$TEMP_DIR/$RESTORE_DIR/queue.db" ]; then
    cp "$PROJECT_DIR/queue.db" "$PROJECT_DIR/queue.db.pre-restore" 2>/dev/null || true
    cp "$TEMP_DIR/$RESTORE_DIR/queue.db" "$PROJECT_DIR/queue.db"
    echo "  queue.db restored"
fi
if [ -f "$TEMP_DIR/$RESTORE_DIR/config.json" ]; then
    cp "$TEMP_DIR/$RESTORE_DIR/config.json" "$PROJECT_DIR/config.json"
    echo "  config.json restored"
fi

# 서버 시작
echo "[4/4] Starting server..."
launchctl load ~/Library/LaunchAgents/com.ollama-admin.server.plist
sleep 3

# 확인
STATUS=$(curl -s http://localhost:8585/api/queue/status 2>/dev/null || echo '{"error":"failed"}')
echo "  Server: $STATUS"

# 정리
rm -rf "$TEMP_DIR"
echo "=== Restore Complete ==="
