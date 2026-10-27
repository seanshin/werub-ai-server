# v2.125.35 — medlaw dedup 삭제분이 이틀 만에 전량 재색인되고 있었다: tombstone으로 churn 봉쇄

2026-09-03 · 라이브 `2.125.35`(commit `ace6a38`) · 게이트 **177/177·베이스라인 0** ·
회귀 **2079 passed · 0 failed**(신규 7)

## 1. 발견 — 후속 확인이 라이브 결함을 드러냈다

v2.125.34 후속 점검 중 dedup dry-run을 다시 돌렸더니 계획이 **5파일/140청크** —
08-31에 지운 **4파일/139청크가 전부 계획에 재등장**했고(동일 쌍) 신규 1쌍이 붙어
있었다. count **13,388 → 13,579**. 즉 삭제가 이틀 만에 사실상 전량 되돌아왔다.

기전은 v2.125.27이 **경고만 남기고 미처치**였던 그 churn 경로다:
- 수집의 `known` 집합이 **컬렉션 파생**(`col.get()`의 source_file 집합) —
  dedup이 지우면 그 자리가 빈다.
- 스케줄러(매일 05 UTC)는 KHIDI 최신 페이지를 스캔하고, 목록에 아직 노출 중인
  게시글의 첨부가 `known`에 없으면 "신규"로 다운로드·재색인한다.
- 신규 색인 후 자동 dedup은 v2.125.27부터 **dry-run**(자동 삭제 금지 규율)이라
  재유입분이 그대로 남는다. ⇒ **삭제는 반드시 하루 안에 무효화된다.**

## 2. 처치 — 삭제 사실을 컬렉션 밖에 기록한다 (tombstone)

컬렉션에만 남는 상태는 삭제와 함께 소실된다. `khidi_state.json`에
`dedup_tombstones`를 기록:

- `dedup_versions(apply=True)`가 **삭제 성공분만** tombstone에 기록(실패분을
  기록하면 코퍼스에서 영구 소실된다 — 회귀 고정).
- `collect_and_index_new`가 tombstone 파일을 건너뛰고 **`tombstone_skipped`로
  보고**(silent drop 금지).
- 같은 게시글에 **다른 첨부**(fileId 상이)가 오면 safe_filename이 달라 막히지
  않는다 — 정당한 갱신은 통과(회귀 고정).

## 3. 재삭제 실행 — 08-31 절차 그대로 (이번엔 tombstone과 함께)

1. 복구본 export를 **레포 스크립트로 고정**(`scripts/export_medlaw_dedup_plan.py` —
   08-31의 임시 코드 재현 가능화): 5파일/140청크를 ids·documents·metadatas·
   **embeddings까지** `/home/deploy/backups/medlaw_dedup_restore_20260903.json`(3.4MB).
2. 워치독(5s·link_lists.bin>50MB·디스크<20G) 동반 `--apply`:
   count **13,579 → 13,439**(정확히 −140) · **TRIP 0** · `link_lists.bin` 128,476B 불변.
3. 검증: 삭제 표본 0청크 · 보존 쌍(119·1청크) 그대로 · 게이트 medlaw **7/7** +
   control **28/28**.
4. 🔑 **tombstone 라이브 실증**: 직후 수집을 실제로 1회 돌림(3페이지) →
   `{'new_files': 0, 'tombstone_skipped': 2}` — 지운 파일이 **아직 목록에 노출
   중인데도** 재색인되지 않았다. 08-31엔 이 지점에서 전량 복귀했다.

## 4. ⚠️ 처치가 만든 2차 사고 — 테스트가 라이브 state를 오염

배포 후 tombstone을 확인하니 **`100-1-foo.pdf`**(테스트 픽스처 이름)가 섞여 있었다.
apply가 state를 쓰게 되자, state 격리가 없던 `test_medlaw_delete_guard`의
`apply=True`가 **배포 중 G4 회귀 실행에서 라이브 `khidi_state.json`에 기록**한 것.
한 픽스처(`test_medlaw_dedup_identity`)만 격리하고 **같은 클래스 전 경로를 안 쓸었던**
내 실수다(v2.124.65 규율 위반을 규율 문서가 있는 레포에서 재연).
처치=라이브 state에서 잔재 제거 + **conftest autouse 전역 격리**(픽스처 단위가 아니라
클래스 봉쇄) — 직후 전체 회귀 2079건 실행 후 tombstone이 5건 그대로임을 확인
(가드가 그 실행으로 스스로 증명됨).

## 5. 부수

- `FAST_MODELS`에 `hyperclova-seed:1.5b` 등재(1.5B 경량 — 큐 fast-lane 자동 부스트.
  v2.125.34 신설 모델의 후속 확인에서 이름 기반 목록 누락 적발).
- 회귀 신규 7(`tests/test_medlaw_dedup_tombstone.py`): 기록·실패분 미기록·dry-run
  무기록·수집 차단+보고·다른 첨부 통과·known 불변.

## 6. 남은 것

- `ambiguous` 4파일은 여전히 사람 판정 대기(§1의 dry-run에 상세 쌍 출력됨 —
  hwpx/pdf 파싱본 3쌍 + 개정판 의심 1쌍).
- tombstone은 수집 차단만 한다 — KHIDI 목록에서 게시글이 밀려나면 자연 무의미해지나
  정리 루틴은 두지 않는다(작은 목록·부작용 없음).
