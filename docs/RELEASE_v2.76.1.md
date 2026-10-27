# Release v2.76.1 — API 키 만료 UI 라이브 반영 (실제 서빙 파일 적용)

> 릴리즈: 2026-06-07 | 이전: v2.76.0

## 요약

v2.76.0에서 추가한 API 키 만료 관리 기능의 **프론트엔드 변경이 라이브에 반영되지 않던 문제**를
바로잡았다. 백엔드 엔드포인트(`POST /api/keys/{id}/expiry`)는 정상 동작했으나, UI 변경을
`app.py`의 **인라인 폴백 템플릿(평소 미사용 dead code)** 에 넣어 실제 서빙 페이지에 나타나지
않았다. 실제 Admin 페이지는 `templates/admin.html` + `static/js/main.js`로 서빙된다.

## 🐛 수정 — 프론트 변경을 실제 서빙 파일에 적용

- `static/js/main.js`:
  - `_cleanTs()`/`formatDate()` — `...+00:00Z` 이중 접미사 타임스탬프 보정(기존 행의 "Invalid Date" 해소).
  - 키 목록 만료 판정(`expired`)에 `_cleanTs` 적용.
  - 키 행에 **`기간`** 버튼 + `setKeyExpiry()`(일수 입력 0=무제한) + `showPrompt()/closePrompt()`.
  - `window._apiKeys` 캐시(다이얼로그에서 키 이름 조회).
- `templates/admin.html`:
  - `promptModal` 추가.
  - `main.js` 캐시 버스팅: `<script src="static/js/main.js?v=2.76.1">` (브라우저 강캐시 우회).
- `app.py`: VERSION 2.76.1.

## 라이브 검증

- GPU 배포(HEAD c8ff4ea) 후: health 200@1.5ms, NRestarts=0, 워커 nlwp 평탄.
- 서빙되는 `/static/js/main.js`에 `setKeyExpiry`/`_cleanTs`/`showPrompt` 포함 확인.
- 콘솔: 모든 키 행에 `기간` 버튼 + EXPIRES/LAST USED 정상 날짜 표시(브라우저 확인 완료).

## 교훈

Admin 프론트 수정은 **`static/js/main.js`(함수) + `templates/admin.html`(모달/HTML)** 이 정본이다.
`app.py` 내부의 거대한 인라인 `render_template_string` 템플릿은 templates 파일 부재 시 폴백일
뿐이므로 여기만 고치면 라이브에 반영되지 않는다. JS 변경 시 `admin.html`의 `main.js?v=` 캐시
버스팅을 함께 갱신한다.

## 변경 파일

```
static/js/main.js     — _cleanTs/formatDate, '기간' 버튼, setKeyExpiry, showPrompt, _apiKeys
templates/admin.html  — promptModal, main.js?v=2.76.1 캐시버스팅
app.py                — VERSION 2.76.1
CLAUDE.md / README    — 버전 동기화
```

## 운영 메모

- 배포: GPU `git pull` + 저트래픽 창 재시작(v2.75.0 graceful drain 적용됨).
- 브라우저가 이전 `main.js`를 캐시 중이면 `?v=2.76.1`로 자동 갱신(필요 시 Cmd+Shift+R).
