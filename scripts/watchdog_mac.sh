#!/bin/bash
# watchdog_mac.sh — iMac M1 워커 노드 감시/자동복구 (apple-silicon-worker Phase 0-5)
# 실행: launchd com.werub.watchdog.plist (StartInterval 300 = 5분)
# 기존 scripts/watchdog.sh(Linux/systemd)의 macOS 이식 — launchctl kickstart 기반
#
# 감시 항목:
#   1. Ollama (:11434)        → com.werub.ollama kickstart
#   2. 워커 (:8587)           → com.werub.worker kickstart (plist 설치된 경우만)
#   3. SSH 터널 (ssh -R)      → com.werub.worker-tunnel kickstart (plist 설치된 경우만)
#   4. caffeinate 잠자기 방지 → com.werub.caffeinate kickstart
#   5. 디스크/메모리 경고     → 로그만 (복구 동작 없음)

set -u
LOG="/Users/user/LLMManager/logs/watchdog_mac.log"
UID_N=$(id -u)
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG"; }

kickstart() {  # $1=label
    launchctl kickstart -k "gui/${UID_N}/$1" 2>>"$LOG" \
        && log RECOVER "$1 kickstart 실행" \
        || log ERROR "$1 kickstart 실패"
}

plist_loaded() { launchctl list 2>/dev/null | grep -q "$1"; }

# ── 1. Ollama ─────────────────────────────────────────────
if ! curl -sf -m 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    log WARN "Ollama 응답 없음 (:11434)"
    plist_loaded "com.werub.ollama" && kickstart "com.werub.ollama"
fi

# ── 2. 워커 (:8587) — Phase 1 이후 활성 ──────────────────
if plist_loaded "com.werub.worker"; then
    if ! curl -sf -m 5 http://127.0.0.1:8587/worker/health >/dev/null 2>&1; then
        log WARN "워커 응답 없음 (:8587)"
        kickstart "com.werub.worker"
    fi
fi

# ── 3. SSH 통합 터널 (link-tunnel: -R 8586/8587 + -L 8590) ─
if plist_loaded "com.werub.link-tunnel"; then
    if ! pgrep -f "ssh.*-R.*8587" >/dev/null 2>&1; then
        log WARN "통합 터널 프로세스 없음 (8586/8587/8590 전 채널 다운)"
        kickstart "com.werub.link-tunnel"
    fi
fi

# ── 4. caffeinate (잠자기 방지) ───────────────────────────
if plist_loaded "com.werub.caffeinate"; then
    if ! pgrep -x caffeinate >/dev/null 2>&1; then
        log WARN "caffeinate 프로세스 없음 — 잠자기 방지 해제 상태"
        kickstart "com.werub.caffeinate"
    fi
fi

# ── 5. 자원 경고 (복구 없음, 관측용) ─────────────────────
FREE_PCT=$(memory_pressure -Q 2>/dev/null | awk -F': ' '/free percentage/{gsub("%","",$2); print int($2)}')
[ -n "${FREE_PCT:-}" ] && [ "$FREE_PCT" -lt 15 ] && log WARN "메모리 여유 ${FREE_PCT}% (<15%)"

DISK_AVAIL_GB=$(df -g / | awk 'NR==2{print $4}')
[ -n "${DISK_AVAIL_GB:-}" ] && [ "$DISK_AVAIL_GB" -lt 10 ] && log WARN "디스크 여유 ${DISK_AVAIL_GB}GB (<10GB)"

THERM=$(pmset -g therm 2>/dev/null | awk '/CPU_Speed_Limit/{print $3}')
[ -n "${THERM:-}" ] && [ "$THERM" -lt 100 ] && log WARN "thermal 제한 CPU_Speed_Limit=${THERM}%"

log OK "사이클 완료 (ollama=$(curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && echo up || echo DOWN))"
