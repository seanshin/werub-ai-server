# v2.125.19 — 잊힌 작업 4건 마감(X1 ERP 확약 API·X5 embed 가드·X6 정정·X2 문서 허위)

날짜: 2026-08-26 | 선행: 기획서 전수 분류가 발굴한 §9 잊힌 작업 11건(`remaining-work-2026-08-19.plan.md`) | 규율: 병렬 조사 에이전트 3대 → 보고서만 받아 중앙 구현(v2.125.13 병렬 규율)·처치 전 사실 재확인

## 1. X1 — ERP 공문 확약 API 2종 구현 (대외 약속 미이행 해소)

`docs/01-plan/references/notice-integration-reply-to-erp.md`(2026-06-07, To: hospital-erp)가
①·⑥에서 확약했는데 `routes/notice.py` 26라우트에 없던 두 API를 구현했다.

- **`GET /api/notice/changes?since={ts}`** — 변경분 일괄 조회(누락 복구·정기 대사 보조 채널).
  - 응답 스키마는 **어느 문서에도 확약된 바 없어**(파라미터명·용도 한 줄뿐) 최소로 설계:
    `{since, count, truncated, changes[]}` · 행=대사 3컬럼(erp_ref_id/revision_no/content_hash)+
    상태·타임스탬프, **body 미포함**(대량 응답 방지) · `limit` 기본 500·최대 2000.
  - 🔑 **타임스탬프 함정**: `_now()`가 `isoformat()+"Z"`라 저장값이 `…+00:00Z` 이형 —
    문자열 부등호 비교는 클라이언트 포맷과 어긋난다 → `_parse_ts()` 관대 파싱 후 datetime 비교
    (Python측 필터·현 규모 수백 행에서 충분, `updated_at` 인덱스 불요).
  - ⚠️ **물리 삭제(DELETE)는 행이 사라져 changes에 못 실린다**(tombstone 없음) — 전체 정합은
    sync/verify 대사가 잡는다고 docstring에 명시. 별도 tombstone은 필요가 관측되면.
- **`POST /api/notice/sync/verify`** — ERP가 전체 스냅샷 push → 대사 → 불일치 응답(회신 ⑥이
  확정한 방향: ERP 인바운드 차단망이라 AI→ERP 호출 불가). 스키마는 설계서
  `erp-notice-rag.design.md` §API **그대로**: `{total, matched, mismatched[{erp_ref_id,
  revision_no, reason: hash_diff|missing|extra}]}`.
  - `extra`는 **active 행만** 센다 — superseded·revoked 제외. ERP 스냅샷이 최신 revision만
    실어도 구행이 extra 위양성을 내지 않게(개정 시 구행은 status=superseded로 남는 스키마).
  - `content_hash`는 `sha256:` 프리픽스 유무 무관(`verify_hash`와 같은 관대 규칙). 상한 20,000건.

## 2. X5 — `_model_ok` fail-closed 전환 (벡터 오염 가드 0 해소)

`core/orchestrator/scheduler.py`의 `_model_ok`가 `return True` 스텁인 채
`embed_model_pinned` 제약 게이트에 배선돼 있었다(v2.84.0부터 불변) — embed capability를
켜는 순간 컬렉션 벡터 오염 가드가 0으로 동작.

- 조사 결과 **현재는 죽은 경로**: config에 embed capability 없음 · constraints 키 자체가 전무 ·
  `/worker/embed` 미구현 · `dispatch.run("embed")` 호출 0곳. 단 "켜는 3조건이 갖춰지는 순간"
  가드가 무조건 통과가 되는 구조였다.
- **실검증은 레포 안에서 완결 불가**: 판정 재료(worker health `embed_models` 광고·컬렉션-모델
  맵·`client.call`의 hints 전달)가 전부 부재·일부는 운영자(iMac) 작업. →
  **fail-closed(`return False`)로 전환** = 미정합 취급 = remote 부적격 = GPU 로컬 100%
  (p2-embed 기획 불변식 "미정합=기존 GPU 임베딩 100%"와 일치·현행 동작 변화 0).
  실검증은 `embed-offload-agreement` 가드 4종이 갖춰진 뒤 이 함수만 교체.
- `ORCHESTRATOR_GUIDE.md` embed 행 문구 갱신(스텁→fail-closed).

## 3. X6 — 🔴 정정: "한국어 필터 소실"은 인과가 틀렸다 + raw 계약 고정

§9 X6의 "`routes/chat.py` 삭제(v2.125.15)와 함께 필터 소실" 주장을 git 이력으로 재현한 결과:

- **그 필터는 현행 app.py 체제에서 한 번도 라이브가 아니었다.** chat.py는 v2.28.0 모듈 분리
  이후 app.py에 등록된 적이 없고(`git log -S"chat_bp" -- app.py` **0건** — 등록 이력은 구
  엔트리포인트 `app_flask.py`뿐), 삭제 시점엔 import조차 불가였다. 실경로 `routes/ollama.py`의
  `/api/generate`는 **v2.28.0부터 줄곧 raw 중계**. v2.125.13이 이 죽은 파일을 router로
  "이관"까지 해 산 코드처럼 보인 것이 오독의 경위.
- **raw가 옳은 계약이다**: `/api/generate`는 범용 프록시라 번역·영문 프롬프트가 정상 사용례
  (CLAUDE.md 예시 다수)이고 `force_korean`(한글 없는 줄 삭제)을 걸면 그 사용례가 깨진다.
  한국어 강제는 도메인 경로 소관(story·writing·novel·hr_law는 `force_korean_token` 배선 확인).
- 처치 = **계약 고정 테스트**(`tests/test_generate_raw_passthrough.py`): 혼합 언어 upstream
  NDJSON이 바이트 동일로 통과해야 함 — 필터가 끼어도, 지워져도 걸린다("재발 무성" 해소).
- 🟡 **부수 발견(별건 후보)**: `/api/chat` 스트리밍은 **클라이언트=raw · 캐시/세션=`force_korean`
  필터본**(finally 블록·v2.62.1)으로 갈려 있다 → 같은 요청의 라이브 스트림과 캐시 히트 재생이
  **다른 텍스트**일 수 있다. 의도(저장 위생) 가능성이 있어 측정·결정 전 처치 안 함.

## 4. X2 — v2.109.2 "잔존 blob 0" 부분 허위 정정

직접 재검증: **medical.db·users.db는 purge 성공**(`git rev-list HEAD` 도달 0) ·
**moderation.db·training.db는 filter-repo에서 빠졌다** — v2.109.2 커밋(`0bcdb5c`)이
`git rm --cached`만 했고 blob은 `9e5f3cd`(2026-05-06)에서 지금도 도달 가능.
내용 실사=모더레이션 **테스트 샘플**(욕설 문구·해시 ID)+훈련 런 메타 1행 — PHI·자격증명
없음·위험 낮음. `RELEASE_v2.109.2.md`에 정정 절 + `RELEASE_DIGEST.md` 뒤집힌 주장 색인에 등재.
이력 재작성(force-push·전 클론 재동기)은 별건으로 운영자 조율.

## 5. 검증

- 신규 회귀 **16건**: notice ERP 11(changes 6 — since 필수/포맷 관대/개정·폐지 가시/limit ·
  verify 5 — 설계 테스트 계획 #6의 hash_diff/missing/extra 3종·프리픽스 관대·superseded
  비-extra) + scheduler fail-closed 3(핀 cap은 iMac 건강+prefer_imac이어도 GPU 로컬·비핀 cap
  위임 불변) + generate raw 계약 2.
- 인접 기존 회귀: orchestrator 4파일+notice 4파일+generate 헬퍼 = **96 passed**
  (실패 2건은 scratch venv에 chromadb 부재 — 변경 전에도 동일 실패·환경 결함).
- 전체 회귀·게이트는 GPU 배포 시 실행.

## 6. 남긴 것

- §9 잔여: X7(meeting language:auto) · X8(보안 잔여 3건 추적) · X9(numbers_match 원인규명) ·
  X10(위임 검증 게이트 미실시 기록) · X11(문서-코드 불일치 일괄).
- 별건: `/api/chat` 스트림 캐시-라이브 텍스트 분기(§3) · git blob 이력 재작성(§4) ·
  ⑦CORS 반사 절·⑤mlx_stt secure_filename(v2.125.18 이월).
