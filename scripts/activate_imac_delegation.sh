#!/usr/bin/env bash
# activate_imac_delegation.sh — GPU 서버에서 iMac 워커 위임을 활성화/검증/해제.
#
# iMac이 가동되면 이 한 커맨드로 위임을 켠다. config.json을 안전하게 편집하고
# (백업 + JSON 파서 — 수동 편집 실수 방지), 터널·워커·위임·폴백을 검증한다.
# node-orchestrator 도입 후에도 --delegate로 capability 목록을 확장해 재사용한다.
#
# 사용법:
#   scripts/activate_imac_delegation.sh --token <TOKEN> [--url URL] [--delegate detect,mark]
#   scripts/activate_imac_delegation.sh --token-file ~/.werub_worker_token
#   scripts/activate_imac_delegation.sh --verify     # 변경 없이 현재 상태만 점검
#   scripts/activate_imac_delegation.sh --disable    # 위임 끄기(롤백, 백업 후)
#
# 안전장치:
#   - iMac(Darwin)에서는 실행 거부 — 위임 개시 노드가 아님(Darwin 가드).
#   - config.json은 항상 타임스탬프 백업 후 편집.
#   - 토큰은 인자/파일로만 받고 저장소엔 남기지 않음(config.json은 .gitignore).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${CONFIG_PATH:-$ROOT/config.json}"
PY="$ROOT/venv/bin/python"; [ -x "$PY" ] || PY="python3"
ADMIN="${ADMIN_BASE:-http://localhost:8585}"

URL="http://localhost:8587"
DELEGATE="detect"
TOKEN=""; MODE="activate"

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 인자 파싱 (--help는 어디서나 동작) ───────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --token)     TOKEN="$2"; shift 2;;
    --token-file) TOKEN="$(cat "$2")"; shift 2;;
    --url)       URL="$2"; shift 2;;
    --delegate)  DELEGATE="$2"; shift 2;;
    --verify)    MODE="verify"; shift;;
    --disable)   MODE="disable"; shift;;
    -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "알 수 없는 인자: $1";;
  esac
done

# ── Darwin 가드 (위임 개시 노드가 아님) ──────────────────────
[ "$(uname -s)" = "Darwin" ] && die "iMac(Darwin)에서는 위임을 개시하지 않습니다. GPU 서버에서 실행하세요."

[ -f "$CONFIG" ] || die "config.json 없음: $CONFIG (CONFIG_PATH로 지정 가능)"

# ── 워커 헬스(터널 경유) 점검 — capabilities 확인 ─────────────
check_worker() {
  local body
  if ! body="$(curl -fsS --max-time 3 "$URL/worker/health" 2>/dev/null)"; then
    warn "워커 헬스 도달 실패: $URL/worker/health (터널 미가동? iMac 워커 미기동?)"
    return 1
  fi
  ok "워커 응답: $URL"
  "$PY" - "$body" "$DELEGATE" <<'PY'
import json, sys
h = json.loads(sys.argv[1]); want = [c for c in sys.argv[2].split(",") if c]
caps = h.get("capabilities", [])
print(f"    version={h.get('version')} platform={h.get('platform')} caps={caps}")
det = h.get("detector", {}); print(f"    detector device={det.get('device')} available={det.get('available')}")
mem = h.get("memory") or {}; print(f"    memory={mem} thermal={h.get('thermal')}")
miss = [c for c in want if c not in caps]
if miss:
    print(f"    [warn] 요청 위임 {miss} 이(가) 워커 capabilities에 없음 → 해당 cap은 폴백됨")
PY
  return 0
}

# ── 현재 config 위임 상태(토큰 마스킹) ───────────────────────
show_config() {
  "$PY" - "$CONFIG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
keys = {k: c.get(k) for k in ("apple_worker_enabled","apple_worker_url","apple_worker_delegate")}
keys["apple_worker_token"] = ("set" if c.get("apple_worker_token") else "unset")
print("    " + json.dumps(keys, ensure_ascii=False))
PY
}

backup() {
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG" "$CONFIG.bak.$ts"
  ok "config 백업: $CONFIG.bak.$ts"
}

# ── config 편집(JSON 파서) ───────────────────────────────────
write_keys() {  # $1=enabled(true/false)
  "$PY" - "$CONFIG" "$1" "$URL" "$TOKEN" "$DELEGATE" <<'PY'
import json, sys
path, enabled, url, token, delegate = sys.argv[1:6]
c = json.load(open(path))
c["apple_worker_enabled"] = (enabled == "true")
if enabled == "true":
    c["apple_worker_url"] = url
    if token:
        c["apple_worker_token"] = token
    c["apple_worker_delegate"] = [d for d in delegate.split(",") if d]
json.dump(c, open(path, "w"), ensure_ascii=False, indent=2)
print(f"    apple_worker_enabled={c['apple_worker_enabled']} "
      f"delegate={c.get('apple_worker_delegate')} url={c.get('apple_worker_url')} "
      f"token={'set' if c.get('apple_worker_token') else 'unset'}")
PY
}

# ── _conf() 동적 로드 확인 (재시작 불필요 검증) ──────────────
verify_conf() {
  "$PY" - <<PY
import sys; sys.path.insert(0, "$ROOT")
from core import apple_worker as a
c = a._conf()
print("    apple_worker._conf() =", "활성" if c else "None(휴면)",
      "| Darwin가드:", a.IS_DARWIN)
PY
}

# ── 위임 스모크(작은 PNG로 detect) + 폴백 안내 ───────────────
smoke() {
  local img; img="$(mktemp /tmp/awdelg.XXXXXX.png)"
  "$PY" - "$img" <<'PY' 2>/dev/null || { echo "    (PIL 없음 — 스모크 생략)"; exit 0; }
import sys
from PIL import Image
Image.new("RGB", (128,128), (120,150,180)).save(sys.argv[1], "PNG")
PY
  local out
  if out="$(curl -fsS --max-time 8 -X POST "$ADMIN/api/detect/image" -F "image=@$img" 2>/dev/null)"; then
    echo "$out" | "$PY" - <<'PY'
import json, sys
d = json.load(sys.stdin); ml = d.get("ml", d)
eng = ml.get("engine"); dev = ml.get("device"); p = ml.get("ai_probability")
tag = "iMac 위임" if eng == "imac" else "GPU 로컬(폴백/미위임)"
print(f"    detect 결과: engine={eng} device={dev} ai_probability={p} → {tag}")
PY
  else
    warn "Admin($ADMIN) detect 스모크 실패 — Admin 미기동이거나 이미지 비활성. 수동 검증 권장."
  fi
  rm -f "$img"
}

echo "── iMac 위임 [$MODE] ──"
case "$MODE" in
  verify)
    log "config(현재):"; show_config
    check_worker || true
    verify_conf
    smoke
    ;;
  disable)
    log "config(변경 전):"; show_config
    backup
    write_keys false
    ok "위임 비활성화 — 즉시 기존 로컬 경로(재시작 불필요)"
    verify_conf
    ;;
  activate)
    [ -n "$TOKEN" ] || die "토큰 필요: --token <값> 또는 --token-file <경로> (iMac ~/.werub_worker_token)"
    log "1) 워커 헬스/capabilities 점검"
    check_worker || warn "워커 미도달 상태로 활성화 — 도달 시 자동 위임, 그 전엔 로컬 폴백"
    log "2) config 백업 + 위임 키 기록"
    backup; write_keys true
    log "3) 동적 로드 확인(재시작 불필요)"
    verify_conf
    log "4) 위임 스모크"
    smoke
    echo
    ok "활성화 완료. 폴백 검증: iMac 터널 중단 후 detect 재호출 → engine 필드 없음(로컬), 오류 0."
    log "롤백: scripts/activate_imac_delegation.sh --disable"
    ;;
esac
