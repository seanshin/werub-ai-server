#!/usr/bin/env bash
# 재부팅 후 자동 검증 (v2.125.14 · 확장 v2.125.68) — 07-25 인시던트 교훈: health 200만으로는 GPU 복구를 알 수 없다.
# 판정 축: ①ollama-admin health 200 ②/api/ps에 상주 모델이 size_vram>0로 적재 ③tok/s(짧은 생성 지연)
#   v2.125.68 추가: ④외부 유입(공개 /api/tags 200 · /api/status 401) ⑤pm2(Caddy=his-proxy·cloudflared=his-tunnel
#   누락 시 `pm2 resurrect` 1회 자가 복구) ⑥docker 실행 수 ⑦옛 공유 라이브러리 잔존 ⑧reboot-required 해소
#   ⑨(유지보수 창이 남긴 기준값이 있으면) 커널·드라이버 기대값·pm2 목록·docker 수 대조.
# GPU 미적재면 NOPASSWD 허용된 `systemctl restart ollama` 1회 재시도 후 재판정. 결과는 로그 RESULT 한 줄 +
# logs/maintenance_last.json(축별 ok/warn/fail). 기준값 파일은 판정 후 타임스탬프 이름으로 보관한다.
# 설치: user crontab `@reboot sleep 90; /home/deploy/WeRUBLLMManager/scripts/post_reboot_verify.sh >> logs/post_reboot_verify.log 2>&1`
#
# ⚠️ 기준값이 없어도(정전·계획 밖 재부팅) ④⑤⑦⑧은 항상 본다 — 2026-10-07 실측: Caddy·cloudflared는 systemd가
#    아니라 pm2 → crontab `@reboot pm2 resurrect`로만 복귀하고, 그 HIS용 @reboot 줄은 추가 후 한 번도 실행된 적이 없었다.
# ⚠️ ollama-tunnel(09-09 은퇴)·ollama-worker-bridge(iMac 의도적 다운)는 inactive가 정상이라 판정에서 뺐다.
set -u
cd "$(dirname "$0")/.."
OL="${MW_OLLAMA_URL:-http://127.0.0.1:11434}"; AD="${MW_ADMIN_URL:-http://127.0.0.1:8585}"
PUB="${MW_PUBLIC_URL:-https://example.org/ollama}"
LOG_DIR="${MW_LOG_DIR:-logs}"
BASELINE="$LOG_DIR/maintenance_baseline.json"
LAST="$LOG_DIR/maintenance_last.json"
NV_PROC="${MW_NV_PROC:-/proc/driver/nvidia/version}"
REBOOT_REQ="${MW_REBOOT_REQUIRED:-/var/run/reboot-required}"
PM2_BIN="${MW_PM2_BIN:-/home/deploy/.nvm/versions/node/v22.22.1/bin}"
POLL="${MW_VERIFY_POLL_S:-10}"          # 테스트는 0으로 줄인다
RESTART_WAIT="${MW_RESTART_WAIT_S:-25}"
KEY_PM2="his-proxy his-tunnel"          # 외부 유입 전체(Caddy·cloudflared)
mkdir -p "$LOG_DIR"
ts() { date '+%F %T'; }
echo "[$(ts)] ===== post-reboot verify start (uptime: $(uptime -p 2>/dev/null || uptime)) kernel=$(uname -r)"

# 축 결과 누적: "이름|ok|값" 한 줄씩 — 마지막에 JSON으로.
AXES=""
axis() { AXES="$AXES$1|$2|$3
"; echo "[$(ts)] [$2] $1: $3"; }

# 1) admin/ollama 기동 대기 (최대 8분)
for i in $(seq 1 48); do
  a=$(curl -s -m 5 -o /dev/null -w "%{http_code}" $AD/api/health || echo 000)
  o=$(curl -s -m 5 -o /dev/null -w "%{http_code}" $OL/ || echo 000)
  [ "$a" = "200" ] && [ "$o" = "200" ] && break
  sleep "$POLL"
done
echo "[$(ts)] admin=$a ollama=$o after ${i}x${POLL}s"
[ "$a" = "200" ] && axis admin ok "$a" || axis admin fail "$a"
# 2) nvidia 모듈/장치
nv=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 | head -1)
echo "[$(ts)] nvidia-smi: $nv"
# 3) 상주 예열 + VRAM 적재 확인 (2회까지)
check_gpu() {
  curl -s -m 300 $OL/api/generate -d '{"model":"qwen2.5:14b","prompt":"워밍업","stream":false,"keep_alive":-1,"options":{"num_predict":4}}' >/dev/null
  t0=$(date +%s.%N)
  r=$(curl -s -m 180 $OL/api/generate -d '{"model":"qwen2.5:14b","prompt":"대한민국의 수도는?","stream":false,"options":{"temperature":0,"num_predict":32}}')
  t1=$(date +%s.%N)
  ec=$(echo "$r" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('eval_count',0))" 2>/dev/null || echo 0)
  vram=$(curl -s -m 10 $OL/api/ps | python3 -c "import sys,json;print(sum(m.get('size_vram',0) for m in json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo 0)
  sec=$(python3 -c "print(round(float('$t1'.replace('N',''))-float('$t0'.replace('N','')),2))" 2>/dev/null || echo "?")
  echo "[$(ts)] gen ${ec}tok/${sec}s size_vram=${vram}"
  [ "${vram:-0}" -gt 1000000000 ]
}
if check_gpu; then gpu=OK; else
  echo "[$(ts)] ⚠ size_vram=0 → sudo -n systemctl restart ollama (NOPASSWD) 후 재판정"
  sudo -n systemctl restart ollama && sleep "$RESTART_WAIT"
  if check_gpu; then gpu=OK-after-restart; else gpu=FAIL; fi
fi
[ "$gpu" = "FAIL" ] && axis gpu fail "$gpu" || axis gpu ok "$gpu"

# 4) 커널·드라이버 — 로드된 모듈과 nvidia-smi(유저랜드)가 같아야 한다(어긋나면 CPU 폴백의 전조)
drv_mod=$(head -1 "$NV_PROC" 2>/dev/null | grep -oE '[0-9]{3}\.[0-9]+(\.[0-9]+)?' | head -1)
drv_smi=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
if [ -n "$drv_mod" ] && [ "$drv_mod" = "$drv_smi" ]; then axis driver_match ok "$drv_mod"
else axis driver_match fail "module=${drv_mod:-?} smi=${drv_smi:-?}"; fi
if [ -f "$REBOOT_REQ" ]; then axis reboot_required warn "아직 있음(재부팅 후 새 패치가 또 들어왔을 수 있음)"
else axis reboot_required ok "없음"; fi

# 5) pm2 — Caddy·cloudflared가 없으면 외부 유입 전체가 끊긴다. 1회 자가 복구.
pm2_list() { bash -c "PATH=$PM2_BIN:\$PATH pm2 jlist" 2>/dev/null | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
for p in d: print(p["name"], p["pm2_env"]["status"])'; }
key_missing() { local l="$1" m=""; for k in $KEY_PM2; do echo "$l" | grep -q "^$k online$" || m="$m $k"; done; echo "${m# }"; }
pl=$(pm2_list); miss=$(key_missing "$pl")
if [ -n "$miss" ]; then
  echo "[$(ts)] ⚠ pm2 핵심 누락: $miss → pm2 resurrect 1회"
  bash -c "PATH=$PM2_BIN:\$PATH pm2 resurrect" >/dev/null 2>&1; sleep "$RESTART_WAIT"
  pl=$(pm2_list); miss2=$(key_missing "$pl")
  if [ -n "$miss2" ]; then axis pm2_key fail "누락: $miss2(resurrect 후에도)"; else axis pm2_key ok "resurrect로 복구($miss)"; fi
else axis pm2_key ok "$KEY_PM2 online"; fi
notup=$(echo "$pl" | awk 'NF==2 && $2!="online"{print $1}' | tr '\n' ' ')
npm2=$(echo "$pl" | grep -c . || true)
# 🔴 목록이 비면 'ok 0 online'이 아니라 fail — pm2 jlist 자체가 실패해도(node 경로·데몬 미기동) 빈 목록이 온다.
#    v2.125.68 실물 검증에서 이 축이 '0 online ok'로 통과하는 것을 봤다(반복 클래스 #1).
if [ "$npm2" = "0" ]; then axis pm2_status fail "목록 비어 있음(pm2 jlist 실패 또는 resurrect 미실행)"
elif [ -n "$notup" ]; then axis pm2_status warn "online 아님: $notup"
else axis pm2_status ok "$npm2 online"; fi

# 6) 외부 유입 — 공개 tags는 200, 인증 필요 경로는 401이어야 한다(200이면 인증 우회 = critical).
#    cloudflared가 막 떠서 터널 등록 중일 수 있어 tags는 최대 6회 재시도.
for j in 1 2 3 4 5 6; do
  t=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$PUB/api/tags" || echo 000)
  [ "$t" = "200" ] && break; sleep "$POLL"
done
[ "$t" = "200" ] && axis ingress_tags ok "$t" || axis ingress_tags fail "$t"
s=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$PUB/api/status" || echo 000)
case "$s" in 401) axis ingress_auth ok "$s" ;; 200) axis ingress_auth fail "200 — 인증 우회(XFF 누락 엣지?)" ;; *) axis ingress_auth warn "$s" ;; esac

# 7) 옛 공유 라이브러리 — 재부팅했으면 0이어야 한다(libc·openssl 패치 반영의 증거)
apid=$(systemctl show -p MainPID --value ollama-admin 2>/dev/null)
MAPS="${MW_ADMIN_MAPS:-/proc/$apid/maps}"   # 테스트가 가짜 maps 파일로 덮는다
if { [ -n "${MW_ADMIN_MAPS:-}" ] || { [ -n "$apid" ] && [ "$apid" != "0" ]; }; } && [ -r "$MAPS" ]; then
  # 🔴 `grep -c … || echo 0`은 0건일 때 grep이 "0"을 찍고 종료 코드 1을 내서 "0\n0"이 됐다 —
  #    2026-10-07 첫 실전 재부팅에서 정상(0)이 warn으로 판정됐다. awk는 항상 숫자 하나만 낸다.
  dl=$(awk '/\.so.* \(deleted\)/{n++} END{print n+0}' "$MAPS" 2>/dev/null)
  [ "$dl" = "0" ] && axis deleted_libs ok 0 || axis deleted_libs warn "$dl"
fi

# 8) docker
dk=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')
rs=$(docker ps --filter status=restarting --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')
[ -n "$rs" ] && axis docker_restarting warn "$rs"

# 9) 유지보수 창 기준값 대조(있을 때만)
if [ -f "$BASELINE" ]; then
  cmp=$(MW_K="$(uname -r)" MW_D="${drv_mod:-}" MW_DK="$dk" MW_PL="$pl" python3 - "$BASELINE" <<'PY'
import json, os, sys, collections
b = json.load(open(sys.argv[1])); e = os.environ
now = collections.Counter(l.split()[0] for l in e["MW_PL"].splitlines() if l.strip())
was = collections.Counter(b.get("pm2", []))
def out(name, st, val): print(f"{name}|{st}|{val}")
out("kernel_expected", "ok" if e["MW_K"] == b["kernel_expected"] else "fail", f'{e["MW_K"]} (기대 {b["kernel_expected"]})')
de = b.get("driver_expected") or ""
out("driver_expected", "ok" if e["MW_D"] == de else "fail", f'{e["MW_D"] or "?"} (기대 {de or "?"})')
missing = sorted((was - now).elements()); extra = sorted((now - was).elements())
out("pm2_vs_baseline", "fail" if missing else ("warn" if extra else "ok"),
    f"누락 {missing or '-'} · 중복/추가 {extra or '-'} (기준 {sum(was.values())} → {sum(now.values())})")
dk = int(e["MW_DK"] or 0)
out("docker_vs_baseline", "ok" if dk >= b.get("docker", 0) else "warn", f'{dk} (기준 {b.get("docker")})')
PY
)
  while IFS='|' read -r n st v; do [ -n "$n" ] && axis "$n" "$st" "$v"; done <<EOF
$cmp
EOF
  mv "$BASELINE" "$LOG_DIR/maintenance_baseline.$(date +%Y%m%d-%H%M%S).json"
else
  axis docker ok "$dk(기준값 없음 — 계획 밖 재부팅이거나 수동 재부팅)"
fi

ver=$(curl -s -m 5 $AD/api/version | python3 -c "import sys,json;print(json.load(sys.stdin).get('version'))" 2>/dev/null)
nfail=$(printf '%s' "$AXES" | grep -c '|fail|' || true)
nwarn=$(printf '%s' "$AXES" | grep -c '|warn|' || true)
result=OK; [ "$nfail" -gt 0 ] && result=FAIL
MW_AX="$AXES" MW_R="$result" MW_TS="$(ts)" MW_K="$(uname -r)" MW_V="${ver:-}" python3 - "$LAST" <<'PY'
import json, os, sys
e = os.environ
axes = {}
for l in e["MW_AX"].splitlines():
    if l.count("|") >= 2:
        n, st, v = l.split("|", 2); axes[n] = {"status": st, "value": v}
json.dump({"ts": e["MW_TS"], "result": e["MW_R"], "kernel": e["MW_K"], "version": e["MW_V"], "axes": axes},
          open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
PY
echo "[$(ts)] ===== RESULT $result gpu=$gpu fail=$nfail warn=$nwarn admin=$a version=$ver kernel=$(uname -r)"
[ "$result" = "OK" ]
