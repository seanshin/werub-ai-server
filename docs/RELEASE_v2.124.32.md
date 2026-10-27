# v2.124.32 — /api/generate 대형 프롬프트 num_ctx 자동 상향 (조용한 절단 봉합)

2026-07-25 | 계기: 라이브 점검 중 GPU CPU-폴백 인시던트 조사 → 오전 부하 추적서 발견

## 배경

라이브 점검에서 GPU 드라이버 불일치(무인 업그레이드 580.159→580.173, 커널 모듈
미리로드)로 추론이 CPU로 폴백된 것을 발견·재부팅으로 해소(별건). 이 조사 중
"오전에 크게 걸린 작업"을 추적하다 **별개의 상시 결함**을 포착:

- `node` 외부 클라이언트가 `POST /api/generate`로 **8K~17K 토큰 프롬프트**를 반복 전송
  (최소 7/5부터 상시, SSH 터널 직결·응답 160~200KB 대형 생성).
- ollama 로그에 `truncating input prompt limit≈4098 prompt=14706 keep=4` 반복 —
  **프롬프트 중간이 조용히 절단**된 채 답변 생성(에러 없음 → 품질 저하 무증상).
- **근본원인**: `routes/ollama.py`의 `/api/generate` 핸들러가 `{model, prompt, system,
  stream}`만 Ollama로 전달하고 **`options.num_ctx`를 미전달** → 요청이 전역 슬롯
  컨텍스트(`OLLAMA_CONTEXT_LENGTH 8192 ÷ NUM_PARALLEL`)에 묶임. 핸들러 주석이 "호출자
  입력 ~30KB"를 가정했으나 이 클라이언트가 초과. GPU 정상일 때도 발생(속도 아닌
  컨텍스트 크기 문제). 대조군 `/v1`(openai_compat)은 요청별 num_ctx 주입이라 무영향.

## 구현 (`routes/ollama.py`)

- **`_estimate_tokens(text)`** 순수 함수: 토크나이저 없이 보수적 토큰 추정(한글/CJK
  ~1.2 tok/자, 그 외 ~0.32 tok/자). 임계 판정 전용이라 **과소추정(잔존 절단)보다
  과대추정(불필요 상향)을 선호**.
- **`_auto_num_ctx(prompt, system)`** 순수 함수: 추정치가 슬롯(8192)에 근접(×1.1)하면
  `options.num_ctx`를 **고정 상한(기본 16384)으로 상향**. 작은 요청은 `None`=미주입=
  기존 동작 불변.
  - **고정 목표 채택 이유**: 가변값(`min(16384, tokens*1.1)`)은 요청마다 다른 num_ctx로
    모델 리로드를 오실레이션시킴 → 단일 상한 버킷으로 리로드 최소화.
  - config 무재배포 튜닝: `generate_auto_num_ctx`(기본 true)·`generate_max_num_ctx`
    (기본 16384). 상한이 슬롯 이하면 자동 비활성. `_load_config` 예외/불량값 graceful.
- 핸들러 주입: system 프롬프트 확정 후 `nc = _auto_num_ctx(...)`; `nc`면
  `payload["options"] = {"num_ctx": nc}`.

## VRAM·트레이드오프

- 16384는 `/v1`(openai_compat) 경로에서 검증된 VRAM-안전 값(KV = num_ctx×NUM_PARALLEL).
  14b@16384×2 ≈ 12GB < 16GB.
- ⚠️ 큰 프롬프트 첫 요청 시 컨텍스트 변경으로 **모델 1회 리로드**(고정 목표라 이후 재사용).
- ⚠️ **상한(16384) 초과 초대형(예: 16.9K tok)은 상한까지만** = 여전히 소폭 클립. cap
  상향은 VRAM 여유 확인 후 별도 결정.

## 검증

- 순수 로직 8개 단언 통과(로컬 독립): 14,706토큰급 → num_ctx 16384 상향 확인.
- 회귀 `tests/test_generate_num_ctx.py` **8**(추정 한글>영문·소형 미주입·대형 상향·
  system 합산·config 비활성/커스텀 캡/슬롯이하/불량값 graceful) — GPU venv 실행.
- 배포: GPU pull → import dry-run → graceful reload(NRestarts 0) → 라이브
  대형 프롬프트 `/api/generate`서 `truncating` 미발생 확인.

## 부수 기록

- **GPU 드라이버 CPU-폴백 인시던트(별건, 코드 무관)**: 무인 드라이버 업그레이드가 커널
  모듈 리로드 없이 진행 → `nvidia-smi` mismatch → ollama CPU 폴백(14b ~2-5 tok/s).
  `/api/health`는 여전히 healthy 보고(GPU 사용 여부 미검증)가 함정. 재부팅(새 커널
  7.0.0-28·매칭 DKMS 580.173.02)으로 해소·88.5 tok/s 복귀. 재발 방지 후보(미조치):
  unattended-upgrades nvidia hold, 또는 health perf에 GPU 사용(size_vram) 축 합류.
