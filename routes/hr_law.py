"""HR Law API Blueprint — 한국 근로기준법 AI 어시스턴트"""
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, date, timezone

import hashlib

import requests
from flask import Blueprint, request, jsonify, Response
from core.cache import TTLCache
from core.config import get_self_base as _get_self_base
from core.security import require_auth

logger = logging.getLogger("ollama-admin")

hr_law_bp = Blueprint("hr_law", __name__)

# 법률 질의 캐시 — 동일 질문 반복 방지 (TTL 1시간, 테넌트별 키 분리)
_hr_law_cache = TTLCache(maxsize=500, ttl=3600)


# 관리자 판정 — app.py에서 init_hr_law(is_admin_request=)로 주입.
# ⚠️ 미주입 시 **fail-closed**(False). 과거 이 이름이 모듈에 없어 admin 엔드포인트
# 6곳이 NameError→500이었다(2026-07-21 감사에서 발견). 주입 실패가 조용한 허용으로
# 바뀌지 않도록 기본값을 False로 둔다.
_is_admin_request = None


def is_admin_request():
    if _is_admin_request is None:
        logger.warning("[hr-law] is_admin_request 미주입 — 관리 요청 거부(fail-closed)")
        return False
    return bool(_is_admin_request())


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# app.py에서 init_hr_law()로 주입
_load_config = None
_ollama_session = None
_llm_generate_korean = None
_force_korean_token = None
_OLLAMA_BASE = None
_BASE_DIR = None

# Thread-local DB connection
_hr_law_db_local = threading.local()


def init_hr_law(*, load_config, ollama_session, llm_generate_korean,
                force_korean_token, ollama_base, base_dir,
                is_admin_request=None):
    global _load_config, _ollama_session, _llm_generate_korean
    global _force_korean_token, _OLLAMA_BASE, _BASE_DIR, _is_admin_request
    if is_admin_request is not None:
        _is_admin_request = is_admin_request
    _load_config = load_config
    _ollama_session = ollama_session
    _llm_generate_korean = llm_generate_korean
    _force_korean_token = force_korean_token
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir


def _get_hr_law_db():
    conn = getattr(_hr_law_db_local, "conn", None)
    if conn is None:
        _hr_path = os.path.join(_BASE_DIR, "hr_law.db")
        conn = sqlite3.connect(_hr_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _hr_law_db_local.conn = conn
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try: conn.close()
        except Exception: pass
        _hr_law_db_local.conn = None
        return _get_hr_law_db()
    return conn


HR_LAW_SYSTEM_PROMPT = """당신은 한국 근로기준법 및 인사/노무 전문 AI 어시스턴트입니다.
반드시 한국어로만 답변하세요. 절대로 다른 언어(중국어, 영어 등)를 사용하지 마세요.
아래 제공된 법률 조문을 근거로 정확하게 답변하세요.

답변 규칙:
1. 모든 답변은 반드시 한국어로 작성하세요
2. 반드시 관련 법 조문 번호와 내용을 인용하세요
3. 법률 해석이 불확실한 경우 "전문가 상담을 권장합니다"라고 안내하세요
4. 판례나 행정해석이 있으면 함께 제시하세요
5. 실무 대응 방법도 함께 안내하세요
6. 상시 5인 미만 사업장 적용 예외가 있으면 반드시 언급하세요

[참조 법률 자료]
{context}
"""


HR_LAW_LANG_PROMPTS = {
    "ko": "반드시 한국어로만 답변하세요.",
    "en": "Answer in English only. Translate all Korean legal terms with the original Korean in parentheses.",
    "vi": "Tr\u1ea3 l\u1eddi b\u1eb1ng ti\u1ebfng Vi\u1ec7t. D\u1ecbch c\u00e1c thu\u1eadt ng\u1eef ph\u00e1p lu\u1eadt H\u00e0n Qu\u1ed1c k\u00e8m nguy\u00ean v\u0103n ti\u1ebfng H\u00e0n trong ngo\u1eb7c.",
    "zh": "\u7528\u4e2d\u6587\u56de\u7b54\u3002\u97e9\u56fd\u6cd5\u5f8b\u672f\u8bed\u8bf7\u9644\u97e9\u6587\u539f\u6587\u3002",
    "ja": "\u65e5\u672c\u8a9e\u3067\u56de\u7b54\u3057\u3066\u304f\u3060\u3055\u3044\u3002\u97d3\u56fd\u306e\u6cd5\u5f8b\u7528\u8a9e\u306f\u97d3\u56fd\u8a9e\u539f\u6587\u3092\u62ec\u5f27\u5185\u306b\u4ed8\u8a18\u3002",
}


HR_LAW_REVIEW_PROMPT = """\u00b9\u00b9\u00b9\u00b9\u00b9 \ud55c\uad6d\uc5b4\ub85c\ub9cc \ub2f5\ubcc0\ud558\uc138\uc694.
\ub2f9\uc2e0\uc740 \ud55c\uad6d \ub178\ubb34\uc0ac\uc785\ub2c8\ub2e4. \uc544\ub798 \ud68c\uc0ac \ucde8\uc5c5\uaddc\uce59\uc744 \uac80\ud1a0\ud558\uc5ec \uadfc\ub85c\uae30\uc900\ubc95 \ub4f1 \uad00\ub828 \ubc95\ub839 \uc704\ubc18 \uc5ec\ubd80\ub97c \ud310\ub2e8\ud558\uc138\uc694.

[\uac80\ud1a0 \uae30\uc900]
1. \uadfc\ub85c\uae30\uc900\ubc95 \ucd5c\uc18c \uae30\uc900 \ubbf8\ub2ec \uc5ec\ubd80
2. \uac15\ud589\ubc95\uaddc \uc704\ubc18 \uc5ec\ubd80
3. \ud310\ub840/\ud589\uc815\ud574\uc11d\uacfc \ub2e4\ub978 \ub0b4\uc6a9
4. \uadfc\ub85c\uc790\uc5d0\uac8c \ubd88\ub9ac\ud55c \uc870\ud56d
5. \ubaa8\ud638\ud55c \ud45c\ud604\uc73c\ub85c \ubd84\uc7c1 \uc18c\uc9c0\uac00 \uc788\ub294 \uc870\ud56d

[\ucc38\uc870 \ubc95\ub839]
{context}

[\ud68c\uc0ac \ucde8\uc5c5\uaddc\uce59]
{rules}

\uac01 \uc870\ud56d\ubcc4\ub85c \ub2e4\uc74c \ud615\uc2dd\uc73c\ub85c \ud310\ub2e8\ud558\uc138\uc694:
- \u274c \uc704\ubc18: \ubc95\ub839 \uc704\ubc18 (\uc870\ubb38 \uba85\uc2dc, \uc218\uc815 \ud544\uc694)
- \u26a0\ufe0f \uc8fc\uc758: \ubd84\uc7c1 \uc18c\uc9c0 \ub610\ub294 \ubaa8\ud638\ud55c \ud45c\ud604 (\uac1c\uc120 \uad8c\uace0)
- \u2705 \uc801\ud569: \ubc95\ub839 \uae30\uc900 \ucda9\uc871

\uac01 \ud310\ub2e8\uc5d0 \uad00\ub828 \ubc95\uc870\ubb38\uacfc \uc218\uc815 \uc81c\uc548\uc744 \ud3ec\ud568\ud558\uc138\uc694.
"""


RULE_TEMPLATE = {
    "sections": [
        {
            "id": "general",
            "title": "총칙",
            "required": True,
            "articles": [
                {"id": "purpose", "title": "목적", "required": True, "hint": "이 규칙은 회사와 직원의 권리·의무를 규정함을 목적으로 한다."},
                {"id": "scope", "title": "적용 범위", "required": True, "hint": "이 규칙은 회사에 근무하는 모든 직원에게 적용한다."},
                {"id": "definition", "title": "용어의 정의", "required": False, "hint": "직원, 정규직, 계약직, 수습기간 등의 정의"},
            ]
        },
        {
            "id": "recruitment",
            "title": "채용 및 근로계약",
            "required": True,
            "articles": [
                {"id": "hiring_docs", "title": "채용 서류", "required": True, "hint": "이력서, 자기소개서, 졸업증명서, 자격증 사본 등"},
                {"id": "probation", "title": "수습 기간", "required": False, "hint": "수습 기간, 수습 중 근로조건, 평가 기준"},
                {"id": "contract", "title": "근로계약", "required": True, "hint": "근로계약서 작성, 필수 기재사항, 교부 의무"},
            ]
        },
        {
            "id": "work_hours",
            "title": "근로시간·휴게·휴일",
            "required": True,
            "legal_basis": "근로기준법 제50조~제55조",
            "articles": [
                {"id": "work_hours", "title": "근로시간", "required": True, "hint": "1일 8시간, 1주 40시간. 시업/종업 시각 명시", "legal": "근로기준법 제50조"},
                {"id": "break_time", "title": "휴게시간", "required": True, "hint": "4시간 근로 시 30분, 8시간 근로 시 1시간", "legal": "근로기준법 제54조"},
                {"id": "weekly_holiday", "title": "주휴일", "required": True, "hint": "주 1회 이상 유급휴일", "legal": "근로기준법 제55조"},
                {"id": "public_holiday", "title": "공휴일", "required": True, "hint": "관공서 공휴일 유급 보장 (5인 이상)", "legal": "근로기준법 제55조 제2항"},
                {"id": "flexible_hours", "title": "탄력적/선택적 근로시간제", "required": False, "hint": "도입 시 근로자대표 서면합의 필요"},
            ]
        },
        {
            "id": "overtime",
            "title": "연장·야간·휴일 근로",
            "required": True,
            "legal_basis": "근로기준법 제53조, 제56조",
            "articles": [
                {"id": "overtime_rule", "title": "연장근로", "required": True, "hint": "1주 12시간 한도, 사전 승인 절차", "legal": "근로기준법 제53조"},
                {"id": "overtime_pay", "title": "가산임금", "required": True, "hint": "연장 150%, 야간(22~06시) +50%, 휴일 150%/200%", "legal": "근로기준법 제56조"},
                {"id": "comp_time", "title": "보상휴가제", "required": False, "hint": "가산임금 대신 유급휴가 부여 가능 (서면합의 필요)"},
            ]
        },
        {
            "id": "leave",
            "title": "휴가",
            "required": True,
            "legal_basis": "근로기준법 제60조~제62조",
            "articles": [
                {"id": "annual_leave", "title": "연차 유급휴가", "required": True, "hint": "1년 80% 출근 시 15일, 1년 미만 월 1일, 3년부터 2년마다 +1일 (최대 25일)", "legal": "근로기준법 제60조"},
                {"id": "leave_promotion", "title": "연차 사용촉진", "required": True, "hint": "만료 6개월 전 촉구 → 미지정 시 2개월 전 사용자 지정", "legal": "근로기준법 제61조"},
                {"id": "sick_leave", "title": "병가", "required": False, "hint": "유급/무급 여부, 진단서 제출 기준"},
                {"id": "special_leave", "title": "경조사 휴가", "required": False, "hint": "결혼, 사망, 출산 등 일수 규정"},
                {"id": "maternity_leave", "title": "출산전후휴가", "required": True, "hint": "90일 (다태아 120일), 출산 후 45일 이상", "legal": "근로기준법 제74조"},
                {"id": "parental_leave", "title": "육아휴직", "required": True, "hint": "8세 이하 자녀, 1년 이내", "legal": "남녀고용평등법 제19조"},
            ]
        },
        {
            "id": "wage",
            "title": "임금",
            "required": True,
            "legal_basis": "근로기준법 제43조~제49조",
            "articles": [
                {"id": "wage_components", "title": "임금 구성항목", "required": True, "hint": "기본급, 수당(직책, 자격, 가족 등), 상여금"},
                {"id": "pay_date", "title": "임금 지급일", "required": True, "hint": "매월 1회 이상 정기지급일 명시", "legal": "근로기준법 제43조"},
                {"id": "pay_method", "title": "지급방법", "required": True, "hint": "통화/직접/전액/정기 지급 원칙"},
                {"id": "minimum_wage", "title": "최저임금", "required": True, "hint": "최저임금 이상 보장", "legal": "최저임금법 제6조"},
            ]
        },
        {
            "id": "severance",
            "title": "퇴직급여",
            "required": True,
            "legal_basis": "근로자퇴직급여보장법 제4조, 제8조",
            "articles": [
                {"id": "severance_type", "title": "퇴직급여 제도", "required": True, "hint": "퇴직금제도(DB) 또는 확정기여형(DC)"},
                {"id": "severance_calc", "title": "퇴직금 산정", "required": True, "hint": "계속근로 1년 × 30일분 평균임금", "legal": "퇴직급여보장법 제8조"},
            ]
        },
        {
            "id": "discipline",
            "title": "상벌/징계",
            "required": True,
            "articles": [
                {"id": "reward", "title": "포상", "required": False, "hint": "포상 기준 및 종류"},
                {"id": "discipline_types", "title": "징계의 종류", "required": True, "hint": "경고, 감봉, 정직, 해고 등 단계"},
                {"id": "discipline_procedure", "title": "징계 절차", "required": True, "hint": "징계위원회, 소명기회 부여, 서면통지"},
                {"id": "discipline_limits", "title": "감봉의 제한", "required": True, "hint": "1회 평균임금 1일분의 1/2, 총액 1/10 이내", "legal": "근로기준법 제95조"},
            ]
        },
        {
            "id": "dismissal",
            "title": "해고/퇴직",
            "required": True,
            "legal_basis": "근로기준법 제23조~제28조",
            "articles": [
                {"id": "dismissal_restriction", "title": "해고 제한", "required": True, "hint": "정당한 이유 없는 해고 금지", "legal": "근로기준법 제23조"},
                {"id": "dismissal_notice", "title": "해고 예고", "required": True, "hint": "30일 전 예고 또는 30일분 통상임금", "legal": "근로기준법 제26조"},
                {"id": "dismissal_written", "title": "서면 통지", "required": True, "hint": "해고사유·시기 서면 통지 필수", "legal": "근로기준법 제27조"},
                {"id": "retirement_age", "title": "정년", "required": False, "hint": "60세 이상으로 정해야 함 (고용상 연령차별금지법)"},
                {"id": "resignation", "title": "자진 퇴직", "required": True, "hint": "퇴직 통보 기한 (보통 30일 전)"},
            ]
        },
        {
            "id": "safety",
            "title": "안전보건/괴롭힘 방지",
            "required": True,
            "articles": [
                {"id": "safety_duty", "title": "안전보건 의무", "required": True, "hint": "산업안전보건법 준수, 안전교육"},
                {"id": "harassment", "title": "직장 내 괴롭힘 금지", "required": True, "hint": "금지 행위, 신고 절차, 조사, 보호 조치", "legal": "근로기준법 제76조의2~3"},
                {"id": "sexual_harassment", "title": "직장 내 성희롱 금지", "required": True, "hint": "예방교육, 신고, 조치", "legal": "남녀고용평등법 제12조"},
            ]
        },
        {
            "id": "privacy",
            "title": "개인정보 보호",
            "required": False,
            "articles": [
                {"id": "privacy_collection", "title": "개인정보 수집/이용", "required": False, "hint": "수집 항목, 이용 목적, 동의"},
                {"id": "privacy_retention", "title": "보관 및 파기", "required": False, "hint": "보관 기간, 파기 방법"},
            ]
        },
    ]
}



@hr_law_bp.route("/api/hr-law/ask", methods=["POST"])
@require_auth
def hr_law_ask():
    """근태/인사 법률 질의응답 (RAG + LLM)
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: false
        description: "회사별 API 키 (있으면 해당 회사 취업규칙 포함 검색)"
      - in: body
        name: body
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string, description: "법률 질문"}
            category: {type: string, description: "카테고리 필터 (work_hours, annual_leave, wage, dismissal 등)"}
            model: {type: string, description: "LLM 모델 (기본: default_model)"}
            language: {type: string, description: "답변 언어 (ko/en/vi/zh/ja, 기본: ko)", default: ko}
    responses:
      200:
        description: "법적 근거 포함 답변"
        schema:
          type: object
          properties:
            answer: {type: string}
            sources: {type: array, items: {type: object}}
            tokens: {type: integer}
            tenant: {type: string, description: "테넌트명 (없으면 null)"}
      429:
        description: "일일 쿼리 한도 초과"
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    # 테넌트 식별 (선택)
    tenant = _resolve_tenant(request)
    tenant_id = tenant["id"] if tenant else "__common__"
    if tenant:
        # 사용량 체크
        allowed, current, limit = _check_tenant_limit(tenant_id)
        if not allowed:
            return jsonify({"error": f"일일 쿼리 한도 초과 ({current}/{limit})"}), 429

    category = data.get("category")
    language = data.get("language", "ko")  # ko, en, vi, zh, ja
    model = data.get("model") or _load_config().get("default_model", "qwen2.5:14b")

    # 캐시 조회 (테넌트+질문+카테고리+언어 복합 키)
    _ck_raw = f"{tenant_id}|{question}|{category or ''}|{language}|{model}"
    _ck = hashlib.md5(_ck_raw.encode("utf-8", errors="replace")).hexdigest()
    _cached = _hr_law_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    # 1. RAG 검색 — 관련 법조문 Top-5
    try:
        rag_r = requests.post(f"{_get_self_base()}/api/rag/search",
                              json={"query": question, "top_k": 5}, timeout=10)
        rag_results = rag_r.json().get("results", []) if rag_r.status_code == 200 else []
    except Exception as e:
        logger.warning("HR Law RAG search failed: %s", e)
        rag_results = []

    # 카테고리 필터 (선택)
    if category and rag_results:
        filtered = [r for r in rag_results if r.get("metadata", {}).get("category") == category]
        if filtered:
            rag_results = filtered

    # 2. 컨텍스트 구성
    context_parts = []
    sources = []

    # HR Law DB에서 추가 검색 + 회사 취업규칙
    db_rows = []
    try:
        conn = _get_hr_law_db()
        try:
            if category:
                db_rows = conn.execute(
                    "SELECT law_name, article, title, content FROM law_documents WHERE category=? LIMIT 3",
                    (category,)).fetchall()
            # 회사 취업규칙 검색
            if tenant_id != "__common__":
                rule_rows = conn.execute(
                    "SELECT title, content, category FROM company_rules WHERE tenant_id=? LIMIT 5",
                    (tenant_id,)).fetchall()
                for rr in rule_rows:
                    context_parts.append(f"### [회사 취업규칙] {rr['title']}\n{rr['content']}")
                    sources.append({"law": "취업규칙", "article": "", "title": rr["title"]})
        finally:
            conn.close()
    except Exception as e:
        logger.warning("HR Law DB query failed: %s", e)
    for r in rag_results[:5]:
        meta = r.get("metadata", {})
        text = r.get("text", "")
        law = meta.get("law", "")
        article = meta.get("article", "")
        title = meta.get("title", "")
        context_parts.append(f"### {law} {article} ({title})\n{text}")
        sources.append({"law": law, "article": article, "title": title, "score": round(r.get("score", 0), 3)})

    for row in db_rows:
        key = f"{row['law_name']} {row['article']}"
        if not any(key in p for p in context_parts):
            context_parts.append(f"### {row['law_name']} {row['article']} ({row['title']})\n{row['content']}")
            sources.append({"law": row["law_name"], "article": row["article"], "title": row["title"]})

    context = "\n\n".join(context_parts) if context_parts else "관련 법률 자료가 없습니다."

    # 3. LLM 답변 생성
    lang_instruction = HR_LAW_LANG_PROMPTS.get(language, HR_LAW_LANG_PROMPTS["ko"])
    system = HR_LAW_SYSTEM_PROMPT.replace("{context}", context)
    system = system.replace("반드시 한국어로만 답변하세요.", lang_instruction)
    try:
        answer, _meta = _llm_generate_korean(
            model=model, prompt=question, system=system, timeout=120)
        tokens = _meta.get("eval_count", 0)
    except Exception as e:
        answer = f"LLM 오류: {str(e)}"
        tokens = 0

    # 4. 질의 이력 저장
    try:

        qid = str(uuid.uuid4())
        conn = _get_hr_law_db()
        try:
            conn.execute("INSERT INTO hr_queries (id, question, answer, sources, category, created_at, user) VALUES (?,?,?,?,?,?,?)",
                         (qid, question, answer, json.dumps(sources, ensure_ascii=False), category,
                          datetime.now(timezone.utc).isoformat() + "Z", getattr(request, 'act_key_name', 'unknown')))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("HR Law query history save failed: %s", e)

    # 테넌트 사용량 기록
    if tenant:
        _increment_tenant_usage(tenant_id)

    resp = {
        "answer": answer,
        "sources": sources,
        "tokens": tokens,
        "category": category,
        "model": model,
        "language": language,
        "tenant": tenant["name"] if tenant else None,
    }
    _hr_law_cache.set(_ck, resp)
    return jsonify(resp)





@hr_law_bp.route("/api/hr-law/ask/stream", methods=["POST"])
@require_auth
def hr_law_ask_stream():
    """법률 질의응답 (스트리밍)
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "question required"}}
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    tenant = _resolve_tenant(request)
    tenant_id = tenant["id"] if tenant else "__common__"
    category = data.get("category")
    language = data.get("language", "ko")
    model = data.get("model") or _load_config().get("default_model", "qwen2.5:14b")

    # RAG 검색
    sources = []
    context_parts = []
    try:
        rag_r = requests.post(f"{_get_self_base()}/api/rag/search",
                              json={"query": question, "top_k": 5}, timeout=10)
        rag_results = rag_r.json().get("results", []) if rag_r.status_code == 200 else []
        if category:
            filtered = [r for r in rag_results if r.get("metadata", {}).get("category") == category]
            if filtered: rag_results = filtered
        for r in rag_results[:5]:
            meta = r.get("metadata", {})
            context_parts.append(f"### {meta.get('law','')} {meta.get('article','')} ({meta.get('title','')})")
            sources.append({"law": meta.get("law",""), "article": meta.get("article",""), "title": meta.get("title","")})
    except Exception as e:
        logger.warning("HR Law stream RAG search failed: %s", e)

    # 회사 취업규칙
    if tenant_id != "__common__":
        try:
            conn = _get_hr_law_db()
            try:
                rules = conn.execute("SELECT title, content FROM company_rules WHERE tenant_id=? LIMIT 5", (tenant_id,)).fetchall()
            finally:
                conn.close()
            for rr in rules:
                context_parts.append(f"### [취업규칙] {rr['title']}\n{rr['content']}")
                sources.append({"law": "취업규칙", "article": "", "title": rr["title"]})
        except Exception as e:
            logger.warning("HR Law stream rules query failed: %s", e)

    context = "\n\n".join(context_parts) if context_parts else "관련 법률 자료 없음"
    lang_instruction = HR_LAW_LANG_PROMPTS.get(language, HR_LAW_LANG_PROMPTS["ko"])
    system = HR_LAW_SYSTEM_PROMPT.replace("{context}", context)
    system = system.replace("반드시 한국어로만 답변하세요.", lang_instruction)

    # 스트리밍 생성기
    def generate():
        # 먼저 sources 전송
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources}, ensure_ascii=False)}\n\n"
        # LLM 스트리밍
        try:
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
            # ⚠️ 종전엔 HTTP 오류 본문(`{"error":…}`)이 토큰도 done도 아니라 **무언히 끝나고 빈 답변이 이력에 저장**됐다
            #    (무성 실패 클래스). 라우터는 HTTP≥400을 던지므로 아래 except의 error 이벤트로 드러난다 — 의도된 유일한 차이.
            from core import model_router
            full_response = ""
            for chunk in model_router.stream(
                    "hr_law.answer", question, model=model, system=system, timeout=120,
                    adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)):
                if chunk.done:
                    yield f"data: {json.dumps({'type': 'done', 'tokens': chunk.tokens}, ensure_ascii=False)}\n\n"
                    continue
                token = chunk.text
                if token:
                    full_response += token
                    token = _force_korean_token(token)
                    yield f"data: {json.dumps({'type': 'token', 'token': token}, ensure_ascii=False)}\n\n"
            # 이력 저장 (필터링된 텍스트, v2.62.1)
            try:
                from core.korean import force_korean as _fk_hr
                full_response = _fk_hr(full_response)
                conn = _get_hr_law_db()
                try:
                    conn.execute("INSERT INTO hr_queries (id,question,answer,sources,category,created_at,user,tenant_id) VALUES (?,?,?,?,?,datetime('now'),?,?)",
                        (str(uuid.uuid4()), question, full_response, json.dumps(sources,ensure_ascii=False), category,
                         getattr(request, 'act_key_name', 'unknown'), tenant_id))
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("HR Law stream history save failed: %s", e)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@hr_law_bp.route("/api/hr-law/search", methods=["POST"])
@require_auth
def hr_law_search():
    """법률 키워드 검색
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            query: {type: string}
            category: {type: string}
            law_name: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    category = data.get("category")
    law_name = data.get("law_name")

    conn = _get_hr_law_db()
    try:
        conditions = []
        params = []
        if query:
            conditions.append("(content LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR article LIKE ? ESCAPE '\\')")
            params.extend([f"%{_escape_like(query)}%"] * 3)
        if category:
            conditions.append("category=?")
            params.append(category)
        if law_name:
            conditions.append("law_name=?")
            params.append(law_name)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = conn.execute(f"SELECT * FROM law_documents WHERE {where} ORDER BY law_name, article", params).fetchall()
    finally:
        conn.close()

    return jsonify({
        "count": len(rows),
        "results": [dict(r) for r in rows],
    })


@hr_law_bp.route("/api/hr-law/sources")
@require_auth
def hr_law_sources():
    """수집된 법률 소스 목록
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_hr_law_db()
    try:
        rows = conn.execute("""
            SELECT law_name, COUNT(*) as count, GROUP_CONCAT(DISTINCT category) as categories
            FROM law_documents GROUP BY law_name ORDER BY law_name
        """).fetchall()
    finally:
        conn.close()
    return jsonify({"sources": [dict(r) for r in rows]})


@hr_law_bp.route("/api/hr-law/stats")
@require_auth
def hr_law_stats():
    """HR Law 색인 통계
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_hr_law_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM law_documents").fetchone()[0]
        indexed = conn.execute("SELECT COUNT(*) FROM law_documents WHERE indexed=1").fetchone()[0]
        by_category = conn.execute("SELECT category, COUNT(*) as cnt FROM law_documents GROUP BY category ORDER BY cnt DESC").fetchall()
        by_law = conn.execute("SELECT law_name, COUNT(*) as cnt FROM law_documents GROUP BY law_name ORDER BY cnt DESC").fetchall()
        queries = conn.execute("SELECT COUNT(*) FROM hr_queries").fetchone()[0]
    finally:
        conn.close()
    return jsonify({
        "total_documents": total,
        "indexed": indexed,
        "queries_total": queries,
        "by_category": {r["category"]: r["cnt"] for r in by_category},
        "by_law": {r["law_name"]: r["cnt"] for r in by_law},
    })


@hr_law_bp.route("/api/hr-law/history")
@require_auth
def hr_law_history():
    """질의 이력
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            queries: {type: array}
    """
    conn = _get_hr_law_db()
    try:
        limit = int(request.args.get("limit", 20))
        rows = conn.execute("SELECT * FROM hr_queries ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return jsonify({"queries": [dict(r) for r in rows]})




# ---------------------------------------------------------------------------
# HR Law Multi-Tenant API — 회사별 취업규칙 SaaS
# ---------------------------------------------------------------------------

def _hash_tenant_key(key: str) -> str:
    """테넌트 키 해시 — 본 서버 API 키와 동일 방식(sha256). 키는 고엔트로피
    (secrets.token_hex(16)=128bit)라 비밀번호용 KDF는 불필요."""
    return hashlib.sha256((key or "").encode()).hexdigest()


def _ensure_tenant_key_hash(conn):
    """tenants.key_hash 컬럼 보장(멱등 마이그레이션)."""
    cols = {c[1] for c in conn.execute("PRAGMA table_info(tenants)")}
    if "key_hash" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN key_hash TEXT")
        conn.commit()


def _resolve_tenant(request):
    """요청에서 테넌트 식별 — **X-Tenant-Key 헤더 전용**.

    보안: ①키는 **해시로 저장·조회**(평문 보관 시 DB 유출=전 테넌트 키 노출).
    구 평문 행은 첫 사용 시 해시로 **지연 마이그레이션**하고 평문을 지운다.
    ②쿼리스트링(`?tenant_key=`) 수용은 제거 — URL은 액세스 로그·리퍼러·브라우저
    히스토리에 남아 키가 잔류한다.
    """
    key = request.headers.get("X-Tenant-Key", "")
    if not key:
        return None
    kh = _hash_tenant_key(key)
    conn = _get_hr_law_db()
    try:
        _ensure_tenant_key_hash(conn)
        tenant = conn.execute(
            "SELECT * FROM tenants WHERE key_hash=? AND is_active=1", (kh,)).fetchone()
        if tenant is None:
            # 레거시 평문 행 — 일치 시 해시로 전환하고 평문 제거(지연 마이그레이션)
            tenant = conn.execute(
                "SELECT * FROM tenants WHERE api_key=? AND api_key<>'' AND is_active=1",
                (key,)).fetchone()
            if tenant is not None:
                # api_key는 레거시 스키마가 NOT NULL이라 NULL 대신 빈 문자열로 비운다
                conn.execute("UPDATE tenants SET key_hash=?, api_key='' WHERE id=?",
                             (kh, tenant["id"]))
                conn.commit()
                logger.info(f"[hr-law] tenant key migrated to hash: {tenant['id']}")
                tenant = conn.execute(
                    "SELECT * FROM tenants WHERE id=?", (tenant["id"],)).fetchone()
    finally:
        conn.close()
    if tenant is None:
        return None
    d = dict(tenant)
    d.pop("api_key", None)          # 발급 후 재열람 금지
    d.pop("key_hash", None)
    return d


def _check_tenant_limit(tenant_id):
    """일일 쿼리 제한 확인"""
    from datetime import date
    conn = _get_hr_law_db()
    try:
        today = date.today().isoformat()
        row = conn.execute("SELECT query_count FROM tenant_usage WHERE tenant_id=? AND date=?",
                           (tenant_id, today)).fetchone()
        tenant = conn.execute("SELECT max_queries_day FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    finally:
        conn.close()
    current = row["query_count"] if row else 0
    limit = tenant["max_queries_day"] if tenant else 100
    return current < limit, current, limit


def _increment_tenant_usage(tenant_id):
    """일일 사용량 증가"""
    from datetime import date
    conn = _get_hr_law_db()
    try:
        today = date.today().isoformat()
        conn.execute("""
            INSERT INTO tenant_usage (tenant_id, date, query_count) VALUES (?, ?, 1)
            ON CONFLICT(tenant_id, date) DO UPDATE SET query_count = query_count + 1
        """, (tenant_id, today))
        conn.commit()
    finally:
        conn.close()


@hr_law_bp.route("/api/hr-law/tenant/register", methods=["POST"])
@require_auth
def hr_law_tenant_register():
    """회사(테넌트) 등록 → API 키 발급
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string, description: "회사명"}
            contact_email: {type: string, description: "담당자 이메일"}
            plan: {type: string, enum: [basic, standard, premium], default: basic, description: "basic=100건/일, standard=500건/일, premium=2000건/일"}
            max_queries_day: {type: integer, default: 100, description: "일일 쿼리 한도 (직접 지정)"}
    responses:
      200:
        description: "등록 성공 — API 키 발급"
        schema:
          type: object
          properties:
            tenant_id: {type: string}
            name: {type: string}
            api_key: {type: string, description: "X-Tenant-Key 헤더에 사용"}
            plan: {type: string}
            max_queries_day: {type: integer}
    """
    # 인가: 테넌트 발급은 관리 행위 — 목록·수정·삭제·키재발급과 동일하게 admin 전용.
    # (누락 시 아무 용도의 API 키 보유자가 테넌트를 자가 발급하고 쿼터를 임의 설정)
    if not is_admin_request():
        return jsonify({"error": "admin only"}), 403

    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    tenant_id = str(uuid.uuid4())[:12]
    api_key = "hr-" + secrets.token_hex(16)
    plan = data.get("plan", "basic")
    limits = {"basic": 100, "standard": 500, "premium": 2000}
    plan_cap = limits.get(plan, 100)
    # 요청 값은 plan 상한으로 클램프 — 상한 없으면 plan 체계가 무의미해진다
    try:
        max_q = min(int(data.get("max_queries_day", plan_cap)), plan_cap)
    except (TypeError, ValueError):
        max_q = plan_cap
    max_q = max(1, max_q)

    conn = _get_hr_law_db()
    try:
        _ensure_tenant_key_hash(conn)
        conn.execute("""
            INSERT INTO tenants (id, name, api_key, key_hash, contact_email, plan, max_queries_day, created_at)
            VALUES (?, ?, '', ?, ?, ?, ?, datetime('now'))
        """, (tenant_id, name, _hash_tenant_key(api_key),
              data.get("contact_email", ""), plan, max_q))
        conn.commit()
    finally:
        conn.close()

    logger.info(f"HR Tenant registered: {name} ({tenant_id})")
    return jsonify({
        "tenant_id": tenant_id,
        "name": name,
        "api_key": api_key,
        "plan": plan,
        "max_queries_day": max_q,
        "message": "이 API 키를 안전하게 보관하세요. X-Tenant-Key 헤더로 사용합니다.",
    })


@hr_law_bp.route("/api/hr-law/tenants")
@require_auth
def hr_law_tenant_list():
    """테넌트 목록 (페이지네이션 지원)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - {name: page, in: query, type: integer, default: 1}
      - {name: per_page, in: query, type: integer, default: 10}
      - {name: search, in: query, type: string, description: "회사명 검색"}
    responses:
      200:
        schema:
          type: object
    """
    if not is_admin_request():
        return jsonify({"error": "admin only"}), 403

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 10, type=int)))
    search = request.args.get("search", "").strip()
    offset = (page - 1) * per_page

    conn = _get_hr_law_db()
    try:
        where = "t.id != '__common__'"
        params = []
        if search:
            where += " AND t.name LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(search)}%")

        total = conn.execute(f"SELECT COUNT(*) FROM tenants t WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"""
            SELECT t.*,
                   (SELECT COUNT(*) FROM company_rules WHERE tenant_id=t.id) as rules_count,
                   (SELECT COUNT(*) FROM hr_queries WHERE tenant_id=t.id) as queries_count,
                   (SELECT query_count FROM tenant_usage WHERE tenant_id=t.id AND date=date('now')) as today_usage
            FROM tenants t WHERE {where} ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()
    finally:
        conn.close()
    def _safe(row):
        d = dict(row)
        d.pop("api_key", None)      # 발급 후 재열람 금지(해시만 보관)
        d.pop("key_hash", None)
        return d

    return jsonify({
        "tenants": [_safe(r) for r in rows],
        "pagination": {"page": page, "per_page": per_page, "total": total, "total_pages": max(1, (total + per_page - 1) // per_page)},
    })


@hr_law_bp.route("/api/hr-law/tenant/<tenant_id>", methods=["PUT"])
@require_auth
def hr_law_tenant_update(tenant_id):
    """테넌트 정보 수정
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: tenant_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            name: {type: string}
            plan: {type: string, enum: [basic, standard, premium]}
            max_queries_day: {type: integer}
            is_active: {type: integer, enum: [0, 1]}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    if not is_admin_request():
        return jsonify({"error": "admin only"}), 403

    data = request.json or {}
    updates = []
    params = []
    for field in ["name", "contact_email", "plan", "max_queries_day", "is_active"]:
        if field in data:
            updates.append(f"{field}=?")
            params.append(data[field])
    if not updates:
        return jsonify({"error": "no fields to update"}), 400
    params.append(tenant_id)
    conn = _get_hr_law_db()
    try:
        conn.execute(f"UPDATE tenants SET {','.join(updates)} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@hr_law_bp.route("/api/hr-law/tenant/<tenant_id>", methods=["DELETE"])
@require_auth
def hr_law_tenant_delete(tenant_id):
    """테넌트 완전 삭제 (관련 규칙/질의 이력 포함)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: tenant_id
        type: string
        required: true
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            deleted_rules: {type: integer}
            deleted_queries: {type: integer}
    """
    if not is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    conn = _get_hr_law_db()
    try:
        # 관련 데이터 삭제
        rules_del = conn.execute("DELETE FROM company_rules WHERE tenant_id=?", (tenant_id,)).rowcount
        queries_del = conn.execute("DELETE FROM hr_queries WHERE user LIKE ? ESCAPE '\\'", (f"%{_escape_like(tenant_id)}%",)).rowcount
        conn.execute("DELETE FROM tenants WHERE id=?", (tenant_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"HR Tenant deleted: {tenant_id} (rules={rules_del}, queries={queries_del})")
    return jsonify({"ok": True, "deleted_rules": rules_del, "deleted_queries": queries_del})


@hr_law_bp.route("/api/hr-law/tenant/<tenant_id>/regenerate-key", methods=["POST"])
@require_auth
def hr_law_tenant_regenerate_key(tenant_id):
    """테넌트 API 키 재발급
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: tenant_id
        type: string
        required: true
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            api_key: {type: string}
            tenant_id: {type: string}
    """
    if not is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    conn = _get_hr_law_db()
    try:
        tenant = conn.execute("SELECT id, name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        if not tenant:
            return jsonify({"error": "tenant not found"}), 404
        new_key = "hr-" + secrets.token_hex(16)
        _ensure_tenant_key_hash(conn)
        conn.execute("UPDATE tenants SET api_key='', key_hash=?, "
                     "key_regenerated_at=datetime('now') WHERE id=?",
                     (_hash_tenant_key(new_key), tenant_id))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"HR Tenant key regenerated: {tenant['name']} ({tenant_id})")
    return jsonify({"ok": True, "api_key": new_key, "tenant_id": tenant_id})



@hr_law_bp.route("/api/hr-law/tenant/<tenant_id>/stats")
@require_auth
def hr_law_tenant_stats(tenant_id):
    """테넌트 호출 통계 (일별/카테고리별/최근 질의 목록)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - {name: tenant_id, in: path, type: string, required: true}
      - {name: days, in: query, type: integer, default: 30, description: "최근 N일"}
      - {name: limit, in: query, type: integer, default: 20, description: "최근 질의 건수"}
    responses:
      200:
        schema: {type: object}
    """
    if not is_admin_request():
        return jsonify({"error": "admin only"}), 403

    days = min(90, max(1, request.args.get("days", 30, type=int)))
    limit = min(100, max(1, request.args.get("limit", 20, type=int)))
    conn = _get_hr_law_db()
    try:
        tenant = conn.execute("SELECT id, name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        if not tenant:
            return jsonify({"error": "tenant not found"}), 404

        # 일별 호출 통계
        daily = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as count
            FROM hr_queries WHERE tenant_id=?
            AND created_at >= datetime('now', ?)
            GROUP BY day ORDER BY day DESC
        """, (tenant_id, f"-{days} days")).fetchall()

        # 카테고리별 통계
        by_category = conn.execute("""
            SELECT COALESCE(category, 'general') as cat, COUNT(*) as count
            FROM hr_queries WHERE tenant_id=?
            GROUP BY cat ORDER BY count DESC
        """, (tenant_id,)).fetchall()

        # 총 통계
        total = conn.execute("SELECT COUNT(*) FROM hr_queries WHERE tenant_id=?", (tenant_id,)).fetchone()[0]

        # 최근 질의 목록
        recent = conn.execute("""
            SELECT id, question, category, created_at, user
            FROM hr_queries WHERE tenant_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (tenant_id, limit)).fetchall()
    finally:
        conn.close()
    return jsonify({
        "tenant_id": tenant_id,
        "tenant_name": tenant["name"],
        "total_queries": total,
        "daily": [{"date": r["day"], "count": r["count"]} for r in daily],
        "by_category": [{"category": r["cat"], "count": r["count"]} for r in by_category],
        "recent_queries": [{"id": r["id"], "question": r["question"][:100], "category": r["category"], "created_at": r["created_at"], "user": r["user"]} for r in recent],
    })


@hr_law_bp.route("/api/hr-law/rules", methods=["POST"])
@require_auth
def hr_law_rules_add():
    """회사 취업규칙 등록 (X-Tenant-Key 필수)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
        description: "회사 API 키"
      - in: body
        name: body
        schema:
          type: object
          required: [title, content]
          properties:
            title: {type: string, description: "규칙 제목 (예: 연차휴가 규정)"}
            content: {type: string, description: "규칙 전문"}
            category: {type: string, description: "카테고리 (annual_leave, overtime_pay, dismissal 등)"}
            effective_date: {type: string, description: "시행일 (YYYY-MM-DD)"}
    responses:
      200:
        description: "등록 성공"
        schema:
          type: object
          properties:
            rule_id: {type: string}
            tenant_id: {type: string}
            indexed: {type: boolean}
      401:
        description: "X-Tenant-Key 누락 또는 유효하지 않음"
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    data = request.json or {}
    title = data.get("title", "").strip()
    content_text = data.get("content", "").strip()
    if not title or not content_text:
        return jsonify({"error": "title and content required"}), 400

    rule_id = str(uuid.uuid4())
    conn = _get_hr_law_db()
    try:
        conn.execute("""
            INSERT INTO company_rules (id, tenant_id, category, title, content, effective_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (rule_id, tenant["id"], data.get("category", "general"), title, content_text,
              data.get("effective_date", "")))
        conn.commit()

        # RAG 색인 (테넌트 구분 메타데이터 포함)
        rag_id = f"rule_{tenant['id']}_{rule_id[:8]}"
        rag_text = f"[{tenant['name']} 취업규칙] {title}\n\n{content_text}"
        try:
            r = requests.post(f"{_get_self_base()}/api/rag/index",
                              json={"id": rag_id, "text": rag_text,
                                    "metadata": {"tenant_id": tenant["id"], "type": "company_rule",
                                                 "title": title, "category": data.get("category", "general")}},
                              timeout=30)
            if r.status_code == 200:
                conn.execute("UPDATE company_rules SET indexed=1 WHERE id=?", (rule_id,))
                conn.commit()
        except Exception as e:
            logger.warning("HR Law rule RAG indexing failed: %s", e)
    finally:
        conn.close()
    return jsonify({"rule_id": rule_id, "tenant_id": tenant["id"], "indexed": True})


@hr_law_bp.route("/api/hr-law/rules")
@require_auth
def hr_law_rules_list():
    """회사 취업규칙 목록 (X-Tenant-Key 필수)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
    responses:
      200:
        schema:
          type: object
          properties:
            tenant: {type: string}
            rules: {type: array, items: {type: object}}
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    conn = _get_hr_law_db()
    try:
        rows = conn.execute("SELECT * FROM company_rules WHERE tenant_id=? ORDER BY created_at DESC",
                            (tenant["id"],)).fetchall()
    finally:
        conn.close()
    return jsonify({"tenant": tenant["name"], "rules": [dict(r) for r in rows]})


@hr_law_bp.route("/api/hr-law/rules/<rule_id>", methods=["DELETE"])
@require_auth
def hr_law_rules_delete(rule_id):
    """취업규칙 삭제 (X-Tenant-Key 필수)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
      - in: path
        name: rule_id
        type: string
        required: true
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    conn = _get_hr_law_db()
    try:
        conn.execute("DELETE FROM company_rules WHERE id=? AND tenant_id=?", (rule_id, tenant["id"]))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})





@hr_law_bp.route("/api/hr-law/rules/template")
@require_auth
def hr_law_rules_template():
    """취업규칙 표준 템플릿 (고용노동부 표준 기반)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    responses:
      200:
        description: "표준 취업규칙 항목 구조"
        schema:
          type: object
    """
    return jsonify(RULE_TEMPLATE)


@hr_law_bp.route("/api/hr-law/rules/completeness")
@require_auth
def hr_law_rules_completeness():
    """회사 취업규칙 완성도 체크 (X-Tenant-Key 필수)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
    responses:
      200:
        description: "필수 항목별 등록 여부 + 완성도 %"
        schema:
          type: object
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    conn = _get_hr_law_db()
    try:
        rules = conn.execute("SELECT category, title, content FROM company_rules WHERE tenant_id=?",
                             (tenant["id"],)).fetchall()
    finally:
        conn.close()

    # 카테고리별 등록 현황
    registered_cats = set()
    registered_articles = {}
    for r in rules:
        cat = r["category"] or "general"
        registered_cats.add(cat)
        if cat not in registered_articles:
            registered_articles[cat] = []
        registered_articles[cat].append({"title": r["title"], "content_length": len(r["content"])})

    # 완성도 계산
    results = []
    total_required = 0
    total_completed = 0
    for section in RULE_TEMPLATE["sections"]:
        sec_id = section["id"]
        required_articles = [a for a in section["articles"] if a["required"]]
        total_articles = len(section["articles"])

        # 해당 섹션의 등록 현황
        sec_rules = registered_articles.get(sec_id, [])
        has_content = len(sec_rules) > 0

        # 필수 항목 중 등록된 것
        completed = min(len(sec_rules), len(required_articles)) if sec_rules else 0
        required_count = len(required_articles)

        if section["required"]:
            total_required += required_count
            total_completed += completed

        results.append({
            "section_id": sec_id,
            "section_title": section["title"],
            "required": section["required"],
            "legal_basis": section.get("legal_basis", ""),
            "total_articles": total_articles,
            "required_articles": required_count,
            "registered": len(sec_rules),
            "status": "완료" if completed >= required_count and has_content else ("일부" if has_content else "미등록"),
            "articles": [
                {
                    "id": a["id"],
                    "title": a["title"],
                    "required": a["required"],
                    "hint": a.get("hint", ""),
                    "legal": a.get("legal", ""),
                    "registered": any(a["title"] in r["title"] for r in sec_rules),
                }
                for a in section["articles"]
            ],
        })

    completeness = round(total_completed / total_required * 100, 1) if total_required > 0 else 0

    return jsonify({
        "tenant": tenant["name"],
        "completeness_pct": completeness,
        "total_required": total_required,
        "total_completed": total_completed,
        "registered_rules": len(rules),
        "sections": results,
        "missing_required": [
            {"section": s["section_title"], "articles": [a["title"] for a in s["articles"] if a["required"] and not a["registered"]]}
            for s in results if s["required"] and s["status"] != "완료"
        ],
    })


@hr_law_bp.route("/api/hr-law/rules/generate", methods=["POST"])
@require_auth
def hr_law_rules_generate():
    """취업규칙 초안 자동 생성 (LLM) — X-Tenant-Key 필수
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [section_id]
          properties:
            section_id: {type: string, description: "생성할 섹션 ID (work_hours, leave, wage 등)"}
            company_info: {type: string, description: "회사 정보 (업종, 규모 등, 선택)"}
    responses:
      200:
        description: "LLM이 생성한 취업규칙 초안"
        schema:
          type: object
          properties:
            tenant: {}
            section_id: {}
            section_title: {}
            draft: {}
            articles: {type: array}
            note: {type: string}
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    data = request.json or {}
    section_id = data.get("section_id", "")
    company_info = data.get("company_info", "")

    # 섹션 찾기
    section = None
    for s in RULE_TEMPLATE["sections"]:
        if s["id"] == section_id:
            section = s
            break
    if not section:
        return jsonify({"error": f"unknown section_id: {section_id}", "available": [s["id"] for s in RULE_TEMPLATE["sections"]]}), 400

    # LLM으로 초안 생성
    articles_desc = "\n".join([f"- {a['title']}: {a.get('hint','')} {'(필수)' if a['required'] else '(선택)'}" for a in section["articles"]])
    legal = section.get("legal_basis", "")

    prompt = f"""다음 취업규칙 섹션의 초안을 작성해주세요.

회사: {tenant['name']}
{f'회사 정보: {company_info}' if company_info else ''}

섹션: {section['title']}
{f'관련 법률: {legal}' if legal else ''}

포함해야 할 항목:
{articles_desc}

작성 규칙:
1. 반드시 한국어로 작성
2. 조문 형식 (제X조, 항, 호)으로 작성
3. 근로기준법 등 관련 법률의 최소 기준 이상으로 작성
4. 실무에서 바로 사용 가능한 수준으로 작성
5. 5인 미만 사업장 해당 시 별도 언급
"""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
        from core import model_router
        res = model_router.generate(
            "hr_law.rules_draft", prompt, model=model,
            system="당신은 한국 노무사입니다. 반드시 한국어로만 답변하세요. 취업규칙 초안을 조문 형식으로 작성합니다.",
            options={"temperature": 0.5, "num_predict": 3000}, timeout=120,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        if not res.ok:
            # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(500) — 종전엔 빈 초안이 200으로 나갔다
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        draft = res.text
    except Exception as e:
        return jsonify({"error": "LLM 처리 중 오류"}), 500

    return jsonify({
        "tenant": tenant["name"],
        "section_id": section_id,
        "section_title": section["title"],
        "draft": draft,
        "articles": [a["title"] for a in section["articles"]],
        "note": "이 초안은 AI가 생성한 것으로, 반드시 노무사 검토 후 사용하세요.",
    })



@hr_law_bp.route("/api/hr-law/rules/review", methods=["POST"])
@require_auth
def hr_law_rules_review():
    """\ucde8\uc5c5\uaddc\uce59 \ubc95 \uc704\ubc18 \uac80\ud1a0 (LLM \ubd84\uc11d) \u2014 X-Tenant-Key \ud544\uc218
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            rule_ids:
              type: array
              items: {type: string}
              description: "\uac80\ud1a0\ud560 \uaddc\uce59 ID (\ube44\uba74 \uc804\uccb4)"
            text:
              type: string
              description: "\uc9c1\uc811 \uc785\ub825\ud55c \ucde8\uc5c5\uaddc\uce59 \ud14d\uc2a4\ud2b8"
    responses:
      200:
        description: "\ubc95\uc704\ubc18 \uac80\ud1a0 \uacb0\uacfc"
        schema:
          type: object
          properties:
            violations: {type: integer}
            warnings: {type: integer}
            compliant: {type: integer}
            review: {type: string}
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        return jsonify({"error": "X-Tenant-Key header required"}), 401

    data = request.json or {}
    rule_ids = data.get("rule_ids", [])
    direct_text = data.get("text", "").strip()

    rules_text = ""
    if direct_text:
        rules_text = direct_text
    else:
        conn = _get_hr_law_db()
        try:
            if rule_ids:
                placeholders = ",".join(["?"] * len(rule_ids))
                rows = conn.execute(
                    f"SELECT title, content FROM company_rules WHERE tenant_id=? AND id IN ({placeholders})",
                    [tenant["id"]] + rule_ids).fetchall()
            else:
                rows = conn.execute(
                    "SELECT title, content FROM company_rules WHERE tenant_id=? ORDER BY created_at",
                    (tenant["id"],)).fetchall()
        finally:
            conn.close()
        if not rows:
            return jsonify({"error": "\ub4f1\ub85d\ub41c \ucde8\uc5c5\uaddc\uce59\uc774 \uc5c6\uc2b5\ub2c8\ub2e4."}), 400
        rules_text = "\n\n".join([f"### {r['title']}\n{r['content']}" for r in rows])

    try:
        rag_r = requests.post(f"{_get_self_base()}/api/rag/search",
                              json={"query": rules_text[:500], "top_k": 10}, timeout=10)
        rag_results = rag_r.json().get("results", []) if rag_r.status_code == 200 else []
    except Exception as e:
        logger.warning("HR Law review RAG search failed: %s", e)
        rag_results = []

    context = "\n\n".join([f"[{r.get('metadata',{}).get('law','')} {r.get('metadata',{}).get('article','')}] {r.get('text','')[:300]}" for r in rag_results])

    prompt = HR_LAW_REVIEW_PROMPT.replace("{context}", context or "\uad00\ub828 \ubc95\ub839 \uc790\ub8cc \uc5c6\uc74c").replace("{rules}", rules_text)
    model = _load_config().get("default_model", "qwen2.5:14b")

    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
        from core import model_router
        res = model_router.generate(
            "hr_law.rules_review", prompt, model=model,
            options={"temperature": 0.3, "num_predict": 3000}, timeout=180,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        if not res.ok:
            # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(500) — 종전엔 "검토 실패"가 200으로 나갔다
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        review = res.text
        tokens = res.tokens
    except Exception as e:
        return jsonify({"error": "LLM 처리 중 오류"}), 500

    violations = review.count("\u274c") + review.count("\uc704\ubc18")
    warnings = review.count("\u26a0") + review.count("\uc8fc\uc758")
    compliant = review.count("\u2705") + review.count("\uc801\ud569")

    try:

        conn = _get_hr_law_db()
        try:
            conn.execute("INSERT INTO hr_queries (id, question, answer, sources, category, created_at, user, tenant_id) VALUES (?,?,?,?,?,datetime('now'),?,?)",
                         (str(uuid.uuid4()), f"[\uaddc\uce59 \uac80\ud1a0] {tenant['name']}", review, "[]", "review",
                          getattr(request, 'act_key_name', 'unknown'), tenant["id"]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("HR Law review history save failed: %s", e)

    return jsonify({
        "tenant": tenant["name"],
        "violations": violations,
        "warnings": warnings,
        "compliant": compliant,
        "total_items": violations + warnings + compliant,
        "review": review,
        "tokens": tokens,
    })

@hr_law_bp.route("/api/hr-law/tenant/usage")
@require_auth
def hr_law_tenant_usage():
    """사용량 조회 (관리자: 전체, 회사: X-Tenant-Key로 자사)
    ---
    tags: [HR Law Tenant]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Tenant-Key
        type: string
        required: false
        description: "회사 API 키 (없으면 관리자 전체 조회)"
    responses:
      200:
        schema:
          type: object
          properties:
            today: {type: integer}
            limit: {type: integer}
            remaining: {type: integer}
    """
    tenant = _resolve_tenant(request)
    if not tenant:
        # 전체 사용량(테넌트 교차 조회)은 **관리자 전용**.
        # 과거엔 테넌트 해석 실패 시 무조건 이 분기로 떨어져, 잘못된 키를 보낸 호출자가
        # 오히려 전 테넌트 사용량을 받았다(인증 실패가 더 큰 권한이 되던 fail-open).
        if not is_admin_request():
            return jsonify({"error": "X-Tenant-Key header required"}), 401
        conn = _get_hr_law_db()
        try:
            rows = conn.execute("""
                SELECT u.tenant_id, t.name, u.date, u.query_count, t.max_queries_day
                FROM tenant_usage u JOIN tenants t ON u.tenant_id = t.id
                ORDER BY u.date DESC, u.query_count DESC LIMIT 100
            """).fetchall()
        finally:
            conn.close()
        return jsonify({"usage": [dict(r) for r in rows]})

    conn = _get_hr_law_db()
    try:
        rows = conn.execute("SELECT * FROM tenant_usage WHERE tenant_id=? ORDER BY date DESC LIMIT 30",
                            (tenant["id"],)).fetchall()
        allowed, current, limit = _check_tenant_limit(tenant["id"])
    finally:
        conn.close()
    return jsonify({
        "tenant": tenant["name"],
        "today": current,
        "limit": limit,
        "remaining": limit - current,
        "history": [dict(r) for r in rows],
    })


@hr_law_bp.route("/api/hr-law/calculate/annual-leave", methods=["POST"])
@require_auth
def hr_law_calc_annual_leave():
    """연차 유급휴가 계산
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [start_date]
          properties:
            start_date: {type: string, description: "입사일 (YYYY-MM-DD)", example: "2023-03-15"}
            reference_date: {type: string, description: "기준일 (기본: 오늘)"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    start_str = data.get("start_date", "")
    ref_str = data.get("reference_date", "")
    if not start_str:
        return jsonify({"error": "start_date required (YYYY-MM-DD)"}), 400
    try:
        from datetime import date
        start = date.fromisoformat(start_str)
        ref = date.fromisoformat(ref_str) if ref_str else date.today()
    except (ValueError, TypeError):
        return jsonify({"error": "invalid date format"}), 400

    days_worked = (ref - start).days
    years = days_worked / 365.25
    months = days_worked / 30.44

    result = {"start_date": str(start), "reference_date": str(ref), "days_worked": days_worked}

    if days_worked < 365:
        # 1년 미만: 1개월 개근 시 1일
        full_months = int(months)
        result["type"] = "1년 미만 (월차)"
        result["total_leave"] = min(full_months, 11)
        result["legal_basis"] = "근로기준법 제60조 제2항"
        result["note"] = f"입사 후 {full_months}개월 경과 → 최대 {result['total_leave']}일 발생 (1개월 개근 시 1일)"
    else:
        # 1년 이상: 15일 + 2년마다 1일 가산 (최대 25일)
        full_years = int(years)
        base = 15
        bonus = max(0, (full_years - 1) // 2)  # 3년차부터 2년마다 1일
        total = min(base + bonus, 25)
        result["type"] = "1년 이상"
        result["years_worked"] = full_years
        result["base_leave"] = base
        result["bonus_leave"] = bonus
        result["total_leave"] = total
        result["legal_basis"] = "근로기준법 제60조 제1항, 제4항"
        result["note"] = f"근속 {full_years}년 → 기본 15일 + 가산 {bonus}일 = {total}일"

    return jsonify(result)


@hr_law_bp.route("/api/hr-law/calculate/overtime", methods=["POST"])
@require_auth
def hr_law_calc_overtime():
    """초과근무수당 계산
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [hourly_wage]
          properties:
            hourly_wage: {type: number, description: "시급 (원)"}
            extended_hours: {type: number, description: "연장근로 시간", default: 0}
            night_hours: {type: number, description: "야간근로 시간 (22시~06시)", default: 0}
            holiday_hours: {type: number, description: "휴일근로 시간", default: 0}
            holiday_over8: {type: number, description: "휴일 8시간 초과분", default: 0}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "hourly_wage required (> 0)"}}
    """
    data = request.json or {}
    wage = float(data.get("hourly_wage", 0))
    if wage <= 0:
        return jsonify({"error": "hourly_wage required (> 0)"}), 400

    ext = float(data.get("extended_hours", 0))
    night = float(data.get("night_hours", 0))
    holiday = float(data.get("holiday_hours", 0))
    holiday_o8 = float(data.get("holiday_over8", 0))

    extended_pay = int(wage * 1.5 * ext)        # 통상 + 50%
    night_pay = int(wage * 0.5 * night)          # 추가 50% (기본급은 별도)
    holiday_pay = int(wage * 1.5 * holiday)      # 통상 + 50%
    holiday_o8_pay = int(wage * 2.0 * holiday_o8)  # 통상 + 100%

    total = extended_pay + night_pay + holiday_pay + holiday_o8_pay

    return jsonify({
        "hourly_wage": wage,
        "breakdown": {
            "extended": {"hours": ext, "rate": "150%", "amount": extended_pay},
            "night": {"hours": night, "rate": "+50%", "amount": night_pay},
            "holiday_8h": {"hours": holiday, "rate": "150%", "amount": holiday_pay},
            "holiday_over8": {"hours": holiday_o8, "rate": "200%", "amount": holiday_o8_pay},
        },
        "total_overtime_pay": total,
        "legal_basis": "근로기준법 제56조",
    })


@hr_law_bp.route("/api/hr-law/calculate/severance", methods=["POST"])
@require_auth
def hr_law_calc_severance():
    """퇴직금 계산
    ---
    tags: [HR Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [start_date, end_date, avg_daily_wage]
          properties:
            start_date: {type: string, description: "입사일"}
            end_date: {type: string, description: "퇴직일"}
            avg_daily_wage: {type: number, description: "평균임금 (일급, 원)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "start_date, end_date (YYYY-MM-DD), avg_daily_wage required"}}
    """
    data = request.json or {}
    try:
        from datetime import date
        start = date.fromisoformat(data["start_date"])
        end = date.fromisoformat(data["end_date"])
        avg_wage = float(data["avg_daily_wage"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "start_date, end_date (YYYY-MM-DD), avg_daily_wage required"}), 400

    days = (end - start).days
    if days < 365:
        return jsonify({"error": "계속근로기간 1년 미만은 퇴직금 지급 의무 없음", "legal_basis": "근로자퇴직급여보장법 제4조 제1항"})

    # 퇴직금 = 평균임금 × 30일 × (총 근로일수 / 365)
    severance = int(avg_wage * 30 * (days / 365))

    return jsonify({
        "start_date": str(start),
        "end_date": str(end),
        "working_days": days,
        "years": round(days / 365, 1),
        "avg_daily_wage": avg_wage,
        "severance_pay": severance,
        "formula": "평균임금 × 30일 × (근속일수 / 365)",
        "legal_basis": "근로자퇴직급여보장법 제8조 제1항",
    })

