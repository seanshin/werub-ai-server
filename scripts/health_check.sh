#!/bin/bash
# WeRUBLLMManager 서버 상태 모니터링
DISK_THRESHOLD=90
MEM_THRESHOLD=90
SWAP_THRESHOLD=80
PROJECT_DIR=/home/deploy/WeRUBLLMManager
LOG=$PROJECT_DIR/logs/health_check.log

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 디스크 (LC_ALL=C: 한국어 로케일에서 df/free 헤더 현지화로 awk 매칭 실패 방지)
DISK_PCT=$(LC_ALL=C df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ -n "$DISK_PCT" ] && [ "$DISK_PCT" -ge "$DISK_THRESHOLD" ]; then
  echo "$(ts) [WARN] Disk usage ${DISK_PCT}% >= ${DISK_THRESHOLD}%" >> $LOG
fi

# 메모리
MEM_PCT=$(LC_ALL=C free | awk '/Mem:/{printf "%.0f", $3/$2*100}')
if [ -n "$MEM_PCT" ] && [ "$MEM_PCT" -ge "$MEM_THRESHOLD" ]; then
  echo "$(ts) [WARN] Memory usage ${MEM_PCT}% >= ${MEM_THRESHOLD}%" >> $LOG
fi

# 스왑
SWAP_TOTAL=$(LC_ALL=C free | awk '/Swap:/{print $2}')
if [ -n "$SWAP_TOTAL" ] && [ "$SWAP_TOTAL" -gt 0 ]; then
  SWAP_PCT=$(LC_ALL=C free | awk '/Swap:/{printf "%.0f", $3/$2*100}')
  # 상태 변화 때만 기록 + 높은 상태가 지속되면 1시간마다 1줄(2026-09-28: 5분마다 WARN이
  # 4,936줄 쌓여 다른 경고를 묻고 있었다 — 원인은 동거 스택·운영자 항목이라 조치 불가 반복).
  SWAP_STATE_FILE=$PROJECT_DIR/logs/.health_check_swap_state
  PREV=$(cat "$SWAP_STATE_FILE" 2>/dev/null || echo "ok 0")
  PREV_STATE=${PREV%% *}; PREV_TS=${PREV##* }
  NOW_S=$(date +%s)
  if [ -n "$SWAP_PCT" ] && [ "$SWAP_PCT" -ge "$SWAP_THRESHOLD" ]; then
    if [ "$PREV_STATE" != "high" ]; then
      echo "$(ts) [WARN] Swap usage ${SWAP_PCT}% >= ${SWAP_THRESHOLD}% (진입)" >> $LOG
      echo "high $NOW_S" > "$SWAP_STATE_FILE"
    elif [ $((NOW_S - PREV_TS)) -ge 3600 ]; then
      echo "$(ts) [WARN] Swap usage ${SWAP_PCT}% >= ${SWAP_THRESHOLD}% (지속·1h 요약)" >> $LOG
      echo "high $NOW_S" > "$SWAP_STATE_FILE"
    fi
  elif [ -n "$SWAP_PCT" ] && [ "$PREV_STATE" = "high" ]; then
    echo "$(ts) [INFO] Swap usage ${SWAP_PCT}% < ${SWAP_THRESHOLD}% (해소)" >> $LOG
    echo "ok $NOW_S" > "$SWAP_STATE_FILE"
  fi
fi

# 서비스 상태 (ollama-tunnel은 ingress_mode=ssh_tunnel일 때만 — 2026-09-09 은퇴 가능)
SVCS="ollama ollama-admin"
INGRESS_MODE=$(python3 -c "import json;print((json.load(open('$PROJECT_DIR/config.json')).get('ingress_mode') or 'ssh_tunnel'))" 2>/dev/null || echo ssh_tunnel)
[ "$INGRESS_MODE" = "ssh_tunnel" ] && SVCS="$SVCS ollama-tunnel"
for svc in $SVCS; do
  if ! systemctl is-active --quiet $svc; then
    echo "$(ts) [CRIT] Service $svc is DOWN" >> $LOG
  fi
done

# Ollama health
HTTP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8585/api/health 2>/dev/null)
if [ "$HTTP" != "200" ]; then
  echo "$(ts) [CRIT] ollama-admin health check failed (HTTP $HTTP)" >> $LOG
fi

# 하트비트: 매 실행 시각 기록 (이상 시에만 로그 기록하므로 '로그 침묵=정상 OR cron사망' 모호성 제거).
# 점검: cat logs/.health_check_last  (값이 5분 이상 미갱신이면 cron 사망 의심)
echo "$(date '+%s') $(ts) last_http=${HTTP}" > "$(dirname "$LOG")/.health_check_last"
