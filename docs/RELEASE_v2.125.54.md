# v2.125.54 — 관리 콘솔 설정 경로 결함 5건 봉합 (메뉴·설정 정리 Phase 0a/0b)

기준 v2.125.53 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md`(r2 · 운영자 권장안 채택 2026-09-29)

메인 메뉴·Settings 재편(Phase 1~3)에 앞서, 조사에서 드러난 **설정 경로 자체의 결함**을 먼저 닫는다.
화면 구조는 바꾸지 않았다(카드 문구·버튼만).

## 1. D1 — Ollama 성능 카드: 틀린 값 표시 + 저장 불능

- **표시**: 카드는 systemd drop-in만 읽었는데 유닛의 `EnvironmentFile=-/etc/default/ollama`가 그 값을
  **덮어쓴다**. 라이브 화면 `OLLAMA_MAX_LOADED_MODELS=1` · 실제 **3**(v2.125.49에서 한 번 겪은 함정의 화면판).
- **저장**: 앱 계정 `weruby`는 두 파일 모두 쓸 수 없다(`root:root 0644` 실측) → `POST /api/ollama/perf`가
  권한 오류로 **500**. 쓸 수 있는 환경이어도 EnvironmentFile에 있는 키는 drop-in에 써 봐야 **무성 무효**.
- 처치: `_effective_ollama_env()`가 systemd 우선순위대로 **실제 값**과 `source`·`shadowed`를 계산
  (`current`는 이제 실제 값 · `effective`·`writable`·`read_only_reason` 추가). 쓸 수 없거나 가려진 키면
  **409 + 이유**(500·무성 무효 대신). 화면은 읽기 전용 표시 + 출처·"⚠ …을 덮어씀" 줄, 적용 버튼 숨김.
  drop-in 재작성 시 `OLLAMA_*` 아닌 줄 보존(예전엔 통째로 지웠다). `daemon-reload`는 `sudo -n`.
  라벨 오류(GPU 서버를 "🖥 iMac"으로 표시) 정정. 정본 파일=`/etc/default/ollama`(운영 결정 — 중복 키 정리는 운영 작업으로 분리).

## 2. D2 — `/api/config`가 시크릿 원문을 브라우저로 보냈다

- 관리자 세션이면 45키 전부 원문: `s3_secret_key`·`s3_access_key`·`datago_api_key`·`apple_worker_token`·
  `admin_password_hash` 등(라이브 실측 6종). Settings뿐 아니라 **Models 탭·모델 선택 목록 채우기**에서도 호출됐다.
- 처치: 민감 키 = **명시 목록 ∪ 이름 패턴**(`key|secret|token|password|passwd|hash|credential`).
  명시 목록에 **webhook URL 3종**(URL 자체가 쓰기 권한 — 패턴에 안 걸린다)과 c2pa 경로. GET·POST 응답 모두
  `{set, hint:"…끝4자"}`. POST는 마스크 객체·빈 값 = **변경 없음**, 삭제는 `{"clear": true}`만.
  화면 webhook 칸은 "설정됨 (…abcd) · 지우기" 상태 줄로.

## 3. D3 — 인증 완화가 한 번의 클릭으로, 외부 세션에서도

- 처치(0b · 운영 결정): **보안을 약하게 하는 변경**(`auth_enabled`→false · `local_bypass`→true)은 서버가
  **localhost 요청만** 받는다(외부 관리자 세션은 403 `local_only`). 조이는 방향과 같은 값 재저장은 허용.
  체크박스 즉시 저장을 없애고 "인증 설정 저장" 버튼 + 인증 끄기 확인 대화상자.

## 4. D4 — 실패해도 "저장됨"

- `saveSettings`·`saveAlertConfig`·`saveDefaultModel`이 응답을 안 보고 성공 토스트(세션 만료 403에도 "saved"),
  `createKey`는 실패 시 무표시. 처치: 공용 `saveJSON()` — 응답대로 성공/실패 토스트, 401/403은 "세션 만료",
  거부되면 인증 체크박스를 서버 값으로 되돌림.

## 5. D5 — "기본 모델" 저장이 모델을 VRAM에 영구 적재

- `default_model`은 큐·배치·파이프라인의 **model 미지정 대체값**인데(`routes/queue.py`), 저장 버튼이
  `keep_alive:-1`로 그 모델을 적재했다 → 상주 모델(`llm_resident_model`)과 다르면 **두 모델이 VRAM에 공존**
  (v2.125.49 스래싱과 같은 조건). 현재는 둘 다 14b라 무증상.
- 처치: 적재 호출 제거(적재·상주는 `/api/models/residency`만). 카드 문구를 "큐 기본 모델"로 정정
  (예전 문구 "기본 모델은 항상 GPU에 로드"는 상주 모델이 따로 지정된 라이브에선 거짓이었다).

## 6. 검증

- 신규 `tests/api/test_admin_settings_cleanup.py` 14건 — **구 코드에서 11건 실패/오류**(D1 6 · D2 3 · D3 2) · 신 코드 14/14.
- ⚠️ 내 테스트 결함 2건: ① UI 키 목록 밖 키(`OLLAMA_CONTEXT_LENGTH`)로 409를 기대(무시되어 200) ② `subprocess.run`
  전역 패치가 백그라운드 GPU 모니터의 `nvidia-smi`까지 잡아 `assert_not_called`가 **15회 중 4회** 흔들림 →
  systemctl 호출만 세도록 교정 후 **15/15**.
- 전체 **2325 passed**(GPU 임시 worktree · `set -e`).
