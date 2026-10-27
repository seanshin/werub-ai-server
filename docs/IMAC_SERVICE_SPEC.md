# iMac M칩 서비스 명세 — GPU 서버 호출 가이드

> GPU 서버(Ubuntu RTX 5080)가 iMac M1의 특화 기능을 원격으로 호출하기 위한 명세.  
> 접속 경로: SSH 리버스 터널 (`iMac → Ubuntu`)  
> iMac Admin API: `http://localhost:8586` (GPU 서버 기준)  
> iMac → GPU 역방향: `http://localhost:11435` (iMac 기준, GPU ollama-admin)

---

## 접속 구조

```
iMac (macOS, M1)
  └── SSH 터널 유지 (autossh → deploy@192.0.2.108:819)
        ├── -R 127.0.0.1:8586:127.0.0.1:8585   # GPU쪽 :8586 → iMac Admin API
        └── -L 127.0.0.1:11435:127.0.0.1:8585  # iMac쪽 :11435 → GPU ollama-admin

GPU 서버 (Ubuntu 192.0.2.108)
  ├── localhost:8585  — GPU 자체 ollama-admin (RTX 5080, 34개 모델)
  └── localhost:8586  ──[iMac 리버스 터널]──→  iMac localhost:8585 (Admin API)

nginx (example.org)
  ├── /ollama/      → GPU localhost:8585 (GPU ollama-admin)
  └── /ollama-mac/  → GPU localhost:8586 → iMac Admin API
```

터널 상태 확인:
```bash
# GPU 서버에서
curl -s http://localhost:8585/api/system/platform | python3 -m json.tool
# "platform": "imac" 이면 iMac 연결 정상
```

---

## 인증

iMac은 SSH 터널 경유 시 `localhost`로 인식 → `local_bypass=true` 적용 → **API 키 불필요**.  
외부(HTTPS)로 직접 호출 시 Bearer 토큰 필요.

```python
# 터널 경유 (인증 불필요)
headers = {"Content-Type": "application/json"}

# 직접 호출 시
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-imac-xxxx"
}
```

---

## 서비스 목록

| # | 서비스 | 엔드포인트 | 설명 | GPU 서버 활용 |
|---|--------|-----------|------|--------------|
| 1 | **임베딩 (Ollama)** | `POST /api/embeddings` | bge-m3 Ollama 임베딩 | RAG, 벡터 검색 |
| 2 | **임베딩 (Core ML ANE)** | `POST /api/embed/coreml` | ANE 가속 임베딩 | 고속 RAG |
| 3 | **STT (Metal)** | `POST /api/stt` | faster-whisper + Metal | 회의 녹취 오프로드 |
| 4 | **경량 추론** | `POST /api/gateway/chat` | 스마트 라우팅 채팅 | GPU 부하 분산 |
| 5 | **경량 추론 (직접)** | `POST /api/chat` (8586) | iMac Ollama 직접 호출 | 1.5b~7b 모델 |
| 6 | **플랫폼 상태** | `GET /api/system/platform` | 기능 플래그 확인 | 헬스체크 |
| 7 | **M칩 메트릭** | `GET /api/system/apple-silicon` | CPU/GPU/ANE/메모리 | 모니터링 |
| 8 | **모델 캐시** | `GET /api/models/cache-status` | 로드된 모델 목록 | 라우팅 판단 |
| 9 | **모델 예열** | `POST /api/models/warm` | 모델 메모리 유지 | 사전 예열 |

---

## 1. 임베딩 (Ollama bge-m3)

```bash
curl -X POST http://localhost:8585/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3",
    "input": ["텍스트1", "텍스트2"]
  }'
```

```json
{
  "embeddings": [[0.123, -0.456, ...], [...]],
  "model": "bge-m3"
}
```

**GPU 서버 활용:** ChromaDB 색인/검색 시 임베딩 생성을 iMac에 위임 → GPU VRAM 절약.

---

## 2. 임베딩 (Core ML ANE) — 고속

> 사전 조건: `pip install coremltools` 후 모델 변환 완료

```bash
curl -X POST http://localhost:8585/api/embed/coreml \
  -H "Content-Type: application/json" \
  -d '{
    "input": ["환자 주호소: 흉통, 호흡곤란", "당뇨병 치료 지침"],
    "model": "BAAI/bge-m3",
    "normalize": true
  }'
```

```json
{
  "embeddings": [[...], [...]],
  "model": "BAAI/bge-m3",
  "count": 2,
  "dim": 1024,
  "duration_ms": 45,
  "engine": "coreml_ane"
}
```

**GPU 서버 활용:** Ollama 임베딩 대비 3~5배 빠름. Drug Pipeline, Medical RAG 색인에 사용.

---

## 3. STT — 음성 → 텍스트 (Metal 가속)

```bash
# 파일 직접 업로드
curl -X POST http://localhost:8585/api/stt \
  -F "file=@recording.mp3" \
  -F "language=ko" \
  -F "model=base"

# URL 방식 (S3/Wasabi)
curl -X POST http://localhost:8585/api/stt \
  -H "Content-Type: application/json" \
  -d '{
    "file_url": "https://s3.wasabisys.com/.../recording.mp4",
    "language": "ko",
    "model": "medium",
    "diarize": false
  }'
```

```json
{
  "text": "전체 전사 텍스트",
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "안녕하세요"},
    {"start": 3.5, "end": 7.1, "text": "회의를 시작하겠습니다"}
  ],
  "language": "ko",
  "duration_sec": 1842,
  "model": "medium",
  "engine": "faster-whisper-metal",
  "duration_ms": 38000
}
```

**GPU 서버 활용:** Meeting Analysis STT를 iMac ANE로 오프로드 → GPU 서버 RTX 5080 절약.  
`meeting.py`에서 `file_url` 수신 후 iMac STT API 호출로 대체 가능.

---

## 4. 경량 추론 — Smart Gateway

```bash
curl -X POST http://localhost:8585/api/gateway/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "이 약물의 부작용은?",
    "system": "You are a medical assistant.",
    "force_local": true
  }'
```

```json
{
  "message": "응답 텍스트",
  "model": "qwen2.5:7b",
  "routed_to": "local",
  "confidence": 0.8,
  "duration_ms": 2100
}
```

---

## 5. 경량 추론 — iMac Ollama 직접 (8586)

```bash
# Ollama Chat
curl -X POST http://localhost:8586/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "요약해줘"}],
    "stream": false
  }'

# Ollama Generate
curl -X POST http://localhost:8586/api/generate \
  -d '{"model": "qwen2.5:1.5b", "prompt": "번역: Hello", "stream": false}'
```

**GPU 서버 활용 시나리오:**
- GPU 서버 큐 적체 시 → iMac으로 경량 모델 작업 오프로드
- 현재 `app.py`의 `LIGHTWEIGHT_MODELS` 라우팅 로직 확장 가능

---

## 6. 플랫폼 상태 / 헬스체크

```bash
# iMac 연결 + 기능 확인
curl -s http://localhost:8585/api/system/platform

# iMac 큐 상태
curl -s http://localhost:8585/api/queue/status

# M칩 리소스 상태
curl -s http://localhost:8585/api/system/apple-silicon
```

```json
// /api/system/apple-silicon 응답
{
  "chip": {"name": "Apple M1", "cores_gpu": 8, "ane_tops": 11, "memory_gb": 16},
  "cpu": {"total": 14.5, "p_cores_avg": 18.7, "e_cores_avg": 10.2},
  "memory": {"used_gb": 13.9, "total_gb": 16.0, "pressure": "warning"},
  "thermal": {"label": "Normal", "throttling": false}
}
```

**GPU 서버 활용:** iMac 메모리 압박/과열 감지 → 오프로드 중단 판단.

---

## 7. 모델 캐시 제어

```bash
# 현재 로드 상태 확인
curl http://localhost:8585/api/models/cache-status

# 특정 모델 예열 요청 (GPU 서버가 iMac에 요청)
curl -X POST http://localhost:8585/api/models/warm \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5:7b", "keep_alive": "2h"}'

# 사용 후 메모리 해제 요청
curl -X POST http://localhost:8585/api/models/unload \
  -d '{"model": "qwen2.5:7b"}'
```

---

## GPU 서버 → iMac 라우팅 판단 로직

```python
# GPU 서버의 큐 워커에서 iMac 오프로드 판단
def should_offload_to_imac(job: dict) -> bool:
    """iMac으로 오프로드할지 판단"""
    model = job.get("model", "")
    token_estimate = len(job.get("prompt", "")) // 4

    # 경량 모델이고 iMac이 사용 가능하면 오프로드
    if model in LIGHTWEIGHT_MODELS and _is_imac_available():
        # iMac 메모리 압박 확인
        try:
            r = requests.get("http://localhost:8585/api/system/apple-silicon", timeout=3)
            mem = r.json().get("memory", {})
            if mem.get("pressure") == "critical":
                return False  # 메모리 압박 시 오프로드 중단
        except Exception:
            pass
        return True
    return False
```

---

## Python SDK (GPU 서버용)

```bash
# GPU 서버에서
from client.imac_client import iMacClient

client = iMacClient("http://localhost:8585", ollama_base="http://localhost:8586")

# 연결 확인
assert client.is_available(), "iMac 터널 연결 실패"

# 임베딩
vectors = client.embed(["텍스트1", "텍스트2"])

# STT
transcript = client.stt("recording.mp4", language="ko")

# 경량 추론
response = client.chat("요약해줘", model="qwen2.5:7b")

# M칩 상태
metrics = client.apple_silicon_metrics()
if metrics["memory"]["pressure"] == "critical":
    print("iMac 메모리 부족 — 오프로드 중단")
```

---

## 오류 처리 패턴

```python
from client.imac_client import iMacClient, iMacUnavailable

try:
    client = iMacClient("http://localhost:8585")
    result = client.embed(texts)
except iMacUnavailable:
    # iMac 연결 불가 → GPU 서버 자체 처리로 폴백
    result = local_embed(texts)
except Exception as e:
    logger.error("iMac 서비스 오류: %s", e)
    result = local_embed(texts)
```

---

## 권장 사용 패턴 (GPU 서버 기준)

| 상황 | 권장 동작 |
|------|-----------|
| 임베딩 요청 | iMac Core ML ANE → 실패 시 iMac Ollama → 실패 시 GPU Ollama |
| 경량 모델 추론 (≤7b) | iMac Ollama → 실패 시 GPU 로컬 |
| 대형 모델 추론 (≥14b) | GPU 로컬 (iMac 미사용) |
| 이미지 생성 | GPU 로컬 (iMac 미지원) |
| STT (회의 녹취) | iMac Metal → 실패 시 GPU local faster-whisper |
| iMac 메모리 `critical` | 즉시 오프로드 중단, GPU 로컬 처리 |
| iMac 터널 다운 | GPU 로컬 폴백, 30초 후 재시도 |
