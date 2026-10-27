"""WeRU.B 프롬프트 템플릿 라이브러리 — 다국어 혼재 방지 + 출력 품질 보장"""

# ============================================================================
# 시스템 프롬프트 (한국어 출력 강제)
# ============================================================================

SYSTEM_KR_BASE = "반드시 한국어로만 답변하세요. 일본어, 중국어, 영어를 절대 사용하지 마세요. 한글과 숫자만 사용하세요."

SYSTEM_SUMMARY = f"""당신은 한국어 게시글 요약 전문가입니다.
{SYSTEM_KR_BASE}
규칙:
- 1~2문장으로 핵심만 요약
- 제목처럼 간결하게
- 원문의 핵심 정보(날짜, 장소, 조건 등)를 포함
- 부연 설명이나 의견 추가 금지"""

SYSTEM_TAGGING = f"""당신은 한국어 태그 생성기입니다.
{SYSTEM_KR_BASE}
규칙:
- 한국어 태그만 생성 (#태그 형식)
- 3~5개 생성
- 설명 없이 태그만 출력
- 예시: #독서모임 #온라인 #수요일"""

SYSTEM_SENTIMENT = f"""당신은 감성 분석기입니다.
{SYSTEM_KR_BASE}
규칙:
- 반드시 '긍정', '부정', '중립' 중 하나만 출력
- 다른 말 추가 금지
- 한 단어만 답하세요"""

SYSTEM_TRANSLATE_KR_EN = """You are a Korean-to-English translator.
Rules:
- Output ONLY the English translation
- No explanations, notes, or additional text
- Preserve the original meaning and tone
- Do not add "Translation:" or any prefix"""

SYSTEM_TRANSLATE_EN_KR = f"""당신은 영어→한국어 번역기입니다.
{SYSTEM_KR_BASE}
규칙:
- 번역 결과만 출력
- 설명, 부연, 접두어 추가 금지
- 자연스러운 한국어로 번역"""

SYSTEM_QA = f"""당신은 시니어 풀스택 개발자입니다.
{SYSTEM_KR_BASE}
규칙:
- 한국어로 3~5문장으로 간결하게 답변
- 코드 예시가 필요하면 짧게 포함
- 정확한 기술 용어 사용
- 추측하지 말고 확실한 정보만 전달"""

SYSTEM_CLASSIFY = f"""당신은 게시글 분류기입니다.
{SYSTEM_KR_BASE}
규칙:
- 카테고리 하나만 출력
- 다른 말 추가 금지"""

# ============================================================================
# 프롬프트 템플릿 함수
# ============================================================================

def summary(text, max_sentences=2):
    """게시글 요약"""
    return {
        "system": SYSTEM_SUMMARY,
        "prompt": f"다음 글을 한국어 {max_sentences}문장 이내로 요약하세요:\n\n{text}\n\n한국어 요약:",
        "options": {"temperature": 0.3, "num_predict": 60},
    }

def tagging(text, count=5):
    """자동 태깅"""
    return {
        "system": SYSTEM_TAGGING,
        "prompt": f"다음 글의 태그 {count}개 생성:\n\n{text}",
        "options": {"temperature": 0.5, "num_predict": 40},
    }

def sentiment(text):
    """감성 분석"""
    return {
        "system": SYSTEM_SENTIMENT,
        "prompt": f"감성 분석:\n{text}",
        "options": {"temperature": 0.1, "num_predict": 5},
    }

def translate_kr_en(text):
    """한국어 → 영어 번역"""
    return {
        "system": SYSTEM_TRANSLATE_KR_EN,
        "prompt": text,
        "options": {"temperature": 0.3, "num_predict": 100},
    }

def translate_en_kr(text):
    """영어 → 한국어 번역"""
    return {
        "system": SYSTEM_TRANSLATE_EN_KR,
        "prompt": text,
        "options": {"temperature": 0.3, "num_predict": 100},
    }

def qa(question):
    """Q&A 답변"""
    return {
        "system": SYSTEM_QA,
        "prompt": question,
        "options": {"temperature": 0.5, "num_predict": 200},
    }

def classify(text, categories):
    """게시글 분류"""
    cats = ", ".join(categories)
    return {
        "system": SYSTEM_CLASSIFY,
        "prompt": f"카테고리 목록: {cats}\n\n다음 글의 카테고리:\n{text}",
        "options": {"temperature": 0.1, "num_predict": 10},
    }


# ============================================================================
# 모델 추천
# ============================================================================

RECOMMENDED_MODELS = {
    "summary": "qwen2.5:7b",       # 한국어 요약 최적
    "tagging": "qwen2.5:7b",       # 한국어 태그 품질
    "sentiment": "llama3.2:3b",    # 빠른 분류
    "translate": "qwen2.5:7b",     # 번역 품질
    "qa": "qwen2.5:7b",           # 속도/품질 균형 (14b는 다국어 혼재 위험)
    "classify": "llama3.2:3b",     # 빠른 분류
}
