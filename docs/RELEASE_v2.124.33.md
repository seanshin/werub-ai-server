# v2.124.33 — GPU CPU-폴백 health 경보 + 대형 프롬프트 cap 상향 (v2.124.32 잔여 2건)

2026-07-25 | 계기: v2.124.32 잔여 2건 해결안 도출 → 운영자 "전부" 지시

v2.124.32(대형 프롬프트 num_ctx 자동 상향)에서 남긴 두 잔여 사안을 처리.

## 사안1 — 16,384 토큰 초과 프롬프트 클립 → cap 상향 (config)

- **실측**: GPU 16,303 MiB 중 8192/슬롯 로드 시 5,499 MiB 여유. 컨텍스트 슬롯당
  +8,192마다 KV ~+1.5GB. 오전 관측 최대 프롬프트 16,904 tok.
- **조치**: config `generate_max_num_ctx` **16384 → 20480**(GPU config.json, 무재배포).
  20480/슬롯 로드 시 ~12.7GB 사용·~3.6GB 여유 = VRAM-안전. 관측 최대치를 여유 있게 커버.
  코드 기본값(`_GEN_MAX_NUM_CTX_DEFAULT`)은 16384 유지(보수적·이 박스만 config 오버라이드).
- ⚠️ 20480 **초과** 초대형은 여전히 상한까지만 = 소폭 클립. 근본 해법은 그 node 클라이언트가
  프롬프트를 줄이거나 `/v1`(32768·7b)로 이전(외부 팀 조율·별건).

## 사안2 — GPU 드라이버 CPU-폴백 재발 방지 (예방 + 탐지)

### 2b. 탐지 — CPU-폴백 health 경보 (코드, 본 릴리즈)

07-25 인시던트("health는 healthy인데 추론이 CPU"의 사각)를 봉합. GPU 사용 여부를 명시 판정:

- `app._sample_ps_models()` + **`_min_vram_ratio(models)`**(순수): `/api/ps`의
  `min(size_vram/size)` — 완전 GPU=1.0·부분 오프로드=(0,1)·CPU 폴백=0.0·불명=None.
  `_collect_stats`가 stats_history 신규 컬럼 **`model_vram_ratio`**(REAL·마이그레이션)에
  박제(/api/ps 1회로 resident 이름 + 비율 동시 산출).
- `routes/system._gpu_fallback_finding(resident, ratio, vram_used, ratio_min)`(순수):
  - ratio≈0 → **critical**("상주 모델이 CPU에 있음 — 드라이버/VRAM 확인").
  - 0<ratio<ratio_min(기본 0.5) → **warning**(부분 오프로드=CPU 스필로 느려짐).
  - nvidia-smi 불명(vram_used 없음)인데 모델 상주 → **warning**(드라이버 불일치 의심,
    **폴백 발생 전 조기 신호**).
  - 정상 GPU·모델 미상주·nvidia-smi 정상+비율만 불명 → None(오탐 방지).
- `_perf_alerts`가 최신 스냅샷 비율 읽어 판정·경보(쿨다운 6h)·`/api/health` perf에
  `model_vram_ratio` 노출. config `monitoring_thresholds.gpu_fallback_min_vram_ratio`(무재배포).
- **효과**: 이번 인시던트라면 수집 주기(5분) 내 자동 경보 — 수동 점검 불필요.
- 회귀 `tests/test_gpu_fallback_alert.py` **11**(비율 산출 4·판정 7).

### 2a. 예방 — unattended-upgrades nvidia 제외 (운영자 sudo, 별도 실행)

근본원인(무인 드라이버 업그레이드가 커널 모듈 리로드 없이 진행) 차단. 자동 업그레이드만
막고 유지보수 창의 수동 `apt upgrade`는 허용(그때 재부팅/모듈 리로드까지 통제):

```
# /etc/apt/apt.conf.d/50unattended-upgrades 의 Package-Blacklist 블록에 추가:
    "nvidia-";
    "libnvidia-";
```
- `apt-mark hold`보다 우위(hold는 수동 업그레이드도 차단). 트레이드오프=nvidia 보안 패치가
  수동 창으로 연기(단일 GPU 추론 박스·터널 뒤라 수용 가능).
- sudo 필요 → 운영자 실행(본 릴리즈 코드와 무관·백로그).

## 검증

- 순수 로직 로컬 통과(비율 4 + 판정 7). 전체 pytest는 GPU venv.
- 배포: GPU pull → 마이그레이션(ALTER model_vram_ratio) → import dry-run →
  graceful reload(NRestarts 0) → stats_history 컬럼·비율 채워짐 확인·health perf 노출.
- 사안1-A config는 배포 전 라이브 반영·검증 완료(9000자 한글 → num_ctx 20480).
