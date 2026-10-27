# v2.125.64 — 🔴'학습 방향' 저장이 5개월간 아무것도 저장하지 않던 것 + 저장 경로 D4 잔여 봉합

기준 v2.125.63 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md` D4 잔여 · 결함 클래스 #1(200인데 빈 결과)

## 발견 — D4 잔여를 쓸다가

Training 탭 '🎯 학습 방향' 카드(프리셋 7종·카테고리 가중치 7개·시스템 프롬프트)의 **방향 설정 저장**:

- `saveDirection`이 `preset`·`system_prompt`·`category_weights`를 `POST /api/training/config`로 보냈다.
- 서버 허용 목록은 `allowed_hours`·`gpu_max_pct`·`auto_pause`·`max_duration_min`·`auto_trigger_threshold`·`priority` 6개뿐 —
  **세 키를 조용히 버리고 `{"ok": true}`**. 화면은 응답을 안 보고 **'✅ 저장됨'**.
- 2026-04-21 도입 커밋(`d6146e7`)은 메시지에 "방향 설정 저장 → training_config DB"라고 적었지만 **프런트만** 추가됐다.
  git 이력상 서버가 이 키를 받은 적이 없다 ⇒ **도입 이래 저장 0회**.
- 저장해도 효과가 없다: 학습·판정 프롬프트는 `routes/training.py`에 **하드코딩**이고 가중치를 읽는 코드가 없다.
- 라이브 30일 journald에 `/api/training/config` 호출 0건 — 실사용 피해는 관측되지 않음.

## 처치 (운영자 결정 2026-09-30: 정직하게 표시)

- **서버**: 모르는 키가 있으면 **400 `unknown_keys`**(키 목록·사유) — **아무것도 쓰기 전에** 거절(부분 저장 없음).
  이 클래스(조용히 버리고 ok)가 다시 숨지 못하게.
- **화면**: 저장 버튼 비활성 "방향 설정 저장 (미구현)" + 노란 안내 "아직 학습에 반영되지 않으며 저장되지 않습니다".
  프리셋·슬라이더는 미리보기로 유지. `saveDirection`은 호출 경로가 없지만 `saveJSON`으로 바꿔 두었다(배선 시 출발점).
- 실제 배선(서버 키 + 판정 프롬프트가 이 값을 읽게 + 전후 판정 측정)은 **별도 과제**.

## D4 잔여 — 응답을 안 보던 저장 3곳 → `saveJSON`

| 함수 | 예전 | 이제 |
|---|---|---|
| `saveTrainingConfig` | 응답 무시 `alert('Saved')`(브라우저 대화상자) — 세션 만료·탭 권한 403에도 Saved | 실패 사유 토스트 |
| `saveInitConfig` | `try` 없음 — 응답이 JSON이 아니면 예외만, 표시 0 | 〃 |
| `saveImageSettings` | JSON 아닌 응답이면 예외 문구만 작게 | 〃 |

## 검증

- 신규 `tests/api/test_training_config_keys.py` 4건 — **구 코드 3/4 실패**(통과 1건은 불변식 '허용 키는 여전히 저장').
- 전체 **2374 passed**.

## 범위 밖

- `app.py` 인라인 폴백 HTML(템플릿 렌더 예외 시에만 쓰임)에도 같은 옛 코드가 있다 — 폴백 전체가 낡은 사본이라 손대지 않음(기록만).
