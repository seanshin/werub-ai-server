---
name: P3-1 Medical Image Precheck - Design
description: 의료 영상 사전 검증 상세 설계 — 3 Step 구현 명세
type: design
---

# P3-1: 의료 영상 사전 검증 — 설계서

**Date**: 2026-05-20  
**Plan Reference**: docs/01-plan/features/P3-1-Medical-Image-Precheck.plan.md

---

## Step 1: 이미지 품질 검사 함수 (PIL, AI 불필요)

`routes/vision.py`에 추가:

```python
def _check_image_quality(img_bytes):
    """이미지 품질 평가 — PIL 기반, GPU 불필요"""
    # returns: {"score": int, "issues": list, "resolution": str, ...}
```

검사 항목:
- 해상도: 256×256 미만 → LOW_RESOLUTION (-30)
- 파일 크기: 10KB 미만 → OVER_COMPRESSED (-20)
- 선명도: Laplacian variance < 100 → BLURRY (-25)
- 노출: mean < 30 → UNDER_EXPOSED (-15), > 225 → OVER_EXPOSED (-15)
- score = 100 - 감점합계

## Step 2: AI 분류 함수 (MedGemma)

```python
def _classify_medical_image(img_b64):
    """MedGemma로 의료영상 분류 — is_medical, modality, body_part"""
    # num_predict=100, temperature=0.1 (빠른 분류)
```

## Step 3: Precheck API + analyze 통합

1. `POST /api/vision/medical/precheck` 엔드포인트
2. `POST /api/vision/medical/analyze`에 `precheck` 파라미터 추가

---

## 검증 기준 (10항목)

| # | 항목 | 기준 |
|---|------|------|
| 1 | precheck API 응답 | suitable + classification + quality 구조 |
| 2 | 비의료 이미지 | is_medical=false |
| 3 | 모달리티 분류 | modality 필드 반환 |
| 4 | 품질 점수 | score 0-100 |
| 5 | strict 모드 | 부적합 시 422 |
| 6 | warn 모드 | 응답에 precheck 포함 |
| 7 | 모달리티 불일치 | expected vs detected 경고 |
| 8 | 성능 | < 2초 |
| 9 | 에러 코드 | PRECHECK_FAILED |
| 10 | py_compile | 구문 통과 |
