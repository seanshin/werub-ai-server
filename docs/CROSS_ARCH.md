# Cross-Architecture 관리 가이드 (단일 레포 · 다중 머신)

이 레포 하나가 **서로 다른 CPU/가속기 머신**에서 동시에 운영된다.
아키텍처 차이로 인한 크래시·드리프트를 막기 위한 **지속 관리 규칙**을 정의한다.

## 1. 실행 대상 머신

| 머신 | 아키텍처 | 가속기 | OS | 진입점 | 역할 |
|---|---|---|---|---|---|
| Ubuntu 서버 | x86_64 | NVIDIA CUDA (RTX 5080) | Linux | `app.py` | 메인 서버 |
| iMac M1 | arm64 | Apple Metal (MPS) | Darwin | `worker_app.py` | Apple Silicon 워커 :8587 |
| (폴백) | any | 없음 | any | — | CPU |

## 2. 핵심 규칙 — device는 절대 하드코딩 금지

**모든 가속기 선택은 `core/device.py`를 경유한다.** 코드에 `"cuda"` / `"mps"`를
직접 박지 말 것. CUDA 없는 머신에서 `"cuda"`를 그대로 쓰면 모델 로드/추론 시 **즉시 크래시**한다.

```python
from core import device as device_util

dev = device_util.resolve("auto")        # "cuda" > "mps" > "cpu" 자동 (미가용 시 폴백)
dev = device_util.resolve(cfg.get("..._device", "auto"))  # config override 허용
tensor.to(dev)                            # torch
EncoderClassifier.from_hparams(..., run_opts={"device": dev})  # speechbrain

# HuggingFace transformers pipeline은 정수/문자열 인자가 필요:
pipe = pipeline("image-classification", model=m,
                device=device_util.pipeline_arg(dev))   # cuda→0, mps→"mps", cpu→-1
```

### 예외: faster-whisper (CTranslate2)
CTranslate2는 **MPS를 지원하지 않는다**(CUDA/CPU만). 따라서 `core.device.resolve()`의
`mps` 결과를 그대로 넘기면 안 된다. `core/meeting_stt.py`는 `_resolve_device_compute()`로
cuda↔cpu만 매핑하고, CPU에서 `float16`이 크래시하므로 `int8`로 폴백한다.
iMac STT는 faster-whisper 대신 **mlx-whisper**(별도 워커, PoC 완료)를 사용한다.

## 3. 머신별 설정 분리 (config.json / 환경변수)

`config.json`은 `.gitignore` 대상 → **머신마다 독립 보관**이 정상이다(코드는 공유, 설정만 분기).
새 device 관련 키를 추가하면 **양쪽 머신에 수동 반영**하고 아래 표를 갱신한다.

| 키 / ENV | 모듈 | Ubuntu 권장 | iMac 권장 | 기본값 |
|---|---|---|---|---|
| `ai_detector_device` (config) | `core/ai_image_detector.py` | `cpu` 또는 `cuda` | `mps` | `cpu` |
| `DETECTOR_DEVICE` (env) | `worker_app.py` | — | `mps` | `mps` |
| `WHISPER_DEVICE` (env) | `core/meeting_stt.py` | 미설정(auto→cuda) | 미설정(auto→cpu) | auto |
| `WHISPER_COMPUTE` (env) | `core/meeting_stt.py` | 미설정(→float16) | 미설정(→int8) | device 연동 |
| (자동) | `core/meeting_diarize.py` | auto→cuda | auto→mps | auto |

> `*_device` 값은 모두 `core.device.resolve()`를 통과하므로 미가용 시 안전하게 cpu로 폴백한다.
> 따라서 "잘못 설정해도 크래시 대신 느려질 뿐"이 보장된다.

## 4. 의존성 분리

| 파일 | 대상 | 비고 |
|---|---|---|
| `requirements.txt` | Ubuntu (x86_64+CUDA) | torch+cu, faster-whisper, speechbrain |
| `requirements-mac.txt` | iMac (arm64+Metal) | mps torch, mlx-whisper |

가능하면 환경 마커 사용: `pkg; platform_machine=="x86_64"`.

## 5. 진단 — 이 머신이 뭘로 인식됐나

```python
from core import device
device.info()
# {"os": "Darwin", "arch": "arm64", "is_arm": True,
#  "cuda_available": False, "mps_available": True, "auto_device": "mps"}
```
- 런타임 환경 엔드포인트: `routes/activity.py` (`os`/`arch`/`is_arm`/`gpu_type`)

## 6. 새 ML 모듈 추가 시 체크리스트

- [ ] device를 `core.device.resolve()`로 선택 (하드코딩 금지)
- [ ] transformers면 `device.pipeline_arg()` 사용
- [ ] MPS 미지원 백엔드(CTranslate2 등)인지 확인 → 별도 cuda/cpu 매핑
- [ ] config/ENV로 머신별 override 노출, 위 §3 표 갱신
- [ ] 가용성 mock 단위테스트 추가 (예: `tests/test_device.py`)
- [ ] `requirements*.txt` 분리 반영
- [ ] docstring에 `[[device]]` 마킹

## 7. 관련 파일

- `core/device.py` — 단일 진실 공급원
- `core/ai_image_detector.py`, `core/meeting_diarize.py`, `core/meeting_stt.py` — 적용 사례
- `core/apple_worker.py` — GPU→iMac 위임(Darwin 가드)
- `tests/test_device.py` — 가용성 조합 검증
