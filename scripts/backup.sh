#!/bin/bash
# Ollama Admin — 백업 스크립트
# 사용법: ./backup.sh [backup_dir]
# cron 예시: 0 3 * * * /path/to/backup.sh /path/to/backups

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${1:-$PROJECT_DIR/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="ollama-admin-${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

mkdir -p "$BACKUP_PATH"

echo "=== Ollama Admin Backup ==="
echo "Time: $(date)"
echo "Dest: $BACKUP_PATH"

# 1. SQLite DB (온라인 백업 — WAL 모드 안전)
echo "[1/4] Database..."
sqlite3 "$PROJECT_DIR/queue.db" ".backup '$BACKUP_PATH/queue.db'"
echo "  queue.db: $(du -h "$BACKUP_PATH/queue.db" | cut -f1)"

# 2. Config
echo "[2/4] Config..."
cp -f "$PROJECT_DIR/config.json" "$BACKUP_PATH/" 2>/dev/null || echo "  (no config.json)"

# 3. 로그 (최근 것만)
echo "[3/4] Logs..."
mkdir -p "$BACKUP_PATH/logs"
tail -1000 "$PROJECT_DIR/logs/queue.log" > "$BACKUP_PATH/logs/queue.log" 2>/dev/null || true
tail -500 "$PROJECT_DIR/logs/admin-server.log" > "$BACKUP_PATH/logs/admin-server.log" 2>/dev/null || true

# 4. 압축
echo "[4/4] Compressing..."
cd "$BACKUP_DIR"
tar czf "${BACKUP_NAME}.tar.gz" "$BACKUP_NAME"
rm -rf "$BACKUP_PATH"
FINAL="${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo "  Archive: $FINAL ($(du -h "$FINAL" | cut -f1))"

# 오래된 백업 삭제 (30일 이상)
DELETED=$(find "$BACKUP_DIR" -name "ollama-admin-*.tar.gz" -mtime +30 -delete -print | wc -l | tr -d ' ')
if [ "$DELETED" != "0" ]; then
    echo "  Cleaned: ${DELETED} old backups"
fi

echo "=== Backup Complete ==="
