#!/usr/bin/env bash
# GPU 박스 유지보수 창 — 업그레이드 + 재부팅 전 관문 + 재부팅 (v2.125.68)
#
#   scripts/maintenance_window.sh --check [--alert-days N]        # 무중단·sudo 불요 — 대기 업데이트·재부팅 대기 현황
#   sudo scripts/maintenance_window.sh --schedule "YYYY-MM-DD HH:MM" [--mode full|reboot-only]
#   sudo scripts/maintenance_window.sh --execute [--mode full|reboot-only]   # 보통 타이머가 부른다
#   scripts/maintenance_window.sh --execute --dry-run              # 바꾸는 명령은 출력만(테스트·사전 확인)
#
# 왜 있나: 월 1회 반복되는 수작업(커널·NVIDIA·libc/openssl 패치 → 재부팅 → 검증)을 운영자 sudo 1줄로 줄인다.
#   NVIDIA는 07-25 CPU 폴백 사고 뒤 unattended 블랙리스트라 **수동 적용이 필수**다(docs/MAINTENANCE_GPU_DRIVER.md).
#   2026-10-07 점검 때 재부팅 대기 12일 · 실행 중 gunicorn이 옛 libc/libssl을 물고 있었다.
# 재부팅 후 검증은 user crontab `@reboot … post_reboot_verify.sh`가 이 스크립트가 남긴 기준값 파일로 대조한다.
#
# ⚠️ 재부팅 = 박스 전체 중단: docker 54(PACS·HIS·LIS·ERP·edu·twin·sign·cerno·werub)·pm2 22
#    (his-proxy=Caddy·his-tunnel=cloudflared = 외부 유입 전체). 시각은 사람이 정하고 공지한다 — 그래서 자동 실행은 없다.
# ⚠️ GRUB_DEFAULT=0(2026-10-07 실측)이라 `grub-reboot`로 한 번만 다른 커널을 고르는 방법은 **동작하지 않는다**
#    (grub-reboot는 GRUB_DEFAULT=saved일 때만 먹힌다). 그래서 관문 실패 시 다른 커널로 비켜 가지 않고
#    dkms 재빌드 1회 → 그래도 실패면 재부팅하지 않고 critical로 남긴다.
# ⚠️ bash 3.2 호환(맥 회귀 테스트) — 연관 배열·${v,,}·mapfile 금지. flock 없으면 잠금 생략.
set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# 경로·대상은 환경변수로 덮을 수 있다(테스트가 가짜 /boot·/proc·설정 파일을 쓴다).
LOG_DIR="${MW_LOG_DIR:-$REPO/logs}"
LOG="$LOG_DIR/maintenance_window.log"
BASELINE="$LOG_DIR/maintenance_baseline.json"
CHECK_JSON="$LOG_DIR/maintenance_check.json"
BOOT_DIR="${MW_BOOT_DIR:-/boot}"
UU_CONF="${MW_UU_CONF:-/etc/apt/apt.conf.d/50unattended-upgrades}"
REBOOT_REQ="${MW_REBOOT_REQUIRED:-/var/run/reboot-required}"
NV_PROC="${MW_NV_PROC:-/proc/driver/nvidia/version}"
APP_USER="${MW_APP_USER:-weruby}"
PM2_BIN="${MW_PM2_BIN:-/home/deploy/.nvm/versions/node/v22.22.1/bin}"
ADMIN_URL="${MW_ADMIN_URL:-http://127.0.0.1:8585}"
LOCK="${MW_LOCK:-/run/lock/werub-maintenance.lock}"
UNIT="werub-maintenance"
QUEUE_WAIT_S="${MW_QUEUE_WAIT_S:-600}"
QUEUE_POLL_S="${MW_QUEUE_POLL_S:-15}"
MIN_FREE_GB="${MW_MIN_FREE_GB:-5}"
REQUIRE_ROOT="${MW_REQUIRE_ROOT:-1}"

ACTION=""; MODE="full"; WHEN=""; DRY_RUN=0; ALERT_DAYS=7
while [ $# -gt 0 ]; do
  case "$1" in
    --check) ACTION=check ;;
    --schedule) ACTION=schedule; WHEN="${2:-}"; shift ;;
    --execute) ACTION=execute ;;
    --mode) MODE="${2:-}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --alert-days) ALERT_DAYS="${2:-7}"; shift ;;
    -h|--help) sed -n '2,8p' "$SELF"; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$MODE" in full|reboot-only) ;; *) echo "--mode는 full|reboot-only: $MODE" >&2; exit 2 ;; esac

mkdir -p "$LOG_DIR" 2>/dev/null || true
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }
# 바꾸는 명령은 전부 여기로 — dry-run이면 출력만 한다(테스트가 이 줄을 센다).
run() {
  if [ "$DRY_RUN" = "1" ]; then log "[dry-run] $*"; return 0; fi
  log "+ $*"; "$@" >>"$LOG" 2>&1
}
need_root() {
  if [ "$REQUIRE_ROOT" = "1" ] && [ "$(id -u)" != "0" ] && [ "$DRY_RUN" != "1" ]; then
    echo "sudo로 실행하세요: sudo $SELF $*" >&2; exit 2
  fi
}

# ── 금지 시간대 ─────────────────────────────────────────────────────────────
# 03:00~04:30 = 백업 cron 8개(03:00·03:30·03:40·03:45)+daily-refresh 04:00 · 06:00~07:00 = unattended-upgrades.
# 재부팅이 백업 도중에 끼면 덤프가 잘린다(2026-10-07 crontab 실측).
in_forbidden_window() {  # $1 = "HH:MM"
  local hm="${1//:/}"; hm=$((10#$hm))
  { [ "$hm" -ge 300 ] && [ "$hm" -lt 430 ]; } || { [ "$hm" -ge 600 ] && [ "$hm" -lt 700 ]; }
}

# ── 사실 수집(읽기 전용) ────────────────────────────────────────────────────
newest_kernel() {  # /boot에 설치된 가장 새 커널 = GRUB_DEFAULT=0이 부팅할 커널
  ls "$BOOT_DIR" 2>/dev/null | sed -n 's/^vmlinuz-\(.*\)$/\1/p' | sort -V | tail -1
}
nvidia_userland_version() {  # 설치된(ii) libnvidia-compute-* 의 업스트림 버전(예: 580.173.02)
  dpkg-query -W -f='${db:Status-Abbrev} ${Version}\n' 'libnvidia-compute-*' 2>/dev/null \
    | awk '$1=="ii"{print $2}' | cut -d- -f1 | sort -V | tail -1
}
dkms_ok_for() {  # $1=커널 $2=버전 → 그 커널에 그 버전 nvidia 모듈이 installed인가
  dkms status -m nvidia -k "$1" 2>/dev/null | grep -q "nvidia/$2, $1, .*: installed"
}
blacklist_intact() {
  grep -q '"nvidia-"' "$UU_CONF" 2>/dev/null && grep -q '"libnvidia-"' "$UU_CONF" 2>/dev/null
}
free_gb() { df -Pk / | awk 'NR==2{printf "%d", $4/1048576}'; }
# 첫 줄 예: "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.173.02  Release Build …"
loaded_driver() { head -1 "$NV_PROC" 2>/dev/null | grep -oE '[0-9]{3}\.[0-9]+(\.[0-9]+)?' | head -1; }
pm2_names() {  # 한 줄에 이름 하나(정렬). root에서 부를 땐 앱 계정으로.
  local cmd="PATH=$PM2_BIN:\$PATH pm2 jlist"
  if [ "$(id -u)" = "0" ]; then runuser -u "$APP_USER" -- bash -c "$cmd" 2>/dev/null
  else bash -c "$cmd" 2>/dev/null; fi | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
for p in sorted(x["name"] for x in d): print(p)'
}
docker_running() { docker ps -q 2>/dev/null | wc -l | tr -d ' '; }
deleted_libs() {  # 실행 중 앱이 지워진(=업데이트로 교체된) 공유 라이브러리를 몇 개 물고 있나
  local pid maps; pid=$(systemctl show -p MainPID --value ollama-admin 2>/dev/null)
  maps="${MW_ADMIN_MAPS:-/proc/$pid/maps}"
  [ -n "${MW_ADMIN_MAPS:-}" ] || { [ -n "$pid" ] && [ "$pid" != "0" ]; } || { echo ""; return; }
  # awk: 0건이어도 숫자 하나(종전 `grep -c || echo 0`은 "0\n0" — post_reboot_verify.sh 같은 결함)
  awk '/\.so.* \(deleted\)/{n++} END{print n+0}' "$maps" 2>/dev/null
}
reboot_pending_days() {
  [ -f "$REBOOT_REQ" ] || { echo ""; return; }
  local now m; now=$(date +%s); m=$(stat -c %Y "$REBOOT_REQ" 2>/dev/null || stat -f %m "$REBOOT_REQ")
  echo $(( (now - m) / 86400 ))
}
upgradable() { apt list --upgradable 2>/dev/null | grep '/' ; }
# 설치 미디어(cdrom) 저장소가 살아 있으면 `apt-get update`가 오류로 끝나 --execute가 ABORT한다.
# 2026-10-07 첫 실전: /etc/apt/sources.list 1번 줄 `deb [check-date=no] file:///cdrom …` 때문에 중단됐다
# (매일 도는 unattended는 이 오류를 무시해서 그전까지 드러나지 않았다). --check에서 미리 잡는다.
APT_SOURCES="${MW_APT_SOURCES:-/etc/apt/sources.list /etc/apt/sources.list.d/*.list}"
cdrom_sources() { grep -hE '^[[:space:]]*deb[^#]*(cdrom:|file:/+cdrom)' $APT_SOURCES 2>/dev/null; }

# ── --check ────────────────────────────────────────────────────────────────
do_check() {
  local up total nv kern days dl k_run k_new linger reb pm2n dk cd
  up=$(upgradable)
  total=$(printf '%s\n' "$up" | grep -c '/' || true)
  nv=$(printf '%s\n' "$up" | grep -cE '^(lib)?nvidia|^xserver-xorg-video-nvidia' || true)
  kern=$(printf '%s\n' "$up" | grep -cE '^linux-(image|headers|generic|modules)' || true)
  days=$(reboot_pending_days); dl=$(deleted_libs)
  k_run=$(uname -r); k_new=$(newest_kernel)
  linger=$(loginctl show-user "$APP_USER" -p Linger --value 2>/dev/null || echo "?")
  reb=$(crontab -l 2>/dev/null | grep -c '^@reboot' || true)
  pm2n=$(pm2_names | grep -c . || true); dk=$(docker_running)
  echo "커널: 실행 $k_run / 설치 최신 $k_new"
  echo "재부팅 대기: ${days:-없음}${days:+일} · 옛 라이브러리 매핑(ollama-admin·maps 줄): ${dl:-?}"
  echo "대기 업데이트: ${total}개 (NVIDIA $nv · 커널 $kern)"
  echo "dkms:"; dkms status -m nvidia 2>/dev/null | sed 's/^/  /'
  echo "Linger=$linger · @reboot 줄 $reb · pm2 $pm2n · docker $dk"
  cd=$(cdrom_sources | head -1)
  [ -n "$cd" ] && echo "⚠️ 설치 미디어 저장소 활성: $cd — --execute의 apt-get update가 실패한다"
  MW_J_TS="$(ts)" MW_J_KRUN="$k_run" MW_J_KNEW="$k_new" MW_J_DAYS="$days" MW_J_DL="$dl" \
  MW_J_TOTAL="$total" MW_J_NV="$nv" MW_J_KERN="$kern" MW_J_LINGER="$linger" MW_J_PM2="$pm2n" MW_J_DK="$dk" \
  python3 - "$CHECK_JSON" <<'PY'
import json, os, sys
e = os.environ
num = lambda k: int(e[k]) if e.get(k, "").isdigit() else None
json.dump({"ts": e["MW_J_TS"], "kernel_running": e["MW_J_KRUN"], "kernel_newest": e["MW_J_KNEW"],
           "reboot_pending_days": num("MW_J_DAYS"), "deleted_libs": num("MW_J_DL"),
           "upgradable": num("MW_J_TOTAL"), "nvidia_pending": num("MW_J_NV"), "kernel_pending": num("MW_J_KERN"),
           "linger": e["MW_J_LINGER"], "pm2": num("MW_J_PM2"), "docker": num("MW_J_DK")},
          open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
PY
  # 주간 cron용: 재부팅 대기가 길거나 NVIDIA 대기분이 있으면 WARN 한 줄 + exit 1(정상이면 아무것도 안 쌓는다).
  local warn=""
  [ -n "$days" ] && [ "$days" -gt "$ALERT_DAYS" ] && warn="재부팅 대기 ${days}일(>${ALERT_DAYS})"
  [ "${nv:-0}" -gt 0 ] && warn="${warn:+$warn · }NVIDIA 대기 ${nv}개(수동 적용 필요)"
  [ -n "$cd" ] && warn="${warn:+$warn · }cdrom 저장소 활성(주석 처리 필요: sudo sed -i 's|^deb .*cdrom|# &|' …)"
  if [ -n "$warn" ]; then log "[WARN] 유지보수 필요: $warn — docs/MAINTENANCE_GPU_DRIVER.md"; return 1; fi
  return 0
}

# ── --schedule ─────────────────────────────────────────────────────────────
do_schedule() {
  need_root --schedule "$WHEN" --mode "$MODE"
  echo "$WHEN" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}$' \
    || { echo "시각 형식: \"YYYY-MM-DD HH:MM\" — 받은 값: $WHEN" >&2; exit 2; }
  local hm="${WHEN#* }"
  if in_forbidden_window "$hm"; then
    echo "거부: $hm 은 금지 시간대(03:00~04:30 백업 · 06:00~07:00 자동 업데이트)" >&2; exit 2
  fi
  if systemctl list-timers --all --no-pager 2>/dev/null | grep -q "$UNIT.timer"; then
    echo "이미 예약돼 있다 — 취소 후 다시: sudo systemctl stop $UNIT.timer" >&2; exit 2
  fi
  run systemd-run --on-calendar="$WHEN:00" --unit="$UNIT" \
    --description="WeRUB 유지보수 창(mode=$MODE) — 예약 $(ts)" \
    /bin/bash "$SELF" --execute --mode "$MODE" || { log "[FAIL] 예약 실패"; exit 1; }
  log "예약됨: $WHEN mode=$MODE — 취소: sudo systemctl stop $UNIT.timer"
  systemctl list-timers --no-pager 2>/dev/null | grep -i "$UNIT" || true
}

# ── --execute ──────────────────────────────────────────────────────────────
write_baseline() {  # 재부팅 후 post_reboot_verify.sh가 대조할 기대값
  local tmp="$LOG_DIR/.pm2_names.$$"; pm2_names > "$tmp"
  MW_B_TS="$(ts)" MW_B_MODE="$MODE" MW_B_KB="$(uname -r)" MW_B_DB="$(loaded_driver)" \
  MW_B_KE="$1" MW_B_DE="$2" MW_B_DK="$(docker_running)" \
  python3 - "$BASELINE" "$tmp" <<'PY'
import json, os, sys
e = os.environ
names = [l.strip() for l in open(sys.argv[2]) if l.strip()]
json.dump({"ts": e["MW_B_TS"], "mode": e["MW_B_MODE"],
           "kernel_before": e["MW_B_KB"], "driver_before": e["MW_B_DB"],
           "kernel_expected": e["MW_B_KE"], "driver_expected": e["MW_B_DE"],
           "pm2": names, "docker": int(e["MW_B_DK"] or 0)},
          open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
PY
  rm -f "$tmp"
  chown "$APP_USER" "$BASELINE" "$LOG" 2>/dev/null || true   # 재부팅 후 앱 계정 스크립트가 읽고 옮긴다
}

wait_queue_idle() {  # 진행 중 LLM 작업이 끝나길 최대 QUEUE_WAIT_S 기다린다(넘으면 기록하고 진행)
  local waited=0 running
  while :; do
    running=$(curl -s -m 5 "$ADMIN_URL/api/queue/status" 2>/dev/null \
      | python3 -c 'import sys,json;print(json.load(sys.stdin).get("running",0))' 2>/dev/null || echo 0)
    [ "${running:-0}" = "0" ] && { log "큐 running=0 — 진행"; return; }
    [ "$waited" -ge "$QUEUE_WAIT_S" ] && { log "[WARN] 큐 running=$running 인 채 ${QUEUE_WAIT_S}s 대기 초과 — 진행"; return; }
    sleep "$QUEUE_POLL_S"; waited=$((waited + QUEUE_POLL_S))
  done
}

gate() {  # 재부팅 전 관문. 통과=0. 실패 사유는 로그로.
  local k="$1" v="$2" ok=0
  if [ -z "$v" ]; then log "[GATE] NVIDIA 유저랜드 버전을 못 읽음"; ok=1; fi
  if [ ! -f "$BOOT_DIR/initrd.img-$k" ]; then log "[GATE] initrd 없음: initrd.img-$k"; ok=1; fi
  if [ -n "$v" ] && ! dkms_ok_for "$k" "$v"; then log "[GATE] dkms: $k 에 nvidia/$v installed 없음"; ok=1; fi
  if ! blacklist_intact; then log "[GATE] unattended 블랙리스트(nvidia-·libnvidia-)가 사라짐: $UU_CONF"; ok=1; fi
  local f; f=$(free_gb)
  if [ "${f:-0}" -lt "$MIN_FREE_GB" ]; then log "[GATE] 디스크 여유 ${f}GB < ${MIN_FREE_GB}GB"; ok=1; fi
  return $ok
}

do_execute() {
  need_root --execute --mode "$MODE"
  if [ "${MW_IGNORE_WINDOW:-0}" != "1" ] && in_forbidden_window "${MW_NOW_HM:-$(date +%H:%M)}"; then
    log "[ABORT] 금지 시간대에 실행됨 — 아무것도 하지 않음"; exit 2
  fi
  if command -v flock >/dev/null 2>&1 && [ "$DRY_RUN" != "1" ]; then
    exec 9>"$LOCK"; flock -n 9 || { log "[ABORT] 다른 유지보수 실행 중($LOCK)"; exit 2; }
  fi
  log "===== 유지보수 창 시작 mode=$MODE dry_run=$DRY_RUN kernel=$(uname -r) driver=$(loaded_driver)"

  # pm2 목록 고정 — 재부팅 후 @reboot pm2 resurrect가 이 dump로 Caddy·cloudflared를 다시 띄운다.
  if [ "$(id -u)" = "0" ]; then run runuser -u "$APP_USER" -- bash -c "PATH=$PM2_BIN:\$PATH pm2 save"
  else run bash -c "PATH=$PM2_BIN:\$PATH pm2 save"; fi
  wait_queue_idle

  if [ "$MODE" = "full" ]; then
    local cd_hint=""; [ -n "$(cdrom_sources)" ] && cd_hint="원인 후보: 설치 미디어(cdrom) 저장소 활성 — docs/MAINTENANCE_GPU_DRIVER.md"
    run apt-get update || { log "[ABORT] apt-get update 실패 — 재부팅 안 함${cd_hint:+ · $cd_hint}"; exit 1; }
    # confold: 설정 파일 질문에 기존 유지(50unattended-upgrades 블랙리스트가 여기서 덮이면 안 된다).
    # Lock::Timeout: 06시대 unattended가 아직 돌고 있으면 dpkg 잠금을 기다린다.
    if ! DEBIAN_FRONTEND=noninteractive run apt-get -y -o DPkg::Lock::Timeout=600 \
         -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade; then
      # 여기서 실패하면 NVIDIA만 반쯤 바뀌었을 수 있다 — 관문이 판단하게 계속 간다.
      log "[WARN] apt-get full-upgrade 비정상 종료 — 관문으로 판정"
    fi
  fi

  local k v; k=$(newest_kernel); v=$(nvidia_userland_version)
  log "부팅될 커널=$k · NVIDIA 유저랜드=$v"
  if ! gate "$k" "$v"; then
    # 🔴 관문 실패: NVIDIA 유저랜드가 이미 바뀐 상태라면 '재부팅 안 함'도 안전하지 않다 —
    #    로드된 모듈과 버전이 어긋나 다음 모델 적재가 CPU로 떨어진다(07-25 사고). 그래서 dkms 재빌드를 1회 시도한다.
    log "관문 실패 → dkms autoinstall -k $k 1회 재시도"
    run dkms autoinstall -k "$k" || true
    if [ "$DRY_RUN" = "1" ] || ! gate "$k" "$v"; then
      [ "$DRY_RUN" = "1" ] && { log "[dry-run] 관문 실패 경로 — 실제였다면 재판정 후 결정"; exit 1; }
      log "[CRITICAL] 관문 재실패 — 재부팅하지 않음. 로드된 드라이버=$(loaded_driver) / 유저랜드=$v"
      log "[CRITICAL] 버전이 다르면 새 모델 적재가 CPU 폴백된다 — 운영자 조치: dkms status · docs/MAINTENANCE_GPU_DRIVER.md '문제 발생 시'"
      exit 1
    fi
  fi
  log "관문 통과 — 기준값 기록 후 재부팅"
  # dry-run은 별도 파일 — 진짜 기준값을 남기면 다음(계획 밖) 재부팅의 검증이 엉뚱한 기대값과 대조한다.
  [ "$DRY_RUN" = "1" ] && BASELINE="$BASELINE.dry-run"
  write_baseline "$k" "$v"
  run journalctl --vacuum-size=1G || true
  log "===== 재부팅 (검증: logs/post_reboot_verify.log · logs/maintenance_last.json)"
  run systemctl reboot
}

case "$ACTION" in
  check) do_check ;;
  schedule) do_schedule ;;
  execute) do_execute ;;
  *) sed -n '2,8p' "$SELF"; exit 2 ;;
esac
