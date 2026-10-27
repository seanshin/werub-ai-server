# WeRUBLLMManager Image Generation API Guide

## Overview

WeRUBLLMManager (v2.29.0)는 ComfyUI 백엔드를 통해 텍스트-to-이미지 생성을 제공합니다.
GPU 서버(RTX 5080 16GB)에서 실행되며, 큐 기반 비동기 처리 + **ComfyUI on-demand** 방식입니다.

> **v2.25.0 변경**: ComfyUI는 기본 OFF 상태. 이미지 요청 시 자동 시작(~5초), 5분 idle 후 자동 종료.

## Access

| 항목 | 값 |
|------|-----|
| **Base URL (외부)** | `https://example.org/ollama` |
| **Base URL (내부/SSH)** | `http://127.0.0.1:8585` |
| **인증** | Bearer API Key 또는 로컬 바이패스 (127.0.0.1) |
| **Swagger** | `https://example.org/ollama/docs` |
| **갤러리** | Admin UI > Images 탭 |

## SSH를 통한 내부 호출 (인증 불필요)

서버 SSH 접속 후 `curl http://127.0.0.1:8585/api/image/...` 로 호출하면 `local_bypass`로 인증 없이 사용 가능합니다.

```bash
ssh deploy@203.0.113.10 "curl -s -X POST http://127.0.0.1:8585/api/image/generate \
  -H 'Content-Type: application/json' \
  -d '{\"prompt\":\"a cat\", \"model\":\"dreamshaper\", \"style\":\"illustration\"}'"
```

---

## Endpoints

### 1. POST /api/image/generate

이미지 생성 요청을 큐에 등록합니다.

**Parameters:**

| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
|---------|------|------|--------|------|
| `prompt` | string | Y | - | 텍스트 프롬프트 (영어 권장) |
| `model` | string | N | `flux-schnell` | 모델 ID (아래 모델 목록 참조) |
| `style` | string | N | `none` | 스타일 프리셋 (아래 목록 참조) |
| `negative_prompt` | string | N | - | 제외할 요소 (SDXL 계열만 지원) |
| `width` | int | N | 1024 | 이미지 너비 (최대 2048) |
| `height` | int | N | 1024 | 이미지 높이 (최대 2048) |
| `steps` | int | N | 모델별 기본값 | 생성 단계 수 |
| `cfg` | float | N | 7.0 | CFG scale (SDXL 계열만) |
| `seed` | int | N | 랜덤 | 재현용 시드 |
| `character_id` | string | N | - | 등록된 캐릭터 UUID (IP-Adapter로 동일 캐릭터 유지) |
| `ip_adapter_strength` | float | N | 0.7 | 캐릭터 참조 강도 (0.0~1.0) |
| `ip_adapter_mode` | string | N | `full` | `full`(전체) 또는 `face`(얼굴 특화) |
| `lcm_mode` | bool | N | false | LCM 초고속 모드 강제 활성화 (스타일과 무관하게 LCM-LoRA 적용) |
| `priority` | int | N | 5 | 큐 우선순위 |
| `webhook_url` | string | N | - | 완료 시 콜백 URL |

**Response:**
```json
{
  "job_id": "uuid",
  "status": "pending",
  "position": 1,
  "model": "dreamshaper",
  "model_name": "DreamShaper XL Turbo",
  "style": "concept-art"
}
```

### 2. GET /api/image/status/{job_id}

생성 진행 상태를 확인합니다.

**Response (완료 시):**
```json
{
  "status": "completed",
  "images": [{"filename": "ollama-admin_00001_.png", "subfolder": ""}]
}
```

### 3. GET /api/image/view/{filename}

생성된 이미지를 조회합니다. 브라우저에서 직접 열거나 다운로드 가능합니다.

### 4. GET /api/image/models

사용 가능한 모델, 스타일, LoRA 목록을 반환합니다.
스타일에는 LoRA/LCM/해상도 정보가 포함됩니다.

**Response (일부):**
```json
{
  "models": [
    {"id": "dreamshaper", "name": "DreamShaper XL Turbo", "family": "sdxl", ...}
  ],
  "styles": {
    "lcm-fast": {
      "description": "LCM 초고속 생성 (4-8 step, SDXL 전용)",
      "lora": "lcm-lora-sdxl.safetensors",
      "lora_strength": 1.0,
      "lcm_mode": true,
      "recommended_steps": "4-8",
      "recommended_cfg": 1.8
    },
    "high-detail": {
      "description": "고디테일 강화 (Detail Tweaker LoRA)",
      "lora": "detail-tweaker-xl.safetensors",
      "lora_strength": 0.7
    },
    "pixel-art-character": {
      "description": "MMORPG 캐릭터 스프라이트 (픽셀 아트)",
      "lora": "pixel-art-xl.safetensors",
      "lora_strength": 0.9,
      "default_size": [512, 512],
      "has_negative": true
    }
  },
  "online": true
}
```

### 5. GET /api/image/gallery

생성된 이미지 갤러리 목록을 반환합니다.

### 6. GET /api/image/storage

이미지 저장소 상태 (파일 수, 용량)를 반환합니다.

### 7. POST /api/image/cleanup

오래된 이미지를 정리합니다.

### 8. DELETE /api/image/delete/{filename}

특정 이미지를 삭제합니다.

### 9. POST /api/image/settings

이미지 저장 설정을 변경합니다.

### 10. POST /api/image/character/create (multipart/form-data)

캐릭터를 등록합니다 (IP-Adapter용 참조 이미지).

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `image` | file | Y | 참조 이미지 (PNG/JPG) |
| `name` | string | Y | 캐릭터 이름 |
| `description` | string | N | 설명 |
| `tags` | string | N | 태그 (콤마 구분 또는 JSON 배열) |

### 11. GET /api/image/characters

등록된 캐릭터 목록을 반환합니다.

### 12. GET /api/image/character/{id}

캐릭터 상세 정보를 반환합니다.

### 13. DELETE /api/image/character/{id}

캐릭터를 삭제합니다.

### 14. GET /api/image/character/{id}/view

캐릭터 참조 이미지를 반환합니다.

### 15. GET /api/image/character/{id}/images

해당 캐릭터로 생성된 이미지 목록을 반환합니다.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `limit` | int (query) | 50 | 최대 조회 수 (max 200) |

**Response:**
```json
{
  "character_id": "uuid",
  "character_name": "Golden Knight",
  "count": 3,
  "generated_images": [
    {
      "job_id": "uuid",
      "status": "completed",
      "prompt": "standing in a dark forest...",
      "images": [{"filename": "ollama-admin_00027_.png", "subfolder": ""}],
      "view_urls": ["/api/image/view/ollama-admin_00027_.png"],
      "created_at": "2026-04-24T08:19:16Z",
      "duration_ms": 6005
    }
  ]
}
```

---

## Models (9종)

| ID | 이름 | 계열 | Steps | 속도 | 용도 |
|----|------|------|-------|------|------|
| `flux-schnell` | FLUX.1 Schnell | flux | 4 | **fast** | 빠른 프로토타입 |
| `flux-dev` | FLUX.1 Dev | flux | 20 | normal | 고품질, 텍스트 렌더링 |
| `sdxl` | Stable Diffusion XL | sdxl | 25 | normal | 범용 |
| `dreamshaper` | DreamShaper XL Turbo | sdxl | 8 | **fast** | 일러스트/컨셉아트/판타지 |
| `animagine` | Animagine XL 3.1 | sdxl | 25 | normal | 애니메이션/만화/캐릭터 |
| `juggernaut` | Juggernaut XL v9 | sdxl | 30 | normal | 실사 인물/풍경/제품 |
| `realvis` | RealVisXL v5 | sdxl | 25 | normal | 실사 사진/건축 렌더링 |
| `juggernaut-ragnarok` | Juggernaut XL Ragnarok | sdxl | 30 | normal | 실사 최고 품질 (v9 후속) |
| `cyberrealistic` | CyberRealistic XL v6 | sdxl | 25 | normal | 실사 인물/패션/영화 |

### 모델 계열 차이

- **flux**: negative_prompt 미지원, CFG 미지원. 프롬프트만으로 제어.
- **sdxl**: negative_prompt, CFG scale 지원. 더 세밀한 제어 가능. LoRA/IP-Adapter/ControlNet 호환.

---

## LoRA (4종)

| 파일명 | 크기 | 스타일 | 효과 |
|--------|------|--------|------|
| `pixel-art-xl.safetensors` | 163MB | `pixel-art`, `pixel-art-character`, `pixel-art-sprite`, `pixel-art-scene` | 픽셀 아트 스타일 변환 |
| `lcm-lora-sdxl.safetensors` | 376MB | `lcm-fast` | 4-8 step 초고속 생성 (sampler/cfg 자동 오버라이드) |
| `detail-tweaker-xl.safetensors` | 218MB | `high-detail` | 디테일 수준 강화 |
| `flux-realism-lora.safetensors` | ~200MB | `flux-realism` | FLUX 모델 실사 품질 향상 |

> LoRA는 스타일 프리셋 선택 시 **자동 적용**됩니다. 직접 지정할 필요 없습니다.

### LCM (Latent Consistency Model) 모드

LCM-LoRA는 SDXL 모델의 생성 속도를 **3~5배 가속**합니다.

**자동 오버라이드:**
- `steps`: 최대 8 (기본 6)
- `sampler`: `lcm`
- `scheduler`: `sgm_uniform`
- `cfg`: `1.8` (LCM은 낮은 CFG가 최적)

**사용법 1 — 스타일로 사용:**
```json
{"prompt": "a knight in forest", "model": "dreamshaper", "style": "lcm-fast"}
```

**사용법 2 — 다른 스타일과 LCM 병용 (lcm_mode 파라미터):**
```json
{"prompt": "a knight", "model": "dreamshaper", "style": "concept-art", "lcm_mode": true}
```

---

## Styles (15종)

| ID | 설명 | LoRA | 기본 해상도 |
|----|------|------|------------|
| `none` | 원본 그대로 | - | 모델 기본 |
| `photorealistic` | 사실적 사진 (RAW photo, 8k) | - | 1024x1024 |
| `illustration` | 디지털 일러스트레이션 | - | 1024x1024 |
| `anime` | 애니메이션 (cel shading) | - | 1024x1024 |
| `oil-painting` | 유화 스타일 | - | 1024x1024 |
| `concept-art` | 컨셉 아트 (cinematic) | - | 1024x1024 |
| `watercolor` | 수채화 | - | 1024x1024 |
| `pixel-art` | 픽셀 아트 (범용) | pixel-art-xl | 512x512 |
| `pixel-art-character` | RPG 캐릭터 스프라이트 | pixel-art-xl | 512x512 |
| `pixel-art-sprite` | 스프라이트 시트 (다방향) | pixel-art-xl | 1024x512 |
| `pixel-art-scene` | 게임 배경/맵 타일 | pixel-art-xl | 1024x512 |
| `logo` | 로고/아이콘 디자인 | - | 1024x1024 |
| `high-detail` | 고디테일 강화 | detail-tweaker-xl | 모델 기본 |
| `lcm-fast` | LCM 초고속 (4-8 step) | lcm-lora-sdxl | 모델 기본 |
| `flux-realism` | FLUX + Realism LoRA (실사 최적화) | flux-realism-lora | 1024x1024 |

> - `pixel-art-*` 스타일은 자동으로 LoRA + 해상도 변경됩니다. DreamShaper 모델과의 조합 권장.
> - `lcm-fast`는 SDXL 계열 전용입니다. flux 모델에서는 효과 없음.
> - `high-detail`은 다른 스타일의 프롬프트와 함께 사용하려면 프롬프트에 직접 수식어를 추가하세요.

---

## ControlNet (1종, 설치 완료)

| 파일명 | 크기 | 용도 |
|--------|------|------|
| `controlnet-openpose-sdxl.safetensors` | 1.8GB | 포즈 기반 캐릭터 생성 |

> ControlNet은 현재 설치만 완료된 상태이며, API 연동은 추후 업데이트 예정입니다.
> 연동 완료 시 `pose_image` 파라미터가 generate API에 추가됩니다.

---

## Recommended Combinations

### 초고속 생성 (LCM)
```json
// 가장 빠른 조합 — dreamshaper + lcm-fast (~2-3초)
{"model": "dreamshaper", "style": "lcm-fast",
 "prompt": "a warrior in golden armor, fantasy, epic lighting"}

// 다른 스타일 + LCM 가속
{"model": "animagine", "style": "anime", "lcm_mode": true,
 "prompt": "a cute anime girl with cat ears"}
```

### 고품질 + 디테일 강화
```json
// juggernaut + high-detail — 실사 극대화
{"model": "juggernaut", "style": "high-detail",
 "prompt": "professional portrait of a young woman, studio lighting, 8k"}

// dreamshaper + high-detail — 일러스트 디테일 강화
{"model": "dreamshaper", "style": "high-detail",
 "prompt": "dragon boss battle, cinematic lighting, concept art"}
```

### 카툰/만화
```json
{"model": "dreamshaper", "style": "illustration"}
{"model": "animagine", "style": "anime"}
```

### MMORPG / 게임 아트
```json
// 전투/보스/시네마틱 배경
{"model": "dreamshaper", "style": "concept-art",
 "negative_prompt": "(photorealistic:1.3), photograph, blurry"}

// 캐릭터 일러스트 (한국/일본 RPG 스타일)
{"model": "animagine", "style": "anime"}

// 월드맵/배경/풍경
{"model": "dreamshaper", "style": "concept-art"}
```

### 실사/사진
```json
{"model": "juggernaut", "style": "photorealistic"}
{"model": "realvis", "style": "photorealistic"}
```

### 픽셀 아트 (LoRA 자동 적용)
```json
{"model": "dreamshaper", "style": "pixel-art-character"}
{"model": "dreamshaper", "style": "pixel-art-sprite"}
{"model": "dreamshaper", "style": "pixel-art-scene"}
```

### 아트/디자인
```json
{"model": "sdxl", "style": "oil-painting"}
{"model": "sdxl", "style": "watercolor"}
{"model": "flux-dev", "style": "none"}  // 프롬프트 직접 제어
```

---

## Character Consistency (IP-Adapter)

등록된 캐릭터의 외형을 다른 배경/장면에서 동일하게 유지하는 기능입니다.

### 워크플로우
```
1. 캐릭터 이미지 생성 (또는 기존 이미지 사용)
2. POST /api/image/character/create 로 캐릭터 등록 → character_id 획득
3. POST /api/image/generate 에 character_id 추가 → job_id 획득
4. GET /api/image/status/{job_id} 로 완료 확인 → filename 획득
5. GET /api/image/view/{filename} 으로 이미지 조회/다운로드
6. GET /api/image/character/{id}/images 로 해당 캐릭터의 전체 생성 이력 조회
```

### 사용 예시
```bash
# 1. 캐릭터 등록
curl -X POST http://127.0.0.1:8585/api/image/character/create \
  -F "image=@knight.png" \
  -F "name=Golden Knight" \
  -F "tags=warrior,mmorpg"
# → {"character_id": "abc-123", ...}

# 2. 같은 캐릭터를 다른 배경에서 생성
curl -X POST http://127.0.0.1:8585/api/image/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "standing in a dark forest, moonlight, fantasy",
    "model": "dreamshaper",
    "style": "concept-art",
    "character_id": "abc-123",
    "ip_adapter_strength": 0.7
  }'

# 3. LCM 가속 + 캐릭터 일관성 동시 사용
curl -X POST http://127.0.0.1:8585/api/image/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "standing in a snowy mountain",
    "model": "dreamshaper",
    "style": "concept-art",
    "character_id": "abc-123",
    "lcm_mode": true
  }'
```

### ip_adapter_strength 가이드
| 값 | 효과 |
|----|------|
| 0.3~0.5 | 약한 참조 — 전체적인 색감/분위기만 유사 |
| 0.6~0.8 | **권장** — 캐릭터 외형 유지 + 배경 변경 |
| 0.9~1.0 | 강한 참조 — 원본에 매우 가깝지만 배경 변경이 약해짐 |

### ip_adapter_mode
| 모드 | 설명 |
|------|------|
| `full` | 전체 이미지 참조 (장비, 의상, 색상 등 전체 유지) |
| `face` | 얼굴 특화 (인물 얼굴 일관성에 최적) |

---

## Prompting Tips

### 기본 구조
```
[주요 대상] + [행동/상황] + [환경/배경] + [분위기/조명] + [스타일 힌트]
```

### 효과적인 수식어

**품질**: `masterpiece`, `best quality`, `highly detailed`, `8k`
**조명**: `cinematic lighting`, `dramatic lighting`, `volumetric light`, `golden hour`
**구도**: `epic composition`, `wide shot`, `close-up portrait`, `bird eye view`
**분위기**: `dark fantasy`, `ethereal`, `menacing`, `triumphant`, `mysterious`
**게임**: `game art`, `concept art`, `key visual`, `character design sheet`

### negative_prompt (SDXL 계열 권장)
```
(worst quality:1.4), (low quality:1.4), blurry, deformed, ugly, bad anatomy, bad hands
```

---

## Usage Examples (curl)

### 외부 호출 (API Key 필요)
```bash
# 이미지 생성
curl -X POST https://example.org/ollama/api/image/generate \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "epic dragon boss battle in dark dungeon, cinematic lighting",
    "model": "dreamshaper",
    "style": "concept-art"
  }'

# LCM 초고속 생성
curl -X POST https://example.org/ollama/api/image/generate \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a knight in golden armor",
    "model": "dreamshaper",
    "style": "lcm-fast"
  }'

# 상태 확인
curl https://example.org/ollama/api/image/status/{job_id} \
  -H "Authorization: Bearer YOUR_API_KEY"

# 이미지 보기
curl -O https://example.org/ollama/api/image/view/{filename}
```

### Python 배치 생성
```python
import requests, time

BASE = "http://127.0.0.1:8585"  # 내부 또는 https://example.org/ollama
HEADERS = {"Content-Type": "application/json"}
# 외부 호출 시: HEADERS["Authorization"] = "Bearer YOUR_API_KEY"

# 1. 생성 요청
r = requests.post(f"{BASE}/api/image/generate", json={
    "prompt": "your prompt here",
    "model": "dreamshaper",
    "style": "concept-art"
}, headers=HEADERS)
job_id = r.json()["job_id"]

# 2. 폴링으로 완료 대기
while True:
    s = requests.get(f"{BASE}/api/image/status/{job_id}", headers=HEADERS).json()
    if s["status"] == "completed":
        filename = s["images"][0]["filename"]
        print(f"Done: {BASE}/api/image/view/{filename}")
        break
    elif s["status"] == "failed":
        print("Failed:", s.get("error"))
        break
    time.sleep(2)
```

---

## Performance Reference (RTX 5080)

| 워크플로우 | Steps | 해상도 | 소요 시간 | VRAM |
|-----------|-------|--------|----------|------|
| flux-schnell | 4 | 1024x1024 | ~3s | ~5GB |
| **dreamshaper + lcm-fast** | **6** | **1024x1024** | **~2-3s** | **~5.5GB** |
| dreamshaper (turbo) | 8 | 1024x1024 | ~6s | ~5GB |
| dreamshaper + high-detail | 8 | 1024x1024 | ~6s | ~5.5GB |
| dreamshaper + LoRA (pixel-art) | 8 | 512x512 | ~4s | ~5.5GB |
| animagine | 25 | 1024x1024 | ~9s | ~5GB |
| sdxl | 25 | 1024x1024 | ~9s | ~5GB |
| juggernaut | 30 | 1024x1024 | ~12s | ~5GB |
| flux-dev | 20 | 1024x1024 | ~15s | ~5GB |
| **SDXL + IP-Adapter** | 8 | 1024x1024 | **~4s** | **~8-9GB** |
| **SDXL + IP-Adapter + LoRA** | 8 | 512x512 | **~4s** | **~9-10GB** |

큐 기반이므로 여러 건을 동시에 등록해도 순차 처리됩니다 (최대 10,000건 대기 가능).

---

## Installed Extensions

| 구분 | 파일 | 크기 | 위치 |
|------|------|------|------|
| **LoRA** | pixel-art-xl.safetensors | 163MB | ComfyUI/models/loras/ |
| **LoRA** | lcm-lora-sdxl.safetensors | 376MB | ComfyUI/models/loras/ |
| **LoRA** | detail-tweaker-xl.safetensors | 218MB | ComfyUI/models/loras/ |
| **ControlNet** | controlnet-openpose-sdxl.safetensors | 1.8GB | ComfyUI/models/controlnet/ |
| IP-Adapter | ip-adapter-plus_sdxl_vit-h.safetensors | 809MB | ComfyUI/models/ipadapter/ |
| IP-Adapter Face | ip-adapter-plus-face_sdxl_vit-h.safetensors | 809MB | ComfyUI/models/ipadapter/ |
| CLIP Vision | CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors | 2.4GB | ComfyUI/models/clip_vision/ |
| Custom Node | ComfyUI_IPAdapter_plus | - | ComfyUI/custom_nodes/ |

---

## Quick Reference Card

### 가장 빠른 생성
```json
{"model": "dreamshaper", "style": "lcm-fast", "prompt": "..."}
```

### 가장 고품질
```json
{"model": "juggernaut", "style": "high-detail", "steps": 30, "prompt": "..."}
```

### 게임 캐릭터 (픽셀 아트)
```json
{"model": "dreamshaper", "style": "pixel-art-character", "prompt": "..."}
```

### 캐릭터 일관성 유지
```json
{"model": "dreamshaper", "style": "concept-art", "character_id": "uuid", "prompt": "..."}
```

### 모델/스타일 목록 API
```bash
curl http://127.0.0.1:8585/api/image/models
```


---

## ComfyUI On-Demand Mode (v2.25.0)

ComfyUI는 기본 OFF 상태로 운영됩니다. GPUResourceManager가 자동 관리합니다.

### 동작 흐름

```
이미지 요청 → GPUResourceManager
  └ ComfyUI OFF? → LLM VRAM 해제 → ComfyUI 시작 (~5초) → 이미지 생성
  └ ComfyUI ON?  → LLM VRAM 해제 → 바로 생성
생성 완료 → ComfyUI /free → LLM 복원 → 5분 idle → ComfyUI 자동 종료
```

### 주의사항
- 첨 이미지 요청 시 콜드스타트 ~5초 추가 소요
- 연속 요청 시 ComfyUI 유지 (idle 타이머 리셋)
- LLM과 동시 사용 불가 (VRAM 교대 사용)

### 상태 확인

```typescript
const status = await gpuFetch("/api/gpu/status");
// resource_manager.comfyui_running: true/false
// resource_manager.idle_timer_active: true/false
```

### 이미지 전체 삭제

```typescript
// retention_days=0, max_count=0 → 전체 삭제
const result = await gpuFetch("/api/image/cleanup", {
  method: "POST",
  body: { retention_days: 0, max_count: 0 }
});
// { deleted: 851, freed_mb: 670.2 }
```

---

## AI 생성물 표시(마킹) + 판별 (AI 기본법 §31 / 정보통신망법) — v2.77.0~2.78.1

> 근거: 「인공지능 기본법」 §31 투명성 확보 의무(2026-01-22 시행) + 정보통신망법 개정(게시·플랫폼 단계). 본 서버는 ComfyUI로 이미지를 **생성·제공**하므로 표시 의무 주체.

### 자동 마킹 (생성물)

`/api/image/generate`로 만든 이미지는 **조회/업로드 시 자동으로 마킹**됩니다(별도 호출 불필요).

- **가시 워터마크**: 우하단 반투명 라벨 "AI 생성 · AI-generated"(한글 폰트 없으면 `AI-generated`).
- **기계판독 메타데이터**(PNG iTXt): `AI-Generated=true`, `AI-Generator`, `AI-Model`, `AI-Created`(ISO8601 UTC), `AI-Marking-Spec`.
- 적용 경로: `/api/image/status`(S3 자동 업로드 시) · `/api/image/view`(ComfyUI 프록시 폴백) · `/api/image/s3/upload/{filename}`.
- **사용자 업로드(`/api/image/s3/upload-raw`)는 마킹 안 함** — 우리 생성물이 아니므로 허위 표시 방지.
- idempotent(이미 마킹된 이미지는 재마킹 안 함), 마킹 실패 시 원본 보존.

#### config 토글 (`config.json`, 기본 ON)

| 키 | 기본 | 설명 |
|---|---|---|
| `ai_marking_enabled` | `true` | 마킹 전체 on/off |
| `ai_marking_visible` | `true` | 가시 워터마크 |
| `ai_marking_metadata` | `true` | PNG 메타데이터 |
| `ai_marking_label` | (자동) | 워터마크 문구 |
| `ai_marking_position` | `"br"` | br/bl/tr/tl/bottom |
| `ai_marking_opacity` | `0.85` | 0.0~1.0 |

### 판별 API

#### POST /api/detect/image

임의 이미지가 AI 생성인지 판별. **메타데이터/출처(확실) + ML 화소 분류(참고용)** 종합.

**요청** (둘 중 하나)
```bash
# 1) 파일 업로드 (권장)
curl -X POST https://example.org/ollama/api/detect/image \
  -H "Authorization: Bearer sk-..." \
  -F 'image=@photo.png'

# 2) URL (공개 http/https)
curl -X POST https://example.org/ollama/api/detect/image \
  -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/photo.jpg", "ml": true}'
```
- `image`(multipart) 또는 `url`(JSON). 최대 20MB.
- `ml`(선택): ML 화소 분류 on/off. 생략 시 config `ai_detector_enabled`(기본 on).

**응답**
```json
{
  "ai_generated": true,
  "confidence": "high",
  "our_marking": {"marked": true, "generator": "WeRU.B AI (ComfyUI)", "model": "flux-schnell", "created": "...", "source": "png-text"},
  "provenance": {"c2pa": false, "ai_metadata": ["parameters"], "software": "stable diffusion", "hints": ["software:stable diffusion"]},
  "ml": {"available": true, "ai_probability": 0.94, "label": "artificial", "model": "Organika/sdxl-detector", "scores": [...]},
  "ai_probability": 0.94,
  "method": "metadata+ml",
  "disclaimer": "메타데이터/출처 + ML 화소 추정 기반입니다. ... 법적 판단의 단독 근거가 될 수 없습니다."
}
```

**confidence 판정**
| 값 | 조건 |
|---|---|
| `high` | 우리 마킹 또는 C2PA 감지 (확정적) |
| `medium` | 타툴 메타데이터/소프트웨어 힌트 |
| `low` | ML 추정만 (`ai_probability ≥ threshold`) |
| `none` | 신호 없음 |

#### config (`config.json`)

| 키 | 기본 | 설명 |
|---|---|---|
| `ai_detector_enabled` | `true` | ML 분류 on/off |
| `ai_detector_model` | `Organika/sdxl-detector` | HF 모델 |
| `ai_detector_device` | `"cpu"` | cpu/cuda |
| `ai_detector_threshold` | `0.7` | ML AI 판정 임계 |
| `ai_detector_threads` | `4` | torch CPU 스레드 캡 |

> ⚠️ **정확도 한계**: ML 추정치는 신규 생성기·압축·스크린샷·편집본에 오탐/미탐이 있습니다. 확정 신호는 우리 마킹/C2PA가 우선이며, 본 결과는 자동 차단·법적 판단의 단독 근거로 쓰지 마세요. SynthID 워터마크는 비공개라 미탐지됩니다.
> ⚠️ ML 첫 호출은 모델 로드로 ~8초, 이후 추론 CPU ~0.1초. 엔드포인트는 GPU 서버 전용입니다.
