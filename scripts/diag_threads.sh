#!/bin/bash
# Ollama Admin — Thread Forensics (SSE 스레드 고갈 인시던트 진단)
# 용도: 워커 무응답(HTTP 000) 발생 순간 gunicorn 워커의 전 스레드 대기지점을 캡처.
#       [[project_incident_worker_deadlock]] 근본원인 확정 — 97스레드가 SSE Queue.get vs
#       DB락 vs 기타 어디서 대기 중인지 py-spy(파이썬 프레임) + /proc wchan(커널 대기채널)로 이중 확인.
#
# 실행: ./scripts/diag_threads.sh           # 수동 (인시던트 순간 또는 평시 베이스라인)
#       watchdog.sh가 000/502/503 감지 시 재시작 직전 자동 호출 (포렌식 자동 캡처)
#
# 권한: py-spy dump는 ptrace 필요 → sudo NOPASSWD 권장(없으면 wchan 집계만으로도 시그니처 판별 가능).
#       wchan/ps/stat은 동일 유저(weruby) 소유 프로세스라 sudo 불필요.
# 주의: GPU 서버(Ubuntu) 전용. 비파괴(읽기 전용) — 프로세스를 멈추거나 죽이지 않음.
#       py-spy dump는 대상 스레드를 수 ms 일시정지하므로 평시 남발 금지(인시던트 캡처용).

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$PROJECT_DIR/logs/diag"
TS="$(date '+%Y%m%d-%H%M%S')"
OUT="$OUT_DIR/diag_${TS}.txt"
# py-spy 경로: 환경변수 > venv > PATH 순 자동탐지 (수동 실행도 venv 설치본을 찾도록)
if [ -z "${PYSPY:-}" ]; then
    if   [ -x "$PROJECT_DIR/venv/bin/py-spy" ]; then PYSPY="$PROJECT_DIR/venv/bin/py-spy"
    elif command -v py-spy >/dev/null 2>&1;     then PYSPY="py-spy"
    else PYSPY="py-spy"; fi   # 미설치 — 아래에서 SKIP 처리
fi

mkdir -p "$OUT_DIR"

# stdout + 파일 동시 기록
exec > >(tee -a "$OUT") 2>&1

echo "==================================================================="
echo " Ollama Admin Thread Forensics  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo " host=$(hostname)  out=$OUT"
echo "==================================================================="

# ── 1) gunicorn 워커 PID 자동탐지 ─────────────────────────────────────
# master(부모) 제외, 자식 워커만. workers=1이라 보통 1개. cmdline에 gunicorn + :8585 바인딩.
ALL_PIDS="$(pgrep -f 'gunicorn' 2>/dev/null)"
if [ -z "$ALL_PIDS" ]; then
    echo "[ERR] gunicorn 프로세스를 찾지 못함 (서비스 다운?). systemctl status ollama-admin 확인."
    exit 1
fi

WORKERS=""
MASTER=""
for p in $ALL_PIDS; do
    [ -r "/proc/$p/status" ] || continue
    ppid="$(awk '/^PPid:/{print $2}' "/proc/$p/status" 2>/dev/null)"
    if echo "$ALL_PIDS" | grep -qw "$ppid"; then
        WORKERS="$WORKERS $p"     # 부모가 gunicorn → 워커
    else
        MASTER="$MASTER $p"       # 부모가 systemd → 마스터
    fi
done
WORKERS="$(echo "$WORKERS" | xargs 2>/dev/null)"
MASTER="$(echo "$MASTER" | xargs 2>/dev/null)"
echo
echo "## 프로세스 트리"
echo "master: ${MASTER:-<none>}    workers: ${WORKERS:-<none>}"
if [ -z "$WORKERS" ]; then
    echo "[WARN] 워커 미검출 — 마스터만 존재(기동 중이거나 워커 크래시 직후). 마스터로 대체 진단."
    WORKERS="$MASTER"
fi

# ── 2) 빠른 헬스 스냅샷 (sudo 불필요) ──────────────────────────────────
echo
echo "## 헬스 스냅샷"
HC="$(curl -s -o /dev/null -w '%{http_code} %{time_total}s' --connect-timeout 5 --max-time 12 http://127.0.0.1:8585/api/health 2>/dev/null || echo '000 -')"
echo "GET /api/health -> $HC"
echo "ollama active : $(systemctl is-active ollama 2>/dev/null || echo unknown)"

# ── 3) 워커별 스레드/CPU/wchan 집계 (sudo 불필요, 핵심 시그니처) ───────
for w in $WORKERS; do
    [ -d "/proc/$w" ] || { echo "[skip] $w 사라짐"; continue; }
    echo
    echo "==================== WORKER $w ===================="
    echo "## ps 요약 (스레드수 nlwp / cpu / mem / 상태 / 가동시간)"
    ps -o pid,nlwp,pcpu,pmem,stat,etime,cmd -p "$w" 2>/dev/null | cut -c1-120

    # nlwp = 스레드 수. 96 근접/초과면 고갈 시그니처.
    NLWP="$(ps -o nlwp= -p "$w" 2>/dev/null | tr -d ' ')"
    echo
    echo "## 스레드 수: ${NLWP:-?}  (gthread 풀 threads=96 — 90+ 근접 시 고갈 의심)"

    echo
    echo "## 커널 대기채널(wchan) 집계  [전 스레드가 어디서 자고 있나]"
    # ⚠️ 주의: 유휴 gthread 풀 스레드도 futex_do_wait가 정상(베이스라인 nlwp=9 중 8 futex).
    #    → futex '존재'는 신호 아님. 판별은 (1)스레드 수(nlwp 90+) (2)py-spy로 futex가
    #      SSE Queue.get인지 유휴 풀 대기인지 구분. 동일 유저 프로세스라 sudo 없이 읽힘.
    {
        for t in /proc/"$w"/task/*/; do
            tid="$(basename "$t")"
            wc="$(cat "${t}wchan" 2>/dev/null || echo '?')"
            st="$(awk '/^State:/{print $2}' "${t}status" 2>/dev/null || echo '?')"   # R/S/D (status: comm 공백 안전)
            echo "$st $wc"
        done
    } | sort | uniq -c | sort -rn | head -25
    echo "  (열: 횟수  상태(R실행/S수면/D디스크)  wchan)"

    echo
    echo "## D-state(언인터럽터블, DB/IO 블록 의심) 스레드"
    awk_d=0
    for t in /proc/"$w"/task/*/; do
        st="$(awk '/^State:/{print $2}' "${t}status" 2>/dev/null)"
        [ "$st" = "D" ] && { echo "  tid=$(basename "$t") wchan=$(cat "${t}wchan" 2>/dev/null)"; awk_d=1; }
    done
    [ "$awk_d" = 0 ] && echo "  없음 (DB락/IO 블록 아님)"

    # ── 4) py-spy 파이썬 프레임 덤프 (sudo 필요) ───────────────────────
    echo
    echo "## py-spy dump (파이썬 스택 — 어떤 코드 줄에서 대기 중인지)"
    if ! command -v "$PYSPY" >/dev/null 2>&1 && [ ! -x "$PYSPY" ]; then
        echo "  [SKIP] py-spy 미설치. 설치: pip install py-spy  (또는 venv/bin/pip)"
        echo "         미설치여도 위 wchan 집계로 고갈(futex 다수) 여부는 판별 가능."
    elif sudo -n "$PYSPY" dump --pid "$w" 2>/tmp/diag_pyspy_err.$$; then
        :
    else
        echo "  [SKIP] sudo -n py-spy 실패: $(cat /tmp/diag_pyspy_err.$$ 2>/dev/null | head -1)"
        echo "         NOPASSWD에 py-spy 미등록이거나 ptrace 권한 부족."
        echo "         → sudoers에 추가:  weruby ALL=(root) NOPASSWD: $(command -v "$PYSPY" 2>/dev/null || echo "$PYSPY") dump *"
    fi
    rm -f /tmp/diag_pyspy_err.$$ 2>/dev/null
done

echo
echo "==================================================================="
echo " 완료. 저장: $OUT"
echo " 판독: ①1차 신호 = 스레드 수(nlwp). 베이스라인 ~9 → 90+이면 고갈 확정(sudo 불필요)."
echo "       ②futex_do_wait는 유휴 풀 스레드도 정상 → '존재'만으론 판별 불가. py-spy 스택이"
echo "         다수 SSE generate()/q.get(monitor_stream/dashboard)면 = SSE 고갈 원인 확정."
echo "       ③D-state + sqlite wchan + py-spy commit 스택이면 = DB 락 데드락(별개 가설)."
echo "==================================================================="
