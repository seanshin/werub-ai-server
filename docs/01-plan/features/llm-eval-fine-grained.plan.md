# LLM 품질 평가 세밀화 — 검토 보고서 + 단계 계획 (2026-07-06 · 2차 조사 07-07)

## [2차 조사] 추가 측정 축 (2026-07-07 — "품질 지표를 더 측정할 방법" 재검색)

v2.120~123 반영분(MiniCheck·체크리스트·질의 풀·종합 지수) 이후 잔여 공백 축 조사.

### 추천 우선순위 (전부 자체 구현 가능·외부 API 0)

1. **검색(Retrieval) 품질 축** — 현 스택은 생성 충실도만 측정, 검색 품질 미측정
   (실사고 2건을 수동 발견: v2.114.1 rank-폴백·legal_min_score). RAGAS 계열
   **context precision**(청크별 관련성 — 기존 judge 이진 판정)+**context recall**·
   noise sensitivity 개념. 야간 배치 합류 시 retrieval 회귀 자동 감시.
2. **judge 신뢰도 meta-eval(자기일관성)** — 같은 답변 3~5회 반복 채점 → 점수 분산·
   체크 항목별 flip rate. 근거: [Reliability without Validity(2026, 21모델 54만 판정
   — kappa deflation 3~4×)](https://arxiv.org/pdf/2606.19544) ·
   [IRT 진단(2026)](https://arxiv.org/pdf/2602.00521) · 우리 실증(conditions_kept
   엄격성·정의 미추종). K-HALU(AI-HUB 대기)의 로컬 보완재.
3. **KoSimpleQA 벤치** — 한국어 사실성 1,000문항([HF bzantium/KoSimpleQA](https://huggingface.co/datasets/bzantium/KoSimpleQA)
   · [논문](https://arxiv.org/abs/2510.18368), 번역 아닌 한국 지식·최강 33.7%).
   **HF 직다운=사용자 액션 불요**. eval_bench 4번째 벤치(단답=judge 채점).
4. **Semantic Entropy**(응답 수준 UQ) — 샘플 클러스터링(의미)→엔트로피
   ([Farquhar Nature 2024 계열](https://arxiv.org/abs/2406.15927)). SelfCheck의
   응답 수준 개선판 — bge-m3 로컬 임베딩 클러스터링으로 구현. 비-RAG 경로 강화.
5. 패러프레이즈 강건성 — 동일 질문 2~3 표현 → 답변 의미 유사도(bge-m3).

### 비채택/보류 (2차)
- **Semantic Entropy Probes**: hidden states 필요 → Ollama 불가.
- **UQLM 패키지**([CVS Health](https://github.com/cvs-health/uqlm)): 유용한 스코어러
  모음이나 LangChain 계열 의존 — 기법만 자체 구현(기조: 의존 최소).
- **MedSafetyBench**([2403.03744](https://arxiv.org/abs/2403.03744), AMA 윤리 기반
  의료 안전 900케이스)·FACTS Leaderboard: 영어 중심 — 안전 축 설계 개념만 참조.

---


> 요청: "LLM 품질 평가 카드를 더 디테일하고 세밀하게 구성 가능한지 검토. 새로 나온
> 논문들도 검토하고, 오픈소스 있으면 검색해서 추가."
> 기조(불변): **외부 API 0**(자체호스팅·로컬 judge) · 16GB VRAM · 측정 선행 · HHEM 사용금지.

## 1. 현행 스택 (v2.113~114)

| 축 | 구현 | 세밀도 |
|---|---|---|
| RAG 충실도 | `core/faithfulness.py` — 주장 분해→supported/unsupported/contradicted | 주장 단위 3분류(단일 judge) |
| 가치/품질 | `core/llm_judge.py` — G-Eval 4차원(정확·도움·안전·자연성) 1~5+사유 | 차원 단위 5점 척도 |
| 비-RAG 환각 | `core/selfcheck.py` — 재샘플 일관성 | 주장 단위 |
| 벤치 | `scripts/eval_bench.py` — KorMedMCQA 400 | 모델×정답률 |
| 운영 | 야간 cron 03:30 → eval.db → Admin 카드(현재값+추세) | 질의 단위 집계 |

**세밀화 공백**: ①충실도가 단일 judge 의견(교차 검증 없음) ②1~5 점수는 원인 비특정
(왜 4점인지→개선 지점 불명) ③judge 자체의 한국어 환각 검출력 미검증(meta-eval 부재)
④한국어 환각 전용 벤치 미도입.

## 2. 논문·오픈소스 조사 (2025~2026)

### 채택 (이번 릴리즈 반영)
- **MiniCheck / Bespoke-MiniCheck-7B** (EMNLP 2024, [GitHub](https://github.com/Liyan06/MiniCheck) ·
  [Ollama](https://ollama.com/library/bespoke-minicheck)): 근거 대비 주장 검증 **전용** 소형모델
  (SOTA fact-checking, Document+Claim→Yes/No). Ollama 배포라 인프라 추가 0.
  **🎯 한국어 변별 PoC 10/10 GO**(2026-07-06 GPU 실측): HHEM이 2/4였던 통제 페어 4종 +
  Q5-class 난문(괄호 한정조건 드롭·호 번호-내용 오배치) + 수치 스왑 + 패러프레이즈 전부 정확.
  → **주장 단위 교차검증**으로 채택(아래 §3-①).
- **체크리스트 분해 계열** — CheckEval(이진 체크리스트 집계)·
  [Decomposed Criteria (EMNLP 2025 Industry)](https://aclanthology.org/2025.emnlp-industry.136.pdf)·
  [BiGGen Bench(2025, instance-rubric)](https://arxiv.org/html/2603.25133v1) 등: **이진 판정이
  1~5 점수보다 judge 신뢰도↑·해석 가능성↑**(연구 일관). → 4차원×3 이진 기준=12항목 채택(§3-②).

### 도입 대기 (외부 게이트)
- **K-HALU** (ICLR 2025, [GitHub](https://github.com/J-Seo/K-HALU)): 한국어 환각 검출 벤치
  2,170문항·7도메인·다중정답. **judge(qwen2.5:14b)의 한국어 환각 검출력 meta-eval**에 최적.
  ⚠️ 데이터가 **AI-HUB 사용신청**(aihub.or.kr 계정·정책 동의) 게이트 → **사용자 액션 대기**.
  신청 완료 시 `k_halu_test_v1_1.jsonl` → eval_bench-style 러너 추가(반나절).
- **KMMLU-Redux/Pro** (2025, [HF LGAI-EXAONE](https://huggingface.co/datasets/LGAI-EXAONE/KMMLU-Pro)):
  오염제거 한국어 전문시험 벤치. HF 직접 다운로드 가능 — KorMedMCQA 보완 벤치 후보(요청 시).

### 검토 후 비채택
- **Lynx-8B**(Patronus, 환각검출 특화): 영어 중심·8B 추가 상주 부담, MiniCheck가 동급 역할
  + 한국어 검증 완료라 중복. **LettuceDetect**(2025, 토큰 단위 RAG 환각): ModernBERT 영어
  전용 — 한국어 부적합. **RAGTruth/HaluBench**: 영어 코퍼스. **HHEM**: 기존 NO-GO 유지.
- **DeepEval/RAGAS/promptfoo 프레임워크 도입**: 핵심 기법(주장 분해·G-Eval)은 이미 자체 구현
  — 프레임워크 의존 추가보다 로컬 최적화 유지(기조 일관).

## 3. 이번 반영 (v2.120.0)

### ① MiniCheck 주장 단위 교차검증 (`core/faithfulness.py`)
- `minicheck_verify`(주장별 Yes/No/None)·`merge_cross_check`(순수 집계)·`cross_check`.
- 신규 지표: **mc_agree**(judge↔minicheck 일치율)·**mc_conflicts**(불일치=리뷰 트리거)·
  **strict_faithfulness**(양측 모두 supported 비율 — 보수 지표).
- **faithfulness(주 KPI)는 불변** = 추세 연속성 유지. 교차검증은 세밀 진단 축.
- 러너 2단계 배칭: judge(상주) 전 질의 → minicheck 일괄(모델 스왑 1회, 야간 배치 오버헤드 ~1분).
  모델 미설치/실패 시 자동 생략(비차단·`--no-cross-check`).
- VRAM: minicheck 7B(4.7GB)는 상주 14b와 동시 불가(16GB) → 스왑 후 상주 자동복원(keep_alive).

### ② 체크리스트 분해 judge (`core/llm_judge.py`)
- `CHECKLIST` 4차원×3 이진 기준(12항목): 수치 일치·**한정조건 유지**(Q5-class 직접 반영)·
  근거 인용·면책·처방지시 금지·번역투 등. `judge(checklist=True)`.
- 신규 지표: **checks_rate**·**checks_failed**(실패 항목 = 곧 개선 지점, 폐루프 연결).
- 점수(1~5)·overall 의미 불변(스키마 확장만) — 추세 카드 연속.

### ③ eval.db 마이그레이션 + 야간 배치 자동 적용
- faithfulness_results +3컬럼(mc_agree·mc_conflicts·strict_faithfulness),
  quality_results +2컬럼(checks_rate·checks_failed). ALTER 멱등.
- 야간 cron은 러너 기본값 변경으로 자동 적용(크론탭 무변경).

## 4. 후속 로드맵 (우선순위순)

1. **K-HALU meta-eval** — AI-HUB 신청(사용자) → judge 검출력 측정·judge 모델 선택 근거화.
2. **Admin 카드 세밀 표시** — mc_agree/strict/checks_failed 노출(데이터 축적 후, 카드 개편 1회로).
3. **KMMLU-Pro(HF) 벤치 확장** — 의료 subset, eval_bench 4번째 config.
4. **instance-specific rubric**(BiGGen 방향) — 질의 유형별 체크리스트 가변화(연구성, 보류).
5. **주장 단위 영속화** — 현재 claims/minicheck 판정이 카운트만 DB 저장(원문은 로그) →
   claim-level JSON 컬럼 추가 시 conflicts 원인 드릴다운 가능(저우선).

## 5. 검증
- 회귀: test_faithfulness 10→14·test_llm_judge 6→10(순수 로직 — 병합·집계·스키마·정규화).
- 라이브: PoC 10/10 + 야간 배치 실경로 E2E(mc_agree·checks 산출) — 릴리즈 노트 참조.
