---
name: P3-1 Medical Image Precheck - Plan
description: 의료 영상 AI 판독 전 사전 검증 — 이미지 품질·적합성·모달리티 자동 분류
type: plan
---

# P3-1: 의료 영상 사전 검증 (Precheck) — 기획서

**Status**: Plan Phase  
**Feature**: P3-1-Medical-Image-Precheck  
**Date**: 2026-05-20

---

## 1. 배경

현재 `/api/vision/medical/analyze`는 전달받은 이미지를 무조건 MedGemma에 전송하여 판독합니다.
문제:

- **비의료 이미지** (사진, 스크린샷, 문서 등) → 무의미한 판독 결과 + GPU 낭비
- **품질 불량 이미지** (과도한 압축, 흐림, 잘림) → 오판독 위험
- **모달리티 불일치** (CT를 xray로 전송) → 판독 정확도 저하
- **방향 오류** (좌우 반전, 회전) → 소견 위치 오류

PACS에서 자동 전송 시 사전 필터 없이 대량 요청이 들어올 수 있으므로, 
**판독 전 자동 검증 → 부적합 이미지 조기 거절**이 필요합니다.

---

## 2. 목표

### 제공할 기능
1. **이미지 유형 분류** — 의료 영상 vs 비의료 이미지 판별
2. **모달리티 자동 감지** — xray/ct/mri/ultrasound/pathology/fundus/dermoscopy 분류
3. **품질 평가** — 판독 가능 여부 판정 (해상도, 노출, 선명도)
4. **촬영 부위 추정** — chest/abdomen/brain/spine/extremity 등
5. **사전 검증 통합** — analyze 호출 시 자동 precheck 옵션

### 비목표 (Scope Out)
- DICOM 파싱 (별도 PACS 연동 프로젝트)
- 환자 정보 추출/매핑
- 판독 품질 보정 (이미지 보정/향상)

---

## 3. API 설계

### 3-1. 독립 Precheck API

**`POST /api/vision/medical/precheck`**

| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|:----:|------|
| image | file | ✅ | 검증할 이미지 파일 |
| expected_modality | string | | 예상 모달리티 (불일치 검출용) |

**응답 (200)**

```json
{
  "suitable": true,
  "confidence": 0.94,
  "classification": {
    "is_medical": true,
    "modality": "xray",
    "modality_confidence": 0.97,
    "body_part": "chest",
    "body_part_confidence": 0.92
  },
  "quality": {
    "score": 85,
    "resolution_ok": true,
    "resolution": "2048x2048",
    "exposure_ok": true,
    "sharpness_ok": true,
    "issues": []
  },
  "warnings": [],
  "model": "medgemma:4b",
  "duration_ms": 800
}
```

**부적합 응답 (200, suitable=false)**

```json
{
  "suitable": false,
  "confidence": 0.88,
  "classification": {
    "is_medical": false,
    "modality": null,
    "modality_confidence": 0,
    "body_part": null,
    "body_part_confidence": 0
  },
  "quality": {
    "score": 0,
    "resolution_ok": true,
    "resolution": "1920x1080",
    "issues": ["NOT_MEDICAL_IMAGE"]
  },
  "warnings": ["이미지가 의료 영상이 아닙니다."],
  "rejection_reason": "NOT_MEDICAL_IMAGE",
  "model": "medgemma:4b",
  "duration_ms": 500
}
```

### 3-2. analyze에 precheck 통합 옵션

**기존 `POST /api/vision/medical/analyze`에 파라미터 추가**

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|:------:|------|
| precheck | string | `off` | `off`: 기존 동작, `warn`: 검증 후 경고 포함, `strict`: 부적합 시 거절 |

**strict 모드 거절 응답 (422)**

```json
{
  "error": "image failed precheck — not suitable for medical analysis",
  "code": "PRECHECK_FAILED",
  "precheck": {
    "suitable": false,
    "rejection_reason": "LOW_QUALITY",
    "quality_score": 35,
    "issues": ["BLURRY", "LOW_RESOLUTION"]
  }
}
```

**warn 모드 — 정상 응답에 precheck 결과 포함**

```json
{
  "findings": "...",
  "impression": "...",
  "precheck": {
    "suitable": true,
    "quality_score": 72,
    "warnings": ["이미지 해상도가 권장(1024px) 이하입니다."]
  },
  "disclaimer": "..."
}
```

---

## 4. 검증 항목 상세

### 4-1. 의료 영상 여부 (is_medical)

| 판정 | 기준 |
|------|------|
| ✅ 의료 | X-ray, CT 슬라이스, MRI 슬라이스, 초음파, 병리 슬라이드, 안저, 피부경 |
| ❌ 비의료 | 일반 사진, 스크린샷, 문서, 다이어그램, 일러스트, 빈 이미지 |

**구현**: MedGemma에 분류 프롬프트 전송 (num_predict=50, 빠른 응답)

```
Is this a medical/clinical image (x-ray, CT, MRI, ultrasound, pathology slide, 
fundus photo, dermoscopy)? Answer: {"is_medical": true/false, "modality": "...", 
"body_part": "...", "confidence": 0.0-1.0}
```

### 4-2. 모달리티 분류

| 코드 | 설명 | 특징 |
|------|------|------|
| xray | X-ray | 흑백, 뼈/공기 대조 |
| ct | CT 슬라이스 | 축상면/관상면/시상면, 조직 대조 |
| mri | MRI 슬라이스 | 연부조직 대조, T1/T2 |
| ultrasound | 초음파 | 부채꼴, 에코 패턴 |
| pathology | 병리 | 고배율, 세포/조직 |
| fundus | 안저 | 원형, 혈관/시신경유두 |
| dermoscopy | 피부경 | 고배율 피부 병변 |

### 4-3. 품질 평가

| 항목 | 기준 | 코드 |
|------|------|------|
| 해상도 | 최소 256×256, 권장 512×512 이상 | `LOW_RESOLUTION` |
| 파일 크기 | 최소 10KB (과도한 압축 방지) | `OVER_COMPRESSED` |
| 선명도 | Laplacian variance < 100 → 흐림 | `BLURRY` |
| 노출 | 평균 밝기 < 30 또는 > 225 → 과다/부족 | `UNDER_EXPOSED` / `OVER_EXPOSED` |
| 잘림 | 유효 영역 < 이미지의 50% | `CROPPED` ⚠️**미구현(컷)** — 판정 로직·상수 모두 부재(X11·2026-08-26. "유효 영역" 정의도 미기술이라 구현하려면 사양 확정 선행) |
| 색상 | 비정상 컬러스페이스 (CMYK 등) | `INVALID_COLORSPACE` ⚠️**미구현(컷)** — 상수조차 없음(동일) |

**품질 점수 계산**

```
score = 100
- LOW_RESOLUTION: -30
- OVER_COMPRESSED: -20
- BLURRY: -25
- UNDER/OVER_EXPOSED: -15
- CROPPED: -20

suitable = score >= 50 AND is_medical
```

### 4-4. 모달리티 불일치 검출

사용자가 `expected_modality=xray`를 전달했는데 AI가 `ct`로 분류하면:

```json
{
  "warnings": ["모달리티 불일치: 예상 xray, 감지 ct — 영상 종류를 확인해주세요."],
  "classification": {
    "modality": "ct",
    "expected_modality": "xray",
    "modality_mismatch": true
  }
}
```

---

## 5. 구현 계획

### Step 1: 이미지 품질 검사 (PIL 기반, AI 불필요)

```python
from PIL import Image
import numpy as np

def check_image_quality(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    issues = []
    
    if w < 256 or h < 256:
        issues.append("LOW_RESOLUTION")
    if len(img_bytes) < 10240:
        issues.append("OVER_COMPRESSED")
    
    # 선명도 (Laplacian variance)
    gray = np.array(img.convert("L"), dtype=np.float64)
    laplacian_var = cv2_laplacian_variance(gray)
    if laplacian_var < 100:
        issues.append("BLURRY")
    
    # 노출
    mean_brightness = gray.mean()
    if mean_brightness < 30:
        issues.append("UNDER_EXPOSED")
    elif mean_brightness > 225:
        issues.append("OVER_EXPOSED")
    
    score = 100 - sum({"LOW_RESOLUTION": 30, "OVER_COMPRESSED": 20, 
                        "BLURRY": 25, "UNDER_EXPOSED": 15, 
                        "OVER_EXPOSED": 15}.get(i, 10) for i in issues)
    return {"score": max(0, score), "issues": issues, 
            "resolution": f"{w}x{h}", "resolution_ok": w >= 256 and h >= 256}
```

### Step 2: 의료 영상 분류 (MedGemma, 빠른 추론)

```python
def classify_medical_image(img_b64):
    r = requests.post(f"{OLLAMA_BASE}/api/generate", json={
        "model": "medgemma:4b",
        "prompt": CLASSIFICATION_PROMPT,
        "images": [img_b64],
        "stream": False,
        "options": {"num_predict": 100, "temperature": 0.1},
    }, timeout=30)
    # JSON 파싱 → is_medical, modality, body_part, confidence
```

### Step 3: Precheck API 엔드포인트

```python
@vision_bp.route("/api/vision/medical/precheck", methods=["POST"])
def api_vision_medical_precheck():
    # 1. 이미지 검증 (_validate_image)
    # 2. 품질 검사 (check_image_quality)
    # 3. AI 분류 (classify_medical_image)
    # 4. 종합 판정 (suitable = quality >= 50 AND is_medical)
```

### Step 4: analyze 통합

```python
@vision_bp.route("/api/vision/medical/analyze", methods=["POST"])
def api_vision_medical_analyze():
    precheck_mode = request.form.get("precheck", "off")
    if precheck_mode != "off":
        precheck_result = run_precheck(img_data)
        if precheck_mode == "strict" and not precheck_result["suitable"]:
            return jsonify({"error": "...", "code": "PRECHECK_FAILED", 
                           "precheck": precheck_result}), 422
```

---

## 6. 의존성

| 패키지 | 용도 | 설치 |
|--------|------|------|
| Pillow | 이미지 메타데이터, 해상도 | 이미 설치됨 |
| numpy | 선명도/노출 계산 | 이미 설치됨 |
| MedGemma 4B | 의료 영상 분류 | 이미 설치됨 |

**추가 패키지 불필요** — 기존 환경에서 모두 구현 가능.

---

## 7. 성능 예상

| 단계 | 예상 시간 |
|------|:---------:|
| 이미지 검증 + 품질 검사 (PIL) | ~50ms |
| AI 분류 (MedGemma, num_predict=100) | ~500-800ms |
| **Precheck 총 응답** | **~1초** |
| analyze + precheck(warn) | 기존 + ~1초 |
| analyze + precheck(strict, 거절) | ~1초 (GPU 절약) |

---

## 8. 검증 기준

| # | 항목 | 기준 |
|---|------|------|
| 1 | precheck API 동작 | suitable true/false 정확 반환 |
| 2 | 비의료 이미지 거절 | 일반 사진 → is_medical=false |
| 3 | 모달리티 분류 | xray/ct/mri 정확 분류 |
| 4 | 품질 점수 | 흐린/어두운 이미지 → score < 50 |
| 5 | strict 모드 | 부적합 시 422 반환 |
| 6 | warn 모드 | 정상 응답에 precheck 포함 |
| 7 | 모달리티 불일치 | expected vs detected 경고 |
| 8 | 성능 | precheck 단독 < 2초 |
| 9 | 에러 코드 | PRECHECK_FAILED 에러 형식 |
| 10 | py_compile | 구문 검증 통과 |
