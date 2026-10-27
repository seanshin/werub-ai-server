# Korean Language Processing API Guide

**버전**: v2.60.0
**Blueprint**: `routes/korean.py`
**Core 모듈**: `core/korean_humanize.py` (1,200줄) + `core/korean_spellcheck.py` (256줄)
**총 API**: 24건
**외부 의존**: 0 (순수 Python + Ollama LLM + KSS)

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [오케스트레이션 API](#2-오케스트레이션-api)
3. [분석 API (10건)](#3-분석-api)
4. [교정/변환 API (3건)](#4-교정변환-api)
5. [소설 전용 API (5건)](#5-소설-전용-api)
6. [참조 API (4건)](#6-참조-api)
7. [통합 포인트](#7-통합-포인트)
8. [출처 및 라이선스](#8-출처-및-라이선스)

---

## 1. 아키텍처 개요

### 처리 파이프라인

```
텍스트 입력
  │
  ├─ [1단계] force_korean()          비한국어 제거 (core/korean.py)
  ├─ [2단계] compute_korean_quality() 22 메트릭 품질 분석 (core/korean_humanize.py)
  │           ↓ risk=high → S1 규칙 삽입 자동 재생성
  ├─ [3단계] spellcheck()            맞춤법 교정 (core/korean_spellcheck.py, Ollama)
  ├─ [4단계] compute_readability()   가독성 5등급 (core/korean_humanize.py)
  └─ [5단계] compute_cohesion()      응집성 분석 (core/korean_humanize.py)
```

### 모듈 구성

| 모듈 | 역할 | 의존성 |
|------|------|--------|
| `core/korean.py` | 비한국어 필터 + 비율 검증 | stdlib |
| `core/korean_humanize.py` | 22 메트릭 + 가독성 + 응집성 + 통계 + 분석 | stdlib + KSS |
| `core/korean_spellcheck.py` | 맞춤법 검사 (Ollama LLM) | requests |
| `core/data/baseline.json` | KatFish 코퍼스 baseline | - |
| `core/data/humanize_rules.json` | S1/S2 패턴 규칙 | - |
| `routes/korean.py` | 24건 API Blueprint | Flask |

### 데이터 출처

| 출처 | 라이선스 | 사용 내용 |
|------|---------|----------|
| [im-not-ai](https://github.com/epoko77-ai/im-not-ai) v2.0 | MIT | 22 메트릭, S1/S2 패턴, baseline |
| [KSS](https://github.com/hyunwoongko/kss) v6.0 | BSD-3 | 문장 분리 (pecab 백엔드) |
| [NovelWriter](https://github.com/KudoShusak/NovelWriter) | MIT | 캐릭터 경험 추적 패턴 |
| KatFish (Park et al.) | 학술 | 인간 470편 vs AI 1,624편 코퍼스 |

---

## 2. 오케스트레이션 API

### POST /api/korean/pipeline

전체 파이프라인을 한번에 실행합니다. steps 파라미터로 실행할 단계를 선택할 수 있습니다.

```bash
curl -X POST http://localhost:8585/api/korean/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "text": "결론적으로 이 연구는 혁신적이다.",
    "genre": "essay",
    "steps": ["filter", "analyze", "spellcheck", "readability", "cohesion"]
  }'
```

**응답**:
```json
{
  "original": "...",
  "processed": "...",
  "pipeline_steps": ["filter", "analyze", "readability", "cohesion"],
  "filter": {"korean_ratio": 1.0, "passed": true},
  "analyze": {"risk_band": "low", "risk_score": 2, "metrics": {...}},
  "readability": {"score": 85, "grade": "초등"},
  "cohesion": {"score": 60, "grade": "fair"},
  "duration_ms": 15
}
```

### POST /api/korean/novel-diagnosis

소설 텍스트 전용 종합 진단. 8개 분석을 일괄 실행하고 종합 점수(A~D)를 산출합니다.

```bash
curl -X POST http://localhost:8585/api/korean/novel-diagnosis \
  -H "Content-Type: application/json" \
  -d '{"text": "소설 텍스트...", "genre": "literary"}'
```

**응답 구조**:
```json
{
  "quality": {"risk_band": "low", "risk_score": 0},
  "readability": {"score": 85, "grade": "초등"},
  "cohesion": {"score": 70, "grade": "good"},
  "repetition": {"score": 90},
  "honorific": {"dominant": "해라체", "consistency": 0.9},
  "pov": {"pov": "1인칭", "confidence": 0.85},
  "sensory": {"show_dont_tell_score": 75, "senses": {...}},
  "dialogue": {"dialogue_ratio": 0.3, "narration_ratio": 0.7},
  "stats": {"sentence_count": 20, "word_count": 150},
  "overall": {
    "score": 82.5,
    "grade": "A",
    "component_scores": {
      "quality": 100, "readability": 85, "cohesion": 70,
      "repetition": 90, "sensory": 75
    }
  },
  "duration_ms": 180
}
```

---

## 3. 분석 API

### POST /api/korean/analyze — AI 패턴 탐지

22개 메트릭으로 AI 생성 텍스트 특성을 정량 분석합니다.

```bash
curl -X POST http://localhost:8585/api/korean/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "분석할 텍스트", "genre": "essay"}'
```

**메트릭 그룹**:

| 그룹 | 메트릭 수 | 설명 |
|------|----------|------|
| comma (v1.6) | 4 | 쉼표 포함률, 사용률, 연결어미+쉼표, 구간 길이 |
| lexicon (v1.6) | 2 | 결산 피벗, 안전 균형 어휘 |
| vocabulary (v1.6) | 2 | 한자어 밀도, 어휘 다양성(TTR) |
| simplification (v2.0) | 2 | 어휘 밀도, 종결 다양성 |
| normalisation (v2.0) | 2 | 평서형 집중도, -다 streak |
| interference (v2.0) | 9 | T1~T8 번역투 간섭 신호 |

**최강 신호**: `ending_comma_rate` — 인간 4.10% vs AI 19.83% (**4.84배**, KatFish)

### POST /api/korean/readability — 가독성 5등급

```bash
curl -X POST http://localhost:8585/api/korean/readability \
  -H "Content-Type: application/json" \
  -d '{"text": "분석할 텍스트"}'
```

**등급 기준**:

| 등급 | 점수 | 대상 |
|------|------|------|
| 초등 | 80+ | 초등학생도 읽기 쉬움 |
| 중등 | 65+ | 중학생 수준 |
| 고등 | 50+ | 고등학생~성인 |
| 전문 | 35+ | 전문가 수준 |
| 학술 | ~35 | 학술·논문 수준 |

### POST /api/korean/cohesion — 응집성 분석

TAACO 방법론 기반. 문장 간 연결성을 측정합니다.

| 지표 | 설명 |
|------|------|
| ASO (Adjacent Sentence Overlap) | 인접 문장 어휘 겹침률 |
| 접속사 밀도 | 문두 접속사 사용 빈도 |
| 지시어 체인 | 이것/그것/저것 등 연결 |
| 주제어 반복 | 핵심 어휘의 문단 간 분포 |

### POST /api/korean/stats — 텍스트 통계

```json
{
  "char_count": 500,
  "word_count": 120,
  "sentence_count": 15,
  "paragraph_count": 3,
  "avg_sentence_length": 8.0,
  "word_frequency": [{"word": "그는", "count": 5}],
  "repeated_words": [{"word": "그러나", "count": 6}],
  "sentence_types": {"declarative": 0.8, "interrogative": 0.13, "exclamatory": 0.07},
  "dialogue_ratio": 0.25,
  "vocabulary_richness": {"ttr": 0.75, "hapax_ratio": 0.6, "top10_coverage": 0.3}
}
```

### POST /api/korean/honorific — 경어법 판별

5단계 경어체를 문장별로 분류하고 혼용을 감지합니다.

| 경어체 | 예시 |
|--------|------|
| 하십시오체 | ~습니다, ~습니까 |
| 해요체 | ~해요, ~세요, ~죠 |
| 해체 | ~해, ~야, ~지 |
| 하라체 | ~하라, ~해라 |
| 해라체 | ~한다, ~이다, ~였다 |

### POST /api/korean/repetition — 반복/중복 탐지

- 동일 문장 중복 (위치 표시)
- 반복 어구 (2~4어절 ngram, 3회+)
- 종결어미 단조로움 (3연속+ 동일 어미)
- 점수: 100=반복 없음

### POST /api/korean/pov — 시점 판별

1인칭(나는/내가) vs 3인칭(그는/그녀는) 대명사 빈도 + 내면 표현으로 판별.

### POST /api/korean/sensory — 감각 묘사 분석

5감(시각/청각/후각/촉각/미각) 사용 빈도 + Show Don't Tell 점수.
감정 직접 서술("슬펐다", "기뻤다") 감점.

### POST /api/korean/expressiveness — 생동감 분석

의성어 50종 + 의태어 40종 사전 기반 탐지 + 감각 묘사 통합.

### POST /api/korean/metrics/detail — 개별 메트릭 선택

```bash
curl -X POST http://localhost:8585/api/korean/metrics/detail \
  -H "Content-Type: application/json" \
  -d '{"text": "텍스트", "metrics": ["ending_comma_rate", "conclusion_pivot_count"]}'
```

---

## 4. 교정/변환 API

### POST /api/korean/spellcheck — 맞춤법 검사

Ollama qwen2.5:7b 기반 로컬 교정. 외부 API 0건.

```bash
curl -X POST http://localhost:8585/api/korean/spellcheck \
  -H "Content-Type: application/json" \
  -d '{"text": "안녕 하세요. 맛있는거 먹고 싶다. 됬다."}'
```

**응답**:
```json
{
  "success": true,
  "original": "안녕 하세요. 맛있는거 먹고 싶다. 됬다.",
  "checked": "안녕하세요. 맛있는 거 먹고 싶다. 됐다.",
  "errors": 3,
  "corrections": [
    {"original": "하세요.", "corrected": "안녕하세요."},
    {"original": "맛있는거", "corrected": "맛있는 거"},
    {"original": "됬다.", "corrected": "됐다."}
  ]
}
```

### POST /api/korean/filter — 비한국어 제거

중국어/일본어/CJK 구두점/영어 액션 텍스트 제거 + 한국어 비율 검증.

### POST /api/korean/style-transfer — 문체 변환

6종 문체 변환 (Ollama LLM):

| target_style | 설명 | 종결 예시 |
|-------------|------|----------|
| `formal` | 존댓말 (합쇼체) | ~습니다, ~합니다 |
| `informal` | 반말 (해체) | ~야, ~해, ~이야 |
| `literary` | 문어체/소설체 | 서사적 묘사, 은유 |
| `colloquial` | 구어체/대화체 | 자연스러운 말투 |
| `narration` | 3인칭 나레이션 | 객관적 서술 |
| `dialogue` | 대화문 형식 | 인물 간 대사 |

```

> ⚠️ **실패 계약 — 폴백은 200으로 온다.** LLM 타임아웃·예외 시 `success:false` + `error_detail`
> 과 함께 **`checked`에 원문을 그대로** 돌려준다(교정 못 했으면 원문 보존이 설계 의도).
> 따라서 호출자는 **`success`를 반드시 확인**해야 한다 — 상태 코드만 보면 "교정 실패"가
> "교정 결과"로 위장된다(2026-08-18 배포 게이트 오분류 실증). 내부 호출자(novel·pipeline)는
> 전부 `success`를 본다.
bash
curl -X POST http://localhost:8585/api/korean/style-transfer \
  -H "Content-Type: application/json" \
  -d '{"text": "오늘 날씨가 좋다.", "target_style": "formal"}'
```

---

## 5. 소설 전용 API

### POST /api/korean/dialogue-analysis — 대화문 분석

소설 텍스트에서 대사/묘사/내면 비율 + 화자 자동 식별.

**응답**:
```json
{
  "composition": {
    "dialogue_ratio": 0.4,
    "narration_ratio": 0.5,
    "inner_voice_ratio": 0.1
  },
  "speakers": {"수영": 3, "도윤": 2},
  "dialogue_stats": {"avg_length": 15.5},
  "recommendations": ["대화 비중이 적절합니다."]
}
```

### POST /api/korean/character-voice — 캐릭터 말투 분석

캐릭터별 대사 추출 → 종결어미 패턴 + 격식/비격식 스타일 판정.

### POST /api/korean/diff — 텍스트 비교

수정 전후 문장 단위 diff (stdlib `difflib.SequenceMatcher`).
변경률 가드: 30% 주의, 50% 과윤문 경고.

```bash
curl -X POST http://localhost:8585/api/korean/diff \
  -H "Content-Type: application/json" \
  -d '{
    "original": "결론적으로 혁신적이다.",
    "revised": "이 결과는 의미 있다."
  }'
```

### POST /api/korean/feedback — AI 글쓰기 피드백

정량 분석(quality+readability+cohesion+stats) 선행 후, LLM이 5항목 피드백 생성:

1. **정확성** — 내용 정확성, 논리적 오류
2. **효과성** — 전달력, 설득력/몰입도
3. **우선순위** — 가장 먼저 고칠 부분
4. **이해도** — 대상 독자 수준 적합성
5. **격려** — 잘한 점 인정 + 동기부여

```bash
curl -X POST http://localhost:8585/api/korean/feedback \
  -H "Content-Type: application/json" \
  -d '{"text": "글 내용...", "genre": "novel", "focus": "expression"}'
```

---

## 6. 참조 API

| API | 설명 |
|-----|------|
| `GET /api/korean/rules` | S1 18종 + S2 12종 패턴 규칙 + 장르 설정 + Do-NOT |
| `GET /api/korean/rules/prompt` | S1 규칙 system prompt 삽입용 텍스트 |
| `GET /api/korean/metrics` | 22개 메트릭 레지스트리 (이름/그룹/설명/baseline) |
| `GET /api/korean/status` | 시스템 상태 (KSS 활성/모듈 버전/메트릭 수) |
| `POST /api/korean/check` | 한국어 비율 검증 (30% 이상 필요) |

---

## 7. 통합 포인트

Korean 품질 시스템이 자동으로 작동하는 곳 (19곳):

| 영역 | 함수 | 동작 |
|------|------|------|
| Story | story_generate, story_continue | quality_metrics + risk=high 재생성 |
| Novel | chapter_write, chapter_rewrite | quality_metrics + risk=high 재생성 |
| Novel | _scene_handler (5종) | quality_metrics + risk=high 재생성 |
| Novel | revise_polish | S1 규칙 사전 삽입 + 등급 A~D |
| Novel | chapter_expand, chapter_condense | S1 규칙 사전 삽입 |
| Novel | scene_write_with_ref | quality_metrics |
| Writing | writing_draft | 장르별 규칙 + 재생성 |
| Writing | writing_polish | S1 규칙 사전 삽입 |
| Chat | _chat_non_streaming | 경량 humanize 요약 (50자+) |
| Medical | summarize, explain | quality.humanize |
| Medical | research, patient-education | quality_metrics |
| Dashboard | /api/dashboard/quality | 시스템 현황 |
| Dashboard | /api/dashboard/summary | korean_quality 섹션 |

---

## 8. 출처 및 라이선스

### 오픈소스 이식

| 프로젝트 | 버전 | 라이선스 | 이식 내용 |
|---------|------|---------|----------|
| im-not-ai | v2.0 | MIT | 22 메트릭 + S1/S2 패턴 + baseline + rewriting playbook |
| KSS | v6.0 | BSD-3 | 문장 분리기 (pecab 백엔드) |
| NovelWriter | - | MIT | 캐릭터 경험 추적 + 스냅샷 + 컨텍스트 강화 |

### 학술 참조

| 출처 | 내용 |
|------|------|
| KatFish (Park et al.) | 인간 470편 vs AI 1,624편 코퍼스, 연결어미 쉼표 4.84배 |
| Toral 2019 | Post-editese = 강화된 번역투 (simplification/normalisation/interference) |
| 김정우 2007 | 한국어 8대 번역투 유형 |
| TAACO | 텍스트 응집성 분석 방법론 |
| 한국어 글쓰기 피드백 연구 2025 | 5항목 평가 기준 (정확성/효과성/우선순위/이해도/격려) |

### 원칙

- **외부 API 호출 0건** — 모든 처리는 로컬 (Ollama LLM + 순수 Python)
- **MIT/BSD 라이선스만** — 상업적 사용 가능
- **graceful degrade** — KSS 미설치 시 regex fallback, Ollama 장애 시 원문 반환
