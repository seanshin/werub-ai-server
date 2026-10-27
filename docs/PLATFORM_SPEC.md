# 플랫폼 명세 — iMac M칩 vs GPU 서버

> 단일 소스코드(git)로 iMac과 GPU 서버를 동시 운영한다.  
> `core/platform.py`가 런타임에 플랫폼을 자동 감지하여 기능을 분기한다.

---

## 플랫폼 감지 로직

```
환경변수 PLATFORM_OVERRIDE 있으면 우선 사용 (테스트용)
    ↓
sys.platform == "darwin"  →  IMAC
    ↓
nvidia-smi 응답 성공       →  GPU
    ↓
그 외                      →  CPU
```

| 플랫폼 | 감지 조건 | 레이블 |
|--------|-----------|--------|
| `imac` | macOS (darwin) | iMac (Apple Silicon) |
| `gpu`  | Linux + nvidia-smi | GPU Server (NVIDIA) |
| `cpu`  | Linux, GPU 없음 | CPU Server |

---

## 기능 플래그 (Feature Flags)

### iMac 전용

| 기능 | 설명 | 모듈 | 엔드포인트 |
|------|------|------|-----------|
| `apple_silicon_monitor` | M칩 CPU/GPU/ANE/메모리 실시간 모니터 | `core/apple_silicon.py` | `GET /api/system/apple-silicon` |
| `coreml_embed` | Core ML ANE 임베딩 (bge-m3) | `core/coreml_embed.py` | `POST /api/embed/coreml` |
| `smart_gateway` | 요청 복잡도 분류 → 로컬/GPU 자동 라우팅 | `routes/gateway.py` | `POST /api/gateway/chat` |
| `ane_stt` | faster-whisper + Metal (ANE) STT | `core/apple_silicon.py` | Meeting STT 연동 |
| `model_cache_manager` | Unified Memory keep-alive 관리 | `routes/system.py` | `POST /api/models/warm` |

### GPU 서버 전용

| 기능 | 설명 | 비고 |
|------|------|------|
| `image_gen` | ComfyUI 이미지 생성 | CUDA 필요 |
| `gpu_training` | CUDA Fine-tuning (unsloth) | RTX 5080 |
| `drug_pipeline_gpu` | Drug Batch GPU 스케줄러 | |
| `medical_training` | Medical Training Blueprint | GPU 전용 |

### 공통 (iMac + GPU + CPU)

| 기능 | 엔드포인트 |
|------|-----------|
| `queue` | `/api/queue/*` |
| `rag` | `/api/rag/*` |
| `chat` / `generate` | `/api/chat`, `/api/generate` |
| `korean` / `writing` | `/api/korean/*`, `/api/writing/*` |
| `search` | `/api/search/*` |
| `orchestrator` | `/api/orchestrator/*` |
| `medical_ai` | `/api/medical/*` (경량 모델) |
| `mcp` | `/mcp` |

---

## iMac 전용 기능 상세

### 1. Apple Silicon 실시간 모니터

```bash
GET /api/system/apple-silicon
```

```json
{
  "chip": {"name": "Apple M1", "cores_cpu": 8, "cores_e": 4, "cores_p": 4,
           "cores_gpu": 8, "memory_gb": 16, "ane_tops": 11},
  "cpu":  {"total": 14.5, "p_cores_avg": 18.7, "e_cores_avg": 10.2, "per_core": [...]},
  "gpu":  {"available": true, "renderer_util": 45, "tiler_util": 12, "device_util": 45},
  "memory": {"total_gb": 16, "used_gb": 13.9, "percent": 87, "pressure": "warning",
             "wired_gb": 10.5, "compressed_gb": 0.8},
  "thermal": {"level": 0, "label": "Normal", "throttling": false},
  "powermetrics": {"available": false}
}
```

> `powermetrics`로 GPU/ANE 전력까지 보려면 sudoers 설정 필요:
> ```
> echo "$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/powermetrics" | sudo tee /etc/sudoers.d/powermetrics
> ```

---

### 2. Smart Gateway

iMac이 GPU 서버의 프론트엔드 역할. 요청을 분류하여 자동 라우팅.

```
클라이언트 → iMac Gateway
               ├── 단순 질의 (< 50자, 일상어) → 로컬 Ollama (qwen2.5:1.5b)
               └── 복잡 요청 (의학, 코드, 긴 문서) → GPU 서버 포워딩
```

**설정:**
```bash
# GPU 서버 연결 설정
curl -X POST http://localhost:8585/api/gateway/config \
  -H "Content-Type: application/json" \
  -d '{
    "gpu_base": "http://192.0.2.108:8585",
    "gpu_api_key": "CHANGE_ME",
    "local_model": "qwen2.5:7b",
    "threshold": 0.6
  }'
```

**사용:**
```bash
# 자동 라우팅 채팅
curl -X POST http://localhost:8585/api/gateway/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "안녕하세요"}'
# → routed_to: "local"

curl -X POST http://localhost:8585/api/gateway/chat \
  -d '{"message": "당뇨병 환자의 인슐린 용량 계산 방법을 설명해줘"}'
# → routed_to: "gpu"

# 라우팅 결과만 확인 (LLM 호출 없음)
curl -X POST http://localhost:8585/api/gateway/route \
  -d '{"message": "코드 알고리즘 설명해줘"}'
```

---

### 3. Core ML ANE 임베딩

```bash
# 1. coremltools 설치
pip install coremltools sentence-transformers

# 2. 모델 변환 (최초 1회, 약 5-10분 소요)
curl -X POST http://localhost:8585/api/embed/coreml/convert \
  -d '{"model": "BAAI/bge-m3", "compute_units": "ALL"}'

# 3. 변환 상태 확인
curl http://localhost:8585/api/embed/coreml/status

# 4. 임베딩 생성 (ANE 가속)
curl -X POST http://localhost:8585/api/embed/coreml \
  -H "Content-Type: application/json" \
  -d '{"input": ["안녕하세요", "Hello world"], "normalize": true}'
```

응답: `{"embeddings": [[...], [...]], "engine": "coreml_ane", "duration_ms": 45}`

---

### 4. Unified Memory 스마트 캐싱

M1의 통합 메모리 특성을 활용하여 자주 쓰는 모델을 메모리에 유지.

```bash
# 모델 예열 (메모리에 로드)
curl -X POST http://localhost:8585/api/models/warm \
  -d '{"model": "qwen2.5:7b", "keep_alive": "2h"}'

# 모델 즉시 해제 (메모리 확보)
curl -X POST http://localhost:8585/api/models/unload \
  -d '{"model": "qwen2.5:14b"}'

# 현재 로드 상태 확인
curl http://localhost:8585/api/models/cache-status
```

> **전략:** 16GB 통합 메모리에서 7b(4.7GB) + bge-m3(1.2GB) 동시 유지 가능.  
> 14b(9GB)는 단독으로 유지하거나, 작업 후 즉시 해제.

---

## 아키텍처 다이어그램

```
외부 클라이언트
      │
      ▼
┌─────────────────────────────────────┐
│  iMac M1 (localhost:8585)           │
│                                     │
│  Smart Gateway                      │
│  ├── 단순/경량 → 로컬 Ollama        │
│  │     ├── qwen2.5:7b               │
│  │     └── qwen2.5:1.5b (분류기)    │
│  │                                  │
│  ├── 임베딩 → Core ML ANE           │
│  │     └── bge-m3.mlpackage         │
│  │                                  │
│  ├── STT → Metal (faster-whisper)   │
│  │                                  │
│  └── 복잡/대형 → GPU 서버 포워딩   │
│                       │             │
└───────────────────────┼─────────────┘
                        │
                        ▼
          ┌─────────────────────────┐
          │  Ubuntu GPU (RTX 5080)  │
          │                         │
          │  qwen3:8b / 14b / 32b   │
          │  ComfyUI (이미지)       │
          │  Medical / HR / Game    │
          │  Fine-tuning            │
          └─────────────────────────┘
```

---

## 소스코드 분기 패턴

```python
# 플랫폼별 기능 분기 — 코드에서 사용하는 방법
from core.platform import is_imac, is_gpu, feature

# 방법 1: 플랫폼 직접 확인
if is_imac():
    from core.apple_silicon import get_all_metrics
    metrics = get_all_metrics()

# 방법 2: 기능 플래그 확인 (권장)
if feature("smart_gateway"):
    app.register_blueprint(gateway_bp)

if feature("image_gen"):
    app.register_blueprint(comfyui_bp)

# 방법 3: API 엔드포인트 내 가드
@system_bp.route("/api/system/apple-silicon")
def api_apple_silicon():
    if not is_imac():
        return jsonify({"error": "Apple Silicon 전용"}), 400
    ...
```

---

## 환경변수

| 변수 | 설명 | 예시 |
|------|------|------|
| `PLATFORM_OVERRIDE` | 플랫폼 강제 지정 (테스트용) | `imac`, `gpu`, `cpu` |
| `BATCH_MODE` | 배치 전용 모드 | `1` |
| `GPU_BASE` | GPU 서버 URL (gateway용) | `http://192.0.2.108:8585` |

---

## 플랫폼 API

```bash
# 현재 플랫폼 및 활성 기능 목록
curl http://localhost:8585/api/system/platform
```

```json
{
  "platform": "imac",
  "label": "iMac (Apple Silicon)",
  "features": {
    "apple_silicon_monitor": true,
    "coreml_embed": true,
    "smart_gateway": true,
    "ane_stt": true,
    "model_cache_manager": true,
    "image_gen": false,
    "gpu_training": false,
    "queue": true,
    "rag": true,
    ...
  }
}
```
