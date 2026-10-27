# WeRUBLLMManager 이미지 생성 API — 실전 사용 가이드

> 다른 CLI에서 대규모 이미지 생성 시 참조하는 문서입니다. (v2.29.0)

## 접속 방법

```bash
# SSH 경유 (인증 불필요 — local_bypass)
ssh deploy@203.0.113.10 "curl -s -X POST http://127.0.0.1:8585/api/image/generate ..."
```

- **내부 Base URL**: `http://127.0.0.1:8585`
- **외부 Base URL**: `https://example.org/ollama` (API Key 필요)
- SSH 경유 시 127.0.0.1로 호출하면 인증 없이 사용 가능

---

## 이미지 생성 (핵심 3단계)

### 1단계: 생성 요청

```bash
curl -s -X POST http://127.0.0.1:8585/api/image/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "영어 프롬프트",
    "model": "모델ID",
    "style": "스타일ID"
  }'
```

**응답:**
```json
{"job_id": "uuid", "status": "pending", "position": 1}
```

### 2단계: 완료 대기

```bash
curl -s http://127.0.0.1:8585/api/image/status/{job_id}
```

**응답 (완료 시):**
```json
{"status": "completed", "images": [{"filename": "ollama-admin_00030_.png"}]}
```

> pending → running → completed 순서. 2~30초 소요 (모델에 따라 다름)

### 3단계: 이미지 다운로드

```bash
curl -O http://127.0.0.1:8585/api/image/view/{filename}
```

---

## generate 파라미터 (전체)

| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
|---------|------|------|--------|------|
| `prompt` | string | **Y** | - | 영어 프롬프트 |
| `model` | string | N | `flux-schnell` | 모델 ID (아래 표 참조) |
| `style` | string | N | `none` | 스타일 프리셋 (아래 표 참조) |
| `negative_prompt` | string | N | - | 제외 요소 (sdxl 계열만) |
| `width` | int | N | 1024 | 너비 (최대 2048) |
| `height` | int | N | 1024 | 높이 (최대 2048) |
| `steps` | int | N | 모델 기본값 | 생성 단계 수 |
| `cfg` | float | N | 7.0 | CFG scale (sdxl 계열만) |
| `seed` | int | N | 랜덤 | 재현용 시드 |
| `character_id` | string | N | - | 등록된 캐릭터 UUID (IP-Adapter) |
| `ip_adapter_strength` | float | N | 0.7 | 캐릭터 참조 강도 (0.0~1.0) |
| `ip_adapter_mode` | string | N | `full` | `full` 또는 `face` |
| `lcm_mode` | bool | N | false | LCM 초고속 모드 강제 (스타일과 무관하게 LCM-LoRA 적용) |
| `priority` | int | N | 5 | 큐 우선순위 (낮을수록 먼저) |

---

## 모델 ID (7종)

| model ID | 이름 | 계열 | 기본 steps | 소요시간 | 추천 용도 |
|----------|------|------|-----------|---------|----------|
| `flux-schnell` | FLUX.1 Schnell | flux | 4 | ~3s | 빠른 테스트 |
| `flux-dev` | FLUX.1 Dev | flux | 20 | ~15s | 고품질, 텍스트 렌더링 |
| `dreamshaper` | DreamShaper XL Turbo | sdxl | 8 | ~6s | **만능** — 일러스트/컨셉아트/픽셀아트 |
| `animagine` | Animagine XL 3.1 | sdxl | 25 | ~9s | 애니메이션/캐릭터 |
| `sdxl` | Stable Diffusion XL | sdxl | 25 | ~9s | 범용 |
| `juggernaut` | Juggernaut XL v9 | sdxl | 30 | ~12s | 실사 인물/풍경 |
| `realvis` | RealVisXL v5 | sdxl | 25 | ~9s | 실사 사진/건축 |

**주의**: `negative_prompt`, `cfg`는 sdxl 계열만 지원. flux 계열은 무시됨.

---

## 스타일 ID (14종)

| style ID | 설명 | 자동 LoRA | 기본 해상도 | 비고 |
|----------|------|----------|-----------|------|
| `none` | 원본 프롬프트 그대로 | - | 1024x1024 | |
| `photorealistic` | 사실적 사진 | - | 1024x1024 | |
| `illustration` | 디지털 일러스트 | - | 1024x1024 | |
| `anime` | 애니메이션 | - | 1024x1024 | |
| `concept-art` | 컨셉 아트 | - | 1024x1024 | |
| `oil-painting` | 유화 | - | 1024x1024 | |
| `watercolor` | 수채화 | - | 1024x1024 | |
| `pixel-art` | 픽셀 아트 (범용) | pixel-art-xl | **512x512** | |
| `pixel-art-character` | RPG 캐릭터 스프라이트 | pixel-art-xl | **512x512** | |
| `pixel-art-sprite` | 스프라이트 시트 | pixel-art-xl | **1024x512** | |
| `pixel-art-scene` | 게임 배경/맵 | pixel-art-xl | **1024x512** | |
| `logo` | 로고/아이콘 | - | 1024x1024 | |
| `high-detail` | 디테일 강화 | detail-tweaker-xl | 모델 기본 | **NEW** |
| `lcm-fast` | LCM 초고속 (4-8 step) | lcm-lora-sdxl | 모델 기본 | **NEW** SDXL전용 |

### LoRA 목록 (3종)

| LoRA 파일 | 크기 | 적용 스타일 | 효과 |
|-----------|------|------------|------|
| pixel-art-xl | 163MB | pixel-art-* 4종 | 픽셀 아트 변환 |
| lcm-lora-sdxl | 376MB | lcm-fast | 4-8 step 초고속 (sampler/cfg 자동 오버라이드) |
| detail-tweaker-xl | 218MB | high-detail | 디테일 수준 강화 |

> - pixel-art-* 스타일은 **dreamshaper** 모델과 조합이 가장 좋음
> - lcm-fast는 **SDXL 계열 전용** (flux에서는 효과 없음)
> - LoRA는 스타일 선택 시 **자동 적용**됨. 직접 지정 불필요

---

## LCM 초고속 모드 (신규)

LCM-LoRA는 SDXL 모델의 생성 속도를 **3~5배 가속**합니다.

### 자동 오버라이드 값
| 항목 | 일반 | LCM 모드 |
|------|------|---------|
| steps | 8~30 | **6** (최대 8) |
| sampler | euler_ancestral 등 | **lcm** |
| scheduler | normal/karras | **sgm_uniform** |
| cfg | 7.0 | **1.8** |

### 사용법

```json
// 방법 1: lcm-fast 스타일 사용 (가장 간단)
{"model": "dreamshaper", "style": "lcm-fast", "prompt": "a knight in armor"}

// 방법 2: 다른 스타일 + lcm_mode (스타일 프롬프트 유지 + LCM 가속)
{"model": "dreamshaper", "style": "concept-art", "lcm_mode": true, "prompt": "a knight"}

// 방법 3: 캐릭터 일관성 + LCM 가속
{"model": "dreamshaper", "character_id": "uuid", "lcm_mode": true, "prompt": "..."}
```

---

## 추천 조합 (용도별)

```json
// 가장 빠른 생성 (~2-3초)
{"model": "dreamshaper", "style": "lcm-fast"}

// 고품질 + 디테일 극대화
{"model": "juggernaut", "style": "high-detail", "steps": 30}

// 카툰/만화
{"model": "dreamshaper", "style": "illustration"}

// 일본 애니메이션
{"model": "animagine", "style": "anime"}

// MMORPG 전투/배경/시네마틱
{"model": "dreamshaper", "style": "concept-art",
 "negative_prompt": "(photorealistic:1.3), photograph, blurry"}

// MMORPG 캐릭터 일러스트
{"model": "animagine", "style": "anime"}

// 픽셀 아트 캐릭터
{"model": "dreamshaper", "style": "pixel-art-character"}

// 실사 사진
{"model": "juggernaut", "style": "photorealistic"}

// 빠른 프로토타입
{"model": "flux-schnell", "style": "none"}
```

---

## 대규모 생성 패턴 (Python)

```python
import requests, time, json

BASE = "http://127.0.0.1:8585"

def generate_image(prompt, model="dreamshaper", style="concept-art", **kwargs):
    """이미지 생성 후 완료까지 대기, filename 반환"""
    payload = {"prompt": prompt, "model": model, "style": style, **kwargs}
    r = requests.post(f"{BASE}/api/image/generate", json=payload, timeout=10)
    job_id = r.json()["job_id"]

    # 폴링 (최대 120초)
    for _ in range(60):
        time.sleep(2)
        s = requests.get(f"{BASE}/api/image/status/{job_id}", timeout=5).json()
        if s["status"] == "completed":
            return s.get("images", [{}])[0].get("filename")
        elif s["status"] == "failed":
            raise Exception(f"Failed: {s.get('error')}")
    raise Exception("Timeout")

# 단일 생성
filename = generate_image("a dragon in the sky", model="dreamshaper", style="concept-art")
print(f"Done: {BASE}/api/image/view/{filename}")

# LCM 초고속 배치
filename = generate_image("a knight", model="dreamshaper", style="lcm-fast")

# 대규모 배치 — 큐에 먼저 모두 등록 후 폴링
prompts = [
    "warrior knight in golden armor",
    "female mage with crystal staff",
    "dark elf assassin with daggers",
]
job_ids = []
for p in prompts:
    r = requests.post(f"{BASE}/api/image/generate", json={
        "prompt": p, "model": "animagine", "style": "anime"
    }, timeout=10)
    job_ids.append(r.json()["job_id"])
    print(f"Queued: {r.json()['job_id'][:8]} | {p}")

# 전체 완료 대기
for job_id in job_ids:
    while True:
        s = requests.get(f"{BASE}/api/image/status/{job_id}", timeout=5).json()
        if s["status"] in ("completed", "failed"):
            fn = s.get("images", [{}])[0].get("filename", "none")
            print(f"{job_id[:8]} → {s['status']} | {fn}")
            break
        time.sleep(2)
```

> **중요**: 큐에 한번에 많이 넣어도 됩니다 (최대 10,000건). 순차 처리되므로 VRAM 경합 없음.
> 처리 순서: priority ASC → created_at ASC (같은 모델 우선 처리하여 모델 스왑 최소화)

---

## 캐릭터 일관성 (같은 캐릭터 + 다른 배경)

### 캐릭터 등록

```bash
curl -s -X POST http://127.0.0.1:8585/api/image/character/create \
  -F "image=@/path/to/character.png" \
  -F "name=캐릭터이름" \
  -F "tags=warrior,mmorpg"
# → {"character_id": "uuid", ...}
```

### 같은 캐릭터로 다른 장면 생성

```bash
# 일반 속도
curl -s -X POST http://127.0.0.1:8585/api/image/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "standing in a dark forest, moonlight",
    "model": "dreamshaper",
    "style": "concept-art",
    "character_id": "등록된-uuid",
    "ip_adapter_strength": 0.7
  }'

# LCM 가속 + 캐릭터 일관성
curl -s -X POST http://127.0.0.1:8585/api/image/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "standing in a snowy mountain",
    "model": "dreamshaper",
    "style": "concept-art",
    "character_id": "등록된-uuid",
    "lcm_mode": true
  }'
```

### 캐릭터별 생성 이력 조회

```bash
curl -s http://127.0.0.1:8585/api/image/character/{character_id}/images
# → 해당 캐릭터로 생성한 모든 이미지 목록 + view_urls
```

### 주의사항
- `character_id` 사용 시 **sdxl 계열 모델만 지원** (dreamshaper, animagine, sdxl, juggernaut, realvis)
- flux 모델은 IP-Adapter 미지원
- `ip_adapter_strength` 0.6~0.8 권장 (너무 높으면 배경 변경이 약해짐)

---

## 기타 엔드포인트

```bash
# 캐릭터 목록
curl -s http://127.0.0.1:8585/api/image/characters

# 모델+스타일+LoRA 목록 (상세 정보 포함)
curl -s http://127.0.0.1:8585/api/image/models

# 갤러리 (생성된 이미지 전체)
curl -s http://127.0.0.1:8585/api/image/gallery

# 큐 상태
curl -s http://127.0.0.1:8585/api/queue/status

# 이미지 삭제
curl -s -X DELETE http://127.0.0.1:8585/api/image/delete/{filename}
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 계속 pending | 워커 스레드 멈춤 | `sudo systemctl restart ollama-admin` |
| unknown style 에러 | 잘못된 style ID | 위 스타일 표 참조 (14종) |
| character not found | 잘못된 character_id | `GET /api/image/characters`로 확인 |
| IP-Adapter 실패 | flux 모델 사용 | sdxl 계열 모델로 변경 |
| 이미지 깨짐 | animagine + pixel-art | dreamshaper로 변경 |
| LCM 품질 저하 | cfg 너무 높음 | lcm-fast는 자동 cfg=1.8, 수동 시 1.5~2.0 |
