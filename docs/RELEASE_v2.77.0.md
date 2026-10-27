# Release v2.77.0 — AI 생성물 표시(마킹) + 판별

> 2026-06-07 | 근거: 「인공지능 기본법」 §31 투명성 확보 의무 + 정보통신망법 개정(게시·플랫폼 단계 AI 생성물 표시)

## 배경

2개 규제 트랙에 대응:
- **AI 기본법 §31** (2026-01-22 시행, 과기정통부): 생성형 AI 사업자는 산출물에 "AI 생성" 표시 의무. 본 서버는 ComfyUI로 이미지를 생성·제공 → **의무 주체**.
- **정보통신망법 개정** (방통위): 게시자·플랫폼의 AI 생성물 표시·관리 의무. 우리 서버를 호출하는 커뮤니티/플랫폼의 관리의무 이행을 **판별 API**로 지원.

## 신규 기능

### (A) AI 생성물 표시(마킹) — `core/content_marking.py`
- 생성 이미지에 **가시 워터마크**("AI 생성 · AI-generated", 한글 폰트 없으면 ASCII 폴백) + **PNG iTXt 기계판독 메타데이터** 5키(`AI-Generated`/`AI-Generator`/`AI-Model`/`AI-Created`/`AI-Marking-Spec`) 자동 부착.
- 적용 지점(생성물 3경로): `_auto_s3_upload`(이미지 조회 시 S3 자동 업로드) / `/api/image/view` ComfyUI 프록시 폴백 / `/api/image/s3/upload/<filename>`.
- **사용자 업로드(`/api/image/s3/upload-raw`)는 제외** — 우리 생성물이 아니므로 허위 표시 방지.
- 특성: **idempotent**(이중 마킹 방지), **실패 시 원본 보존**(파이프라인 무중단), Pillow만 사용(외부 의존 0).
- config 토글: `ai_marking_enabled`(기본 ON) / `ai_marking_visible` / `ai_marking_metadata` / `ai_marking_label` / `ai_marking_position`(br/bl/tr/tl/bottom) / `ai_marking_opacity`.

### (B) AI 판별 — `POST /api/detect/image`
- 입력: multipart `image`(권장) 또는 JSON `{url}`(공개 http/https).
- 판독: 우리 마킹(`read_marking`, 고신뢰) + 외부 출처 휴리스틱(`detect_provenance` — C2PA 바이트 시그니처, A1111/ComfyUI/Midjourney 등 타툴 메타·소프트웨어 힌트).
- 응답: `ai_generated`(bool), `our_marking`, `provenance`, `confidence`(high/low/none), `ai_probability`(null — ML B2/B3 예정), `disclaimer`.
- **면책**: 메타데이터 기반 판독. 메타 제거·변조 시 미탐지. 법적 판단 단독 근거 불가. SynthID 워터마크는 비공개라 미탐지.

## 검증
- `scripts/check_syntax.py` 121 파일 통과.
- `tests/test_content_marking.py` **14건 PASS** (마킹 round-trip/idempotent/force/메타전용/passthrough + provenance C2PA·A1111·clean).
- 시각검증: 1024×768 워터마크 렌더 + 한글 + 메타 5키 round-trip 확인.

## 배포 주의
- **한글 워터마크**: GPU(리눅스)에 한글 폰트(`fonts-nanum` 등) 없으면 "AI-generated"(영문)로 폴백. 한글 표기 원하면 `sudo apt install -y fonts-nanum`.
- 마킹으로 이미지 바이트가 변경되며 PNG로 정규화됨(content_type=image/png).

## 잔여 (후속)
- A3 config 가이드 문서 / A4 C2PA 서명 매니페스트(인증서) / B2·B3 ML 화소 분류기 / 영상 마킹(ffmpeg, image2video 도입 시).
