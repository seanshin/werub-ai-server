#!/bin/bash
# HIS/PACS SSH Tunnel — AI 서버(GPU)에서 실행
# HIS/PACS 서버(198.51.100.73)의 stats agent를 localhost:9101로 포워딩
#
# Usage:
#   ./setup-his-tunnel.sh          # systemd 서비스 등록
#   ./setup-his-tunnel.sh manual   # 수동 실행 (포그라운드)
#   ./setup-his-tunnel.sh autossh  # autossh로 백그라운드 실행

set -e

HIS_HOST="deploy@198.51.100.73"
HIS_SSH_PORT="819"
LOCAL_PORT="9101"
REMOTE_PORT="8585"
SERVICE_NAME="his-pacs-tunnel"

case "${1:-systemd}" in
  manual)
    echo "[tunnel] Connecting to HIS/PACS (localhost:$LOCAL_PORT → $HIS_HOST:$REMOTE_PORT)..."
    ssh -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" "$HIS_HOST" -p "$HIS_SSH_PORT" -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3
    ;;

  autossh)
    echo "[tunnel] Starting autossh tunnel..."
    autossh -M 0 -f -N \
      -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" \
      "$HIS_HOST" -p "$HIS_SSH_PORT" \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes
    echo "[tunnel] autossh started (localhost:$LOCAL_PORT → HIS/PACS:$REMOTE_PORT)"
    ;;

  systemd)
    echo "[tunnel] Setting up systemd service: $SERVICE_NAME"

    cat > /tmp/$SERVICE_NAME.service << UNIT
[Unit]
Description=SSH Tunnel to HIS/PACS Server (port $LOCAL_PORT)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
ExecStart=/usr/bin/ssh -N -L $LOCAL_PORT:127.0.0.1:$REMOTE_PORT $HIS_HOST -p $HIS_SSH_PORT -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

    sudo mv /tmp/$SERVICE_NAME.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
    sleep 1
    sudo systemctl status "$SERVICE_NAME" --no-pager

    echo ""
    echo "=== Tunnel Active ==="
    echo "localhost:$LOCAL_PORT → HIS/PACS($HIS_HOST):$REMOTE_PORT"
    echo ""
    echo "Verify: curl -s http://127.0.0.1:$LOCAL_PORT/api/server/stats | python3 -m json.tool"
    ;;

  *)
    echo "Usage: $0 [manual|autossh|systemd]"
    exit 1
    ;;
esac
