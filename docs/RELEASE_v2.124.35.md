# v2.124.35 — GPU 드라이버 유지보수 런북 + 운영 하드닝 (ops, 코드 변경 0)

2026-07-25 | 계기: 07-25 세션의 운영 조치(드라이버 인시던트 후속·보안 잔여·VLM 인프라)
정본화 + 주기 유지보수 절차 기록

VERSION 상수만 갱신(코드 로직 변경 0). 아래는 이 세션에서 **운영자가 서버에서 직접
실행**한 조치와, 그에 따라 남긴 문서.

## 1. GPU 드라이버 유지보수 런북 (신규 문서)

`docs/MAINTENANCE_GPU_DRIVER.md`. 무인 업그레이드가 nvidia를 커널 모듈 리로드 없이
올려 CPU 폴백을 유발한 인시던트(v2.124.32/33 참조) 이후, GPU 스택을 **수동 관리**로
전환하며 절차를 고정:

- **주기**: 월 1회 `apt list --upgradable 2>/dev/null | grep -Ei 'nvidia|libnvidia'`로
  대기분 확인 → 있으면 유지보수 창서 적용(실제 적용 ≈ 분기 1회 예상). 보안 패치라
  대기분 무기한 보류 금지.
- **적용**: `apt install --only-upgrade nvidia-*/libnvidia-*/nvidia-container-toolkit`
  → `dkms status` 확인 → `reboot`.
- **재부팅 후 검증**: nvidia-smi 버전 일치·모델 `size_vram>0`·14b ~88 tok/s·health·
  `model_vram_ratio` 1.0.
- **안전망**: v2.124.33 `model_vram_ratio` 경보가 CPU 폴백/드라이버 불일치를 자동 감지.

## 2. 운영 하드닝 (운영자 sudo 실행분 — 코드 무관)

- **unattended-upgrades nvidia 제외**: `50unattended-upgrades` Package-Blacklist에
  `"nvidia-";`·`"libnvidia-";` 추가 → 무인 드라이버 업그레이드 차단(인시던트 근본원인
  예방). 접두 매칭이라 `nvidia-container-toolkit`도 함께 제외(수동 창서 갱신).
  백업 `~/50unattended-upgrades.bak-2026-07-25`.
- **보안 표면 축소**: `xrdp`·`xrdp-sesman` `disable --now`(3389 리스너 제거) +
  포트 80 레거시 `dataset.weruby.co.kr` nginx 사이트 제거(정적·민감 0). 포트 80 자체는
  cert 갱신용 `default` 블록만 유지.
- **Docker CUDA 해금**: `nvidia-container-toolkit` 설치 +
  `nvidia-ctk runtime configure --runtime=docker` → 컨테이너서 RTX 5080(CUDA 13.0)
  인식 검증. VLM 보조추론 사이드카(`docs/01-plan/features/auxiliary-inference-sidecar.plan.md`)
  의 P0-b 인프라 벽(toolkit 미설치) 해소. ⚠️16GB VRAM 천장은 그대로(실용 사이드카는
  상주 VRAM mutex 또는 24GB+ GPU 필요).

## 3. 07-25 세션 릴리즈 계보

- v2.124.32 — /api/generate 대형 프롬프트 num_ctx 자동 상향(조용한 절단 봉합).
- v2.124.33 — GPU CPU-폴백 health 경보(`model_vram_ratio`) + cap 20480.
- v2.124.34 — 대시보드 GPU 상주 비율 타일.
- **v2.124.35 — 유지보수 런북 + 운영 하드닝 정본화(본 릴리즈)**.

## 검증

- 점검(07-25): 대기 nvidia 업데이트 0(이미 580.173.02 최신)·드라이버=커널 모듈 일치·
  모델 GPU 상주(10.5GB)·health True·vram_ratio 1.0.
