# GPU 박스 유지보수 런북 — 드라이버·커널·공용 라이브러리 업데이트와 재부팅

> 대상: RTX 5080 GPU 박스 (deploy@203.0.113.10:22) · 최종 갱신 2026-10-07 (v2.125.68 자동화)

## 왜 수동인가

2026-07-25, 무인 업그레이드(`unattended-upgrades`)가 NVIDIA 드라이버를 올렸는데
**커널 모듈을 리로드하지 않아** `nvidia-smi`가 버전 불일치로 실패 → ollama가 로드
모델을 **CPU로 폴백**(14b ~2-5 tok/s)한 인시던트 발생. `/api/health`는 healthy로
오인. 재부팅으로 해소.

→ 재발 방지로 **nvidia/libnvidia 계열을 자동 업그레이드에서 제외**함
(`/etc/apt/apt.conf.d/50unattended-upgrades` Package-Blacklist에 `"nvidia-";`·
`"libnvidia-";`). 접두 매칭이라 `nvidia-container-toolkit`도 함께 제외됨.
**따라서 GPU 스택(드라이버·툴킷) 보안 패치는 이제 이 런북대로 수동 적용**해야 한다.

## ⭐ 기본 경로 — 자동화 (v2.125.68)

운영자 sudo **1줄**로 예약하면 업그레이드 → 재부팅 전 관문 → 재부팅 → 부팅 후 검증·자가 복구까지 돈다.
아래 0단계 이후의 수동 절차는 **부록**(스크립트가 실패했을 때·내부 동작 이해용)이다.

```bash
# 1) 현황 (무중단·sudo 불요) — 재부팅 대기 일수·NVIDIA/커널 대기분·dkms·옛 라이브러리·pm2/docker 수
~/WeRUBLLMManager/scripts/maintenance_window.sh --check

# 2) (선택) 무엇을 할지 미리 보기 — 바꾸는 명령은 출력만
~/WeRUBLLMManager/scripts/maintenance_window.sh --execute --dry-run

# 3) 예약 (sudo) — full=업그레이드+재부팅 / reboot-only=이미 받은 패치만 반영
sudo ~/WeRUBLLMManager/scripts/maintenance_window.sh --schedule "2026-10-08 22:00" --mode full
#    취소: sudo systemctl stop werub-maintenance.timer

# 4) 재부팅 후 판독 (자동 검증은 부팅 90초 뒤 @reboot cron이 돌린다)
tail -25 ~/WeRUBLLMManager/logs/post_reboot_verify.log      # RESULT OK|FAIL
cat ~/WeRUBLLMManager/logs/maintenance_last.json             # 축별 ok/warn/fail
```

**예약 시각에 자동으로 하는 일** (`--execute`):
1. 금지 시간대 거부 — 03:00~04:30(백업 cron 8개·daily-refresh) · 06:00~07:00(unattended-upgrades).
2. `pm2 save` — 재부팅 후 `@reboot pm2 resurrect`가 이 dump로 **Caddy(his-proxy)·cloudflared(his-tunnel)**를 다시 띄운다.
3. 큐 running이 0이 될 때까지 최대 10분 대기(넘으면 기록하고 진행).
4. (full) `apt-get update` → `full-upgrade`(질문 없이·**설정 파일은 기존 유지** — 블랙리스트가 덮이지 않게·dpkg 잠금 600s 대기).
5. **재부팅 전 관문**: 부팅될 커널(=/boot 최신, `GRUB_DEFAULT=0`)에 ①initrd ②**설치된 NVIDIA 유저랜드와 같은 버전의 dkms 모듈** ③블랙리스트 유지 ④디스크 5GB+.
   - 실패 → `dkms autoinstall -k <커널>` 1회 → 재판정 → 그래도 실패면 **재부팅하지 않고 `[CRITICAL]`** 로그(아래 "문제 발생 시").
   - ⚠️ `grub-reboot`로 다른 커널에 비켜 가는 방법은 `GRUB_DEFAULT=0`이라 **동작하지 않는다**(saved일 때만). 쓰지 않는다.
6. 기준값 `logs/maintenance_baseline.json`(기대 커널·드라이버·pm2 목록·docker 수) → journal 1G로 정리 → 재부팅.

**부팅 후 자동 검증** (`post_reboot_verify.sh`, 기준값이 없어도 ④~⑧은 항상):
①admin health ②GPU 적재(size_vram·필요 시 ollama 재시작 1회) ③드라이버 모듈=유저랜드 ④외부 유입(공개 `/api/tags` 200 · `/api/status` **401** — 200이면 인증 우회=FAIL)
⑤pm2 핵심(his-proxy·his-tunnel) — 없으면 **`pm2 resurrect` 1회 자가 복구** ⑥docker restarting ⑦옛 공유 라이브러리 0 ⑧reboot-required 해소 ⑨기준값 대조(커널·드라이버·pm2 누락/중복·docker 수).

**주간 확인 cron** (user crontab, sudo 불요): 재부팅 대기 7일 초과 또는 NVIDIA 대기분이 있으면 `logs/maintenance_window.log`에 `[WARN]` 한 줄(정상이면 아무것도 안 쌓음).
```cron
0 9 * * 1 /home/deploy/WeRUBLLMManager/scripts/maintenance_window.sh --check >/dev/null 2>&1
```

> ⚠️ **재부팅 = 박스 전체 중단**(2026-10-07 실측): docker 54(PACS·HIS·LIS·ERP·edu·twin·sign·cerno·werub)·pm2 22.
> 시각은 사람이 정하고 관련 팀에 공지한다. 업무 외 평일 22:00 전후 권장. `schedule_evening_reboot.sh`는 이제 `--mode reboot-only` 래퍼다.

## 주기(cadence)

- **월 1회 "확인"**: 대기 중인 nvidia 업데이트가 있는지 점검(아래 0단계). 없으면 끝.
- **있으면 유지보수 창에서 적용**: 실제 적용은 대략 분기 1회 정도로 예상. 보안
  패치이므로 대기 중 업데이트를 무기한 미루지 말 것(다음 창에 반영).
- 다른 OS/앱 패키지는 자동 업그레이드가 계속 동작함(nvidia만 제외).

## 부록 — 수동 절차

### 0단계 — 대기 중 업데이트 확인 (언제나·무중단)

blacklist는 *자동 설치*만 막고 `apt list`에는 보인다:

```bash
apt list --upgradable 2>/dev/null | grep -Ei 'nvidia|libnvidia'
```

- 출력 없음 → 조치 불필요. 끝.
- 출력 있음 → 아래 유지보수 창 절차로 적용.

### 유지보수 창 절차 (서비스 중단·재부팅 동반)

```bash
# 1) 진행 중 작업 없는지 확인 (pending/running 0 권장)
curl -s http://localhost:8585/api/queue/status

# 2) GPU 스택 업데이트 (드라이버 + 컨테이너 툴킷)
sudo apt update
sudo apt install --only-upgrade 'nvidia-*' 'libnvidia-*' nvidia-container-toolkit

# 3) DKMS가 (신규 커널 포함) 모든 커널에 nvidia 모듈을 빌드했는지 확인
#    각 설치 커널에 대해 'installed'가 보여야 재부팅 후 GPU 정상 인식
dkms status | grep -i nvidia
ls -1 /boot/vmlinuz-* | sed 's#/boot/vmlinuz-##'

# 4) 재부팅 (드라이버 커널 모듈 정합 — 이 단계가 인시던트의 핵심 교훈)
sudo reboot
```

### 재부팅 후 복귀 검증 (필수)

```bash
# a) 드라이버 = 커널 모듈 버전 일치, GPU 인식
nvidia-smi --query-gpu=driver_version,memory.used,memory.total --format=csv,noheader
cat /proc/driver/nvidia/version | head -1

# b) 서비스·모델이 GPU에 올라왔는지 (size_vram > 0 이어야 함)
systemctl is-active ollama ollama-admin
curl -s http://localhost:11434/api/ps | python3 -c "import sys,json;[print(m['name'],'vram',round(m.get('size_vram',0)/1e9,1),'GB') for m in json.load(sys.stdin).get('models',[])]"

# c) 실제 추론 속도 (14b GPU ≈ 88 tok/s. 2-5 tok/s면 CPU 폴백 = 실패)
curl -s http://localhost:11434/api/generate -d '{"model":"qwen2.5:14b","prompt":"안녕","stream":false}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('tok/s', round(d['eval_count']/(d['eval_duration']/1e9),1))"

# d) health + GPU 상주 비율 (1.0 = 완전 GPU)
curl -s http://localhost:8585/api/health | python3 -c "import sys,json;d=json.load(sys.stdin);p=d['checks']['perf'];print('healthy',d['healthy'],'vram_ratio',p.get('model_vram_ratio'))"

# e) 터널 4종 복귀 — ⚠️ **user 유닛은 linger 없으면 부팅 시 안 뜬다**
for s in ollama-tunnel ollama-worker-bridge prod-stats-tunnel; do echo "$s $(systemctl is-active $s)"; done
echo "his-tunnel(user) $(systemctl --user is-active his-tunnel)"
loginctl show-user weruby -p Linger      # Linger=yes 여야 부팅 시 user 유닛 시작

# f) Docker CUDA (툴킷 업데이트했을 때만)
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi | head -3
```

> ⚠️ **2026-07-29 실사례**: `his-tunnel.service`(9101→HIS/PACS)는 **user 유닛인데 `Linger=no`**
> 였다 — 재부팅했으면 **아무도 SSH 로그인하지 않는 한 계속 죽어 있었을 것**이다.
> 재부팅 전에 `loginctl enable-linger weruby`(sudo 불요)로 해소했고, 부팅 후 4종 전부 active 확인.
> 시스템 유닛(ollama-tunnel·worker-bridge·prod-stats)은 원래 자동 복귀한다.

> 💡 재부팅 사유가 드라이버가 아니라 **`libc6` 등 공용 라이브러리**일 때도 같은 절차를 쓴다.
> 서비스별로 구 라이브러리를 물고 있는지는
> `ls -l /proc/<PID>/map_files | grep -c '(deleted)'`로 확인(루트 프로세스는 sudo 필요).
> `systemctl restart ollama`만으로는 nginx·sshd 등 루트 서비스가 남으므로 **재부팅이 확실**하다.

## 자동 감지 안전망 (v2.124.33)

수동 검증을 놓쳐도, `model_vram_ratio`가 0에 가까우면(CPU 폴백)
`/api/health` `_perf_alerts`가 **critical 경보**를 낸다(대시보드 "GPU 상주" 타일에
`CPU 폴백!` 적색 표시). `nvidia-smi`가 실패해도(드라이버 불일치) 조기 warning.
→ 재부팅 검증 c/d 단계가 이 신호와 일치하는지 재확인.

## 문제 발생 시 (폴백·불일치)

- **드라이버/모듈 불일치**(`nvidia-smi` mismatch): 재부팅으로 커널 모듈 정합. DKMS가
  현재 커널용 모듈을 빌드했으면 재부팅만으로 해소(2026-07-25 사례).
- **재부팅 후에도 GPU 미인식**: `dkms status`에 현재 커널용 nvidia가 없으면
  `sudo dkms autoinstall` 후 재부팅.
- **모델이 CPU 폴백 상태로 로드됨**: `sudo systemctl restart ollama` (또는 재부팅).
- **`maintenance_window.sh`가 `[CRITICAL] 관문 재실패 — 재부팅하지 않음`**: 업그레이드는 끝났는데 부팅될 커널에
  같은 버전 nvidia 모듈이 없다. 이 상태로 두면 **새 모델 적재가 CPU로 떨어진다**(로드된 모듈≠유저랜드).
  `dkms status -m nvidia`·`/var/lib/dkms/nvidia/*/build/make.log` 확인 → 원인 해소 후 `sudo dkms autoinstall -k <커널>`
  → `sudo scripts/maintenance_window.sh --execute --mode reboot-only`(관문을 다시 거친다).
- **`[ABORT] apt-get update 실패`**: 깨진 저장소. 2026-10-07 실사례 = 설치 미디어 줄 `deb … file:///cdrom …`(지금은 주석 처리됨).
  `--check`가 `cdrom 저장소 활성`으로 미리 알린다. `sudo apt-get update` 출력의 `E:` 줄로 원인 저장소를 찾아 주석 처리 → `sudo systemctl reset-failed werub-maintenance-now` 후 재실행.
- **즉시 실행**(예약 대신): `sudo systemctl stop werub-maintenance.timer` → `sudo systemd-run --unit=werub-maintenance-now /bin/bash ~/WeRUBLLMManager/scripts/maintenance_window.sh --execute --mode full`.
  ⚠️ 터미널에서 `--execute`를 바로 돌리지 말 것 — SSH가 끊기면 dpkg가 중간에 멈춘다.
- **검증 `pm2_key` FAIL**(resurrect 후에도 Caddy·cloudflared 없음): 외부 유입 전체 단절. `pm2 logs his-proxy his-tunnel --lines 30`
  → `pm2 start` 개별 기동 → `pm2 save`. `crontab -l | grep @reboot`로 resurrect 줄 확인.

## 관련

- 인시던트·경보 상세: `docs/RELEASE_v2.124.32.md`·`docs/RELEASE_v2.124.33.md`
- 백업된 이전 unattended 설정: `~/50unattended-upgrades.bak-2026-07-25`
- VLM 사이드카(Docker CUDA 활용): `docs/01-plan/features/auxiliary-inference-sidecar.plan.md`
