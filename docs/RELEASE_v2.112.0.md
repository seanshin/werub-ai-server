# Release v2.112.0 — LLM 평가 프레임워크 (기획 + P0 측정 + Phase 1 벤치 하네스)

> 2026-07-03 | 기획 `docs/01-plan/features/llm-evaluation-hallucination.plan.md`
> 성능 모니터링(v2.111.0)의 **품질 축** 확장. 기조: 외부 API 0(자체 호스팅) · 측정 선행 · 의료 안전.

## 요약

LLM **성능·할루시네이션·가치**를 정량 측정·회귀 감시하는 평가 프레임워크를 조사·기획하고, 측정 선행
(P0)과 표준 벤치 하네스(Phase 1)를 구현·실측했다. 특히 의료 자문보조 서버라 **할루시네이션=환자 안전**
직결이며, 표준 벤치가 내부 비표준 수치와 큰 불일치를 드러냈다.

## 조사 (오픈 프로젝트/논문, 자체 호스팅 우선)
- **하네스**: lm-evaluation-harness(MIT)·promptfoo(MIT)·DeepEval·RAGAS — 전부 로컬 judge zero-cost.
- **할루시네이션**: HHEM-2.1-Open(Vectara T5)·SelfCheckGPT·G-Eval/FaithJudge.
- **한국어/의료 벤치**: KorMedMCQA(면허 7,469문항)·KorMedMCQA-V(영상)·KorMedLawQA(의료법)·K-HALU·KoSimpleQA·KMMLU.

## P0 — 측정 선행 (GPU 실측)
- **KorMedMCQA**(doctor+pharm 100문항): 표준 벤치가 내부 수치(med42 "98.7")와 불일치 → Phase 1 GO.
- **HHEM-2.1-Open**: **NO-GO** — 512토큰 한계로 다중청크 한국어 법령 근거 절단(실제 grounded 답변이 ~0
  아티팩트) + 한국어 변별 불안정(통제 4중 2 반전). → Phase 2 충실도를 **LLM-judge(로컬 qwen)로 전환**.
  **측정 선행이 잘못된 기반 위 구축을 차단.**

## Phase 1 — 표준 벤치 하네스
- **`scripts/eval_bench.py`**(신규): MC 벤치 러너. **Ollama 네이티브 제약 디코딩**(`format` JSON 스키마로
  `{answer:int}` 강제) — lm-harness loglikelihood는 Ollama가 echo+logprobs 미지원이라 마찰 큼, 제약
  디코딩이 더 견고·공정(P0 생성형 MC 포맷 편향 제거). 외부 API 0, 결과 → `eval.db`.
- **KorMedMCQA full 400문항(4 config, parse_fail=0 전부)**:

  | 정답률 | 모델 | 속도 |
  |---|---|---|
  | **67.5%** | qwen2.5:14b (범용) | 132s |
  | 60.5% | qwen2.5:7b (범용) | 468s |
  | 52.5% | thewindmom/llama3-med42-8b (의료특화·현 1순위) | 807s |
  | 35.2% | dcarrascosa/medgemma-1.5-4b-it:Q8_0 | 2351s |

  → **범용 Qwen(14b·7b 모두)이 의료특화 모델보다 높음** — 내부 수치와 역전, 제약 디코딩으로 견고.

## ⚠️ 해석 한계 (정직)
- MCQA=파라메트릭 지식 측정. **프로덕션 의료답변은 RAG-grounded**(DUR·의료법·e약은요 정본)라
  즉시 "모델 교체" 결론 아님. med42/MedGemma는 영어중심이라 한국어 면허시험에 약함(모델 특성).
- 임상추론/안전/설명 품질은 MCQA 미포착 → Phase 2(충실도)·Phase 3(judge)에서 별도 측정.
- **모델 위계 재평가 플래그**로 기록(즉시 라우팅 변경 없음). 의료 자문보조·disclaimer 스탠스 불변.

## 검증
- P0 baseline `logs/{kormedmcqa,hhem}_baseline.json` · Phase 1 `eval.db`(4모델 full-400, parse_fail=0).
- 제약 디코딩 라이브 확인(`{"answer": N}`). 측정 후 qwen2.5:14b 상주 복원. health 200.

## 다음 (GO 확정, 미착수)
- Phase 2: `core/faithfulness.py` — RAG groundedness **LLM-judge(로컬 qwen) 주**(HHEM NO-GO 반영), 환각률 안전 KPI.
- Phase 3: `core/llm_judge.py` — G-Eval 루브릭 + 22 한국어 메트릭.
- Phase 4: 야간 배치 + 성능 모니터링 대시보드 "품질" 카드. Phase 5: 테스트 + `docs/EVAL_GUIDE.md`.

## 변경 파일
- 신규: `scripts/eval_bench.py` · `docs/01-plan/features/llm-evaluation-hallucination.plan.md` · `docs/RELEASE_v2.112.0.md`
- 수정: `.gitignore`(eval.db) · CLAUDE.md · README.md
- 런타임(gitignored): `eval.db` · `logs/{kormedmcqa,hhem}_baseline.json`
