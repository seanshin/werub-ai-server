#!/usr/bin/env bash
# 배포 절차 — DEVELOPER_GUIDE §3을 스크립트로 고정 + 게이트 배선 (기획 Phase 3-c)
#
# 왜 스크립트인가: 절차가 문서로만 있으면 **매번 사람이 순서를 지켜야 한다**.
# 실제로 v2.124.51에서 실 DB dry-run을 건너뛰어 부팅 실패로 1분 중단이 났고,
# 2026-08-10에도 같은 단계를 건너뛸 뻔했다.
#
# 순서(각 단계 실패 시 즉시 중단):
#   1) git pull --ff-only
#   2) 실 DB dry-run (import app)      ← 스키마/초기화 사고 방어
#   3) G4 회귀 (pytest)
#   4) graceful HUP + health
#   5) G1 스모크 + G2 골든셋 (--gate)   ← **신규 실패만** 차단
#
# 사용:
#   scripts/deploy.sh                 전체
#   scripts/deploy.sh --skip-gates    게이트 생략(긴급) — **사유가 로그에 남는다**
#   scripts/deploy.sh --no-pull       이미 pull 했을 때
#   SKIP_REASON="핫픽스 …" scripts/deploy.sh --skip-gates
#
# ⚠️ 게이트는 GPU를 쓴다. **평가 배치와 동시에 실행하지 말 것**
#    (2026-08-10 실측: 배치 중 다른 GPU 작업을 겹쳐 배치가 38분간 멎었다).
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
PY=./venv/bin/python
LOG=logs/deploy.log
mkdir -p logs

SKIP_GATES=0
DO_PULL=1
for a in "$@"; do
  case "$a" in
    --skip-gates) SKIP_GATES=1 ;;
    --no-pull)    DO_PULL=0 ;;
    -h|--help)    sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $a" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
rec() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

rec "deploy start (skip_gates=$SKIP_GATES)"

# ── 1) pull ──────────────────────────────────────────────────────────────
if [ "$DO_PULL" = "1" ]; then
  say "1) git pull --ff-only"
  # ⚠️ 실패를 **결과로 남긴다**. 2026-08-11: 검증용으로 scp해 둔 추적되지 않는 파일이
  #    pull을 막았는데 `logs/deploy.log`엔 "deploy start"만 있고 결과 줄이 없었다
  #    — 나중에 로그만 보면 "배포가 돌다 사라진" 것처럼 읽힌다.
  if ! git pull --ff-only; then
    rec "FAIL pull $(git rev-parse --short HEAD)"
    echo "  🔴 pull 실패 — 배포 중단" >&2
    # 흔한 원인을 즉시 지목한다(추적되지 않는 파일이 병합을 막는 경우).
    UNTRACKED=$(git status --porcelain 2>/dev/null | awk '$1=="??"{print "     "$2}')
    if [ -n "$UNTRACKED" ]; then
      echo "  추적되지 않는 파일이 있다(병합을 막을 수 있음):" >&2
      echo "$UNTRACKED" >&2
      echo "  검증용 임시 파일이면 지우고 다시: git clean -n 으로 먼저 확인할 것" >&2
    fi
    exit 1
  fi
fi
REV=$(git rev-parse --short HEAD)
echo "  HEAD=$REV  $(git log -1 --pretty=%s | cut -c1-70)"

# ── 2) 실 DB dry-run ─────────────────────────────────────────────────────
# ⚠️ `import app`만 하면 인터프리터 **종료 단계**에서 네이티브 크래시(exit 139)가 나
#    실제 임포트 오류와 구분되지 않는다(v2.124.93 실측·변경 무관 환경 문제).
#    os._exit(0)로 teardown을 우회한다.
say "2) 실 DB dry-run"
$PY -c "import app, os; os._exit(0)" >/dev/null 2>&1 \
  && echo "  ✅ import OK" \
  || { echo "  🔴 import 실패 — 배포 중단"; rec "FAIL dry-run $REV"; exit 1; }

# ── 3) G4 회귀 ───────────────────────────────────────────────────────────
say "3) G4 회귀"
# ⚠️ **라이브 모델 테스트는 여기서 돌리지 않는다**(`SKIP_NETWORK_TESTS=1`).
# G4는 순수 로직이라 빠르고 결정적이어야 배포마다 돌릴 수 있다. 실제 모델 행동은
# 5단계 스모크의 몫이다. 2026-08-12 실측: 회귀에 섞여 있던 라이브 맞춤법 테스트가
# GPU 경합(모델 스왑) 중 30초 타임아웃으로 **배포를 막았고**, 단독 재실행은 통과했다.
# 흔들리는 게이트는 결국 꺼진다 — 계층을 지킨다.
if SKIP_NETWORK_TESTS=1 $PY -m pytest -q 2>&1 | tail -3; then
  echo "  ✅ 회귀 통과"
else
  echo "  🔴 회귀 실패 — 배포 중단"; rec "FAIL pytest $REV"; exit 1
fi

# ── 4) graceful 재기동 ───────────────────────────────────────────────────
say "4) graceful 재기동"
MASTER=$(systemctl show ollama-admin -p MainPID --value)
[ -n "$MASTER" ] && [ "$MASTER" != "0" ] || { echo "  🔴 마스터 PID 없음"; exit 1; }
kill -HUP "$MASTER"
for _ in $(seq 1 30); do
  sleep 1
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 localhost:8585/api/health || true)
  [ "$code" = "200" ] && break
done
[ "${code:-}" = "200" ] || { echo "  🔴 health $code — 확인 필요"; rec "FAIL health $REV"; exit 1; }
NR=$(systemctl show ollama-admin -p NRestarts --value)
echo "  ✅ health 200 · NRestarts=$NR"
[ "$NR" = "0" ] || echo "  ⚠️ NRestarts=$NR (graceful이 아니었을 수 있음)"

# ── 5) G1 스모크 + G2 골든셋 ─────────────────────────────────────────────
if [ "$SKIP_GATES" = "1" ]; then
  # 우회를 막는 게 아니라 **우회한 사실이 남게** 하는 것이 목적이다.
  echo ""
  echo "  ⚠️ 게이트 생략 — 사유: ${SKIP_REASON:-(미기재)}"
  rec "SKIPPED gates $REV reason=${SKIP_REASON:-none}"
  echo "  배포 완료(게이트 미검증) HEAD=$REV"
  exit 0
fi

say "5) G1 스모크 + G2 골든셋 (신규 실패만 차단)"
if $PY scripts/smoke_contract.py --gate; then
  echo "  ✅ 신규 실패 없음"
  rec "OK $REV"
else
  rc=$?
  echo "  🔴 게이트 차단(exit $rc) — 되돌리거나 베이스라인에 사유와 함께 등록할 것"
  echo "     되돌리기: git reset --hard HEAD~1 && scripts/deploy.sh --no-pull"
  rec "BLOCKED $REV rc=$rc"
  exit "$rc"
fi

echo ""
echo "배포 완료 HEAD=$REV"
