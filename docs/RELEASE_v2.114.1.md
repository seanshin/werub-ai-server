# Release v2.114.1 — 의료법 retrieval 튜닝 + KorMedMCQA-V 영상 벤치

> 2026-07-03 | v2.114.0 후속 소품 릴리즈. 관련: `docs/01-plan/features/llm-evaluation-hallucination.plan.md`

## 요약

LLM 평가 프레임워크가 지목한 마지막 실품질 이슈(의료법 충실도 Q5-class)를 근본 수정하고,
MedGemma1.5의 존재 이유인 vision 능력을 표준 벤치로 정량화했다.

## 1. 의료법 retrieval 튜닝 (실 품질 개선)

- **진단**: "환자 진료기록 사본 발급 거부" 질의에서 정답 **제21조가 rank-2로 인출되나 score 0.234로 legal
  게이트(0.33)에 탈락** — 랭킹은 정상, 절대 점수 게이트가 문제(자연어 질문↔법령 원문 유사도가 전반 낮음).
- **fix**(`routes/medical_law.py _rag_law_search`): rank-기반 폴백 — 게이트 통과 법령이 2건 미만이면 법령
  코퍼스 top을 floor 0.20으로 보강(전권위 큐레이션이라 안전). `scripts/eval_faithfulness.get_evidence`도
  법령 컬렉션 floor 0.2로 정합.
- **결과**: Q5 충실도 **0.250→0.800**(제21조 인용·제28조 오인용 소멸), 5질의 평균 **0.600→0.893**.
  잔여 Q5 contradicted 1건 = 친족 열람권 조건 난문(생성 정밀도, retrieval 아님 → 백로그).

## 2. KorMedMCQA-V 영상 멀티모달 벤치

- **`scripts/eval_kormedmcqa_v.py`**(신규): KorMedMCQA-V(면허시험 영상 문항) — Ollama `images`(raw base64,
  **`data:` 접두 제거 필수**) + format 제약 디코딩. 외부 API 0.
- **결과**(doctor 30문항, pf=0):
  | 모델 | 정답률 |
  |---|---|
  | MedGemma1.5:4b Q8 | **26.7%** (chance 20% 근처) |
  | qwen3-vl:8b | 0% (이미지에 빈 응답 = Ollama 통합 이슈, 품질 판정 아님) |
- → MedGemma **텍스트 35.2% + 영상 26.7%** = vision 가치도 약함(fleet 재검토 근거 보강). ⚠️ 캐비엇:
  MC≠자유 판독·N=30·실경로 판독 프롬프트는 더 정교.

## 검증

- 회귀 22(faithfulness/judge/selfcheck) 통과 · Q5 재검증(제21조 인용·제28조 오인용 소멸) · graceful HUP · health 200.

## 백로그 (측정이 발견)

- qwen3-vl 이미지 통합(`/api/chat` 포맷 — 빈 응답 원인) · Q5 친족 열람권 조건 생성 정밀도 · K-HALU/KMMLU.

## 변경 파일

- 신규: `scripts/eval_kormedmcqa_v.py` · `docs/RELEASE_v2.114.1.md`
- 수정: `routes/medical_law.py`(rank-폴백) · `scripts/eval_faithfulness.py`(법령 floor 0.2) ·
  기획서 · CLAUDE.md · README.md
