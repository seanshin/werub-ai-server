#!/bin/bash
# deploy_imac.sh — iMac M1 워커 배포 (apple-silicon-worker Phase 0-9)
# 사용: bash scripts/deploy_imac.sh [--setup]
#   --setup : 최초 설치 (venv 생성 + plist 설치 + 토큰 생성)
#   기본    : git pull + 의존성 갱신 + 워커 재기동

set -euo pipefail
REPO="/Users/user/LLMManager"
VENV="$REPO/venv-mac"
AGENTS="$HOME/Library/LaunchAgents"
TOKEN_FILE="$HOME/.werub_worker_token"
UID_N=$(id -u)

cd "$REPO"
mkdir -p logs

if [[ "${1:-}" == "--setup" ]]; then
    echo "── 최초 설치 ──"
    [[ -d "$VENV" ]] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r requirements-mac.txt

    # 워커 토큰: 없으면 생성 (GPU 서버 config.json apple_worker_token에 동일 값 설정 필요)
    if [[ ! -f "$TOKEN_FILE" ]]; then
        openssl rand -hex 32 > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        echo "토큰 생성: $TOKEN_FILE — GPU 서버 위임 설정 시 이 값을 사용"
    fi
    TOKEN=$(cat "$TOKEN_FILE")

    # plist 설치 (토큰 치환)
    for P in com.werub.ollama com.werub.caffeinate com.werub.watchdog com.werub.worker; do
        sed "s/__WORKER_TOKEN__/$TOKEN/" "setup/$P.plist" > "$AGENTS/$P.plist"
        launchctl unload "$AGENTS/$P.plist" 2>/dev/null || true
        launchctl load -w "$AGENTS/$P.plist"
        echo "설치: $P"
    done
    echo "── 설치 완료 ──"
else
    echo "── 배포 (git pull + 재기동) ──"
    git pull --ff-only
    "$VENV/bin/pip" install -q -r requirements-mac.txt
    launchctl kickstart -k "gui/${UID_N}/com.werub.worker" 2>/dev/null \
        && echo "워커 재기동" || echo "워커 plist 미설치 — --setup 먼저 실행"
fi

sleep 2
curl -sf -m 5 http://127.0.0.1:8587/worker/health >/dev/null \
    && echo "✅ 워커 헬스 OK" || echo "⚠️ 워커 헬스 응답 없음 (logs/worker.log 확인)"
