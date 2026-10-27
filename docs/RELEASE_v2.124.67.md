# Release v2.124.67 — 충실도 제출 엔드포인트 + 삭제 건수 노출 + keyword enum 제거

날짜: 2026-07-30 | 종류: feat/fix(eval·rag) | 배포: GPU graceful HUP(NRestarts 0)

Cerno `08_AI서버_회신3_답변.md`의 요청 3건 처리.

## 1. 🔴 삭제가 무효인데 `ok:true`였다 (Cerno §5 제보)

`DELETE /api/rag/document/<id>`가 **무엇을 지웠든 `ok:true`만** 반환했다. 실제로 부모 id
삭제가 무효인데도 성공으로 나가 문서가 **조용히 잔존**했다(Cerno가 count로 발견).

- `deleted`(실제 삭제된 벡터 수)·`fts_deleted` 반환 — **0이면 "지울 게 없었다"**로 구분된다.
- **id 접두 폴백 추가**: 청크 정리가 `source_doc_id` 메타에만 의존해, 호출자가 그 키를
  안 남기면(배치 색인 등) 통째로 무효였다. 이제 `{doc_id}__chunk_` 접두로도 지운다.
  구분자까지 비교하므로 `note`로 `note-A`를 지우는 과매칭은 없다(회귀 고정).

> Cerno는 원인을 자기 인제스션(메타 미기록)으로 돌렸지만, **메타에만 의존한 우리 구현이
> 약했다.** 호출자 구현에 정합성을 의존하면 그 계약이 깨질 때 조용히 실패한다.

## 2. 충실도 쌍 제출 — `POST /api/eval/pairs` (Cerno §3)

Cerno 제안(push)대로 신설. 페이로드는 제안 형태 그대로.

- **접수만 하고 채점은 야간 배치** — judge 호출이 요청 지연·동시 슬롯을 잠식하므로.
  스풀(`eval_spool/*.jsonl`, 0600)에 적재하고 `--spool` 모드가 소화 후 `.done` 처리.
- **cron 배선 완료**(03:30 배치 뒤 실행) — 엔드포인트만 만들고 수거를 안 걸면 쌓이기만 한다.
- 한 건이 깨져도 나머지는 접수하고 `rejected`·`reasons`로 사유 반환.
- 라이브 왕복 검증: 제출(accepted 1/rejected 1+사유) → 스풀 → 채점 faith 1.000 →
  `GET /api/eval/status?source=cerno` 조회 → 테스트 행 정리.

**PHI 방침 회신**: 환자 맥락 답변은 **표본에서 제외** 권고. 맥락만 빼고 근거를 보내면
답변에 남은 그 맥락 기반 주장이 unsupported/contradicted로 찍혀 **위양성**이 된다
(07-27 id360이 정확히 그 형태였다).

## 3. `mode="keyword"` enum 제거 (Cerno §4)

실제로는 벡터로 떨어지던 **조용한 계약 위반**. Cerno 권고대로 enum에서 제거하고,
`score`의 의미(vector=코사인 / hybrid=RRF·유사도는 `confidence`)를 설명에 명시.

## 검증

회귀 `tests/test_rag_collection.py` 11→**14**(삭제 건수·접두 폴백·과매칭 방지·0건 보고).
제출→채점→조회 라이브 왕복 확인.
