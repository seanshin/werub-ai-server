# Release v2.124.60 — WEBP 무성 무시 봉합 + 413 JSON 응답 (그룹웨어 V4 후속 조치)

날짜: 2026-07-30 | 종류: fix(image) | 배포: GPU graceful HUP(NRestarts 0)

## 배경 — "안 됨"으로 답하고 끝낼 일이 아니었다

v2.124.59에서 그룹웨어 V4(이미지 규격)에 실측으로 답하며 **WEBP 미인식**과 **413 바디가
HTML**임을 그대로 통지했다. 그런데 둘 다 **우리가 고칠 수 있는 것**이었다.

특히 WEBP는 이 세션에서 반복 봉합해 온 **"호출자가 준 값이 조용히 무시되는"** 클래스다 —
모델이 "이미지가 제공되지 않았습니다"라고 답하고 200이 나가므로 호출자 로그에 흔적이 없다.
`data:` 접두(v2.124.50)와 정확히 같은 사고 형태다.

## 1. WEBP → PNG 서버측 변환

`core/images.py` 신설(단일 소스):

- `sniff_format()` — **base64 앞부분 문자열만** 보고 포맷 판별(디코드 없음). PNG·JPEG·GIF는
  건드리지 않으므로 **정상 경로 비용 0**.
- `normalize_image()` — `data:` 접두 제거 + 업스트림이 못 읽는 포맷(WEBP)만 PNG로 변환.
- 방어: 픽셀 상한 4,000만(디컴프레션 폭탄) · 변환 실패 시 **원본 전달**(동작 후퇴 없음·경고 로그).

적용 경로 **3종 전부** — `/api/chat`·`/api/generate`(`_normalize_images` 위임) 및
`/v1 image_url`(openai_compat이 자체 파싱하던 것을 같은 함수로 통일).

**라이브 검증**: WEBP raw → "파란색"(정답) · WEBP + `data:` 접두 → "초록색"(정답).
종전에는 둘 다 "이미지가 제공되지 않았습니다".

## 2. 413 응답을 JSON으로

앱 상한(20MB) 초과 시 Flask 기본 HTML이 나가 API 호출자가 파싱할 수 없었다 →
`{"error":"payload too large","limit_bytes":20971520,"hint":"..."}`.

⚠️ **외부 경유는 nginx `client_max_body_size`(10MB)가 먼저 걸러 nginx HTML 413이 나간다**
(앱에 도달하지 않음). 호출자는 **상태 코드로 판별**해야 하며, 이 사실을 회신서에 명시했다.

## 3. 회귀

`tests/test_image_input.py` **8 신규** — 포맷 스니핑·`data:` 제거·WEBP 변환(내용 보존 확인)·
지원 포맷 무변경·손상 입력 폴백·픽셀 상한 생략.

## 4. 회신서 정정

`vision-paths-reply2-to-groupware.md` §4의 "WEBP ❌"를 **"✅ 서버가 PNG로 변환"**으로 갱신.
그룹웨어가 읽기 전에 정정했다.
