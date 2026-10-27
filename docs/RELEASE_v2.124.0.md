# RELEASE v2.124.0 — 검색 품질 축 + judge 자기일관성 meta-eval

**날짜**: 2026-07-07
**헤드라인**: 2차 조사(07-07)의 상위 2축 구현 — ①**검색(Retrieval) 품질**을 직접 측정
(context precision·근거 충분성)해 "검색이 나쁜지 생성이 나쁜지"를 배치에서 분리
②**judge 자기일관성**(반복 채점 분산·flip rate)으로 채점자 신뢰도를 정량화.

---

## ① 검색 품질 축 (`core/retrieval_eval.py`)

- **배경**: 기존 스택은 생성 충실도만 측정 — 검색 결함(v2.114.1 제21조 rank 탈락 등)은
  충실도 하락을 통해 간접·수동으로만 발견됨.
- **context precision**: 검색된 청크(상한 10) 각각을 judge가 관련/무관 이진 판정 →
  관련 비율(노이즈 지표). **근거 충분성**: 근거만으로 완전 답변 가능한가
  yes/partial/no → 1.0/0.5/0.0(커버리지 지표 — 낮으면 검색 누락).
- **배선**: `get_evidence_chunks` 리팩터(청크 단위 단일 소스), faithfulness 러너에 합류
  (judge=상주 모델·스왑 0·실패 비차단), `faithfulness_results` +4컬럼
  (ctx_precision/relevant/chunks/sufficiency)+detail JSON에 청크별 판정 영속.
- **노출**: `/api/eval/status`(집계·질문별·추세) · 모니터 타일 2종(검색 정밀도<50%·
  충분성<75% 경고색)+신뢰성 차트 4번째 선+**드릴다운 "검색 청크별 관련성" 테이블**
  (어느 청크가 무관한지 원문 확인).

## ② judge 자기일관성 meta-eval (`scripts/eval_judge_consistency.py`)

- **배경**: LLM-judge 절대 점수의 반복 불안정(Reliability without Validity 2026 —
  kappa deflation 3~4×)+우리 실증(conditions_kept 정의 미추종). 품질 지수의
  신뢰 구간 해석 근거이자 K-HALU meta-eval(AI-HUB 대기)의 로컬 보완재.
- **방법**: 같은 (질문, 답변)을 시드만 바꿔 R회(기본 4) 채점 → `overall_std`·차원 점수
  range·**checks_flip_rate**(반복 간 판정이 갈린 체크 항목 비율 — flip 잦은 항목이
  곧 정의 개선 대상). `eval.db judge_consistency` 저장·`/api/eval/status`
  judge_consistency 블록·모니터 "judge 일관성" 타일(flip>20% 경고).
- **권장 주기**: 주 1회(무거움 — 문항×R회 채점).

## 🐞 라이브 검증이 잡은 측정 결함 (배포 당일 폐루프)

1차 배치서 ctx_precision 0.0~0.1 vs 충분성 1.0 **모순** → 드릴다운으로 정답 청크
(제15조)까지 전량 False 오판 확인 = **10청크 일괄 번호 판정이 judge에 불안정** →
**청크별 개별 boolean 판정으로 재설계**(판정 불능=None 분모 제외). 재실행서 정답
청크 True 정상 판정.

## 라이브 실측 (core 5, 재설계 후)

- **검색 정밀도 0.0~0.5·충분성 0.5~1.0**: 정답 청크는 정확히 관련 판정되나 top_k
  5×2컬렉션+시드가 담는 주변 청크 다수가 무관 — **검색 노이즈의 첫 정량 실측**
  (recall 지향 게이트(floor 0.2)의 트레이드오프가 수치로 표면화. 성급한 게이트 상향
  금지 — /ask는 권위 위계·rank 폴백 의존, 관측 축적 후 판단).
- **judge 자기일관성(R=3) 베이스라인**: 평균 flip rate **21.7%**·overall std 0.218 —
  `accuracy.conditions_kept`가 **4/5 문항서 flip = 최불안정 항목 정량 확정**(기존
  정성 관찰을 수치화). flip 잦은 항목=체크 정의 개선 대상.
- 배치 충실도 0.833(Q4 0.5·Q5 0.667 플래그 1) — 배치 변동, 야간 폐루프 검토 대상.

## 검증

- 회귀: test_retrieval_eval 6(개별 호출·None 분모 제외·상한·충분성·집계)+
  consistency_stats 순수 통계 — 로컬·GPU 통과(종료코드 직접 확인).
- ⚠️ 세션 교훈: `pytest | tail`은 실패를 삼킴(파이프 종료코드) — 종료코드 직접 검증.
  테스트 모크 마커는 프롬프트 지시문과 겹치지 않게("관련" 오매칭).

## 영향 / 하위호환

- 기존 지표·API 필드 불변(추가만). qi-1.0 지수 구성 불변(retrieval·일관성은 관측 축 —
  지수 합류는 qi-1.1로 별도 결정). 야간 배치 소요 +수 분(청크 판정 judge 호출).
