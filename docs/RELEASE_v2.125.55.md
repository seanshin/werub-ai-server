# v2.125.55 — 메인 메뉴 그룹화(Phase 1) + 사고형 비전 예산 2048→4096

기준 v2.125.54 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md` Phase 1 ·
사전 등록 `docs/01-plan/references/vision-thinking-budget-2026-09-29.md`

## 1. 메인 메뉴 그룹화 (Phase 1)

- 평면 탭 12 + 새 창 링크 7(3줄 줄바꿈)을 **한 줄 5그룹**으로: Dashboard · 운영(Queue·Activity·Logs) ·
  모델(Models·Training·Model Manager↗) · 도구(Chat·Generate·Image·Knowledge) · 도메인 콘솔↗(6) · 우측 ⚙ 설정(Settings·Users).
- 그룹 버튼에 현재 위치 표시("운영 · Logs"). 클릭으로 열고 Esc·바깥 클릭으로 닫는다(hover만으로 열지 않음). 새 창 링크는 `.tab-ext`
  (내부 탭과 모양 구분)·`rel="noopener"`. GPU 전용 5개는 `is_imac` 분기 유지.
- `switchTab`: 활성 표시를 `data-tab` **정확 일치**로(예전 `.tab[onclick*=name]` 부분 문자열 매칭 제거) · 알 수 없는 이름은
  Dashboard · 죽은 분기(`hrlaw`·`game`) 제거 · URL hash(`#logs`)로 새로고침·링크 공유 시 같은 탭(replaceState — 뒤로가기 기록 안 쌓음).
- API 불변.

## 2. 사고형 비전 예산 2048 → 4096 (v2.125.54 게이트 차단 원인)

- v2.125.54 배포 게이트가 `vision-general` 2건으로 차단 — `qwen3-vl:8b`가 사고만으로 2048을 소진(사고 6,470자) →
  빈 본문 → 폴백 `minicpm-v`(명함 OCR 0). 이번 변경과 무관한 **기존 결함**(기본 temperature 1 → 사고 길이 확률적).
- 사전 등록 A/B(각 N=20×2회차): A(2048) 빈 본문 **2/40** · B(4096) **0/40**·이름/전화 40/40·p95 ≤ 10.1s.
  실경로 페이로드 회차에서 B가 **2,424토큰**을 쓴 회차 = 2048이면 잘렸을 꼬리가 실경로에도 있다.
- ⚠️ 내 측정 결함: 1차는 라우터가 붙이는 `repeat_penalty: 1.1`을 빼고 쟀다(사전 등록의 "페이로드 동일" 위반) → 2차 재측정.
- 같은 클래스 점검: `core/meeting_vision.py`(2048·temperature 0.2)는 60일 호출 0건 → 근거 없는 처치 금지, 유지.

## 3. 발견

- 관리 API 의 역할별 권한 강제 — 다음 판(v2.125.56)에서 처리했다.

## 4. 검증

- 신규 `tests/api/test_admin_menu.py` 4건(탭 12개=패널 짝·새 창 7개/iMac 2개·`.tab` 미사용·정확 일치·hash).
  ⚠️ 내 테스트 결함: 처음엔 `/`를 불렀는데 로그인 확인 302 → **인라인 폴백 HTML**을 검사해 헛돌았다 → 템플릿 직접 렌더로.
- 비전 회귀 3곳 예산 값 갱신. 전체 **2329 passed**(GPU worktree).
