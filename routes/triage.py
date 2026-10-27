"""Symptom Triage API — 증상 기반 진료과 추천"""
import json
import logging
import re
import time
import uuid

import requests
from flask import Blueprint, request, jsonify

logger = logging.getLogger("ollama-admin")

triage_bp = Blueprint("triage", __name__)

_ollama_session = None
_OLLAMA_BASE = None

# Session storage for follow-up (TTL: 30 minutes)
_sessions = {}
_SESSION_TTL = 1800  # 30 minutes in seconds
_MAX_SESSIONS = 1000


def init_triage(*, ollama_session, ollama_base):
    global _ollama_session, _OLLAMA_BASE
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base


# ---------------------------------------------------------------------------
# Symptom keyword extraction
# ---------------------------------------------------------------------------

SYMPTOM_KEYWORDS = {
    "머리": "두통", "두통": "두통", "편두통": "두통", "머리가 아프": "두통",
    "머리 아프": "두통", "머리가 깨질": "두통", "관자놀이": "두통",
    "이마가 아프": "두통", "뒷머리": "두통", "머리가 띵": "두통",
    "배": "복통", "배가 아프": "복통", "복통": "복통", "속이 쓰리": "복통",
    "소화가 안": "소화불량", "위": "복통", "명치": "복통", "옆구리": "복통",
    "속이 더부룩": "소화불량", "체한": "소화불량",
    "가슴": "흉통", "가슴이 아프": "흉통", "흉통": "흉통",
    "가슴이 답답": "흉통", "가슴 압박": "흉통", "가슴 두근": "심계항진",
    "열": "발열", "열나": "발열", "열이 나": "발열", "오한": "발열",
    "몸살": "발열", "한기": "발열",
    "기침": "기침", "가래": "가래", "콧물": "콧물", "재채기": "콧물",
    "숨": "호흡곤란", "숨이 차": "호흡곤란", "숨쉬기 힘": "호흡곤란",
    "호흡곤란": "호흡곤란", "숨이 가빠": "호흡곤란",
    "허리": "허리통증", "무릎": "무릎통증", "어깨": "어깨통증",
    "목 아프": "목통증", "목이 뻣뻣": "목통증", "손목": "관절통",
    "발목": "관절통", "팔": "관절통", "다리": "관절통",
    "관절": "관절통", "뼈": "관절통",
    "피부": "피부증상", "두드러기": "두드러기", "가려": "가려움",
    "뾰루지": "피부증상", "습진": "피부증상", "발진": "발진", "종기": "피부증상",
    "소변": "비뇨증상", "오줌": "비뇨증상", "자주 마려": "빈뇨",
    "피가 섞인 소변": "혈뇨", "혈뇨": "혈뇨",
    "목이 아프": "인후통", "목감기": "인후통", "삼키기 힘": "인후통",
    "코막힘": "코막힘", "코가 막혀": "코막힘", "비염": "코막힘",
    "귀": "귀증상", "귀가 아프": "귀통증", "이명": "이명",
    "잘 안 들려": "청력저하", "귀에서 소리": "이명",
    "눈": "안과증상", "눈 충혈": "눈충혈", "눈이 빨개": "눈충혈",
    "눈 아프": "눈통증", "시력": "시력저하", "잘 안 보여": "시력저하",
    "눈이 흐려": "시력저하", "눈물": "안과증상",
    "우울": "우울", "불안": "불안", "잠이 안": "불면", "잠을 못": "불면",
    "스트레스": "불안", "무기력": "우울", "의욕이 없": "우울",
    "짜증": "불안", "공황": "불안",
    "피곤": "피로", "기운이 없": "피로", "피로": "피로",
    "살이 빠져": "체중감소", "살이 쪄": "체중증가",
    "어지러": "어지러움", "현기증": "어지러움", "빙빙": "어지러움",
    "구토": "구토", "토하": "구토", "울렁": "구토", "메스꺼": "구토",
    "설사": "설사", "변비": "변비", "혈변": "혈변",
    "마비": "마비", "저림": "저림", "저려": "저림",
    "말이 어눌": "언어장애", "경련": "경련", "기절": "의식소실",
    "의식": "의식소실", "생리": "생리증상", "임신": "임신",
    "출혈": "출혈", "코피": "코피", "잇몸 출혈": "출혈",
    "당뇨": "당뇨", "혈압": "혈압", "고혈압": "혈압",
    # C: 신규 키워드 추가
    # 신경외과
    "디스크": "디스크", "허리디스크": "디스크", "목디스크": "디스크",
    "척추": "척추통증", "척추관": "척추통증", "척추협착": "척추통증",
    "다리가 저려": "저림", "다리 저림": "저림",
    # 류마티스
    "류마티스": "류마티스", "통풍": "통풍", "루푸스": "류마티스",
    "관절이 붓": "관절부종", "손가락이 붓": "관절부종",
    # 치과
    "이가 아프": "치통", "치통": "치통", "잇몸": "잇몸증상",
    "사랑니": "치통", "이가 흔들": "잇몸증상", "치아": "치통",
    # 피부과 추가
    "탈모": "탈모", "머리카락": "탈모", "두피": "두피증상",
    "건선": "피부증상", "백반": "피부증상", "사마귀": "피부증상",
    # 응급
    "화상": "화상", "불에 데": "화상", "뜨거운 것": "화상",
    "식은땀": "식은땀", "땀이 많이": "식은땀",
    "얼굴이 비뚤": "안면마비", "입이 돌아": "안면마비", "안면마비": "안면마비",
    # 소아
    "열성경련": "경련", "고열경련": "경련",
    "배꼽": "복통", "영아": "소아증상", "아기": "소아증상",
    # 노인
    "넘어": "낙상", "낙상": "낙상", "골절": "골절", "뼈가 부러": "골절",
    "치매": "치매", "기억력": "치매", "깜빡": "치매",
    "골다공증": "골다공증",
}

# ---------------------------------------------------------------------------
# Symptom → Department mapping rules
# ---------------------------------------------------------------------------

DEPT_RULES = {
    "두통": [("신경과", 0.70, "NR"), ("내과", 0.50, "IM")],
    "두통+발열": [("내과", 0.85, "IM"), ("신경과", 0.40, "NR"), ("이비인후과", 0.30, "ENT")],
    "두통+어지러움": [("신경과", 0.80, "NR"), ("이비인후과", 0.50, "ENT")],
    "두통+구토": [("신경과", 0.80, "NR"), ("응급의학과", 0.50, "EM")],
    "두통+시력저하": [("안과", 0.70, "EY"), ("신경과", 0.70, "NR")],
    "복통": [("내과", 0.60, "IM"), ("외과", 0.40, "GS")],
    "복통+발열": [("내과", 0.70, "IM"), ("외과", 0.60, "GS")],
    "복통+설사": [("내과", 0.80, "IM"), ("소화기내과", 0.70, "GI")],
    "복통+구토": [("내과", 0.70, "IM"), ("외과", 0.60, "GS"), ("응급의학과", 0.40, "EM")],
    "복통+구토+발열": [("외과", 0.75, "GS"), ("내과", 0.65, "IM"), ("응급의학과", 0.55, "EM")],
    "소화불량": [("내과", 0.70, "IM"), ("소화기내과", 0.65, "GI")],
    "흉통": [("심장내과", 0.70, "CV"), ("내과", 0.60, "IM"), ("응급의학과", 0.50, "EM")],
    "흉통+호흡곤란": [("응급의학과", 0.90, "EM"), ("심장내과", 0.85, "CV")],
    "심계항진": [("심장내과", 0.75, "CV"), ("내과", 0.55, "IM")],
    "허리통증": [("정형외과", 0.80, "OS"), ("재활의학과", 0.60, "RM")],
    "무릎통증": [("정형외과", 0.85, "OS")],
    "어깨통증": [("정형외과", 0.80, "OS"), ("재활의학과", 0.60, "RM")],
    "목통증": [("정형외과", 0.70, "OS"), ("재활의학과", 0.60, "RM")],
    "관절통": [("정형외과", 0.75, "OS"), ("류마티스내과", 0.50, "RH")],
    "기침": [("내과", 0.70, "IM"), ("호흡기내과", 0.60, "PU")],
    "기침+발열": [("내과", 0.80, "IM"), ("호흡기내과", 0.70, "PU")],
    "가래": [("내과", 0.70, "IM"), ("호흡기내과", 0.65, "PU")],
    "콧물": [("이비인후과", 0.65, "ENT"), ("내과", 0.60, "IM")],
    "호흡곤란": [("호흡기내과", 0.70, "PU"), ("심장내과", 0.60, "CV"), ("응급의학과", 0.50, "EM")],
    "발열": [("내과", 0.80, "IM")],
    "피부증상": [("피부과", 0.85, "DM")],
    "발진": [("피부과", 0.85, "DM")],
    "가려움": [("피부과", 0.80, "DM")],
    "두드러기": [("피부과", 0.80, "DM"), ("내과", 0.40, "IM")],
    "비뇨증상": [("비뇨의학과", 0.80, "UR")],
    "빈뇨": [("비뇨의학과", 0.75, "UR")],
    "혈뇨": [("비뇨의학과", 0.85, "UR")],
    "인후통": [("이비인후과", 0.70, "ENT"), ("내과", 0.60, "IM")],
    "코막힘": [("이비인후과", 0.80, "ENT")],
    "귀증상": [("이비인후과", 0.80, "ENT")],
    "귀통증": [("이비인후과", 0.85, "ENT")],
    "이명": [("이비인후과", 0.80, "ENT"), ("신경과", 0.40, "NR")],
    "청력저하": [("이비인후과", 0.90, "ENT")],
    "안과증상": [("안과", 0.85, "EY")],
    "눈충혈": [("안과", 0.85, "EY")],
    "눈통증": [("안과", 0.85, "EY")],
    "시력저하": [("안과", 0.90, "EY")],
    "우울": [("정신건강의학과", 0.85, "PS")],
    "불안": [("정신건강의학과", 0.80, "PS")],
    "불면": [("정신건강의학과", 0.75, "PS"), ("신경과", 0.40, "NR")],
    "피로": [("내과", 0.60, "IM"), ("가정의학과", 0.55, "FM")],
    "체중감소": [("내과", 0.70, "IM"), ("내분비내과", 0.55, "EN")],
    "체중증가": [("내분비내과", 0.70, "EN"), ("가정의학과", 0.50, "FM")],
    "어지러움": [("신경과", 0.70, "NR"), ("이비인후과", 0.55, "ENT")],
    "저림": [("신경과", 0.75, "NR")],
    "생리증상": [("산부인과", 0.85, "OB")],
    "임신": [("산부인과", 0.95, "OB")],
    "당뇨": [("내분비내과", 0.80, "EN"), ("내과", 0.60, "IM")],
    "혈압": [("내과", 0.75, "IM"), ("심장내과", 0.60, "CV")],
    "혈변": [("소화기내과", 0.80, "GI"), ("외과", 0.60, "GS")],
    "코피": [("이비인후과", 0.75, "ENT")],
    "출혈": [("응급의학과", 0.70, "EM")],
    # v2.37.0: 다증상 조합 룰 확장
    "기침+가래+발열": [("호흡기내과", 0.90, "PU"), ("내과", 0.70, "IM")],
    "기침+가래": [("호흡기내과", 0.80, "PU"), ("내과", 0.60, "IM")],
    "콧물+기침+발열": [("내과", 0.85, "IM"), ("이비인후과", 0.65, "ENT")],
    "콧물+코막힘": [("이비인후과", 0.85, "ENT")],
    "인후통+발열": [("이비인후과", 0.80, "ENT"), ("내과", 0.65, "IM")],
    "어지러움+구토": [("신경과", 0.80, "NR"), ("이비인후과", 0.60, "ENT")],
    "복통+발열+설사": [("내과", 0.85, "IM"), ("소화기내과", 0.75, "GI")],
    "허리통증+저림": [("정형외과", 0.85, "OS"), ("신경외과", 0.70, "NS")],
    "두통+발열+구토": [("응급의학과", 0.80, "EM"), ("신경과", 0.75, "NR")],
    "흉통+발열+기침": [("호흡기내과", 0.80, "PU"), ("심장내과", 0.60, "CV")],
    "피부증상+가려움": [("피부과", 0.90, "DM")],
    "발진+발열": [("내과", 0.75, "IM"), ("피부과", 0.70, "DM")],
    "불면+불안": [("정신건강의학과", 0.85, "PS")],
    "우울+불면": [("정신건강의학과", 0.85, "PS")],
    "피로+체중감소": [("내과", 0.75, "IM"), ("내분비내과", 0.65, "EN")],
    # C: 신규 룰 추가
    # 신경외과
    "디스크": [("신경외과", 0.85, "NS"), ("정형외과", 0.70, "OS"), ("재활의학과", 0.50, "RM")],
    "척추통증": [("신경외과", 0.75, "NS"), ("정형외과", 0.70, "OS")],
    "허리통증+저림+디스크": [("신경외과", 0.90, "NS"), ("정형외과", 0.65, "OS")],
    # 류마티스
    "류마티스": [("류마티스내과", 0.90, "RH")],
    "통풍": [("류마티스내과", 0.85, "RH"), ("내과", 0.50, "IM")],
    "관절부종": [("류마티스내과", 0.75, "RH"), ("정형외과", 0.60, "OS")],
    "관절부종+발열": [("류마티스내과", 0.80, "RH"), ("내과", 0.60, "IM")],
    # 치과
    "치통": [("치과", 0.95, "DT")],
    "잇몸증상": [("치과", 0.90, "DT")],
    "치통+발열": [("치과", 0.80, "DT"), ("내과", 0.40, "IM")],
    # 화상/응급
    "화상": [("응급의학과", 0.85, "EM"), ("피부과", 0.60, "DM")],
    "안면마비": [("신경과", 0.80, "NR"), ("이비인후과", 0.50, "ENT")],
    # 탈모
    "탈모": [("피부과", 0.85, "DM"), ("내분비내과", 0.40, "EN")],
    # 낙상/골절
    "낙상": [("정형외과", 0.85, "OS"), ("응급의학과", 0.60, "EM")],
    "골절": [("정형외과", 0.95, "OS"), ("응급의학과", 0.70, "EM")],
    # 치매
    "치매": [("신경과", 0.85, "NR"), ("정신건강의학과", 0.50, "PS")],
    # 골다공증
    "골다공증": [("내분비내과", 0.75, "EN"), ("정형외과", 0.55, "OS")],
}

# ---------------------------------------------------------------------------
# Emergency detection
# ---------------------------------------------------------------------------

EMERGENCY_PATTERNS = [
    {
        "keywords": ["흉통", "호흡곤란"],
        "match": "all",
        "level": "critical",
        "message": "갑작스러운 흉통과 호흡곤란은 심근경색 또는 폐색전증의 가능성이 있습니다. 즉시 119에 연락하거나 응급실을 방문하세요.",
        "call_119": True,
    },
    {
        "keywords": ["마비", "언어장애"],
        "match": "any",
        "level": "critical",
        "message": "갑작스러운 편측 마비나 언어 장애는 뇌졸중의 가능성이 있습니다. 즉시 119에 연락하세요. (FAST: 얼굴/팔/말/시간)",
        "call_119": True,
    },
    {
        "keywords": ["의식소실"],
        "match": "any",
        "level": "critical",
        "message": "의식 소실은 다양한 응급 상황의 징후입니다. 즉시 119에 연락하세요.",
        "call_119": True,
    },
    {
        "keywords": ["경련"],
        "match": "any",
        "level": "critical",
        "message": "경련이 발생한 경우 즉시 응급실을 방문하세요.",
        "call_119": True,
    },
    {
        "keywords": ["흉통"],
        "match": "any",
        "age_over": 50,
        "level": "warning",
        "message": "50세 이상에서 흉통은 심장 질환 가능성이 높습니다. 빠른 시일 내 심장내과 또는 응급실 방문을 권장합니다.",
        "call_119": False,
    },
    # B: 응급 패턴 추가
    {
        "keywords": ["두드러기", "호흡곤란"],
        "match": "all",
        "level": "critical",
        "message": "두드러기와 호흡곤란이 동반되면 아나필락시스(과민성 쇼크) 가능성이 있습니다. 즉시 119에 연락하세요.",
        "call_119": True,
    },
    {
        "keywords": ["식은땀", "어지러움"],
        "match": "all",
        "level": "warning",
        "message": "식은땀과 어지러움이 함께 나타나면 저혈당 또는 심혈관 문제일 수 있습니다. 즉시 내과 또는 응급실을 방문하세요.",
        "call_119": False,
    },
    {
        "keywords": ["흉통", "의식소실"],
        "match": "all",
        "level": "critical",
        "message": "흉통과 의식 소실이 동반되면 심정지 또는 심근경색 가능성이 매우 높습니다. 즉시 119에 연락하세요.",
        "call_119": True,
    },
    {
        "keywords": ["안면마비"],
        "match": "any",
        "level": "warning",
        "message": "안면 마비(입이 돌아가거나 얼굴이 비뚤어짐)는 뇌졸중 또는 안면신경마비의 징후일 수 있습니다. 즉시 응급실을 방문하거나 119에 연락하세요.",
        "call_119": True,
    },
    {
        "keywords": ["마비", "두통", "구토"],
        "match": "all",
        "level": "critical",
        "message": "두통, 구토, 마비가 동시에 나타나면 뇌졸중 또는 뇌출혈 가능성이 있습니다. 즉시 119에 연락하세요.",
        "call_119": True,
    },
    {
        "keywords": ["골절"],
        "match": "any",
        "level": "warning",
        "message": "골절이 의심되는 경우 해당 부위를 고정하고 즉시 응급실 또는 정형외과를 방문하세요.",
        "call_119": False,
    },
]


def _extract_symptoms(text):
    """자연어에서 증상 키워드 추출"""
    found = []
    for keyword, symptom in sorted(SYMPTOM_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if keyword in text and symptom not in found:
            found.append(symptom)
    return found


def _check_emergency(symptoms, age=None):
    """응급 상황 판별"""
    for pattern in EMERGENCY_PATTERNS:
        if pattern.get("age_over") and (not age or int(age) < pattern["age_over"]):
            continue
        kws = pattern["keywords"]
        if pattern["match"] == "all":
            if all(k in symptoms for k in kws):
                return pattern
        else:
            if any(k in symptoms for k in kws):
                return pattern
    return None


def _get_dept_recommendations(symptoms, age=None, sex=None, duration=None, severity=None):
    """증상 조합으로 진료과 추천 — 순서 무관 조합 매칭"""
    if age is not None:
        try:
            age = int(age)
        except (ValueError, TypeError):
            age = None

    dept_scores = {}

    # 1. 조합 규칙 매칭 (순서 무관, 긴 조합 우선)
    matched_combo = False
    symptom_set = set(symptoms)

    for combo_len in range(min(3, len(symptoms)), 1, -1):
        for key in DEPT_RULES:
            parts = key.split("+")
            if len(parts) == combo_len and set(parts) <= symptom_set:
                for dept, score, code in DEPT_RULES[key]:
                    if dept not in dept_scores or dept_scores[dept][0] < score:
                        dept_scores[dept] = (score, code)
                matched_combo = True

    # 2. 개별 증상 매칭 (조합 매칭 시 가중치 감소)
    for symptom in symptoms:
        if symptom in DEPT_RULES and "+" not in symptom:
            for dept, score, code in DEPT_RULES[symptom]:
                adj = score * (0.8 if matched_combo else 1.0)
                if dept not in dept_scores or dept_scores[dept][0] < adj:
                    dept_scores[dept] = (adj, code)

    # A: severity 반영
    if severity == "severe":
        # 중증 → 응급의학과 가중치 부스트
        if "응급의학과" in dept_scores:
            s, c = dept_scores["응급의학과"]
            dept_scores["응급의학과"] = (min(s + 0.20, 1.0), c)
        else:
            dept_scores["응급의학과"] = (0.60, "EM")
    elif severity == "mild":
        # 경증 → 응급의학과 가중치 감소
        if "응급의학과" in dept_scores:
            s, c = dept_scores["응급의학과"]
            dept_scores["응급의학과"] = (max(s - 0.20, 0.10), c)

    # A: duration 반영 (만성 → 가정의학과/전문과 우선, 급성 → 내과 우선)
    if duration:
        chronic_keywords = ["한 달", "한달", "수개월", "수 개월", "1달", "2달", "3달", "몇 달", "오래"]
        acute_keywords = ["오늘", "갑자기", "방금", "지금", "갑작스"]
        is_chronic = any(k in duration for k in chronic_keywords)
        is_acute = any(k in duration for k in acute_keywords)
        if is_chronic:
            # 만성 → 전문과 우선, 가정의학과 추가
            if not dept_scores:
                dept_scores["가정의학과"] = (0.65, "FM")
            else:
                dept_scores["가정의학과"] = dept_scores.get("가정의학과", (0.50, "FM"))
        if is_acute and "응급의학과" in dept_scores and severity != "mild":
            s, c = dept_scores["응급의학과"]
            dept_scores["응급의학과"] = (min(s + 0.10, 1.0), c)

    # 3. Age/sex 보정
    if sex == "F" and any(s in symptoms for s in ["복통", "하복부통"]):
        if "산부인과" not in dept_scores:
            dept_scores["산부인과"] = (0.60, "OB")
        else:
            s, c = dept_scores["산부인과"]
            dept_scores["산부인과"] = (min(s + 0.15, 1.0), c)

    # D: 소아 강화
    if age is not None and age < 15:
        # 소아는 기본적으로 소아청소년과 우선
        base_score = 0.90 if age < 3 else 0.85
        dept_scores["소아청소년과"] = (base_score, "PD")
        # 영아(3세 미만) 복통+구토 → 장중첩 가능, 소아청소년과 최우선
        if age < 3 and any(s in symptoms for s in ["복통", "구토"]):
            dept_scores["소아청소년과"] = (0.95, "PD")
        # 소아 발열 → 소아청소년과 우선
        if "발열" in symptoms:
            dept_scores["소아청소년과"] = (max(dept_scores.get("소아청소년과", (0.85, "PD"))[0], 0.90), "PD")

    # D: 노인 강화
    if age is not None and age >= 65:
        # 어지러움 → 신경과 가중치 증가
        if "어지러움" in symptoms and "신경과" in dept_scores:
            s, c = dept_scores["신경과"]
            dept_scores["신경과"] = (min(s + 0.15, 1.0), c)
        # 낙상/골절 → 응급의학과 추가
        if any(s in symptoms for s in ["낙상", "골절"]):
            dept_scores["응급의학과"] = (max(dept_scores.get("응급의학과", (0.60, "EM"))[0], 0.75), "EM")
        # 치매 증상 → 신경과 강화
        if "치매" in symptoms:
            dept_scores["신경과"] = (max(dept_scores.get("신경과", (0.80, "NR"))[0], 0.85), "NR")
        # 고령 흉통 → 응급의학과 추가
        if "흉통" in symptoms:
            dept_scores["응급의학과"] = (max(dept_scores.get("응급의학과", (0.60, "EM"))[0], 0.70), "EM")
    elif age is not None and age >= 60:
        if "어지러움" in symptoms and "신경과" in dept_scores:
            s, c = dept_scores["신경과"]
            dept_scores["신경과"] = (min(s + 0.10, 1.0), c)

    # 4. 점수 정렬
    ranked = sorted(dept_scores.items(), key=lambda x: -x[1][0])
    return [(dept, round(score, 2), code) for dept, (score, code) in ranked[:5]]


# ---------------------------------------------------------------------------
# Department info
# ---------------------------------------------------------------------------

DEPARTMENTS = {
    "내과": {"code": "IM", "full": "내과(일반내과)", "desc": "감기, 발열, 소화기, 호흡기, 심혈관, 내분비 등 내부 장기 질환 전반"},
    "외과": {"code": "GS", "full": "외과(일반외과)", "desc": "수술이 필요한 질환 — 충수염, 탈장, 담석, 유방 질환 등"},
    "정형외과": {"code": "OS", "full": "정형외과", "desc": "뼈, 관절, 근육, 인대 — 골절, 관절염, 디스크, 오십견 등"},
    "신경과": {"code": "NR", "full": "신경과", "desc": "두통, 어지러움, 저림, 경련, 치매, 뇌졸중 등 신경계 질환"},
    "심장내과": {"code": "CV", "full": "순환기내과", "desc": "흉통, 부정맥, 심부전, 고혈압 등 심혈관 질환"},
    "호흡기내과": {"code": "PU", "full": "호흡기내과", "desc": "기침, 천식, COPD, 폐렴 등 호흡기 질환"},
    "소화기내과": {"code": "GI", "full": "소화기내과", "desc": "위염, 역류, 간질환, 대장 질환, 내시경 등"},
    "내분비내과": {"code": "EN", "full": "내분비대사내과", "desc": "당뇨, 갑상선, 골다공증 등 호르몬/대사 질환"},
    "산부인과": {"code": "OB", "full": "산부인과", "desc": "임신, 출산, 생리 이상, 부인과 질환"},
    "소아청소년과": {"code": "PD", "full": "소아청소년과", "desc": "소아/청소년(18세 미만) 질환 전반"},
    "이비인후과": {"code": "ENT", "full": "이비인후과", "desc": "귀, 코, 목 — 중이염, 비염, 편도염, 부비동염 등"},
    "안과": {"code": "EY", "full": "안과", "desc": "눈 질환 — 결막염, 시력 저하, 녹내장, 백내장 등"},
    "피부과": {"code": "DM", "full": "피부과", "desc": "피부 질환 — 습진, 두드러기, 여드름, 피부염 등"},
    "비뇨의학과": {"code": "UR", "full": "비뇨의학과", "desc": "소변 이상, 전립선, 요로결석, 방광염 등"},
    "정신건강의학과": {"code": "PS", "full": "정신건강의학과", "desc": "우울, 불안, 불면, 스트레스, 공황장애 등"},
    "재활의학과": {"code": "RM", "full": "재활의학과", "desc": "근골격 재활, 통증 치료, 물리치료"},
    "가정의학과": {"code": "FM", "full": "가정의학과", "desc": "건강검진, 만성질환 관리, 종합 상담"},
    "응급의학과": {"code": "EM", "full": "응급의학과", "desc": "응급 상황 — 심한 통증, 출혈, 의식 변화 등"},
    # C: 누락 진료과 추가
    "신경외과": {"code": "NS", "full": "신경외과", "desc": "디스크, 척추관협착증, 뇌종양, 뇌출혈 수술적 치료"},
    "류마티스내과": {"code": "RH", "full": "류마티스내과", "desc": "류마티스관절염, 통풍, 루푸스 등 자가면역·결합조직 질환"},
    "치과": {"code": "DT", "full": "치과(구강악안면외과)", "desc": "치통, 잇몸 질환, 사랑니, 구강 질환"},
    "흉부외과": {"code": "TS", "full": "흉부외과", "desc": "심장·폐·식도 수술, 기흉, 대동맥 질환"},
}

COMMON_SYMPTOMS = [
    {"symptom": "머리가 아파요", "category": "두통"},
    {"symptom": "배가 아파요", "category": "복통"},
    {"symptom": "기침이 나요", "category": "호흡기"},
    {"symptom": "열이 나요", "category": "발열"},
    {"symptom": "허리가 아파요", "category": "근골격"},
    {"symptom": "무릎이 아파요", "category": "근골격"},
    {"symptom": "눈이 아파요", "category": "안과"},
    {"symptom": "피부에 뭐가 났어요", "category": "피부"},
    {"symptom": "소변볼 때 아파요", "category": "비뇨기"},
    {"symptom": "목이 아파요", "category": "이비인후"},
    {"symptom": "가슴이 답답해요", "category": "흉통"},
    {"symptom": "어지러워요", "category": "신경"},
    {"symptom": "잠이 안 와요", "category": "정신"},
    {"symptom": "살이 빠져요", "category": "전신"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@triage_bp.route("/api/medical/triage", methods=["POST"])
def triage():
    """Symptom-based department recommendation
    ---
    tags: [Medical - Triage]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [symptom]
          properties:
            symptom: {type: string, description: "증상 설명 (자연어)"}
            age: {type: integer}
            sex: {type: string, enum: [M, F]}
            duration: {type: string, description: "기간 (3일, 1주 등)"}
            severity: {type: string, enum: [mild, moderate, severe]}
    responses:
      200:
        description: 진료과 추천 결과
    """
    data = request.json or {}
    symptom_text = data.get("symptom", "").strip()
    if not symptom_text:
        return jsonify({"error": "symptom is required"}), 400

    age = data.get("age")
    if age is not None:
        try:
            age = int(age)
        except (ValueError, TypeError):
            age = None
    sex = data.get("sex")
    duration = data.get("duration")
    severity = data.get("severity")

    # 1. Extract symptoms
    symptoms = _extract_symptoms(symptom_text)
    if not symptoms:
        # LLM fallback for unrecognized symptoms
        symptoms = _extract_symptoms_llm(symptom_text)

    # 2. Emergency check
    emergency = _check_emergency(symptoms, age)

    # 3. Department recommendations (rules) — A: duration/severity 전달
    recommendations_raw = _get_dept_recommendations(symptoms, age, sex, duration, severity)

    # 4. LLM enrichment — add reasons and possible conditions
    recommendations = []
    for dept, confidence, code in recommendations_raw[:3]:
        dept_info = DEPARTMENTS.get(dept, {})
        rec = {
            "rank": len(recommendations) + 1,
            "department": dept,
            "department_code": code,
            "department_full": dept_info.get("full", dept),
            "confidence": confidence,
            "reason": "",
            "possible_conditions": [],
            "urgency": "high" if emergency else ("moderate" if severity == "severe" else "low"),
        }
        recommendations.append(rec)

    # LLM for natural language guidance
    guidance = ""
    if symptoms and recommendations:
        guidance = _generate_guidance(symptom_text, symptoms, recommendations, age, sex, duration, severity)
        # Fill in reasons from LLM if available
        if guidance:
            for i, rec in enumerate(recommendations):
                if not rec["reason"]:
                    rec["reason"] = f"{rec['department']}에서 관련 증상을 진료합니다."

    result = {
        "recommendations": recommendations,
        "symptom_analysis": {
            "input": symptom_text,
            "extracted_symptoms": symptoms,
            "duration": duration,
            "severity": severity,
            "age_group": _age_group(age) if age else None,
        },
        "guidance": guidance,
        "emergency_warning": {
            "level": emergency["level"],
            "message": emergency["message"],
            "call_119": emergency.get("call_119", False),
        } if emergency else None,
        "disclaimer": "이 추천은 AI 참고 자료이며, 정확한 진단은 의사의 진료를 통해 이루어집니다.",
    }

    return jsonify(result)


@triage_bp.route("/api/medical/triage/follow-up", methods=["POST"])
def triage_follow_up():
    """Follow-up question for ambiguous symptoms
    ---
    tags: [Medical - Triage]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            session_id: {type: string}
            answer: {type: string}
            symptom: {type: string, description: "초기 증상 (세션 없을 때)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "session not found. provide symptom to start."}}
      503:
        description: "의존 서비스 사용 불가"
        schema:
          type: object
          properties: {error: {type: string, example: "too many active sessions, try again later"}}
    """
    data = request.json or {}
    session_id = data.get("session_id")
    answer = data.get("answer", "").strip()
    initial = data.get("symptom", "").strip()

    # Purge expired sessions
    now = time.time()
    expired = [k for k, v in _sessions.items() if now - v.get("created_at", 0) > _SESSION_TTL]
    for k in expired:
        del _sessions[k]

    if not session_id and initial:
        if len(_sessions) >= _MAX_SESSIONS:
            return jsonify({"error": "too many active sessions, try again later"}), 503
        # New session
        session_id = str(uuid.uuid4())[:12]
        symptoms = _extract_symptoms(initial)
        _sessions[session_id] = {
            "symptoms": symptoms,
            "original": initial,
            "step": 0,
            "answers": [],
            "created_at": now,
        }

    session = _sessions.get(session_id)
    if not session:
        return jsonify({"error": "session not found. provide symptom to start."}), 400

    if answer:
        session["answers"].append(answer)
        # Extract additional symptoms from answer
        new_symptoms = _extract_symptoms(answer)
        for s in new_symptoms:
            if s not in session["symptoms"]:
                session["symptoms"].append(s)

    session["step"] += 1

    # Determine next question
    questions = [
        {"q": "언제부터 증상이 있으셨나요?", "options": ["오늘부터", "2-3일 전", "1주일 전", "한 달 이상"]},
        {"q": "다른 증상이 더 있으신가요?", "options": ["열이 나요", "어지러워요", "구토가 있어요", "다른 증상 없어요"]},
        {"q": "증상이 얼마나 심한가요?", "options": ["가벼워요", "보통이에요", "많이 아파요", "참기 힘들어요"]},
    ]

    if session["step"] > len(questions):
        # Enough info — return recommendation
        recs = _get_dept_recommendations(session["symptoms"])
        result_recs = []
        for dept, conf, code in recs[:3]:
            result_recs.append({"department": dept, "department_code": code, "confidence": conf})
        del _sessions[session_id]
        return jsonify({
            "complete": True,
            "recommendations": result_recs,
            "symptoms": session["symptoms"],
            "session_id": session_id,
        })

    q = questions[session["step"] - 1]
    return jsonify({
        "complete": False,
        "question": q["q"],
        "options": q["options"],
        "session_id": session_id,
        "current_symptoms": session["symptoms"],
    })


@triage_bp.route("/api/medical/triage/departments")
def triage_departments():
    """List all departments with descriptions
    ---
    tags: [Medical - Triage]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            departments: {}
            count: {type: integer}
    """
    depts = []
    for name, info in DEPARTMENTS.items():
        depts.append({
            "name": name,
            "code": info["code"],
            "full_name": info.get("full", name),
            "description": info["desc"],
        })
    return jsonify({"departments": depts, "count": len(depts)})


@triage_bp.route("/api/medical/triage/common-symptoms")
def triage_common_symptoms():
    """List common symptoms for quick selection
    ---
    tags: [Medical - Triage]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            symptoms: {}
            count: {type: integer}
    """
    return jsonify({"symptoms": COMMON_SYMPTOMS, "count": len(COMMON_SYMPTOMS)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _age_group(age):
    if age < 3:
        return "영아"
    elif age < 15:
        return "소아"
    elif age < 20:
        return "청소년"
    elif age < 40:
        return "청년"
    elif age < 60:
        return "중년"
    else:
        return "고령"


# ── 증상 리스트 파싱 (2026-08-26 실측 기반) ─────────────────────────────────
# 사전 등록 측정(N=30·`docs/01-plan/references/triage-json-retry-preregistration-2026-08-26.md`)에서
# 이 경로의 LLM은 **10%(3/30)**가 `["체중감소", "목` 처럼 배열을 쓰다 말고 중국어 잡담으로
# 폭주했다. P5-1 기획서가 지정한 `generate_with_validation` 재시도는 **8회 시도 중 0회 전환**
# (같은 문장 5회 반복도 5/5 실패)이라 사전 등록 기준에 따라 기각했다 — 폭주는 결정적이라
# 교정 프롬프트로 안 잡힌다.
#
# 🔴 측정이 드러낸 진짜 결함은 재시도 부재가 아니라 **쉼표 분리 폴백이 그 쓰레기를 그대로
#    증상으로 내보내고 있었다**는 것이다(200인데 빈 결과 클래스의 변종 — 응답은 200이고
#    `extracted_symptoms`에 `'["체중감소"'`·`'"목干燥后的答案为："'`가 실려 나갔다):
#      현행: ['["손발冰冷"', '"手指变白"]', '["손발이 차다"', '"손끝이 하얗게 변함"]']
#      처치: ['손발이 차다', '손끝이 하얗게 변함']
# 처치는 좁게 둔다 — (a) 응답 안의 JSON 배열 조각을 긁고 (b) 대괄호·따옴표 잔재를 털고
# (c) **한자·가나가 섞인 항목만** 버린다. 영어 항목(`olfaction loss` 1건 관측)은 오염과 달리
# 근거가 없으므로 손대지 않는다(효과 미확인 처치 금지).
# 검증: 위 원자료 35건을 그대로 회귀 입력으로 재사용 — 개선 8·퇴행 0·오염 잔존 8→0.
_SYMPTOM_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")   # 한자·가나
_JSON_ARRAY_FRAGMENT = re.compile(r"\[[^\[\]]*\]")


def _clean_symptom(s):
    """쉼표 분리가 남기는 `["체중감소"` 같은 대괄호·따옴표 잔재를 턴다."""
    return s.strip().strip("-·•").strip().strip('[]"\u201c\u201d').strip()


def _symptom_acceptable(s):
    # 정화 후에도 JSON 구조 문자가 남아 있으면 증상이 아니라 잔해다.
    # (모델이 `{"증상": "두통"}` 같은 객체를 돌려주면 쉼표 분리가 통째로 한 항목으로 만든다 —
    #  종전 코드는 이 경우 `["기타증상"]`이었고 그 동작을 유지한다.)
    if any(ch in s for ch in '{}[]"'):
        return False
    return bool(s) and len(s) < 20 and not _SYMPTOM_CJK.search(s)


def _parse_symptom_list(raw):
    """LLM 응답에서 증상 문자열 리스트를 뽑는다. 항상 비지 않은 리스트를 돌려준다."""
    from core.quality import force_json_array
    items = force_json_array(raw)
    if not items:
        # 모델이 정상 배열을 쓴 뒤 잡담을 붙이면 전체 파싱은 실패한다 → 배열 조각만 긁는다.
        for m in _JSON_ARRAY_FRAGMENT.finditer(raw):
            try:
                v = json.loads(m.group(0))
            except Exception:
                continue
            if isinstance(v, list):
                items.extend(v)
    out, seen = [], set()
    for s in items:
        if not isinstance(s, str):
            continue
        c = _clean_symptom(s)
        if _symptom_acceptable(c) and c not in seen:
            seen.add(c)
            out.append(c)
    if out:
        return out
    # 최종 방어선: 쉼표 분리(계약=항상 리스트). 같은 정화를 여기에도 건다.
    for s in raw.replace("\n", ",").split(","):
        c = _clean_symptom(s)
        if _symptom_acceptable(c) and c not in seen:
            seen.add(c)
            out.append(c)
    return out or ["기타증상"]


def _extract_symptoms_llm(text):
    """LLM으로 다증상 추출 (규칙 실패 시) — JSON 구조화 출력"""
    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_chat_story_triage.py)
        from core import model_router
        res = model_router.generate(
            "triage.extract_symptoms",
            f'다음 문장에서 모든 의학적 증상 키워드를 추출하세요.\nJSON 배열만 출력: ["증상1", "증상2", ...]\n\n문장: "{text}"',
            model="qwen2.5:14b",
            system="증상 키워드 추출 전문가. JSON 배열만 출력. 예: [\"두통\", \"발열\"]",
            options={"temperature": 0.1, "num_predict": 100}, timeout=15,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        # v2.125.16 무성 실패 일괄(2차): HTTP 오류를 아래 except와 같은 분기로.
        # ⚠️ 결과는 종전과 같다(둘 다 `["기타증상"]` 폴백) — 바뀐 것은 **실패가 로그에 남는다**는 점뿐이다.
        #    호출자 계약(항상 리스트 반환)은 그대로 둔다: 증상 추출 실패로 분류 API를 500으로 만들 이유가 없다.
        if not res.ok:
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        return _parse_symptom_list(res.text)
    except Exception as e:
        # 🔴 v2.125.16이 "실패가 로그에 남는다"고 적었으나 이 except는 **로그 호출이 0건**이었다
        #    (모듈에 logger가 있는데 파일 전체에서 한 번도 안 썼다) — 처치는 결과를 바꾸지 않고
        #    주장한 효과만 실제로 이행한다. 규칙 추출이 실패한 뒤의 폴백 경로라 조용히 `["기타증상"]`이
        #    나가면 분류 품질 저하가 사람 눈에 띄는 경로가 없다(200인데 빈 결과 클래스).
        logger.warning(f"triage 증상 LLM 추출 실패 (['기타증상']로 폴백): {e}")
        return ["기타증상"]


def _generate_guidance(symptom_text, symptoms, recommendations, age, sex, duration, severity):
    """LLM으로 자연어 안내문 생성"""
    try:
        dept_list = ", ".join([f"{r['department']}({r['confidence']})" for r in recommendations[:3]])
        prompt = f"환자 증상: {symptom_text}\n추출 증상: {', '.join(symptoms)}\n추천 진료과: {dept_list}"
        if age:
            prompt += f"\n나이: {age}세"
        if duration:
            prompt += f"\n기간: {duration}"

        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_chat_story_triage.py)
        from core import model_router
        res = model_router.generate(
            "triage.guidance", prompt, model="qwen2.5:14b",
            system="당신은 환자에게 진료과를 안내하는 AI입니다. 반드시 한국어로만 답변하세요. 2-3문장으로 간결하게 어떤 진료과를 먼저 방문해야 하는지, 이유와 함께 한국어로 안내하세요.",
            options={"temperature": 0.3, "num_predict": 200}, timeout=30,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        from core.korean import force_korean as _fk_triage
        # v2.125.16: HTTP 오류 → 아래 except(빈 안내문)와 같은 분기. 결과는 종전과 동일하고
        # 실패만 로그에 남는다(안내문 부재는 호출자가 이미 처리하는 계약).
        if not res.ok:
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        return _fk_triage(res.text).strip()
    except Exception as e:
        # 🔴 site 1과 같은 결함 — v2.125.16이 "실패만 로그에 남는다"고 적었는데 로그가 없었다.
        #    빈 안내문은 호출자(`classify`)가 이미 처리하는 계약이라 결과는 그대로 두고 로그만 남긴다.
        logger.warning(f"triage 안내문 LLM 생성 실패 (빈 안내문으로 진행): {e}")
        return ""
