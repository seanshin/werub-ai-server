# Release v2.110.2 — AI 생성물 워터마크 폰트 축소 + 요청별 토글

**날짜**: 2026-06-29
**유형**: UX 개선 (인터페이스 하위호환)
**대상**: `core/content_marking.py`, `routes/image.py`
**근거**: 「인공지능 기본법」 §31 — 생성물 표시 의무 유지하면서 가시성 조정

## 배경

운영 중 생성 이미지의 가시 워터마크("AI 생성 · AI-generated")가 과도하게 크게 표시되는
문제 제보. 근본 원인 = 폰트 크기가 이미지 **높이**에 비례(`H * 0.030`)해서, 세로로 긴
웹툰/패널형 이미지(예: 800×4000)에서 폰트가 **120px**까지 커져 라벨이 화면을 지배.
또한 특정 생성물에 한해 가시 워터마크를 끄는 기능 요청.

## 변경

### 1. 워터마크 폰트 크기 축소 (`core/content_marking._draw_watermark`)
- 기준을 이미지 높이 → **짧은 변 `min(W, H)`** 으로 변경 + 비율 0.030 → **0.022**
- 라벨이 폭을 지배하지 않도록 **폭 기준 상한 `W // 16`** 추가
- `config.json` **`ai_marking_font_scale`**(기본 0.022)로 무재배포 미세조정. 비정상값은
  0.005~0.08로 클램프. `mark_image_bytes(font_scale=)` 인자 추가.

| 이미지 | 기존 폰트 | 신규 폰트 |
|---|---|---|
| 1024×1024 | 30px | 22px |
| 800×4000 (세로 웹툰) | **120px** | **17px** |
| 900×250 (배너) | ~7px | 12px(하한) |

### 2. 요청별 가시 워터마크 토글 (`POST /api/image/generate`)
- 요청 본문에 **`watermark: false`** 추가 → 가시 워터마크만 생략.
- **기계판독 PNG 메타데이터(AI-Generated 플래그)·C2PA 서명은 유지** → §31 투명성 의무 충족.
- job `options`에 기록 → `_auto_s3_upload` 마킹 시점에 `_apply_marking(visible_override=False)`
  적용. 이미지 워커는 해당 키를 무시(무해). 기본값 `true`(미지정 시 기존 동작 유지).
- idempotency: 메타데이터만 마킹된 이미지는 이후 view 프록시에서 가시 워터마크가 재합성되지
  않음. (예외: S3 비활성 + `watermark:false` 동시 조건의 비-S3 view 경로만 config 기본값
  적용 — 라이브는 S3 활성이라 무영향.)

```bash
curl -X POST .../api/image/generate -H "Content-Type: application/json" \
  -d '{"prompt":"...", "watermark": false}'
```

## 검증
- GPU venv `pytest tests/test_content_marking.py` **17 passed**(14→17, 폰트 스케일 비교·세로
  이미지 회귀방지·클램프 안전성 3건 추가). 로컬 py_compile.
- graceful HUP(MainPID 77188 유지·**NRestarts 0**)·health 200.
- **라이브**: `watermark:false` 생성 요청 → job `options`에 `{"watermark": false}` 저장 확인.
  샘플 렌더로 폰트 축소(우하단 소형 라벨) 시각 확인.

## 잔여 (기존, 변동 없음)
- 보안 P2 신중처리(M3/M4/M5 = HIS/트윈 협의)·P3 방어심층·admin 비번 로테이션(저긴급).
