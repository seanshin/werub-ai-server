#!/bin/bash
# WeRU.B Server Stats Agent — 원격 서버 배포 스크립트
# Usage: ./deploy-stats-agent.sh [user@host] [ssh-port] [stats-port]
#
# 예시:
#   ./deploy-stats-agent.sh deploy@198.51.100.73 819        # HIS/PACS
#   ./deploy-stats-agent.sh deploy@192.0.2.108 819 9200  # Production (커스텀 포트)

set -e

TARGET="${1:-deploy@198.51.100.73}"
SSH_PORT="${2:-819}"
STATS_PORT="${3:-8585}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/opt/werub-stats"
SERVICE_NAME="werub-stats"

echo "=== WeRU.B Stats Agent Deploy ==="
echo "Target: $TARGET (port $SSH_PORT)"
echo "Stats port: $STATS_PORT"
echo "Remote dir: $REMOTE_DIR"
echo ""

# 1. 원격 디렉토리 생성
echo "[1/4] Creating remote directory..."
ssh -p "$SSH_PORT" "$TARGET" "sudo mkdir -p $REMOTE_DIR && sudo chown \$(whoami) $REMOTE_DIR"

# 2. 파일 복사
echo "[2/4] Uploading server-stats.py..."
scp -P "$SSH_PORT" "$SCRIPT_DIR/server-stats.py" "$TARGET:$REMOTE_DIR/server-stats.py"

# 3. Flask 설치 확인
echo "[3/4] Checking Flask dependency..."
ssh -p "$SSH_PORT" "$TARGET" "python3 -c 'import flask' 2>/dev/null || pip3 install flask --break-system-packages 2>/dev/null || pip3 install flask"

# 4. systemd 서비스 생성
echo "[4/4] Setting up systemd service..."
ssh -p "$SSH_PORT" "$TARGET" "cat > /tmp/$SERVICE_NAME.service << 'UNIT'
[Unit]
Description=WeRU.B Server Stats Agent
After=network.target

[Service]
Type=simple
User=$(echo $TARGET | cut -d@ -f1)
WorkingDirectory=$REMOTE_DIR
ExecStart=/usr/bin/python3 $REMOTE_DIR/server-stats.py --host 127.0.0.1 --port $STATS_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo mv /tmp/$SERVICE_NAME.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME
sleep 1
sudo systemctl status $SERVICE_NAME --no-pager"

echo ""
echo "=== Deploy Complete ==="
echo "Stats agent running on $TARGET:$STATS_PORT"
echo ""
echo "Verify:"
echo "  ssh -p $SSH_PORT $TARGET 'curl -s http://127.0.0.1:$STATS_PORT/api/server/stats | python3 -m json.tool'"
echo ""
echo "Next: Set up SSH tunnel from AI server:"
echo "  autossh -M 0 -f -N -L 9101:127.0.0.1:$STATS_PORT $TARGET -p $SSH_PORT"
