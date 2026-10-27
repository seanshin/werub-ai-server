# v2.125.68 — 유지보수 창 자동화: 업그레이드·재부팅·부팅 후 검증을 운영자 sudo 1줄로

기준 v2.125.67 · 발견 경위: 2026-10-07 서버 점검(읽기 전용) · **앱 동작 변화 없음**(스크립트·cron·문서만)

## 왜

- 10-07 점검: **재부팅 대기 12일**(커널 -34가 09-25 설치·DKMS 빌드 완료인데 실행은 -31) · libc6(09-12)·openssl(10-01) 패치가
  실행 중 gunicorn에 **미반영**(`/proc/<pid>/maps`에 `(deleted)` 10줄) · 대기 업데이트 90개(커널 -38·NVIDIA 580.178.04·toolkit 1.20.1).
- NVIDIA는 07-25 CPU 폴백 사고 뒤 unattended 블랙리스트라 **수동 적용이 필수**인데, 절차(업그레이드→dkms 확인→재부팅→검증)는 매달 사람이 손으로 했다.
- 재부팅은 **박스 전체 중단**이다(docker 54·pm2 22). 특히 외부 유입 전체인 **Caddy·cloudflared가 systemd가 아니라 pm2**이고
  crontab `@reboot pm2 resurrect`로만 복귀하는데, 그 HIS용 줄은 09-09 이후 **한 번도 실행된 적이 없었다**(`pm2-resurrect.log` 부재).
  기존 `post_reboot_verify.sh`는 GPU만 봤다.

## 변경

1. **`scripts/maintenance_window.sh` 신설**
   - `--check`(무중단·sudo 불요): 재부팅 대기 일수·NVIDIA/커널 대기분·dkms·옛 라이브러리·Linger·@reboot·pm2/docker → `logs/maintenance_check.json`.
     재부팅 대기 >7일 또는 NVIDIA 대기분이 있으면 `[WARN]` 한 줄 + exit 1(정상이면 아무것도 안 쌓음 — 주간 cron용).
   - `--schedule "YYYY-MM-DD HH:MM" --mode full|reboot-only`(sudo): **금지 시간대 거부**(03:00~04:30 백업 cron 8개·06:00~07:00 unattended) →
     `systemd-run --on-calendar`로 `--execute` 예약(중복 예약 거부).
   - `--execute`: `pm2 save` → 큐 idle 대기(최대 10분) → (full) `apt-get full-upgrade`(noninteractive·**confold**·dpkg 잠금 600s) →
     **재부팅 전 관문**(부팅될 커널의 initrd · **설치된 NVIDIA 유저랜드와 같은 버전의 dkms 모듈** · 블랙리스트 유지 · 디스크 5GB+) →
     기준값 `logs/maintenance_baseline.json` → journal 1G → 재부팅.
     관문 실패 → `dkms autoinstall -k` 1회 → 재실패면 **재부팅하지 않고 `[CRITICAL]`**.
   - `--dry-run`: 바꾸는 명령은 출력만(기준값은 `.dry-run` 파일로 — 진짜 기준값을 남기면 다음 계획 밖 재부팅 검증이 엉뚱한 기대값과 대조한다).
2. **`scripts/post_reboot_verify.sh` 확장**(기존 GPU 판정 유지): 드라이버 모듈=유저랜드 · reboot-required 해소 ·
   **pm2 핵심(his-proxy·his-tunnel) 누락 시 `pm2 resurrect` 1회 자가 복구** · 외부 유입(공개 `/api/tags` 200 · `/api/status` **401**, 200이면 인증 우회=FAIL) ·
   옛 공유 라이브러리 0 · docker restarting · 기준값 대조(커널·드라이버·**pm2 누락/중복** — `@reboot` resurrect 줄이 2개라 중복 기동 가능 · docker 수).
   결과 `logs/maintenance_last.json`(축별 ok/warn/fail) + RESULT 한 줄. **기준값이 없어도(정전 등 계획 밖 재부팅) 유입·pm2·라이브러리는 항상 본다.**
   은퇴 유닛(ollama-tunnel·worker-bridge)은 판정에서 뺐다(inactive가 정상).
3. `scripts/schedule_evening_reboot.sh` → `maintenance_window.sh --schedule … --mode reboot-only` 래퍼(이름·사용법 호환).
4. `docs/MAINTENANCE_GPU_DRIVER.md` — 자동화 경로를 기본으로, 수동 절차는 부록으로. `[CRITICAL]`·`pm2_key FAIL` 대응 추가.

## 지시서와 달라진 점(정직 기록)

- 지시서는 관문 실패 시 "`grub-reboot`로 모듈이 있는 다른 커널로 비켜 재부팅"이었으나, 이 박스는 **`GRUB_DEFAULT=0`**(10-07 실측)이라
  `grub-reboot`가 **효과가 없다**(saved일 때만 먹힌다). GRUB 설정을 바꾸는 건 범위 밖이라 **dkms 재빌드 1회 → 재실패면 재부팅 중단 + CRITICAL**로 바꿨다.
  이 경우 로드된 모듈≠유저랜드라 **새 모델 적재가 CPU로 떨어질 수 있다** — 런북 "문제 발생 시"에 운영자 조치를 적었다.
- 알림 채널: 셸에서 쓸 기존 경보 경로가 없어(health_check.sh도 로그만) **로그 + JSON**으로만 남긴다. 새 경보 체계는 만들지 않았다.

## 검증

- 신규 `tests/test_maintenance_window.py` **30건** — 바꾸는 명령(apt-get·systemctl·systemd-run·dkms·journalctl·runuser·sudo)은 **전부 PATH stub이 가로챈다**
  (배포 게이트 pytest에서 진짜가 불리지 않게 — 스크립트에 새 명령을 쓰면 `test_every_mutating_command_is_stubbed`가 잡는다).
  금지 시간대 5 · 예약 생성/형식/중복 · 실행 순서(update→full-upgrade→journal→reboot)·confold · 관문 차단 4종 · dkms 재빌드 복구/재실패 · dry-run 무변경 ·
  check JSON·조용함 · 검증 OK/자가 복구/복구 실패/**빈 pm2 목록=fail**/인증 우회/커널 기대값/모듈≠유저랜드/pm2 중복/기준값 없음.
- 확장한 검증 스크립트 회귀 8건은 **구 `post_reboot_verify.sh`에서 8/8 실패 → 신 8/8 통과**(실측).
- 맥 bash 3.2 호환 확인 중 결함 1건 수정: `"$total개"` — bash 3.2가 한글 바이트를 변수 이름에 붙여 `unbound variable`(→ `${total}개`).

### GPU 서버 실물 검증 (커밋 전·레포 밖 `/tmp/mwtest`·끝나고 삭제)

- `--check`: 실행 -31 / 최신 -34 · **재부팅 대기 12일** · 옛 라이브러리 매핑 65줄 · 대기 89개(NVIDIA 19·커널 3) · Linger yes · @reboot 3줄 · pm2 22 · docker 54 → `[WARN]`+exit 1(의도대로).
- `--execute --dry-run`(weruby·바꾸는 명령 출력만): pm2 save → 큐 0 → apt 2단계 → 관문 통과(-34·580.173.02) → journal → reboot 순서 확인.
- `post_reboot_verify.sh`(sudo·`pm2 resurrect`는 가짜 명령으로 차단, dry-run 기준값으로 대조): 재부팅 전이라 **`kernel_expected`만 fail**(-31 vs 기대 -34)·reboot_required/deleted_libs warn —
  나머지 admin·gpu(size_vram 10.4GB)·driver_match·pm2_key·pm2 22·ingress tags 200·auth 401·docker 54 전부 ok. 기대와 정확히 일치.
- 🔴 **실물 검증이 찾은 결함 1건**: 1차 실행에서 내 가짜 pm2 래퍼가 node 경로를 빠뜨려 `pm2 jlist`가 실패했는데, 검증 스크립트가 **`pm2_status ok "0 online"`**으로 통과시켰다(클래스 #1).
  운영 경로(cron, nvm bin = pm2+node)에선 안 나지만 pm2 데몬 미기동 등에서도 같은 빈 목록이 온다 → **빈 목록=fail**로 고치고 회귀 추가.

## 운영자 실행(이 릴리즈 후)

```bash
~/WeRUBLLMManager/scripts/maintenance_window.sh --check
sudo ~/WeRUBLLMManager/scripts/maintenance_window.sh --schedule "YYYY-MM-DD 22:00" --mode full
```
재부팅 후 판독은 점검 세션에서 `logs/maintenance_last.json`·`logs/post_reboot_verify.log`.

## 후속 — 첫 실전 실행 (2026-10-07 같은 날)

운영자가 22:00 예약을 취소하고 즉시 실행(`systemd-run --unit=werub-maintenance-now … --execute --mode full`).

1. **1차 10:53 — `[ABORT] apt-get update 실패 — 재부팅 안 함`**(안전장치 의도대로·서버 무변경).
   원인: `/etc/apt/sources.list` 1번 줄 **설치 미디어 저장소** `deb [check-date=no] file:///cdrom noble main restricted`
   → `E: The repository 'file:/cdrom noble Release' no longer has a Release file.` 매일 도는 unattended는 이 오류를 무시해 드러나지 않았다.
   운영자가 주석 처리(백업 `.bak-20261007`) 후 재실행.
2. **2차 10:57 — 관문 통과(커널 -38·NVIDIA 580.178.04) → 재부팅 → 11:00 자동 검증 `RESULT OK`**:
   커널 7.0.0-38 · 드라이버 모듈=유저랜드 580.178.04 · qwen2.5:14b VRAM 1.0·**89.7 tok/s** · pm2 22(**HIS용 `@reboot pm2 resurrect` 첫 실행 성공** — Caddy·cloudflared 복귀) ·
   외부 tags 200·status 401 · docker 54 · reboot-required 해소 · 부팅 후 5xx 0 · journal 2.3GB 정리 · 대기 업데이트 90→1(dnsmasq-base·단계 배포).
3. **실전이 드러낸 결함 2건 → 수정**:
   - `deleted_libs` 판정이 **0건을 warn으로** 냈다 — `grep -c … || echo 0`은 0건일 때 "0"을 찍고 종료 코드 1이라 `"0\n0"`이 됐다(두 스크립트 동일). → `awk`로 숫자 하나.
   - `--check`가 cdrom 저장소를 못 잡았다 → 활성 cdrom 줄 감지(`[WARN]`+exit 1) · `--execute` ABORT 메시지에 원인 후보 표시.
   - 회귀 +7(옛 스크립트에서 deleted_libs 4/4 실패) · 테스트가 실제 `/etc/apt`·`/proc`을 보지 않게 고정.
