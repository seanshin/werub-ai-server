# v2.125.67 — 관리 화면 사용자 정보 주입·사용자 표 이스케이프 + 사용자 수정 API 검증

기준 v2.125.66 · 발견 경위: v2.125.66에서 `index()`를 고치다 주입 코드를 읽음 · 설정 권한은 **admin뿐**(위험 낮음·잠재)

## 결함 (라이브 데이터: 사용자 2명·특수문자 0·비객체 권한 0 ⇒ 무증상)

1. **헤더 표시 HTML 주입** — `index()`가 `<!--USER_DISPLAY-->`를 `display_name (role)`로 **그대로** 치환.
   `<img onerror>`·`</script>…`가 든 이름이면 그 사용자의 관리 화면에서 실행된다. *(회귀 테스트를 쓰다 발견 — 처음엔 2번만 보고 있었다.)*
2. **`window.__USER__` 스크립트 주입** — 문자열 이어 붙이기. 이름은 `"`만 빼고, permissions는 **DB 원문 그대로**.
   JSON이 아닌 permissions면 인라인 스크립트 문법 오류 → **그 사용자 화면 전체 불능**, `</script>`면 탈출.
3. **`PUT /api/admin/users/<id>`** — 역할을 검증하지 않고(생성은 검증) permissions **문자열을 그대로 저장** → 2번의 입력원.
4. **사용자 표(`loadUsers`)** — username·display_name·role을 innerHTML에 그대로, 권한 버튼 onclick엔 `'`만 바꿔 넣음.
   생성 API가 username 형식을 검사하지 않는다.

## 처치

1. 헤더 표시값 `markupsafe.escape`.
2. `json.dumps` + `</` → `<\/`. permissions는 파싱해 **객체가 아니면 `{}`**(모르면 보이지 않게 — `allowed_tabs`도 같은 값 사용).
3. 알 수 없는 역할·객체가 아닌 permissions는 **400**(부분 저장 없음 — 검증이 쓰기 전).
4. 표 셀 `escHtml`, onclick 인자는 JSON 문자열을 HTML 속성용으로 이스케이프(`_jsArg`).

## 검증

- 신규 `tests/api/test_user_inject_hardening.py` 6건 — 구 코드에서 **5/6 실패**(통과 1 = 정상 권한 객체 저장, 불변식).
- 전체 **2384 passed**.

## 범위 밖(기록)

- username 형식 제한(서버) — 정책 변경이라 보류. 표시·주입 쪽은 이스케이프로 막았다.
