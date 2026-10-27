"""도메인별 우선순위 큐 — 자동 우선순위 매핑 + 긴급 프리엠션 (P3-2)

우선순위 레벨:
  0: 응급 (triage emergency, drug interaction critical)
  1: 의료 긴급 (SOAP, 감별진단, 추론 체인)
  2: 의료 일반 (설명, 참고, RAG)
  3: 일반 (글쓰기, 코딩, 번역)
  4: 배경 (게임 NPC, 배치 저우선)
"""
import logging
import re

logger = logging.getLogger("ollama-admin")

# 도메인별 우선순위 매핑
DOMAIN_PRIORITY = {
    # 의료 — 응급 (0)
    "triage_emergency": 0,
    "drug_interaction_critical": 0,

    # 의료 — 긴급 (1)
    "summarize": 1,
    "analyze": 1,
    "draft": 1,
    "reasoning_chain": 1,
    "reasoning_normalize": 1,
    "reasoning_differential": 1,
    "reasoning_ranking": 1,
    "reasoning_tests": 1,
    "reasoning_assessment": 1,
    "auto_record_ner": 1,
    "auto_record_soap": 1,
    "clinical_analyze": 1,
    "clinical_drug": 1,
    "predict_readmission": 1,
    "predict_complication": 1,
    "predict_adr": 1,

    # 의료 — 일반 (2)
    "explain": 2,
    "reference": 2,
    "translate": 2,
    "medical_rag": 2,
    "triage": 2,
    "vision_medical": 2,

    # 일반 (3)
    "writing": 3,
    "coding": 3,
    "hr_law": 3,
    "chat": 3,

    # 배경 (4)
    "game_npc": 4,
    "batch_low": 4,
    "embed": 4,
}

# API 경로 → 도메인 매핑 (자동 감지용)
PATH_DOMAIN_MAP = [
    (r"/api/triage", "triage"),
    (r"/api/medical/reasoning", "reasoning_chain"),
    (r"/api/medical/auto-record", "auto_record_ner"),
    (r"/api/medical/predict", "predict_readmission"),
    (r"/api/medical/clinical", "clinical_analyze"),
    (r"/api/medical/summarize", "summarize"),
    (r"/api/medical/analyze", "analyze"),
    (r"/api/medical/draft", "draft"),
    (r"/api/medical/explain", "explain"),
    (r"/api/medical/reference", "reference"),
    (r"/api/medical/drug-interactions", "drug_interaction_critical"),
    (r"/api/medical/batch", "summarize"),
    (r"/api/vision/medical", "vision_medical"),
    (r"/api/writing", "writing"),
    (r"/api/coding", "coding"),
    (r"/api/hr-law", "hr_law"),
    (r"/api/game", "game_npc"),
    (r"/api/chat", "chat"),
]


def get_domain_priority(action=None, path=None, user_priority=None):
    """도메인 + 사용자 지정 우선순위에서 최종 우선순위 계산

    Args:
        action: 작업 유형 (summarize, analyze 등)
        path: API 경로 (자동 도메인 감지용)
        user_priority: 사용자 지정 우선순위 (0-9)

    Returns:
        int: 최종 우선순위 (0=최고, 9=최저)
    """
    domain_prio = 5  # 기본값

    # 1. action 기반
    if action and action in DOMAIN_PRIORITY:
        domain_prio = DOMAIN_PRIORITY[action]

    # 2. path 기반 (action 없을 때 fallback)
    elif path:
        for pattern, domain in PATH_DOMAIN_MAP:
            if re.search(pattern, path):
                domain_prio = DOMAIN_PRIORITY.get(domain, 5)
                break

    # 3. 사용자 지정이 있으면 도메인 우선순위와 min (더 높은 우선순위 선택)
    if user_priority is not None:
        return min(domain_prio, int(user_priority))

    return domain_prio


def classify_urgency(action=None, content=None):
    """콘텐츠 기반 긴급도 분류 — 응급 키워드 감지

    Returns:
        {"urgency": "emergency"|"urgent"|"normal"|"low", "priority": int, "reason": str}
    """
    priority = get_domain_priority(action=action)
    urgency = "normal"
    reason = ""

    # 응급 키워드 감지
    if content:
        emergency_keywords = [
            "심정지", "cardiac arrest", "의식소실", "무호흡", "대량출혈",
            "아나필락시스", "anaphylaxis", "뇌출혈", "stroke", "심근경색",
            "myocardial infarction", "패혈증", "sepsis", "기도폐쇄",
        ]
        content_lower = content.lower() if content else ""
        for kw in emergency_keywords:
            if kw.lower() in content_lower:
                priority = 0
                urgency = "emergency"
                reason = f"응급 키워드 감지: {kw}"
                break

        if urgency == "normal":
            urgent_keywords = [
                "급성", "acute", "흉통", "chest pain", "호흡곤란",
                "dyspnea", "의식변화", "고열", "경련", "seizure",
            ]
            for kw in urgent_keywords:
                if kw.lower() in content_lower:
                    priority = min(priority, 1)
                    urgency = "urgent"
                    reason = f"긴급 키워드 감지: {kw}"
                    break

    if priority >= 4:
        urgency = "low"
    elif priority >= 3 and urgency == "normal":
        urgency = "normal"
    elif priority <= 1 and urgency == "normal":
        urgency = "urgent"

    return {
        "urgency": urgency,
        "priority": priority,
        "reason": reason or f"domain priority={priority}",
    }


def get_priority_stats():
    """우선순위 시스템 설정 정보 반환"""
    return {
        "levels": {
            0: "응급 (즉시 처리)",
            1: "의료 긴급 (5초 이내)",
            2: "의료 일반 (15초 이내)",
            3: "일반 (30초 이내)",
            4: "배경 (60초 이내)",
        },
        "domain_count": len(DOMAIN_PRIORITY),
        "path_rules": len(PATH_DOMAIN_MAP),
    }
