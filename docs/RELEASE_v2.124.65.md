# Release v2.124.65 — 🔴 hybrid 검색이 메타 필터를 우회하던 격리 결함 봉합

날짜: 2026-07-30 | 종류: fix(security/rag) | 배포: GPU graceful HUP(NRestarts 0)

## 배경 — Cerno 보안 결함 보고

`/api/rag/search`의 `mode=hybrid`가 **메타데이터 필터를 적용하지 않았다.** 라이브 100% 재현:
`filter={"owner":"AAA"}`로 검색해도 `owner=BBB` 문서가 반환되고, **존재하지 않는 owner로도
전건**이 나왔다. Cerno는 기다리지 않고 `mode="hybrid"`를 **ValueError로 금지**해 두었다.

## 원인

`hybrid_search()`는 벡터(Chroma — `where` 적용)와 키워드(FTS5)를 RRF로 병합하는데,
**FTS5 테이블에는 메타데이터가 없다.** FTS에만 걸린 문서는 필터 판정 자체가 불가능한 채로
병합돼 그대로 나갔다. 제보의 "유출분은 metadata가 비어 있다"가 정확히 이 경로다
(FTS 행에서 만들 수 있는 건 title·category·tenant뿐).

## 수정

1. **병합 후 필터 재강제** — FTS-only 히트는 Chroma에서 메타를 보강해 판정하고,
   **보강 실패 시 배제**(fail-closed). Chroma 조회 자체가 실패하면 FTS-only 전량 배제.
2. **`core/meta_filter.matches()` 신설** — Chroma `where` 문법(`$eq`·`$ne`·`$in`·`$nin`·
   비교·`$and`·`$or`)을 서버에서 평가. **메타 미상 = 배제**, **미지원 연산자 = 불일치**
   (격리 판정에서 "해석 못 했으니 통과"는 곧 유출).
3. **`confidence`(코사인) 노출** — `score`는 RRF **순위** 값이라 관련성 임계로 쓸 수 없었다
   (1순위면 내용 무관 항상 ~0.0167). v2.124.18에서 `/api/medical/reference`에 같은 이유로
   넣은 것과 **동일 클래스가 `/api/rag/search`에 남아 있던 것**. FTS-only는 `null`.

## 검증 (제보 절차 그대로 · 라이브)

| mode | 필터 없음 | `owner=AAA` | `owner=NOBODY` |
|------|-----------|-------------|----------------|
| vector | AAA·BBB | AAA만 | 0건 |
| keyword | AAA·BBB | AAA만 | 0건 |
| **hybrid** | AAA·BBB (conf 0.907·0.898) | **AAA만** | **0건** |

회귀 `tests/test_meta_filter.py` **10 신규**(제보 시나리오·메타 부재 배제·미지원 연산자
fail-closed 포함).

## 영향 범위 — Cerno 한 곳

`where`를 hybrid에 넘기는 호출자는 `/api/rag/search`뿐이다(전수 확인).
twin 의료 KB는 `where` 대신 **tenant 기반 SQL 격리 + 후처리 이중 방어**라 무영향,
edu는 `search`를 쓰지 않는다(`ask`는 벡터 경로).

⚠️ **교훈**: v2.124.18에서 같은 성질의 결함을 `/api/medical/reference`에서 고쳤을 때
**다른 경로까지 쓸었어야 했다.** 한 곳에서 발견한 결함은 같은 클래스 전 경로를 훑는다.

## 부수 발견 — `mode="keyword"`가 실제로는 벡터 검색

`/api/rag/search`가 `hybrid`만 분기하고 나머지는 전부 벡터로 떨어진다(`keyword` 포함).
Swagger enum과 실동작 불일치 — 격리 위험은 없으나(벡터는 필터 정상) 조용한 계약 위반이라
Cerno에 통지했다. 정정 방향(진짜 FTS 노출 vs enum 정리)은 회신 대기.

## 부수 조치

쓰고 있던 `cerno` 키가 **만료 무제한**이라 정책(365일)에 맞춰 2027-07-30으로 설정.
무제한 만료 키 잔여 **0** 복귀.
