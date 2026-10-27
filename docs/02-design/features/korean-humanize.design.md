# 한국어 AI 텍스트 자연화 (Korean Humanize) 설계서

**버전**: 1.0
**작성일**: 2026-06-01
**기획서**: `docs/01-plan/features/korean-humanize.plan.md`
**출처**: epoko77-ai/im-not-ai v2.0.0 (MIT)

---

## 1. 모듈 구조

### 1.1 파일 배치

```
core/
├── korean.py                  # 기존 (force_korean, quality_check)
├── korean_humanize.py         # 신규 — 22 메트릭 함수 + 통합 API
├── data/
│   ├── baseline.json          # KatFish v1.6 baseline (essay/poetry/abstract)
│   ├── baseline_v2.json       # v2.0 post-editese baseline
│   └── humanize_rules.json    # S1/S2 패턴 규칙 + 프롬프트 템플릿
tests/
└── test_korean_humanize.py    # 이식 테스트
```

### 1.2 의존성

- **외부 의존성: 없음** (stdlib only: re, json, math, collections, statistics, os, argparse)
- 기존 `core/korean.py`와 import 관계 없음 (독립 모듈)
- 기존 `core/quality.py`와 import 관계 없음

---

## 2. core/korean_humanize.py 설계

### 2.1 모듈 구조

```python
"""한국어 AI 텍스트 자연화 메트릭 + 품질 분석

원본: epoko77-ai/im-not-ai (MIT License)
- metrics.py v1.6 (8함수)
- metrics_v2.py v2.0 (14함수)
통합 이식본.
"""

# ── 상수 ──
VERSION = "v2.0"

# ── 내부 헬퍼 ──
_split_sentences(text)         # 문장 분리
_eojeols(text)                 # 어절 분리
_strip_punct(token)            # 구두점 제거

# ── v1.6 메트릭 (8함수) ──
comma_inclusion_rate(text)     # 쉼표 포함 문장 비율
comma_usage_rate(text)         # 문장당 평균 쉼표
ending_comma_rate(text)        # 연결어미 뒤 쉼표 비율 ★최강 신호
comma_segment_length(text)     # 쉼표 구간 평균 어절수
conclusion_pivot_count(text)   # 결산 피벗 어휘 출현수
safe_balance_count(text)       # 안전 균형 어휘 출현수
hanja_nominalizer_density(text)# -성/-적/-화 밀도
lexical_diversity(text)        # TTR

# ── v2.0 Simplification 축 (3함수) ──
lexical_diversity_ttr(text)    # TTR 재노출
lexical_density(text)          # 내용어 비율
ending_diversity(text)         # 종결어미 다양성

# ── v2.0 Normalisation 축 (2함수) ──
normalisation_score(text)      # 평서형 집중도
da_streak_rate(text)           # '-다' 4연속 streak

# ── v2.0 Interference 축 T1~T8 (9함수) ──
inanimate_subject_rate(text)   # T1: 무생물 주어
by_passive_count(text)         # T2a: ~에 의해
double_passive_count(text)     # T2b: 이중 피동
pronoun_density(text)          # T3: 대명사 직역
deul_overuse_rate(text)        # T4: 무정물 '-들'
relative_clause_nesting(text)  # T5: 관형절 깊이
have_make_literal_count(text)  # T6: light verb
double_particle_count(text)    # T7: 이중 조사
progressive_aspect_rate(text)  # T8b: '~고 있다'

# ── 통합 지표 ──
interference_index(text)       # T1~T8 가중합

# ── 공개 API ──
compute_korean_quality(text, genre="essay")  # 전체 분석
get_s1_prompt_rules()          # S1 규칙 프롬프트 텍스트
```

### 2.2 compute_korean_quality() 반환 구조

```python
{
    "version": "v2.0",
    "genre": "essay",
    "char_count": 2604,
    
    # v1.6 메트릭
    "metrics": {
        "comma_inclusion_rate": 0.45,
        "comma_usage_rate": 1.8,
        "ending_comma_rate": 0.12,
        "comma_segment_length": 6.2,
        "conclusion_pivot_count": 2,
        "safe_balance_count": 1,
        "hanja_nominalizer_density": 0.04,
        "lexical_diversity": 0.58
    },
    
    # v1.6 z-score (양수=AI방향)
    "z_scores": {
        "comma_inclusion_rate": 0.8,
        "ending_comma_rate": 1.2,  # ★ S1 트리거
        ...
    },
    
    # 리스크 판정
    "risk_band": "medium",   # low / medium / high
    "risk_score": 4,
    
    # v2.0 post-editese
    "v2_metrics": {
        "normalisation_score": 0.65,
        "da_streak_rate": 1,
        "pronoun_density": 0.02,
        ...
    },
    "v2_interference_index": {
        "weighted_total": 0.35,
        "components": {...}
    },
    
    # 증거
    "evidence": {
        "conclusion_pivots": ["따라서", "결론적으로"],
        "safe_balances": ["균형"]
    }
}
```

### 2.3 get_s1_prompt_rules() 반환

```python
"""S1 패턴 제거 규칙을 system prompt 삽입용 텍스트로 반환.

Returns:
    str: 프롬프트에 삽입할 한국어 규칙 텍스트
"""
```

반환 예:
```
[한국어 자연스러움 규칙 — 아래 패턴은 반드시 피하세요]

1. 번역투 금지:
   - "~에 대해" → "~를" 직접 연결
   - "~를 통해" → "~로", "~해서"
   - "~에 있어서" → "~에서"
   - "가지고 있다" → 형용사/동사로 환원
   - 이중 피동 "~되어진다" → "~된다"
   - "그/그녀/그들" 남발 → 생략 또는 명사구

2. AI 관용구 삭제:
   - "결론적으로", "요약하면" → 삭제
   - "시사하는 바가 크다" → 삭제
   - "본질적으로", "핵심적으로" → 삭제
   - "혁신적인", "획기적인" → 구체 서술로
   - "~할 때다", "지금이야말로" → 평서로

3. 구조 규칙:
   - 이모지 사용 금지
   - "X: Y" 콜론 부제 반복 금지
   - 연결어미(-고/-며/-지만) 뒤 쉼표 금지

4. 접속사·형식명사:
   - 문두 접속사 3개 이상 연속 금지
   - "이는", "이 점에서" 남발 금지
   - "~인 것이다" 결말 → 평서형으로
   - 따옴표 강조 5회 이상 금지
```

---

## 3. core/data/humanize_rules.json 설계

```json
{
  "version": "v2.0",
  "source": "epoko77-ai/im-not-ai",
  "s1_patterns": [
    {
      "id": "A-1", "category": "번역투",
      "pattern": "~에 대해(서)",
      "fix": "목적격 조사로 직결"
    },
    {
      "id": "A-2", "category": "번역투",
      "pattern": "~를 통해/통하여 남발",
      "fix": "~로, ~해서, ~함으로써로 분산"
    }
    // ... S1 18종 전부
  ],
  "s2_patterns": [
    // S2 핵심 ~15종
  ],
  "do_not_touch": [
    "고유명사·제품명·모델명",
    "수치·날짜·단위",
    "큰따옴표 안 직접 인용",
    "법률 조문",
    "영어 약어(LLM·GPU·API 등)"
  ],
  "genre_config": {
    "essay": {"allow_emoji": false, "allow_bullet": false, "heading_max": 0},
    "report": {"allow_emoji": false, "allow_bullet": true, "heading_max": 1},
    "blog": {"allow_emoji": true, "allow_bullet": true, "heading_max": 2},
    "column": {"allow_emoji": false, "allow_bullet": false, "heading_max": 0}
  },
  "change_rate_guard": {
    "warning": 0.30,
    "halt": 0.50
  },
  "prompt_template": "..."
}
```

---

## 4. 통합 포인트

### 4.1 Story Engine (routes/story.py) 통합

```python
from core.korean_humanize import compute_korean_quality, get_s1_prompt_rules

# 스토리 생성 후 품질 체크
def _post_generate_quality(text, genre="essay"):
    """생성 후 품질 메트릭 산출. risk_band 반환."""
    result = compute_korean_quality(text, genre=genre)
    return result

# 재생성 판정
def _should_regenerate(quality_result):
    """risk_band=high이면 True"""
    return quality_result.get("risk_band") == "high"

# 프롬프트 보정
def _inject_quality_rules(system_prompt):
    """S1 규칙을 system prompt에 삽입"""
    rules = get_s1_prompt_rules()
    return f"{system_prompt}\n\n{rules}"
```

### 4.2 호출 흐름

```
사용자 요청
    ↓
[1차 생성] (model + system_prompt + prompt)
    ↓
force_korean(response)        ← core/korean.py (문자 필터)
    ↓
compute_korean_quality(text)  ← core/korean_humanize.py (문체 분석)
    ↓
risk_band == "low"  → 반환 (quality_metrics 첨부)
risk_band == "medium" → 반환 (quality_metrics 첨부 + 경고 로그)
risk_band == "high" → [2차 생성] (S1 규칙 삽입)
                        ↓
                      force_korean → compute_korean_quality
                        ↓
                      반환 (quality_metrics 첨부, retry=1)
```

### 4.3 API 응답 확장

```python
# routes/story.py 응답 예시
{
    "story_id": "...",
    "content": "...",
    "quality_metrics": {            # 신규 필드
        "risk_band": "low",
        "risk_score": 2,
        "ending_comma_rate_z": 0.3,
        "conclusion_pivot_count": 1,
        "retry_count": 0
    }
}
```

---

## 5. 테스트 계획

### 5.1 단위 테스트 (test_korean_humanize.py)

```python
# 원본 test_metrics.py 이식 + 확장

class TestV16Metrics:
    """v1.6 8함수 테스트"""
    def test_comma_inclusion_rate_basic(self): ...
    def test_ending_comma_rate_connective(self): ...
    def test_conclusion_pivot_lexicon(self): ...
    def test_lexical_diversity_empty(self): ...

class TestV20Metrics:
    """v2.0 14함수 테스트"""
    def test_normalisation_score(self): ...
    def test_da_streak_rate(self): ...
    def test_pronoun_density(self): ...
    def test_double_passive_count(self): ...
    def test_interference_index(self): ...

class TestComputeAll:
    """통합 API 테스트"""
    def test_compute_korean_quality_low_risk(self): ...
    def test_compute_korean_quality_high_risk(self): ...
    def test_genre_fallback(self): ...

class TestPromptRules:
    """프롬프트 규칙 테스트"""
    def test_get_s1_prompt_rules_not_empty(self): ...
    def test_s1_count_18(self): ...
```

### 5.2 통합 테스트

- AI 생성 텍스트 샘플 5건으로 risk_band 분포 확인
- Story Engine 생성 → quality_metrics 필드 존재 확인
- risk_band=high 시 재생성 트리거 확인

---

## 6. 성능 고려사항

| 항목 | 예상 | 근거 |
|------|------|------|
| compute_korean_quality 실행 시간 | <10ms | 정규식+카운팅만, I/O 없음 |
| 메모리 | <1MB | baseline.json ~5KB, 상수 dict만 |
| 재생성 지연 | 모델 의존 | qwen2.5:7b ~5초, 14b ~10초 |
| API 응답 증가 | ~200B | quality_metrics JSON 추가분 |

---

## 7. Phase별 구현 체크리스트

### Phase 1: 메트릭 모듈 (이번 세션)

- [x] `core/korean_humanize.py` — 22함수 + compute_korean_quality + get_s1_prompt_rules + get_quality_summary
- [x] `core/data/baseline.json` — KatFish v1.6 baseline
- [ ] `core/data/baseline_v2.json` — v2.0 baseline (placeholder, 향후)
- [x] `core/data/humanize_rules.json` — S1 18종 + S2 12종 + 장르 설정 + Do-NOT
- [x] `tests/test_korean_humanize.py` — 44건 테스트 전부 통과 (2026-06-01)
- [ ] `core/__init__.py` 갱신 (Phase 2에서)

### Phase 2: Story/Novel 통합 (2026-06-01 완료)

- [x] `routes/story.py` — story_generate, story_continue에 quality_metrics 첨부
- [x] `routes/novel.py` — novel_chapter_write, novel_chapter_rewrite에 quality_metrics 첨부
- [x] risk_band=high 시 S1 규칙 삽입 후 1회 재생성 로직
- [x] `POST /api/korean/analyze` — 독립 분석 API
- [x] `GET /api/korean/rules` — 규칙 목록 API
- [ ] API 응답 문서 갱신 (다음 세션)

### Phase 3: 전체 확산 (2026-06-01 완료)

- [x] 채팅 경량 체크 — chat.py 비스트리밍 응답에 humanize 요약 삽입 (50자+)
- [ ] 의료 보고서 번역투 제거 — medical_advanced.py는 JSON 위주, 텍스트 생성 별도 검토
- [x] Dashboard API 품질 메트릭 노출 — GET /api/dashboard/quality + summary에 korean_quality 섹션
- [x] Writing 장르별 후처리 — writing_draft (장르 자동 매핑 + 재생성), writing_polish (S1 규칙 사전 삽입)
