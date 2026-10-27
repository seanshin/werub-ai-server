# im-not-ai (Humanize KR) 심층 분석 보고서

**분석일**: 2026-06-01
**대상**: [epoko77-ai/im-not-ai](https://github.com/epoko77-ai/im-not-ai) v2.0.0
**목적**: AI 서버 도입 가능성 평가 + 추적 관리 체계 수립

---

## 1. 프로젝트 개요

### 1.1 메타데이터

| 항목 | 값 |
|------|-----|
| 저장소 | `epoko77-ai/im-not-ai` |
| 라이선스 | MIT |
| 언어 | Python |
| Stars | 1,699 |
| Forks | 174 |
| 생성일 | 2026-04-24 |
| 최종 업데이트 | 2026-05-07 |
| 크기 | 584 KB |
| 설명 | "AI가 쓴 글이 아닌 것처럼 윤문해주는 스킬" |

### 1.2 핵심 미션

ChatGPT, Claude, Gemini 등 LLM이 생성한 한국어 텍스트에서 **내용은 보존하고 문체만 자연스럽게** 윤문하는 Claude Code 스킬. 영어권 humanizer(QuillBot, Hix)가 한국어에 약한 점을 해결.

### 1.3 4대 철칙

1. **의미 불변** — 사실·수치·고유명사·직접 인용 100% 보존
2. **근거 기반** — 탐지된 구간만 수술적 수정
3. **장르 유지** — 문체 특성 보존 (칼럼→에세이 변환 금지)
4. **과윤문 방지** — 변경률 30% 경고, 50% 강제 중단

---

## 2. 아키텍처 상세 분석

### 2.1 듀얼 모드 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    Fast Mode (기본)                       │
│  5,000자 이하 / ~3분 / 3-4 tool calls                     │
│                                                          │
│  입력 → [prepare_monolith_input.py]                      │
│         ├─ metrics.py (8지표 계산)                        │
│         └─ 01_input_with_metrics.txt                     │
│       → [humanize-monolith] (탐지·윤문·자체검증 일괄)      │
│       → final.md + HTML 주석 메타                         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                  Strict Mode (--strict)                   │
│  8,000자+ 자동 / 5단계 파이프라인                          │
│                                                          │
│  입력 → [ai-tell-detector]         span 단위 JSON        │
│       → [korean-style-rewriter]    수술적 윤문            │
│       → 병렬 검증                                        │
│         ├─ [content-fidelity-auditor] 의미 동등성 13항    │
│         └─ [naturalness-reviewer]    자연도 A-D           │
│       → 오케스트레이터 판정                                │
│         (accept / rewrite_round_2 / rollback / hold)     │
└─────────────────────────────────────────────────────────┘
```

### 2.2 에이전트 구성 (12종)

| 에이전트 | 모드 | 역할 | 비고 |
|---------|------|------|------|
| `humanize-monolith` | Fast | 단일 콜 윤문 (탐지+수정+검증) | 핵심 |
| `ai-tell-detector` | Strict | span 단위 JSON 탐지 리포트 | 핵심 |
| `korean-style-rewriter` | Strict | 탐지 기반 수술적 윤문 | 핵심 |
| `content-fidelity-auditor` | Strict | 의미 훼손 감사 (13항) | 검증 |
| `naturalness-reviewer` | Strict | 자연도·잔존 패턴 심사 | 검증 |
| `korean-ai-tell-taxonomist` | 별도 | 분류 체계(SSOT) 관리 | 운영 |
| `humanize-web-architect` | 옵션 | Next.js 15 웹 서비스 설계 | 확장 |
| `korean-translation-scholar` | 별도 | 학술 자료 관리 | v2.0 |
| `post-editese-metric-engineer` | 별도 | post-editese 3축 메트릭 | v2.0 |
| `quick-rules-integrator` | 별도 | 슬림 룰북 통합 | 운영 |
| `taxonomy-gap-analyzer` | 별도 | 분류 체계 갭 분석 | 운영 |
| `translationese-research-distiller` | 별도 | 번역학 연구 증류 | v2.0 |

### 2.3 파일 구조

```
im-not-ai/
├── .claude/
│   ├── agents/                    # 12개 에이전트 정의
│   │   ├── humanize-monolith.md
│   │   ├── ai-tell-detector.md
│   │   ├── korean-style-rewriter.md
│   │   ├── content-fidelity-auditor.md
│   │   ├── naturalness-reviewer.md
│   │   ├── korean-ai-tell-taxonomist.md
│   │   ├── humanize-web-architect.md
│   │   ├── korean-translation-scholar.md
│   │   ├── post-editese-metric-engineer.md
│   │   ├── quick-rules-integrator.md
│   │   ├── taxonomy-gap-analyzer.md
│   │   └── translationese-research-distiller.md
│   ├── commands/                  # 2개 슬래시 커맨드
│   │   ├── humanize.md
│   │   └── humanize-redo.md
│   └── skills/humanize-korean/
│       └── references/            # 9개 참조 파일
│           ├── ai-tell-taxonomy.md    # 60+ 서브패턴 SSOT (590줄)
│           ├── quick-rules.md         # 슬림 룰북 (~150줄)
│           ├── rewriting-playbook.md  # 15항 윤문 처방집
│           ├── scholarship.md         # 29학자 학술 앵커
│           ├── metrics.py             # v1.6 정량 지표 (8함수)
│           ├── metrics_v2.py          # v2.0 post-editese (14함수)
│           ├── baseline.json          # KatFish 코퍼스 baseline
│           ├── baseline_v2.json       # v2.0 baseline
│           └── web-service-spec.md    # 웹 서비스 설계
├── scripts/
│   └── prepare_monolith_input.py  # 전처리 스크립트
├── tests/
│   ├── test_metrics.py            # v1.6 테스트
│   └── test_metrics_v2.py         # v2.0 테스트
├── CLAUDE.md                      # 프로젝트 지시서
├── CONTRIBUTORS.md
├── LICENSE (MIT)
└── README.md
```

---

## 3. AI 패턴 분류 체계 (Taxonomy) 상세

### 3.1 10대 카테고리 × 60+ 서브패턴

#### A. 번역투 (Translation-ese) — 19 서브패턴

| ID | 패턴 | 심각도 | 처방 |
|----|------|--------|------|
| A-1 | "~에 대해(서)" | **S1** | 목적격 조사로 직결 |
| A-2 | "~를 통해/통하여" 남발 | **S1** | "~로", "~해서"로 분산 |
| A-3 | "~에 있어(서)" | **S1** | "~에서", "~을 볼 때" |
| A-4 | "~라는 점에서" 3회+ | S2 | "~서", "~라는 이유로" |
| A-5 | "~와 관련하여" | S2 | "~에", "~의" |
| A-6 | "~에 기반하여" 남발 | S2 | "~로", "~을 보고" |
| A-7 | "가지고 있다" (have 직역) | **S1** | 형용사·동사 환원 |
| A-8 | 이중 피동 "~되어진다" | **S1** | 능동 또는 단일 피동 |
| A-9 | "~에 의해" 피동 | S2 | 행위자를 주어로 |
| A-10 | "~할 수 있다" 남발 | S2 | 단언으로 |
| A-11 | "~을 위해" 목적절 남발 | S2 | "~려고", "~위한" |
| A-15 | 추상 주어 + 만능 동사 | S2 | 구체 주어로 환원 |
| A-16 | "그/그녀/그것" 대명사 직역 | **S1** | 영형(생략) 또는 명사구 (v2.0 신규) |
| A-17 | 무정물 '-들' 과용 | hold | NMT 검증 후 재평가 |
| A-18 | ≥3어절 관형구 좌향 수식 | S2 | 문장 분리 (v2.0 신규) |
| A-19 | 이중 조사 결합 (-에서의 등) | S2 | 절·구로 풀기 (v2.0 신규) |

#### B. 영어 인용·용어 과다 — 2 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| B-1 | 매번 괄호 영어 병기 | S2 |
| B-2 | 번역 가능한 영어 그대로 | S2 |

#### C. 구조적 AI 패턴 — 12 서브패턴

| ID | 패턴 | 심각도 | 특이사항 |
|----|------|--------|----------|
| C-5 | 이모지 남발 | **S1** | |
| C-7 | "먼저·반면·결국" 3단 공식 | S2 | |
| C-9 | 숫자 괄호 인덱싱 | S2 | v1.3 신규 |
| C-10 | 콜론 부제 헤딩 "X: Y" 반복 | **S1** | Gemini 우세 시그널 |
| C-11 | 연결어미 뒤 쉼표 | **S1** | **KatFish 4.84배 분리도** (최강 신호) |
| C-12 | 쉼표 포함률 | S2 | v1.6 신규 |

#### D. AI 특유 관용구 — 7 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| D-1 | 결산 피벗 ("결론적으로" 등) | **S1** |
| D-2 | "시사하는 바가 크다" | **S1** |
| D-3 | "본질적으로/핵심적으로" | **S1** |
| D-4 | hype 어휘 3회+ | **S1** |
| D-5 | 의인화 추상 주어 | **S1** |
| D-6 | 결말 공식 "~할 때다" | **S1** |
| D-7 | 변환 공식 "X에서 Y로" | S2 |

#### E. 리듬·종결어미 — 7 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| E-1 | 문장 길이 균일 (stdev <8) | S2 |
| E-2 | 동일 종결어미 4연속 + "~고 있다" | S2 |
| E-5 | 쉼표 세그먼트 길이 | S2 |
| E-6 | 쉼표 POS 다양성 (에세이/뉴스 한정) | S2 |
| E-7 | 경어법 일관성 손실 | S2 |

#### F. 과도한 수식 — 5 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| F-4 | 한자어 명사화 -성/-적/-화 12회+ | S2 |
| F-5 | "~적 N" 추상 체인 | S2 |

#### G. Hedging — 3 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| G-1 | "~것이다" 미래 단정 남발 | S2 |
| G-2 | "~로 보인다" 추정 남발 | S2 |
| G-3 | 안전 균형 lexicon 4회+ | S2 |

#### H. 접속사 남발 — 4 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| H-1 | 문두 접속사 5회+ | **S1** |
| H-3 | 메타 진입 "이는·이 점에서" 3회+ | **S1** |
| H-4 | "즉" 남발 | S2 |

#### I. 형식명사 — 4 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| I-1 | "~인 것이다" 결말 | **S1** |
| I-2 | "X은 ~라는 점에 있다" | S2 |
| I-3 | "~다는 뜻이다" 결말 | S2 |
| I-4 | 권고형 결말 반복 | S2 |

#### J. 시각 장식 — 3 서브패턴

| ID | 패턴 | 심각도 |
|----|------|--------|
| J-1 | 마크다운 ** 강조 남발 | S2 |
| J-2 | 따옴표 강조 5회+ | **S1** |
| J-3 | 불릿 리스트 (칼럼/리포트) | S2 |

### 3.2 심각도 분류

- **S1 (결정적)**: 단일 출현으로 AI 판정 가능. 필수 제거. **총 18종**
- **S2 (강함)**: 1-2건 허용, 3건+ 제거. **총 ~30종**
- **S3 (약함)**: 패턴 중첩 시에만 문제

### 3.3 학술 기반 (scholarship.md)

| 학자 | 연도 | 기여 |
|------|------|------|
| 이영옥 | 2001 | T1: 무생물 주어 구문 기초 |
| 이근희 | 2005 | T2: 코퍼스 기반 피동 과다 분석 |
| 김정우 | 2007 | 8대 번역투 유형 정립 |
| 김도훈 | 2009 | T3: 영어 대명사 직역 식별 |
| 곽은주·진실로 | 2011 | T4: '-들' 유형학 |
| 박옥수 | 2017-18 | T5: NMT 관계절 좌향 수식 |
| 김혜영 | 2019 | T8: 종결어미 기능·경어법 |
| Baker | 1993 | 번역 보편소 4가지 |
| Toury | 1995 | 간섭 법칙 |
| Toral | 2019 | Post-editese = 강화된 번역투 |

---

## 4. 정량 메트릭 시스템

### 4.1 v1.6 메트릭 (8함수) — `metrics.py`

| 함수 | 설명 | Baseline 출처 |
|------|------|---------------|
| `comma_inclusion_rate` | 쉼표 포함 문장 비율 (0~1) | KatFish: 인간 26.31% vs AI 61.03% |
| `comma_usage_rate` | 문장당 평균 쉼표 수 | KatFish: 인간 1.13 vs AI 2.56 |
| `ending_comma_rate` | 연결어미 뒤 쉼표 비율 | KatFish: 인간 4.10% vs AI 19.83% (**4.84배**) |
| `comma_segment_length` | 쉼표 구간 평균 어절수 | KatFish: 인간 4.35 vs AI 8.56 |
| `conclusion_pivot_count` | 결산 피벗 어휘 출현 수 | 3회 초과 → S1 |
| `safe_balance_count` | 안전 균형 어휘 출현 수 | 4회 초과 → S2 |
| `hanja_nominalizer_density` | -성/-적/-화 토큰 밀도 | 12회/문서 초과 → S2 |
| `lexical_diversity` | TTR (고유 토큰 / 전체 토큰) | 인간 ~0.65 vs AI ~0.55 |

### 4.2 v2.0 메트릭 (14함수 추가) — `metrics_v2.py`

**Simplification 축 (3함수)**:
- `lexical_diversity_ttr` — v1.6 TTR 재노출
- `lexical_density` — 내용어 비율 (접미사+종결 기반 근사)
- `ending_diversity` — 종결어미 다양성 (고유/전체)

**Normalisation 축 (2함수)**:
- `normalisation_score` — 평서형 (~한다/~된다/~이다) 집중도
- `da_streak_rate` — '-다' 4연속 streak 횟수

**Interference 축 (9함수, T1~T8)**:
- `inanimate_subject_rate` — T1: 무생물 주어 + 만능 동사
- `by_passive_count` — T2a: ~에 의해 + 피동
- `double_passive_count` — T2b: 이중 피동 (되어진다 등)
- `pronoun_density` — T3: 영어 대명사 직역 밀도
- `deul_overuse_rate` — T4: 무정물 '-들' 과용
- `relative_clause_nesting` — T5: 관형절 깊이 ≥3 문장 수
- `have_make_literal_count` — T6: light verb 직역
- `double_particle_count` — T7: 이중 조사 결합
- `progressive_aspect_rate` — T8b: '~고 있다' 비율

**통합 지표**:
- `interference_index` — T1~T8 가중합 복합 지표

### 4.3 Baseline 데이터 (`baseline.json`)

```
출처: KatFish (Park et al.)
  - 인간 470편 vs LLM 1,624편
  - 에세이 771 / 시 945 / 초록 378
  - LREAD 인간 판독 실험: 직관 60% → 루브릭 90%
```

### 4.4 리스크 분류

```python
risk_score 계산:
  - comma/ending z>1.0 → +2점씩
  - lexical_diversity z<-1.0 → +1점
  - conclusion_pivot ≥2 → +1점
  - safe_balance ≥2 → +1점
  - hanja z>1.0 → +1점

risk_band: low(<4) / medium(4-5) / high(6+)
```

---

## 5. 품질 등급 체계

### 5.1 자체검증 6항 체크리스트

1. 고유명사·수치·날짜·인용 100% 보존
2. 변경률 30% 이하 (50% 초과 → 중단)
3. 장르 이탈 없음
4. register 보존 (격식체 유지)
5. 잔존 S1 패턴 0건
6. 인공 표현 자제 (원문에 없던 비유 추가 금지)

### 5.2 등급 기준

| 등급 | S1 잔존 | S2 잔존 | 변경률 | 자체검증 | 후속 |
|------|---------|---------|--------|---------|------|
| **A** | 0 | ≤2 | 10-25% | 6항 통과 | 완료 |
| **B** | 0 | ≤4 | - | 5항+ 통과 | 완료 |
| **C** | 1-2 | - | - | 4항 이하 | 2차 윤문 |
| **D** | 3+ | - | 50%+ | - | 작업 중단 |

---

## 6. 운영 체계

### 6.1 패턴 후보 풀 (pattern-candidates.md)

- 임시 ID: `cand-{category}-{YYYY}-{NNN}`
- 4단계 상태: pending → promoted / rejected / merged
- 3채널 입력: detector unmapped / rewriter persistence / reviewer external
- 90일 재발 없으면 자동 만료

### 6.2 패턴 승격 체크리스트 (promotion-checklist.md)

6개 게이트:
1. Pre-check (형식 요건)
2. Recurrence (재발 검증)
3. Main schema duplication (중복 확인)
4. Classification fit (분류 적합성)
5. Prescription fit (처방 적합성)
6. Main hierarchy (계층 정합성)

### 6.3 모델 거버넌스

- Phase 1: 합성 Claude 2편 → C-9 신규
- Phase 2: 외부 GPT 2편 → I-3, H-3 보강
- Phase 3: Gemini 4편 → C-10, D-7 신규 (모델 시그너처 분기 발견)
- **교훈**: GPT 우세 패턴이 Gemini에서는 미미 → 모델별 시그너처 존재

---

## 7. 버전 히스토리

| 버전 | 날짜 | 주요 변경 |
|------|------|----------|
| v1.2 | 2026-04-25 | Author Voice Profile (opt-in) |
| v1.3 | 2026-04-25 | 패턴 후보 풀 운영 체계 + C-9, D-7 |
| v1.5 | 2026-04-26 | Monolith Fast Path (25분→3.5분, 86% 단축) |
| v1.6 | 2026-05-06 | KatFish/LREAD 통합 + 정량 지표 8종 |
| v1.6.1 | 2026-05-07 | final.md 통합 출력 |
| **v2.0** | **2026-05-07** | 번역학계 8유형 + post-editese 3축 14메트릭 |

---

## 8. 도입 가능성 평가

### 8.1 우리 서버와의 연관 매핑

| im-not-ai 구성요소 | AI 서버 대응 영역 | 적합도 |
|--------------------|--------------------|--------|
| 10대 AI 패턴 분류 | `force_korean + quality_check` 정책 | **높음** |
| metrics.py (22함수) | `core/` 신규 모듈 후보 | **높음** |
| baseline.json | 한국어 품질 벤치마크 데이터 | **높음** |
| 7인 에이전트 파이프라인 | Orchestration SSE/async 확장 | 중간 |
| quick-rules.md 슬림 룰북 | 경량 프롬프트 삽입 | **높음** |
| scholarship.md 학술 기반 | 의료 AI 근거 기반과 유사 패턴 | 중간 |
| 패턴 후보 풀 운영 체계 | 장기 품질 관리 프레임워크 | 낮음 |

### 8.2 도입 가능 영역 (5가지)

#### 영역 1: Story Engine / Novel Writer 후처리 (우선순위 1)

**적용 대상**: `routes/story.py` (31 API), `routes/novel.py` (78 API)
**방법**:
- `metrics.py` + `metrics_v2.py`를 `core/korean_quality.py`로 내재화
- 소설/스토리 생성 후 자동 품질 점수 산출
- risk_band가 medium/high이면 자동 윤문 트리거
- quick-rules.md 기반 경량 프롬프트로 Ollama 모델에 윤문 지시

**예상 효과**: 스토리 출력의 번역투·관용구 대폭 감소

#### 영역 2: 채팅 응답 자연화 (우선순위 2)

**적용 대상**: 6도메인 채팅 라우팅
**방법**:
- 응답 생성 후 경량 metrics 체크 (comma_inclusion_rate, ending_comma_rate, conclusion_pivot_count 3종)
- S1 패턴 탐지 시 재생성 또는 후처리
- system prompt에 quick-rules.md S1 항목 주입

**예상 효과**: AI 느낌 감소, 자연스러운 대화

#### 영역 3: 정량 품질 대시보드 (우선순위 3)

**적용 대상**: `routes/dashboard_api.py`
**방법**:
- 기존 대시보드 API에 한국어 품질 메트릭 추가
- 출력 텍스트별 risk_band / z-score 추적
- 시계열 품질 트렌드 모니터링

**예상 효과**: 품질 가시성 확보, 모델 비교 근거

#### 영역 4: 의료 AI 한국어 출력 강화 (우선순위 4)

**적용 대상**: `routes/medical_advanced.py`, Medical Ensemble
**방법**:
- 의료 보고서 생성 시 번역투 패턴 제거
- Do-NOT list에 의료 용어·약물명 추가
- PE 체크리스트 중 PE1(무생물 주어), PE7(have/make), PE9(종결어미) 집중 적용

**예상 효과**: 의료 보고서의 가독성·신뢰감 향상

#### 영역 5: Writing 기능 강화 (우선순위 5)

**적용 대상**: Writing 3종 모델
**방법**:
- rewriting-playbook.md의 장르별 미세 조정 테이블 적용
- 칼럼·에세이·리포트·블로그별 차별화된 후처리
- 품질 등급(A-D) 자동 산출 및 사용자 피드백

**예상 효과**: 전문적 글쓰기 품질

### 8.3 도입 비용 분석

| 항목 | 비용 | 비고 |
|------|------|------|
| metrics.py 이식 | **낮음** | stdlib만 사용, 의존성 0 |
| baseline.json 이식 | **낮음** | 정적 JSON 파일 |
| quick-rules.md 이식 | **낮음** | 프롬프트 텍스트 |
| 전처리 파이프라인 | 중간 | prepare_monolith_input.py 수정 필요 |
| 에이전트 파이프라인 | **높음** | Claude Code 전용, Ollama 적응 필요 |
| 학술 자료 | 해당 없음 | 참조용으로만 활용 |

### 8.4 도입 불가/불필요 영역

| 영역 | 사유 |
|------|------|
| Claude Code 에이전트 시스템 | Claude Code 전용, 우리는 Flask+Ollama |
| Author Voice Profile | 과도한 복잡성, 현 단계에 불필요 |
| 패턴 후보 풀 운영 | 장기 운영 인력 필요, 현 단계 과잉 |
| 웹 서비스 아키텍처 | Next.js, 우리와 기술스택 불일치 |

---

## 9. 도입 로드맵

### Phase 1: 핵심 모듈 이식 (즉시)

```
1. core/korean_quality.py 생성
   - metrics.py 8함수 + metrics_v2.py 14함수 이식
   - baseline.json 포함
   - compute_all() → compute_korean_quality() 래핑

2. core/korean_rules.py 생성
   - quick-rules.md S1 패턴 18종 → JSON/dict 구조화
   - Do-NOT list 포함
   - 장르별 미세 조정 파라미터

3. 단위 테스트
   - test_metrics.py, test_metrics_v2.py 이식
```

### Phase 2: Story/Novel 통합 (단기)

```
1. routes/story.py, routes/novel.py 후처리 파이프라인 추가
   - 생성 후 compute_korean_quality() 호출
   - risk_band 기반 자동 윤문 여부 판정
   - system prompt에 S1 패턴 규칙 삽입

2. API 응답에 quality_metrics 필드 추가
   - risk_band, risk_score, z_scores
```

### Phase 3: 전체 확산 (중기)

```
1. 채팅 6도메인 경량 체크
2. 의료 AI 보고서 한국어 강화
3. 대시보드 품질 메트릭 통합
4. Writing 장르별 후처리
```

---

## 10. 추적 관리 체계

### 10.1 출처 관리

| 항목 | 출처 URL | 추적 방법 |
|------|---------|----------|
| 저장소 | `github.com/epoko77-ai/im-not-ai` | GitHub Watch + Release 알림 |
| 분류 체계 | `references/ai-tell-taxonomy.md` | 버전 태그 비교 (현재 v2.0) |
| 메트릭 | `references/metrics.py` + `metrics_v2.py` | 함수 시그니처 diff |
| 슬림 룰북 | `references/quick-rules.md` | S1/S2 항목 수 추적 |
| Baseline | `references/baseline.json` | 데이터 셀 비교 |
| 학술 자료 | `references/scholarship.md` | 학자 수·인용 추적 |

### 10.2 업데이트 반영 프로세스

```
1. 주기적 확인 (월 1회)
   gh api repos/epoko77-ai/im-not-ai/releases --jq '.[0]'
   gh api repos/epoko77-ai/im-not-ai/commits --jq '.[0:5]'

2. 변경 감지 시
   - taxonomy.md 패턴 추가/보강 → korean_rules.py 갱신
   - metrics.py 함수 추가 → korean_quality.py 갱신
   - baseline.json 데이터 변경 → baseline 파일 교체
   - scholarship.md 학자 추가 → 참조용 기록

3. 버전 추적 기록
   docs/03-analysis/humanize-kr-changelog.md에 기록
```

### 10.3 자동 추적 스크립트 (향후 CI/CD 연동)

```bash
#!/bin/bash
# scripts/check-humanize-kr-updates.sh

REPO="epoko77-ai/im-not-ai"
LAST_KNOWN_SHA="<현재 최신 커밋 SHA>"

LATEST=$(gh api repos/$REPO/commits --jq '.[0].sha')
if [ "$LATEST" != "$LAST_KNOWN_SHA" ]; then
    echo "⚠️  im-not-ai 업데이트 감지: $LATEST"
    gh api repos/$REPO/commits --jq '.[0:3] | .[] | "\(.sha[0:7]) \(.commit.message | split("\n")[0])"'
fi
```

---

## 11. 리스크 및 고려사항

### 11.1 기술적 리스크

| 리스크 | 수준 | 완화 방안 |
|--------|------|----------|
| Ollama 모델 한국어 품질 한계 | 중간 | qwen2.5가 한국어 양호, 프롬프트 보정 |
| 형태소 분석 없는 regex 근사 | 낮음 | 원본도 stdlib만 사용, 동일 조건 |
| 과윤문으로 의미 변질 | 중간 | 변경률 가드 (30%/50%) 그대로 적용 |
| 모델별 시그너처 차이 | 낮음 | Ollama 모델 전용 baseline 축적 필요 |

### 11.2 라이선스

MIT 라이선스 — 상업적 사용, 수정, 배포 모두 허용. 저작권 표시 + 라이선스 포함 의무.

### 11.3 학술 데이터 신뢰도 (Caveats)

원본 프로젝트가 명시한 6가지 주의사항:
1. **C1**: 김혜영(2019) 정량 검증 미완 — T8 임계값은 'estimated'
2. **C2**: NMT/LLM 비교 편향 — 모델 순위 정량치 흡수 금지
3. **C3**: Post-editese 한국어 직접 검증 없음 — Toral(2019) 5개 언어쌍에 한국어 미포함
4. **C4**: 8유형 통합 NMT 연구 없음 — 7명 연구자 종합
5. **C5**: 일본어 번역투 영향 논쟁 — 단순 ~의는 비대상
6. **C6**: LLM 진화 가속 — 6개월 재검증 주기 필요

---

## 12. 결론

im-not-ai는 **한국어 AI 텍스트 자연화**라는 특정 문제에 대해 학술적 근거 + 정량 메트릭 + 운영 체계를 갖춘 프로덕션급 솔루션이다.

우리 AI 서버에 가장 즉시 도입 가능한 부분은:
1. **`metrics.py` + `metrics_v2.py` (22함수)** — stdlib만 사용, 의존성 0, 즉시 이식 가능
2. **`quick-rules.md` S1 패턴 18종** — 프롬프트 삽입으로 즉시 효과
3. **`baseline.json` KatFish 데이터** — 품질 벤치마크 기준선

Story Engine, Novel Writer, 채팅, 의료 AI 등 한국어 출력이 있는 모든 영역에서 **번역투 제거 + 자연스러움 향상** 효과를 기대할 수 있다.

---

*이 보고서는 epoko77-ai/im-not-ai v2.0.0 (2026-05-07 기준) 분석 결과이며, 향후 업스트림 변경 시 docs/03-analysis/humanize-kr-changelog.md에 추적 기록한다.*
