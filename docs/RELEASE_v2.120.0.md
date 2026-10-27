# RELEASE v2.120.0 — LLM 평가 세밀화 (MiniCheck 교차검증 + 체크리스트 judge)

**날짜**: 2026-07-06
**헤드라인**: LLM 품질 평가를 주장·항목 단위로 세밀화 — ①**MiniCheck 교차검증**(검증 특화
로컬 7B, 한국어 PoC 10/10 GO)으로 충실도에 2차 의견 축 추가 ②**체크리스트 분해 judge**
(4차원×3 이진 기준 12항목 — 실패 항목이 곧 개선 지점) ③2025~26 논문·오픈소스 조사 보고.
조사·로드맵: `docs/01-plan/features/llm-eval-fine-grained.plan.md`.

---

## ① MiniCheck 주장 단위 교차검증 (`core/faithfulness.py`)

- **모델**: [Bespoke-MiniCheck-7B](https://ollama.com/library/bespoke-minicheck)(EMNLP 2024,
  grounded fact-checking 전용·Ollama 배포=인프라 추가 0, 4.7GB).
- **🎯 한국어 변별 PoC 10/10**(측정 선행): HHEM이 2/4였던 통제 페어 + **Q5-class 난문**
  (괄호 한정조건 드롭·호 번호-내용 오배치) + 항목별 수치 스왑 + 패러프레이즈 — 전부 정확.
- **신규 지표**(faithfulness 주 KPI는 불변=추세 연속): `mc_agree`(judge↔MiniCheck 일치율)·
  `mc_conflicts`(불일치 주장 수=**리뷰 트리거**)·`strict_faithfulness`(양측 모두 supported
  비율, 보수 지표).
- **🐞 장문 근거 드리프트 fix**: ~4K tok 근거 통째 입력 시 Yes/No 대신 설명문 시작
  (done_reason=length, 라이브 5문항 중 3문항 교차검증 불능) → MiniCheck 정식 프로토콜인
  **청크 분할(문단 경계 1500자)+max-over-chunks**(어느 청크든 지지=supported)로 해결.
  HHEM을 NO-GO시킨 512tok 절단과 같은 클래스지만 MiniCheck는 설계상 청킹이 정답.
- **운영**: 러너 2단계 배칭(judge 전 질의→MiniCheck 일괄=모델 스왑 1회, 상주 자동복원).
  모델 미설치/실패 시 자동 생략(비차단·`--no-cross-check`). 야간 cron 무변경 자동 적용.

## ② 체크리스트 분해 judge (`core/llm_judge.py`, CheckEval-style)

- 근거: 이진 판정이 1~5 점수보다 judge 신뢰도·해석가능성 높음(CheckEval·EMNLP 2025
  Industry·BiGGen 등 2025~26 연구 일관).
- `CHECKLIST` 4차원×3 이진 기준(12항목): 수치 일치·**한정조건 유지**(Q5-class 직접 반영)·
  근거 인용·직접 답변·완결성·면책 고지·처방 지시 금지·전문가 안내·번역투·경어 일관·깨진 문자.
- 신규 지표: `checks_rate`·`checks_failed`(실패 항목 목록 → **측정→개선 폐루프의 개선
  지점 특정**). 1~5 점수·overall 의미 불변(스키마 확장만).

## ③ 조사 결과 (상세는 plan 문서)

- **도입 대기(외부 게이트)**: K-HALU(ICLR 2025 한국어 환각 벤치 2,170문항) — **AI-HUB
  사용신청 필요(사용자 액션)**, 승인 시 judge meta-eval 러너 추가. KMMLU-Redux/Pro(HF
  직접 다운로드)=벤치 확장 후보.
- **비채택**: Lynx(영어·중복)·LettuceDetect(영어 전용)·RAGTruth/HaluBench(영어)·
  HHEM(기존 NO-GO)·DeepEval/RAGAS 프레임워크(핵심 기법 기구현·의존 최소 기조).

## 검증

- 회귀: test_faithfulness 10→**16**(교차검증 병합·집계·청킹·max-over-chunks)+
  test_llm_judge 6→**10**(스키마·정규화·집계·E2E). GPU 통과.
- **라이브 실측**(GPU, 의료법 5질의): faithfulness 전부 1.000 유지(주 KPI 불변) +
  교차검증 — Q1~Q3 `mc_agree=1.0`(완전 일치), Q4 conflicts=1, **Q5 conflicts=3/4**
  (당일 생성 정밀도를 수정했던 바로 그 문항에서 리뷰 트리거 발화 — judge 단독으로는
  안 보이던 세밀 신호). 불일치=검토 신호이지 환각 확정 아님(판독 규율 v2.113 일관).
  eval.db +5컬럼 마이그레이션 확인.

## 영향 / 하위호환

- faithfulness·overall 등 기존 KPI 의미 불변(추세 연속). 신규 지표는 추가 컬럼.
- MiniCheck는 야간 배치 시에만 로드(스왑 1회, 상주 자동복원) — 주간 서빙 무영향.
