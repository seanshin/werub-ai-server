#!/bin/bash
# Ollama Admin — Watchdog (GPU/systemd 자동복구)
# cron: */5 * * * * /home/deploy/WeRUBLLMManager/scripts/watchdog.sh
#
# 역할: HTTP 000/502(=SSE 스레드 고갈·크래시)·Ollama Engine Down 재발 시 자동 재시작.
# 권한: sudo NOPASSWD 허용 = systemctl restart ollama-admin/ollama, daemon-reload (그 외는 graceful degrade).
# 안전장치: 단발 블립 방지 재확인 + 서비스별 재시작 쿨다운(기동/모델로드 안정화 대기).
# 주의: 이 스크립트는 GPU 서버(Ubuntu/systemd) 전용. macOS launchctl 아님.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_DIR/logs/watchdog.log"
STATE_DIR="$PROJECT_DIR/logs/.watchdog"
ADMIN_URL="http://127.0.0.1:8585"
OLLAMA_URL="http://127.0.0.1:11434"
COOLDOWN=600   # 동일 서비스 재시작 최소 간격(초): 기동+34모델 로드 안정화 대기, 플래핑 방지

mkdir -p "$STATE_DIR"
ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) [$1] $2" >> "$LOG"; }
code() { curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 12 "$1" 2>/dev/null || echo 000; }

in_cooldown() {  # $1=unit ; 최근 COOLDOWN초 내 재시작했으면 0(true)
    local f="$STATE_DIR/$1.last"
    [ -f "$f" ] || return 1
    local last now; last=$(cat "$f" 2>/dev/null || echo 0); now=$(date +%s)
    [ $((now - last)) -lt "$COOLDOWN" ]
}
mark_restart() { date +%s > "$STATE_DIR/$1.last"; }

STARTUP_GRACE=90   # systemd가 unit을 기동한 지 이 시간(초) 내면 '부팅 중'으로 보고 재시작 보류
unit_age() {  # $1=unit ; ActiveEnter 이후 경과초 (실패 시 큰 값)
    local started age
    started=$(systemctl show "$1" -p ActiveEnterTimestamp --value 2>/dev/null)
    [ -z "$started" ] && { echo 99999; return; }
    started=$(date -d "$started" +%s 2>/dev/null) || { echo 99999; return; }
    echo $(( $(date +%s) - started ))
}

restart_unit() {  # $1=표시명 $2=systemd unit ; 기동유예/쿨다운/권한 가드 포함
    local name="$1" unit="$2"
    # 기동 유예: 방금 (재)시작됐으면 preload(113모듈) 기동 중일 수 있음 → 배포 레이스 방지
    if [ "$(unit_age "$unit")" -lt "$STARTUP_GRACE" ]; then
        log "WARN" "$name down but recently (re)started (<${STARTUP_GRACE}s) — 부팅 중 추정, skip"
        return 1
    fi
    if in_cooldown "$unit"; then
        log "WARN" "$name down but in cooldown (<${COOLDOWN}s) — skip restart"
        return 1
    fi
    log "CRIT" "$name down — restarting ($unit)"
    if sudo -n systemctl restart "$unit" 2>/dev/null; then
        mark_restart "$unit"
        log "INFO" "$name restart issued"
        return 0
    fi
    log "CRIT" "$name restart FAILED — no NOPASSWD sudo for '$unit' (manual action needed)"
    return 1
}

DIAG_SCRIPT="$SCRIPT_DIR/diag_threads.sh"
DIAG_COOLDOWN=600   # 인시던트당 1회만 포렌식 (py-spy는 대상 스레드를 잠깐 정지 → 남발 금지)
capture_diag() {  # 확정 무응답 시 '재시작 직전' 스레드 덤프 — 재시작이 상태를 지우기 전에 포착.
    [ -x "$DIAG_SCRIPT" ] || return 0
    # 부팅 중 000은 고갈 아님(restart_unit과 동일 가드) → 캡처 skip
    if [ "$(unit_age ollama-admin)" -lt "$STARTUP_GRACE" ]; then
        log "INFO" "diag skip — ollama-admin 부팅 중(<${STARTUP_GRACE}s), 고갈 아님"; return 0
    fi
    local f="$STATE_DIR/diag.last" now last
    now=$(date +%s); last=$(cat "$f" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt "$DIAG_COOLDOWN" ]; then
        log "INFO" "diag skip — 최근 캡처(<${DIAG_COOLDOWN}s)"; return 0
    fi
    echo "$now" > "$f"
    log "CRIT" "ollama-admin 무응답 확정 — 재시작 직전 스레드 포렌식 캡처(diag_threads.sh)"
    local pyspy="$PROJECT_DIR/venv/bin/py-spy"          # venv 설치 우선
    [ -x "$pyspy" ] || pyspy="py-spy"
    PYSPY="$pyspy" timeout 60 "$DIAG_SCRIPT" >/dev/null 2>&1 \
        && log "INFO" "diag 캡처 완료 → logs/diag/" \
        || log "WARN" "diag 캡처 실패/타임아웃 (logs/diag/ 확인)"
}

# 1) Admin 서버: HTTP 000/502/503 = SSE 스레드 고갈/크래시/기동실패
C=$(code "$ADMIN_URL/api/health")
case "$C" in
    000|502|503)
        sleep 5; C2=$(code "$ADMIN_URL/api/health")   # 단발 블립 재확인
        case "$C2" in
            000|502|503)
                capture_diag                                   # 재시작이 상태를 지우기 전 포렌식
                if restart_unit "ollama-admin" "ollama-admin"; then
                    sleep 8; R=$(code "$ADMIN_URL/api/health")
                    [ "$R" = "200" ] && log "INFO" "ollama-admin recovered (HTTP 200)" \
                                     || log "WARN" "ollama-admin HTTP $R after restart (기동/모델로드 중일 수 있음)"
                fi ;;
            *) log "INFO" "ollama-admin transient HTTP $C -> $C2 (자가회복, 무동작)" ;;
        esac ;;
esac

# 2) Ollama 엔진: 프로세스가 실제로 죽었을 때만 재시작.
#    주의: HTTP 무응답(:11434 000) ≠ 다운. 대형모델(20~30b) 로드 중이거나 앱측 스레드
#    포화로 일시 무응답이 흔함(Engine Down 31건 대부분 오탐, NRestarts=0 = 죽은 적 없음).
#    멀쩡한 엔진을 HTTP만 보고 재시작하면 진행 중 모델 로드를 죽여 더 악화 → is-active 기준.
if ! systemctl is-active --quiet ollama; then
    if restart_unit "ollama-engine" "ollama"; then
        sleep 5; R=$(code "$OLLAMA_URL/")
        [ "$R" = "200" ] && log "INFO" "ollama engine recovered" \
                         || log "WARN" "ollama engine restarted but / not 200 yet (모델로드 중일 수 있음)"
    fi
elif [ "$(code "$OLLAMA_URL/")" = "000" ]; then
    # 프로세스는 살아있는데 HTTP 무응답 → 재시작 안 함(오탐/로드중), 기록만
    log "INFO" "ollama active but / unresponsive — 모델 로드/일시 부하 추정, 재시작 보류"
fi

# 3) 리버스 터널: NOPASSWD 미허용 가능 -> 시도 후 graceful
#    2026-09-09: 외부 유입이 Cloudflare Tunnel→Caddy로 바뀌어 ollama-tunnel이 은퇴할 수 있다.
#    config.json `ingress_mode`가 ssh_tunnel(기본)일 때만 감시한다 — 은퇴한 유닛을 5분마다
#    되살리려 시도하면 WARN이 무한 누적돼 진짜 신호를 덮는다.
INGRESS_MODE=$(python3 -c "import json,sys;print((json.load(open('$PROJECT_DIR/config.json')).get('ingress_mode') or 'ssh_tunnel'))" 2>/dev/null || echo ssh_tunnel)
if [ "$INGRESS_MODE" = "ssh_tunnel" ] && ! systemctl is-active --quiet ollama-tunnel; then
    if in_cooldown "ollama-tunnel"; then
        log "WARN" "ollama-tunnel inactive but in cooldown — skip"
    elif sudo -n systemctl restart ollama-tunnel 2>/dev/null; then
        mark_restart "ollama-tunnel"; log "INFO" "ollama-tunnel restart issued"
    else
        log "WARN" "ollama-tunnel inactive — restart needs sudo password (수동 조치 필요)"
    fi
fi

# 4) 로그 로테이션 (50MB↑)
for f in "$PROJECT_DIR/logs/app.log" "$PROJECT_DIR/logs/queue.log" "$LOG"; do
    [ -f "$f" ] || continue
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 52428800 ]; then
        mv "$f" "${f}.$(date +%Y%m%d-%H%M%S)"; touch "$f"
        log "INFO" "rotated $(basename "$f") (${SIZE}B)"
    fi
done

# 5) 하트비트: 매 실행 시각 기록(로그 스팸 없이 watchdog 가동 여부 확인용)
#    점검: cat logs/.watchdog/last_run  (값이 600초 이상 갱신 안 되면 cron 사망 의심)
date '+%s %Y-%m-%d %H:%M:%S' > "$STATE_DIR/last_run"
