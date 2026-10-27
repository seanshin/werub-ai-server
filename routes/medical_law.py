"""Medical Law AI Blueprint — 의료법 AI 어시스턴트 (v2.53.0)
Phase 2: 자동 검사 + 추론체인 연동 + 인폼드컨센트/광고/보관/과태료 가이드"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time

import requests as http_requests
from flask import Blueprint, request, jsonify, Response
from core.cache import TTLCache
from routes.cache_ctx import RequestAwareTTLCache
from core.config import load_config
from core.korean import force_korean, korean_quality_check
from core.korean_humanize import compute_korean_quality, get_quality_summary

logger = logging.getLogger("ollama-admin")

medical_law_bp = Blueprint("medical_law", __name__)

_ollama_session = None
_OLLAMA_BASE = None
_BASE_DIR = None

# 의료법 질의 캐시 (TTL 1시간)
_medical_law_cache = RequestAwareTTLCache(maxsize=300, ttl=3600)
_db_local = threading.local()


def init_medical_law(*, ollama_session, ollama_base, base_dir):
    global _ollama_session, _OLLAMA_BASE, _BASE_DIR
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    conn = _get_db()
    _init_schema(conn)
    _seed_data(conn)
    logger.info("Medical Law AI initialized")


def _get_db():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        db_path = os.path.join(_BASE_DIR, "medical_law.db")
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _db_local.conn = conn
    return conn


def _init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS law_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_name TEXT NOT NULL,
            article_number TEXT NOT NULL,
            article_title TEXT,
            content TEXT NOT NULL,
            category TEXT,
            penalty TEXT,
            effective_date TEXT,
            law_version TEXT DEFAULT 'current',
            embedding_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_law_cat ON law_articles(category);
        CREATE INDEX IF NOT EXISTS idx_law_name ON law_articles(law_name);

        CREATE TABLE IF NOT EXISTS reportable_diseases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disease_name TEXT NOT NULL,
            disease_name_en TEXT,
            icd_codes TEXT,
            class TEXT,
            report_deadline TEXT,
            report_to TEXT DEFAULT '관할 보건소장',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_disease_icd ON reportable_diseases(icd_codes);

        CREATE TABLE IF NOT EXISTS law_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT,
            category TEXT,
            model TEXT,
            tokens INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 초기 데이터 시드
# ---------------------------------------------------------------------------

def _seed_data(conn):
    """핵심 의료법 조문 + 감염병 데이터 초기 입력 (이미 있으면 스킵)"""
    existing = conn.execute("SELECT COUNT(*) FROM law_articles").fetchone()[0]
    if existing > 0:
        return

    articles = [
        # 의료법
        ("의료법", "제2조", "의료인", "의료인이란 보건복지부장관의 면허를 받은 의사, 치과의사, 한의사, 조산사 및 간호사를 말한다.", "general", None),
        ("의료법", "제12조", "의료기술 등에 대한 보호", "의료인이 하는 의료·조산·간호 등 의료기술의 시행에 대하여는 이 법이나 다른 법령에 따로 규정된 경우 외에는 누구든지 간섭하지 못한다.", "general", None),
        ("의료법", "제15조", "진료거부 금지", "의료인 또는 의료기관 개설자는 진료나 조산 요청을 받으면 정당한 사유 없이 거부하지 못한다.", "general", "1년 이하 징역 또는 1천만원 이하 벌금"),
        ("의료법", "제17조", "진단서 등", "의료업에 종사하고 직접 진찰하거나 검안한 의사, 치과의사, 한의사가 아니면 진단서·검안서·증명서를 작성하여 환자에게 교부하지 못한다.", "record", "3년 이하 징역 또는 1천만원 이하 벌금"),
        ("의료법", "제19조", "정보 누설 금지", "의료인이나 의료기관 종사자는 이 법이나 다른 법령에 특별히 규정된 경우 외에는 의료·조산 또는 간호업무나 제17조에 따른 진단서·검안서·증명서 작성·교부 업무, 제18조에 따른 처방전 작성·교부 업무, 제21조에 따른 진료기록부등의 기록·보존 업무 및 제22조에 따른 전자의무기록의 기록·보존 업무를 하면서 알게 된 다른 사람의 정보를 누설하거나 발표하지 못한다.", "privacy", "3년 이하 징역 또는 1천만원 이하 벌금"),
        ("의료법", "제21조", "기록 열람 등", "환자는 의료인, 의료기관의 장 및 의료기관 종사자에게 본인에 관한 기록의 전부 또는 일부에 대하여 열람 또는 그 사본의 발급 등 내용의 확인을 요청할 수 있다.", "record", None),
        ("의료법", "제22조", "진료기록부 등", "의료인은 각각 진료기록부, 조산기록부, 간호기록부, 그 밖의 진료에 관한 기록을 갖추어 두고 그 의료행위에 관한 사항과 의견을 상세히 기록하고 서명하여야 한다.", "record", "500만원 이하 벌금"),
        ("의료법", "제22조의2", "전자의무기록", "의료인이나 의료기관 개설자는 제22조의 규정에도 불구하고 진료기록부등을 전자의무기록으로 작성·보관할 수 있다. 이 경우 보건복지부령으로 정하는 바에 따라 전자서명을 기록하여야 한다.", "record", None),
        ("의료법", "제24조", "요양방법 지도", "의료인은 환자나 환자의 보호자에게 요양방법, 그 밖에 건강관리에 필요한 사항을 지도하여야 한다.", "consent", None),
        ("의료법", "제24조의2", "의료행위에 관한 설명", "의사·치과의사 또는 한의사는 사람의 생명 또는 신체에 중대한 위해를 발생하게 할 우려가 있는 수술, 수혈, 전신마취를 하는 경우 환자에게 설명하고 서면으로 그 동의를 받아야 한다.", "consent", "300만원 이하 과태료"),
        ("의료법", "제33조", "개설", "의료인은 이 법에 따른 의료기관을 개설하지 아니하고는 의료업을 할 수 없다.", "general", "5년 이하 징역 또는 5천만원 이하 벌금"),
        ("의료법", "제56조", "시정명령 등", "보건복지부장관 또는 시·도지사는 의료기관이 이 법을 위반한 때에는 시정을 명할 수 있다.", "general", None),
        # 응급의료법
        ("응급의료에 관한 법률", "제6조", "응급의료 거부 금지", "응급의료종사자는 업무 중에 응급의료를 요청받거나 응급환자를 발견하면 즉시 응급의료를 하여야 하며, 정당한 사유 없이 이를 거부하거나 기피하지 못한다.", "emergency", "3년 이하 징역 또는 3천만원 이하 벌금"),
        ("응급의료에 관한 법률", "제9조", "응급의료의 설명·동의", "응급의료종사자는 응급환자에게 응급의료를 하기 전에 그 환자 또는 법정대리인에게 응급의료에 관하여 설명하고 동의를 받아야 한다.", "consent", None),
        ("응급의료에 관한 법률", "제10조", "응급의료 중단 금지", "응급의료종사자는 정당한 사유 없이 응급환자에 대한 응급의료를 중단하여서는 아니 된다.", "emergency", "3년 이하 징역 또는 3천만원 이하 벌금"),
        # 감염병예방법
        ("감염병의 예방 및 관리에 관한 법률", "제11조", "의사 등의 신고", "감염병 환자등을 진단하거나 그 사체를 검안한 의사, 치과의사 또는 한의사는 소속 의료기관의 장에게 보고하여야 하고, 해당 환자와 그 동거인에게 보건복지부장관이 정하는 감염 방지 방법 등을 지도하여야 한다.", "infection", "500만원 이하 벌금"),
        ("감염병의 예방 및 관리에 관한 법률", "제12조", "그 밖의 신고의무자", "다음 각 호의 어느 하나에 해당하는 사람은 제1급감염병부터 제4급감염병까지에 해당하는 감염병 중 보건복지부령으로 정하는 감염병이 발생한 경우에는 의사, 치과의사 또는 한의사의 진단이나 검안을 요구하거나 해당 주소지를 관할하는 보건소장에게 신고하여야 한다.", "infection", None),
        # 약사법
        ("약사법", "제23조", "의약품 조제", "약사 및 한약사가 아니면 의약품을 조제할 수 없으며, 약사 및 한약사는 각각 면허 범위에서 의약품을 조제하여야 한다.", "pharmacy", "5년 이하 징역 또는 5천만원 이하 벌금"),
        ("약사법", "제26조", "처방전에 의한 조제", "약사는 의사 또는 치과의사의 처방전 없이 의약품을 조제하여서는 아니 된다. 다만, 의사 또는 치과의사의 처방전이 없이도 조제할 수 있는 의약품은 보건복지부령으로 정한다.", "pharmacy", None),
        # 환자안전법
        ("환자안전법", "제4조", "환자안전사고의 보고 등", "보건의료인은 보건의료기관에서 환자안전사고가 발생하였거나 발생할 우려가 있는 경우에는 해당 보건의료기관의 장에게 보고하여야 한다.", "general", None),
    ]

    for law, article, title, content, cat, penalty in articles:
        conn.execute(
            "INSERT INTO law_articles (law_name, article_number, article_title, content, category, penalty) VALUES (?,?,?,?,?,?)",
            (law, article, title, content, cat, penalty))

    # 감염병 신고 대상
    diseases = [
        ("콜레라", "Cholera", "A00", "제2급", "24시간 이내"),
        ("장티푸스", "Typhoid fever", "A01.0", "제2급", "24시간 이내"),
        ("결핵", "Tuberculosis", "A15,A16,A17,A18,A19", "제2급", "24시간 이내"),
        ("홍역", "Measles", "B05", "제2급", "24시간 이내"),
        ("코로나19", "COVID-19", "U07.1,U07.2", "제2급", "24시간 이내"),
        ("에볼라바이러스병", "Ebola", "A98.4", "제1급", "즉시"),
        ("페스트", "Plague", "A20", "제1급", "즉시"),
        ("탄저", "Anthrax", "A22", "제1급", "즉시"),
        ("두창", "Smallpox", "B03", "제1급", "즉시"),
        ("보툴리눔독소증", "Botulism", "A05.1", "제1급", "즉시"),
        ("인플루엔자", "Influenza", "J09,J10,J11", "제4급", "7일 이내"),
        ("수두", "Varicella", "B01", "제2급", "24시간 이내"),
        ("백일해", "Pertussis", "A37", "제2급", "24시간 이내"),
        ("파상풍", "Tetanus", "A33,A34,A35", "제2급", "24시간 이내"),
        ("A형간염", "Hepatitis A", "B15", "제2급", "24시간 이내"),
        ("B형간염", "Hepatitis B", "B16,B18.1", "제2급", "24시간 이내"),
        ("HIV/AIDS", "HIV", "B20,B21,B22,B23,B24", "제3급", "24시간 이내"),
        ("매독", "Syphilis", "A50,A51,A52,A53", "제3급", "24시간 이내"),
        ("말라리아", "Malaria", "B50,B51,B52,B53,B54", "제3급", "24시간 이내"),
        ("뎅기열", "Dengue", "A90,A91", "제3급", "24시간 이내"),
    ]
    for name, name_en, icd, cls, deadline in diseases:
        conn.execute(
            "INSERT INTO reportable_diseases (disease_name, disease_name_en, icd_codes, class, report_deadline) VALUES (?,?,?,?,?)",
            (name, name_en, icd, cls, deadline))

    conn.commit()
    logger.info(f"Medical Law: seeded {len(articles)} articles + {len(diseases)} reportable diseases")


# ---------------------------------------------------------------------------
# LLM 생성
# ---------------------------------------------------------------------------

MEDICAL_LAW_SYSTEM = """당신은 한국 의료법 전문 AI입니다. 다음 규칙을 따르세요:
1. 관련 법률 조문을 정확히 인용하세요 (법률명 + 조문 번호)
2. 근거에 벌칙 조문이 있으면 반드시 포함하세요 — 근거에 벌칙이 없으면 형량·벌금액을 기억으로 창작하지 말고 "벌칙은 근거에서 확인되지 않습니다"로 표기하거나 생략하세요
3. 근거에 열거된 항목별 수치(보존기간·금액·조문 번호 등)는 절대 재그룹·병합하지 말고, 각 항목을 근거 원문의 수치 그대로 항목별로 옮기세요 (예: 항목마다 기간이 다르면 각각 별도로 표기)
4. 조문의 괄호 안 한정 조건과 단서("~에 한정한다", "다만 ~")는 생략·완화하지 말고 그대로 옮기세요. 조문 본문의 수식어와 기한("반드시", "즉시", "기간이 경과하기 전에" 등)도 생략·변경하지 마세요 — "반드시 효과가 있다고 표현하는 광고 금지"를 "치료 효과 광고 금지"로 넓히면 안 됩니다. 선택 규정("~하려는 경우 ~을 받아야 한다")을 의무("~해야 한다")로 바꾸지 말고, 선택하지 않을 때의 의무(예: 즉시 이관)가 근거에 있으면 그것을 함께 서술하세요 — 특히 대상자 목록을 나열할 때 일부 대상자에게만 붙는 괄호 조건을 빼면 안 됩니다 (예: "형제·자매(…이 모두 없는 경우에 한정한다)"는 나열 시 괄호 조건까지 함께 표기). 단서의 결합 조건("~인 경우로서 ~한 경우")은 결합 그대로 옮기고 "또는"으로 분리하지 마세요. 각 호를 나열·요약할 때 호 본문의 공통 요건(동의서·증명서류 첨부 등)을 생략한 채 "동의 없이도 가능" 같은 반대 방향 총괄 서술을 만들지 마세요 — "제N항에도 불구하고 허용"은 "요건 없이 허용"이 아닙니다. 질문이 거부·금지·불가 여부를 묻더라도 조문의 규범 방향은 그대로 옮기세요 — "~하여야 한다"(의무·허용)를 "거부할 수 있는 사유"로 뒤집어 서술하지 말고, 허용 요건 조문은 "요건을 충족하면 허용하여야 하고, 요건을 갖추지 못한 경우에만 거부할 수 있다"처럼 방향을 보존해 답하세요. 조문마다 적용 대상(환자 본인·친족·대리인 등)이 다르면 대상별 규정을 구분해 서술하고, 한 대상에 대한 규정(예: 본인 요청은 정당한 사유 없이 거부 금지)을 다른 대상(요건 미충족 친족 등)에게 확장 적용하지 마세요 — 본인 아닌 자의 요청은 원칙 금지이고 각 호 요건 충족 시에만 허용입니다
5. 특정 호(제N호)를 인용할 때는 근거 원문에서 그 호의 내용만 사용하세요 — 호 번호에 다른 호의 내용이나 근거에 없는 내용을 붙이지 마세요. 근거에서 해당 호의 내용을 확인할 수 없으면 호 번호 인용을 생략하세요. 여러 호를 열거할 때도 같습니다 — 근거 원문에 실제로 보이는 호만 그 번호와 내용 그대로 나열하고, 호를 짧게 요약하더라도 그 호의 전제 조건(매체·형태·행위 주체·"다만 ~ 제외" 단서)은 자르지 말고 함께 표기하며, 근거에서 잘렸거나 보이지 않는 호는 기억으로 채워 넣지 말고 "(이하 호는 근거에서 확인되지 않음)"으로 표기하세요 (다른 법률·조문의 내용으로 대체 금지)
6. 근거에 없는 파생 사항(후속 절차·권한·책임·가능한 행위 등)을 추론해 덧붙이지 마세요 — 근거가 규정하는 범위까지만 서술하고, 그 밖은 생략하거나 "근거에서 확인되지 않습니다"로 표기하세요 (예: 조문이 '수령할 수 있다'까지만 규정하면 수령 이후의 행위를 창작하지 않기)
7. 발간물(법령집·편람·작성요령 등) 근거 라벨의 문서 제목은 법령명이 아닙니다 — 발간물에 수록된 조문을 인용할 때 그 조문의 소속 법령명이 근거 본문에 명시된 경우에만 법령명을 붙이고, 명시가 없으면 법령명을 추정·축약하지 말고 "(발간물 수록 조문)"으로 인용하세요 (예: 「국민건강보험법령집」은 문서 제목이며, 거기 수록된 조문이 국민건강보험법 조문이라는 뜻이 아닙니다)
8. 벌칙을 인용할 때는 그 벌칙 조문이 나열하는 위반 조항의 조·항·호 번호가 질의 행위의 근거 조항과 정확히 일치하는지 확인하세요 — 같은 조라도 항이 다르면 다른 행위이고 벌칙도 다릅니다 (예: 제27조제1항 위반과 제27조제2항 위반은 별개). 근거의 벌칙 목록에서 정확히 일치하는 조·항을 확인할 수 없으면 그 벌칙을 인용하지 말고 "근거에서 확인되지 않습니다"로 표기하세요. 벌칙 목록의 위반 조항 번호가 일치해도 그 번호에 괄호 한정("제N조제M항에서 준용하는 경우만을 말한다" 등)이 붙어 있으면 질의 행위가 그 한정에 해당할 때만 그 벌칙을 적용하고, 결론·요약 문단에서 이 한정을 빼고 다른 벌칙과 병기하지 마세요. 항 아래 호를 지칭할 때는 "제M항제N호"로 정확히 표기하세요("(N)항"처럼 호를 항으로 바꿔 부르지 않기)
9. 제목·소제목·요약 라벨도 본문 내용과 정합해야 합니다 — 원칙과 단서("다만 ~")가 함께 있는 조문은 원칙을 기준으로 제목을 붙이고, 단서의 예외를 그 조문 전체의 제목·구분 라벨로 승격하지 마세요 (예: 부속 의료기관 개설은 원칙이 신고이고 병원급만 단서로 허가이므로, 구분 제목을 "개설 허가"로 달면 안 됩니다)
10. 확실하지 않은 내용은 "확인이 필요합니다"로 표기하세요
11. 반드시 한국어로 답변하세요
12. 마지막에 면책 문구: "이 정보는 법률 정보 제공이며, 법적 조언이 아닙니다. 구체적 사안은 법률 전문가와 상담하세요."
"""

# ── 모델별 생성 규칙 ────────────────────────────────────────────────────────
# 위 MEDICAL_LAW_SYSTEM은 **폐루프 7~18이 qwen2.5:14b의 실패를 보고 누적한 것**이다
# (규칙 4 하나가 ~1,000자 — 단서 탈락·호 오배치·방향 반전·대상 확장을 차례로 막은 흔적).
# 35b급 모델은 실패 방향이 다르다 — 2026-08-04 실측:
#   ①**무관 근거에 끌림**: 408자짜리 상대가치점수 고시 1건이 1,470자 답변의 절반을 차지
#      (같은 근거로 14b는 267자를 쓰며 그 고시를 무시했다)
#   ②**분량 과다**: 규칙 적용 시 1,322자(14b 729자) — 지연 25~30초의 주원인
#   ③**정답 벌칙 미인용**: 근거 87% 지점에 있는 제88조의2를 못 찾고 "확인되지 않습니다"
# 반대로 35b가 **자동으로 하는 것**: 제15조① 단서를 스스로 인용(규칙 4의 핵심 목적)
#   → 단서 부기 게이트가 발동 0건으로 비켜선다.
# ⇒ 35b용은 "빼먹지 마라"를 줄이고 "끌려가지 마라·짧게 써라"를 넣는다.
# ⚠️ 이 변형은 **충실도 N=8 단일 지표**(35b가 minimal에서 1.000)에 근거한다 —
#    규칙 4·5·8이 겨냥하는 conditions_kept·numbers_match는 아직 이 조건에서 측정하지 않았다.
#    적용 전 반드시 체크리스트를 포함해 재측정할 것(기본값은 현행 유지).
MEDICAL_LAW_SYSTEM_CONCISE = """당신은 한국 의료법 전문 AI입니다. 다음 규칙을 따르세요:
1. 관련 법률 조문을 정확히 인용하세요 (법률명 + 조문 번호, 항·호까지)
2. **질의와 직접 관련된 근거만 사용하세요.** 근거 목록에는 질의와 무관한 조문·고시·발간물이 섞여 있을 수 있습니다 — 무관한 근거는 인용하지 말고 무시하세요. 모든 근거를 성실히 반영하려 하지 마세요
3. **간결하게 답하세요.** 질문에 답하는 데 필요한 조문만 인용하고 배경 설명·부연·표를 덧붙이지 마세요 — 다만 **질문에 대한 답을 완결된 문장으로 반드시 서술**하세요. 조문 번호나 제목만 나열하고 끝내지 말고, 그 조문이 무엇을 규정하는지 한 문장 이상으로 설명해야 합니다
4. 근거에 벌칙 조문이 있으면 **목록 뒤쪽에 있더라도 끝까지 찾아** 인용하세요 — 벌칙 조문이 나열하는 위반 조항의 조·항이 질의 행위와 일치하는지 확인하세요. 근거에 없으면 "벌칙은 근거에서 확인되지 않습니다"로 표기하고 형량·벌금액을 창작하지 마세요
5. 조문의 한정 조건과 단서("~에 한정한다", "다만 ~")는 생략·완화하지 말고 그대로 옮기세요. 질문이 거부·금지 여부를 묻더라도 조문의 규범 방향은 뒤집지 마세요
6. 근거에 없는 파생 사항(후속 절차·권한·책임 등)을 추론해 덧붙이지 마세요
7. 발간물(법령집·편람 등) 라벨의 문서 제목은 법령명이 아닙니다 — 소속 법령명이 근거 본문에 명시된 경우에만 붙이세요
8. 확실하지 않은 내용은 "확인이 필요합니다"로 표기하세요
9. 반드시 한국어로 답변하세요
10. 마지막에 면책 문구: "이 정보는 법률 정보 제공이며, 법적 조언이 아닙니다. 구체적 사안은 법률 전문가와 상담하세요."
"""

_SYSTEM_VARIANTS = {"default": None, "concise": MEDICAL_LAW_SYSTEM_CONCISE}


def _default_model():
    """`/ask`·`/ask/stream`의 기본 생성 모델.

    종전에는 `qwen2.5:14b` 하드코딩이었다. 그 탓에 상주 모델을 35b로 두어도(quality 모드)
    **요청마다 35b가 축출되고 12초 콜드 복귀**를 물었다 — 상주가 사실상 무의미했다
    (2026-08-04 실측: 여유 2.0GB에서 14b 10.5GB가 들어올 자리가 없다).
    ⇒ 상주가 켜져 있으면 **상주 모델을 따른다**(스왑 0). 상주가 꺼져 있으면 종전 기본값.
    config `medlaw_default_model`로 명시 고정 가능(연동을 원치 않을 때).
    """
    try:
        cfg = load_config() or {}
        explicit = (cfg.get("medlaw_default_model") or "").strip()
        if explicit:
            return explicit
        from core.llm_residency import is_resident, resident_model
        if is_resident(cfg):
            m = resident_model(cfg)
            if m:
                return m
    except Exception:  # noqa: BLE001
        pass
    return "qwen2.5:14b"


def _system_for(model):
    """모델별 생성 규칙 선택. config `medlaw_system_by_model` = {모델명: 변형이름}.
    미지정·미인식이면 현행 규칙(default) — **기본 동작 100% 불변**."""
    try:
        mapping = load_config().get("medlaw_system_by_model") or {}
        variant = mapping.get(model or "")
        return _SYSTEM_VARIANTS.get(variant) or MEDICAL_LAW_SYSTEM
    except Exception:  # noqa: BLE001
        return MEDICAL_LAW_SYSTEM


def _keep_alive_for(model):
    """상주 모델을 호출할 때 상주 정책의 keep_alive를 함께 실어 **고정을 유지**한다.

    🔴 2026-08-04 실측 결함: 상주 예열은 `keep_alive=-1`(무기한)로 적재하는데, 이후 요청이
    keep_alive 없이 들어오면 **Ollama가 만료를 전역 기본값(5분)으로 재설정**한다 —
    즉 `/ask` 요청 한 번에 고정이 풀리고, 5분 뒤 언로드된다. quality 모드(35b 상주)에서
    `ps`가 비어 있고 매 요청이 콜드 로드(43s)를 무는 원인이 이것이었다.
    ⇒ 대상이 상주 모델이면 정책값을 함께 보낸다. 아니면 아무것도 보내지 않는다(기존 동작).
    """
    try:
        cfg = load_config() or {}
        from core.llm_residency import is_resident, keep_alive_for, resident_model
        if is_resident(cfg) and (model or "") == resident_model(cfg):
            ka = keep_alive_for(cfg)
            if ka is not None:
                return {"keep_alive": ka}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _think_off(model):
    """thinking 모델이면 `think: False`를 실어야 한다.

    2026-08-03 실측: `/api/medical-law/ask`에 `model=qwen3.6:35b-a3b`를 주면 **빈 응답**
    (길이 0)이 온다 — 추론 토큰만 내고 `response`가 빈다. 반대로 **비-thinking 모델에
    think를 실으면 Ollama가 요청 전체를 거절**하므로(v2.124.59 실측), capabilities를
    먼저 확인한 뒤 지원 모델에만 넣는다. 확인 실패 시엔 넣지 않는다(기존 동작 보존).
    """
    try:
        from routes.ollama import model_supports_thinking
        return {"think": False} if model_supports_thinking(model) else {}
    except Exception:
        return {}


#: 생성 실패 표식. 기존 호출자가 `startswith("Error:")`로 거르므로 접두는 유지하되,
#: 뒤에 예외 문자열을 붙이지 않는다 — 붙이면 `HTTPConnectionPool(host='localhost',
#: port=11434)` 같은 내부 주소가 응답 본문으로 나간다(v2.124.25 에러 위생과 같은 클래스가
#: 이 경로에만 남아 있었다). 상세는 서버 로그로만 남긴다.
_GEN_ERROR = "Error: 생성에 실패했습니다"


def _gen_failed(response):
    return isinstance(response, str) and response.startswith("Error:")


def _router_enabled():
    """task 라우터 경유 여부 — config `model_router_enabled`(기본 true).

    1-e 롤백 경로: false면 종전 직접 호출 경로를 그대로 탄다.
    """
    try:
        return bool((load_config() or {}).get("model_router_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def _stream_tokens(model, prompt, system):
    """토큰 이터레이터 — `(text, done, tokens)`. 라우터 경유(기본) 또는 구 경로(토글 off).

    v2.124.97 Phase 1-d 3단계. **소비 루프를 하나로 두려고** 이터레이터로 감쌌다 —
    라우터/구 경로가 각자 yield 루프를 가지면 곧 갈라진다(v2.124.59에서 `/api/chat`과
    `/api/generate`가 갈라져 호출자가 준 값이 조용히 버려지던 클래스).

    🔴 **커넥션 회수**: 클라이언트 조기 종료 시 소비자 제너레이터의 GeneratorExit이
    여기로 전파된다. 라우터 경로는 어댑터의 `finally`가, 구 경로는 아래 `finally`가 닫는다.
    """
    if _router_enabled():
        try:
            from core import model_router
            # 🔴 호출자의 세션·base를 넘긴다 — 어댑터가 자기 세션을 만들면
            #    `_GatedOllamaSession`(ChatFairQueue)을 우회한다(2026-08-10 실측).
            adapter = model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)
            for c in model_router.stream(
                    "medical_law.answer", prompt, model=model, system=system,
                    options={"temperature": 0.3, "num_predict": 1024},
                    timeout=120, adapter=adapter, **_keep_alive_for(model)):
                yield c.text, c.done, c.tokens
                if c.done:
                    return
            return
        except (ImportError, AttributeError) as e:
            # 라우터 **배선** 실패만 강등한다. 생성 중 오류(HTTP·타임아웃)는 그대로 올려야
            # 호출자가 error 이벤트를 낸다 — 여기서 삼키면 절반만 나간 응답이 정상으로 보인다.
            logger.error(f"[medical-law] stream router unavailable, falling back: {e}")

    r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
        "model": model, "prompt": prompt, "system": system, "stream": True,
        **_keep_alive_for(model),
        "options": {"temperature": 0.3, "num_predict": 1024},
    }, timeout=120, stream=True)
    try:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            tok = d.get("response", "")
            if tok:
                yield tok, False, 0
            if d.get("done"):
                yield "", True, d.get("eval_count", 0)
                return
    finally:
        r.close()


def _generate(model, prompt, system, options=None):
    """의료법 생성 — v2.124.96에서 **task 라우터 경유**로 이관(Phase 1-d 1단계).

    이관으로 얻는 것: task 등록 검사(오타 차단) · **`local_only` 정책 강제**(PHI·규제
    경로가 외부 provider로 새지 않는다) · 전송 단일화.
    ⚠️ **모델 선택은 이관하지 않았다** — `_default_model()`이 상주 모델을 동적으로
    따라가기 때문이다(고정값으로 바꾸면 quality 모드에서 매 요청 축출이 재발한다).
    호출자가 고른 모델을 그대로 넘긴다.

    `force_korean`은 **여기 남는다**(seam 위) — §1-6에서 필터가 내용을 지운 사고가 있었고,
    후처리는 도메인이 정할 일이다.
    """
    opts = options or {"temperature": 0.3, "num_predict": 1024}
    if _router_enabled():
        try:
            from core import model_router
            # 🔴 **호출자의 세션·base를 그대로 넘긴다.** 어댑터가 자기 세션을 만들면
            #    `_ollama_session`(= `_GatedOllamaSession`)을 우회해
            #    **ChatFairQueue 슬롯 제어(max_global=3·max_per_user=1)가 통째로 빠진다**.
            #    2026-08-10 실측: 이걸 빠뜨렸더니 실패 주입 테스트에 **실제 LLM 응답**이
            #    돌아왔다(회귀 3건이 잡음). 커넥션 keep-alive 풀도 같이 잃는다.
            adapter = model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)
            r = model_router.generate(
                "medical_law.answer", prompt, model=model, system=system, options=opts,
                timeout=60, adapter=adapter, **_think_off(model), **_keep_alive_for(model))
            if not r.ok:
                logger.error(f"[medical-law] generate failed model={model}: {r.error}")
                return _GEN_ERROR, 0, 0
            return force_korean(r.text), r.tokens, r.duration_ms
        except Exception as e:  # noqa: BLE001 — 라우터 자체 실패는 구 경로로 강등
            logger.error(f"[medical-law] router path failed, falling back: {e}")
    try:
        r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "options": opts,
            **_think_off(model), **_keep_alive_for(model),
        }, timeout=60)
        d = r.json()
        response = force_korean(d.get("response", ""))
        tokens = d.get("eval_count", 0)
        return response, tokens, int(d.get("total_duration", 0) / 1e6)
    except Exception as e:
        logger.error(f"[medical-law] generate failed model={model}: {e}")
        return _GEN_ERROR, 0, 0


def _generate_json(model, prompt, system, options=None):
    """format=json 강제 생성 → dict 파싱. 실패 시 {'_error':...}."""
    opts = options or {"temperature": 0.2, "num_predict": 1024}
    raw = ""
    try:
        r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "format": "json", "options": opts, **_think_off(model),
        }, timeout=90)
        d = r.json()
        raw = (d.get("response") or "").strip()
        return json.loads(raw), d.get("eval_count", 0), int(d.get("total_duration", 0) / 1e6)
    except (json.JSONDecodeError, ValueError) as e:
        return {"_error": f"json parse: {e}", "_raw": raw[:500]}, 0, 0
    except Exception as e:
        return {"_error": str(e)}, 0, 0


# 의료법 RAG 컬렉션 (scripts/ingest_law_3dan.py로 적재한 법령 3단비교 코퍼스)
MEDLAW_COLLECTION = "medical_law_knowledge"


def _like(term: str) -> str:
    """LIKE 패턴 인자 — 사용자 입력의 %·_·\\를 리터럴로 이스케이프.

    주입 문제는 아니다(값은 항상 바인딩). 이스케이프가 없으면 '%'가 든 질의가 전체
    매칭으로 번지고 '_'가 임의 1자로 동작해 **검색 결과가 조용히 달라진다**.
    호출부는 ESCAPE '\\'를 함께 지정해야 한다.
    """
    s = (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{s}%"
# 발간물(편람·심사기준·분류체계 등)은 별도 컬렉션에 분리 색인 — 단일 컬렉션이 ~25K로 커지면
# ChromaDB(Rust) HNSW가 손상되는 이슈 회피([[project_incident_chromadb_disk_full]]).
# 자문 검색은 본 코퍼스 + 보조 컬렉션을 함께 질의·병합한다. 미존재 컬렉션은 무시(폴백).
MEDLAW_AUX_COLLECTIONS = ["medical_pub_knowledge"]
# 본 코퍼스 과다 인출 배수 — 동일 원문 중복(고시 판본 간)이 top-K를 잠식하는 만큼 더
# 뽑아 dedup 후 top_k로 절단. 2배면 실측 최악(5/8 중복)도 흡수한다.
_OVERFETCH = 2


def _safe_top_k(val, default=5, lo=1, hi=10):
    """사용자 top_k 입력을 안전하게 정수 클램프. 비정수/음수/None → 무예외(default·범위 보정).
    (L4: int(data.get('top_k'))가 비정수 입력에서 ValueError→500 내던 것 방지)"""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


# 근거 권위(authority) 위계 — 법적 효력 우선순위. 의료법 자문은 법령>고시>발간물 순으로
# 인용해야 한다(발간물=편람·가이드맵·사례집은 실무참고로 법적 구속력 약함).
# origin → (authority 라벨, 랭킹 tier penalty). 발간물은 본 코퍼스보다 훨씬 커서 순수
# score 정렬 시 법령/고시를 밀어냄 → tier penalty + 법적근거 floor로 보정.
_AUTHORITY = {
    "law3dan":        ("법령", 0.00),
    "khidi_notice":   ("고시", 0.00),
    "hira_valuepoint": ("고시(상대가치)", 0.02),
    "hira_pub":       ("발간물(참고)", 0.06),
}


def _chunk_cap():
    """RAG 청크의 프롬프트 반영 상한(자). config `medlaw_chunk_chars`로 무재배포 튜닝.
    600→1500 상향: 열거 조문(제66조① 각 호 등)이 캡에 잘려 생성 모델이 못 본 호를
    기억으로 채우던 환각 클래스 봉합. 캡1500 실측 프롬프트 ~3.1K tok(+생성 1024)로
    전역 num_ctx 8192 내 — num_ctx 상향(요청별 전환=러너 리로드 ~3s) 불요."""
    try:
        v = int(load_config().get("medlaw_chunk_chars", 1500))
        return max(200, min(v, 4000))
    except Exception:
        return 1500


# ── 단서 부기(conditions_kept 폐루프 16) ──────────────────────────────────────
# 배치 checks_failed 1위 `accuracy.conditions_kept` 47%의 드릴다운 결과:
#   ①judge 위양성 아님 — 저장 답변 재채점 N=8×5문항 **40/40 False**(완전 결정적)
#   ②근거 결손도 아님 — 5/5 문항의 근거에 단서가 **온전히 포함**돼 있었다
#   ③즉 근거에 있는 단서를 생성이 빠뜨린다. 실패의 대부분이 한 조문(시행규칙 제15조①
#     "다만, 계속적인 진료를 위하여 필요한 경우에는 1회에 한정하여 … 연장하여 보존할 수
#     있다")에 몰려 있었다 — 12문항의 서로 다른 결함이 아니라 단서 하나의 반복 탈락.
# 규칙 4·5가 이미 한정조건 보존을 지시하는데도 47%가 실패하므로 지시 강화는 후보에서
# 제외(v2.124.28 이후 반복 확인된 확률 위반 클래스) → **결정적 부기**로 처치한다.
# 근거 원문을 그대로 인용하므로 창작 위험 0·LLM 호출 0.
# 정밀도/재현율 실측(저장 답변 40문항, 기준=배치 conditions_kept): 임계 0.5에서 0.56/0.70.
# ⚠️정밀도가 임계를 올려도 0.58에서 안 오른다 — 기준 자체가 우리가 고치려는 불안정 지표라
#   더 튜닝하면 과적합. 실효 검증은 다음 배치의 conditions_kept 변화로 한다.
_DANSEO_RE = re.compile(r"다만[,，][^.。\n]{10,220}")
# 헤딩=괄호 제목이 붙은 조만("제15조(진료기록부 등의 보존)"). 본문 교차참조
# ("법 제22조제2항에 따른")을 헤딩으로 오인하면 단서가 엉뚱한 조로 귀속된다(실측:
# 제15조① 단서가 그 앞의 "법 제22조제2항" 때문에 제22조로 붙어 게이트가 통째로 무발동).
_ART_HEAD_RE = re.compile(r"(제\s*\d+조(?:의\s*\d+)?)\s*\(")
_ART_REF_RE = re.compile(r"제\s*\d+조(?:의\s*\d+)?")
# 단서 문장의 뼈대어(어느 단서에나 나와 변별력이 없음) — 커버리지 계산에서 제외
_CLAUSE_STOP = {
    "다만", "경우", "해당", "다음", "각호", "각목", "대통령령", "보건복지부령", "정하는",
    "하나에", "있다", "한다", "그러하지", "아니하다", "때에는", "위하여", "필요한",
    "따른", "따라", "이하", "같다", "제외한다", "범위에서",
}


def _clause_cfg():
    """단서 부기 설정 — 무재배포 튜닝. 끄면 기존 동작 100% 복귀."""
    try:
        c = load_config()
        return (bool(c.get("medlaw_clause_note", True)),
                max(0.0, min(float(c.get("medlaw_clause_note_coverage", 0.5)), 1.0)),
                max(1, min(int(c.get("medlaw_clause_note_max", 2)), 5)))
    except Exception:
        return True, 0.5, 2


def _clause_tokens(text):
    return {w for w in re.findall(r"[가-힣]{2,}", text or "") if w not in _CLAUSE_STOP}


def _danseo_by_article(evidence):
    """근거 원문 → {조번호: [단서 문장]}. 조 헤딩으로 분할해 귀속(순수)."""
    out, cur = {}, None
    for part in _ART_HEAD_RE.split(evidence or ""):
        if _ART_REF_RE.fullmatch((part or "").strip()):
            cur = part.replace(" ", "")
            continue
        if not cur:
            continue
        for d in _DANSEO_RE.findall(part or ""):
            bucket = out.setdefault(cur, [])
            if d not in bucket:
                bucket.append(d)
    return out


def _missing_clauses(answer, evidence, coverage=0.5, limit=2):
    """답변이 인용한 조의 단서 중 **내용이 답변에 실리지 않은 것**만 반환(순수).

    답변 전체의 '다만/한정' 어휘 유무로 판정하면 다른 단서를 빠뜨린 경우를 놓친다
    (실측 재현율 0.50→0.70) → 단서별 내용어 커버리지로 개별 판정한다.
    """
    if not answer or not evidence:
        return []
    cited = {a.replace(" ", "") for a in _ART_REF_RE.findall(answer)}
    if not cited:
        return []
    dmap = _danseo_by_article(evidence)
    at = _clause_tokens(answer)
    out = []
    for art in sorted(cited):
        for d in dmap.get(art, []):
            dt = _clause_tokens(d)
            if not dt:
                continue
            if len(dt & at) / len(dt) < coverage:
                out.append((art, d.strip()))
                if len(out) >= limit:
                    return out
    return out


def _append_clause_note(answer, evidence):
    """빠진 단서를 **근거 원문 그대로** 부기. 부기할 게 없으면 답변 불변."""
    enabled, coverage, limit = _clause_cfg()
    if not enabled:
        return answer
    try:
        miss = _missing_clauses(answer, evidence, coverage=coverage, limit=limit)
    except Exception:
        return answer
    if not miss:
        return answer
    lines = ["\n\n[인용 조문의 단서(근거 원문)]"]
    lines += [f"- {art}: {d}" for art, d in miss]
    return answer + "\n".join(lines)


def _evidence_text(rag_hits, articles=None):
    """단서 추출용 근거 원문 — 프롬프트 캡 이전의 전문을 쓴다(캡에 단서가 잘릴 수 있음)."""
    parts = []
    for a in (articles or []):
        try:
            parts.append(f"{a['article_number']}({a['article_title']}) {a['content']}")
        except (KeyError, IndexError, TypeError):
            continue
    for h in (rag_hits or []):
        t = (h or {}).get("text") or ""
        if t:
            parts.append(t)
    return "\n".join(parts)


def _authority_of(origin):
    return _AUTHORITY.get(origin or "", ("기타", 0.06))


# 서식/양식(fillable form) 청크 억제 — 진료비확인 신청서·동의서, 별지 서식, 청구명세서
# 서식 등은 법률 자문의 근거가 아니라 boilerplate인데, 질문 문구와 어휘가 크게 겹쳐
# (예: "발급 요청·거부·동의") 법령보다 높은 벡터 유사도로 상위 랭크되어 근거를 오염시킨다
# (id95 드릴다운 실증: 진료비확인 양식 0.60이 제21조 0.23을 밀어내 judge 오판 유발).
# 고정밀 서명만 사용 — 전수 검증상 실질 조문 오탐 ~0.
def _is_form_chunk(text):
    t = text or ""
    if "동의함" in t and "동의안함" in t:            # 체크박스 동의서
        return True
    if t.count("[ ]") >= 5:                           # 순수 체크리스트 양식
        return True
    if (("㎜×" in t) or ("㎜ ×" in t) or ("mm×" in t) or ("mm ×" in t)) \
            and (("접수번호" in t) or ("별지" in t and "서식" in t)
                 or ("서명 또는 인" in t)):
        return True                                    # 용지규격 + 서식헤더 = 양식 템플릿
    if ("접수번호" in t) and ("처리기간" in t):
        return True                                    # 신청서 헤더
    if ("위임자" in t) and ("수임자" in t):
        return True                                    # 위임장
    return False


def _drop_form_chunks(hits):
    """서식 청크 제거. 전량 서식이면(비정상) 원본 유지 = 근거 소실 방지(fail-safe)."""
    kept = [h for h in hits if not _is_form_chunk(h.get("text") or "")]
    return kept if kept else hits


# 결정적 질의 확장 — 개별 기록물명 질의가 용어 충돌로 정답 조문을 못 잡는 클래스 보정.
# 실증(eval id175): "방사선 사진 및 영상물의 보존기간은?"에서 방사성의약품·핵의학 수가·
# 발생장치 청크가 유사도 top5를 점령해 정답(시행규칙 제15조 '진료기록부 등의 보존',
# 확장 질의 유사도 0.582)이 top-K 밖 탈락 → 조문 표제어로 본 코퍼스 보조 질의해 병합.
# 룰: all(전 토큰 포함) + any(하나 이상 포함, 생략 가능) → query(확장 질의).
# config `medlaw_query_expansions`로 무재배포 추가/교체(동일 스키마 배열).
_DEFAULT_QUERY_EXPANSIONS = [
    {
        "all": ["보존"],
        "any": ["방사선", "영상", "사진", "기록", "명부", "처방전", "진단서",
                "조산", "소견"],
        "query": "진료기록부 등의 보존 기간",
    },
]


def _query_expansions(query):
    """질의에 매칭되는 확장 질의 목록(결정적·최대 2건). 실패는 무확장으로 강등."""
    try:
        rules = load_config().get("medlaw_query_expansions") or _DEFAULT_QUERY_EXPANSIONS
    except Exception:
        rules = _DEFAULT_QUERY_EXPANSIONS
    q = query or ""
    out = []
    for r in rules:
        try:
            if not all(t in q for t in (r.get("all") or [])):
                continue
            any_terms = r.get("any") or []
            if any_terms and not any(t in q for t in any_terms):
                continue
            exp = (r.get("query") or "").strip()
            if exp and exp != q and exp not in out:
                out.append(exp)
        except Exception:  # noqa: BLE001 — 룰 하나가 깨져도 나머지 진행
            continue
    return out[:2]


# 벌칙 역참조 보강 — 처벌 의도 질의에서 의무 조문만 검색되는 클래스 보정.
# 벌칙·과태료·자격정지 조문은 "제19조, 제22조제3항, …" 식 조번호 교차참조 목록이라
# 자연어 질의와 임베딩 유사도가 낮아 top-K 진입에 실패한다(실증 eval id199:
# "진료기록부 거짓 작성 처벌" → 제22조·제14조만 검색, 제88조(제22조제3항 역참조,
# 3년/3천만원) 탈락 → 모델이 존재하지 않는 조문·형량 창작). 검색된 법령 조문의
# 조번호를 본문에서 역참조하는 벌칙류 조문을 결정적으로 근거에 합류시킨다.
# config `medlaw_penalty_backref`(기본 true) 무재배포 토글. 미매칭=빈 리스트=기존 불변.
_PENALTY_INTENT_TERMS = ("처벌", "벌칙", "벌금", "징역", "과태료", "자격정지",
                         "행정처분", "형량")
# 금지·의무형 질의 — 처벌을 직접 묻지 않아도 모델이 벌칙을 덧붙인다(규칙 2가 근거 있을 때
# 포함을 지시). 근거에 벌칙 조문이 없으면 **창작**이 나온다.
# 실증(2026-08-04): "태아 성감별 고지 금지 의무는?" → 정답 벌칙 조(제88조의2)가 근거에 없어
#   모델이 **생명윤리법 제21조/제98조·의료법 제28조/제104조를 지어냄**. 정작 제88조의2는
#   코퍼스에 있었고 "1. 제20조를 위반한 자"로 정확히 역참조까지 하고 있었다 —
#   게이트가 처벌 어휘만 보느라 붙여주지 못했을 뿐이다.
# ⇒ 게이트가 곧 커버리지다. 금지·의무형에도 역참조를 붙여 창작할 이유를 없앤다.
_DUTY_INTENT_TERMS = ("금지", "의무", "위반", "제한", "하여서는", "아니 된다",
                      "아니된다", "해서는 안", "준수", "책무")

# 🔴 `의무`는 **의무기록**(medical record)의 부분문자열이다 — 이 시스템의 핵심 도메인
# 용어라 게이트가 상시 오발동했다(2026-08-18 실측: "전자의무기록으로 진료기록부를
# 대체할 수 있는가?" → `의무` 매칭 위치 2, 문맥 `전자의무기록으로`).
# 연쇄: 게이트 오발동 → 묻지도 않은 벌칙 조문이 근거에 붙음 → 질의가 어느 항도 묻지
#   않으니 `_drop_paragraph_mismatch`는 설계대로 fail-open → 모델이 벌칙을 자발적으로
#   덧붙이며 제23조③(제87조의2·5년/5천만)과 ④(제90조·500만원)를 혼동. **재현 2/5**.
# ⇒ 의도 어휘를 세기 **전에** 도메인 복합어를 지운다. **좁히는 방향**이라 게이트가 원래
#   잡던 "~할 의무는?"·"~금지 의무는?"은 그대로 발동한다(통제군으로 고정).
# 📌 단어 경계 없는 부분문자열 매칭 클래스 — 약물명에서 두 번 고쳤다(v2.101/102:
#   `이소트레티노인`의 `노인`, 연결어 `와`가 `와파린`에 오매칭).
_DUTY_MASK_RE = re.compile(r"(?:전자)?의무기록(?:시스템|사|실)?|의무실|의무관|의무장교")


def _has_duty_intent(question):
    """금지·의무형 질의인가 — **도메인 복합어를 지운 뒤** 의도 어휘를 센다."""
    return any(t in _DUTY_MASK_RE.sub(" ", question or "") for t in _DUTY_INTENT_TERMS)
_PENALTY_TITLE_TERMS = ("벌칙", "과태료", "자격정지")
_BACKREF_CACHE = {}  # {law_name: (ts, chunks)} — 법령별 벌칙류 조문 전수 조회 비용 방어
_BACKREF_CACHE_TTL = 600


def _penalty_article_chunks(law_name):
    """해당 법령의 벌칙·과태료·자격정지 표제 조문 청크(TTL 캐시)."""
    import time as _time
    cached = _BACKREF_CACHE.get(law_name)
    if cached and _time.time() - cached[0] < _BACKREF_CACHE_TTL:
        return cached[1]
    from client.rag import get_collection
    col = get_collection(MEDLAW_COLLECTION)
    res = col.get(where={"law_name": law_name},
                  include=["documents", "metadatas"], limit=3000)
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    out = []
    for i, m in enumerate(metas):
        title = (m or {}).get("article_title") or ""
        if any(t in title for t in _PENALTY_TITLE_TERMS):
            out.append({"id": ids[i], "text": docs[i] or "",
                        "metadata": m or {}, "score": None, "backref": True})
    _BACKREF_CACHE[law_name] = (_time.time(), out)
    return out


_PARA_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"


def _paragraph_blocks(text):
    """조문 본문 → [(항 번호(1-base), 블록)]. 항 표시가 없으면 빈 리스트."""
    idx = [(m.start(), m.group(0)) for m in re.finditer(f"[{_PARA_MARKS}]", text or "")]
    out = []
    for i, (pos, mark) in enumerate(idx):
        end = idx[i + 1][0] if i + 1 < len(idx) else len(text)
        out.append((_PARA_MARKS.index(mark) + 1, text[pos:end]))
    return out


def _listed_paragraphs(penalty_text, art):
    """벌칙 조문이 `art`의 어느 **항**을 열거하는가 → (조 언급 여부, {항 번호}).

    `제27조제2항`·`제33조제1항ㆍ제3항`·`제27조제1항 또는 제5항`처럼 조 뒤에 항이
    이어지는 형태를 읽는다. 구분자를 좁게 잡으면 뒤쪽 항을 놓치고, 그러면 그 항을
    겨냥한 질의에서 **정답 벌칙 조문이 배제**된다(회귀로 고정).
    항 없이 조만 열거하면(`제19조`) 항 집합은 비고 조 언급은 True — 이때는
    항으로 가릴 수 없으므로 필터가 발동하지 않는다."""
    body = penalty_text or ""
    _sep = r"(?:[ㆍ·,]|\s*(?:또는|및|과|와)\s*)"
    pat = re.compile(re.escape(art) + r"(?!의)((?:제\d+항)(?:" + _sep + r"제\d+항)*)?")
    mentioned, paras = False, set()
    for m in pat.finditer(body):
        mentioned = True
        tail = m.group(1) or ""
        for pm in re.finditer(r"제(\d+)항", tail):
            paras.add(int(pm.group(1)))
    return mentioned, paras


def _target_paragraphs(query, act_text, candidates, margin=0.05):
    """질의가 겨냥하는 **항** 번호 집합. 후보 항만 임베딩한다(비용 절감).

    08-10 실측(제27조): 무면허 질의 ↔ ① 0.5582 · ⑤ 0.5581 · **② 0.3757**(-0.18) ·
    질의를 명칭 사용으로 바꾸면 ②가 0.5894로 +0.21 상승 = 방향이 갈린다.
    벌칙 조문 사이의 격차(0.023)보다 한 자릿수 크므로 항 판별은 가능하다.

    최고점에서 margin 이내인 항은 모두 대상으로 본다(제27조 ①·⑤처럼 정답이 복수).
    후보가 2개 미만이거나 임베딩이 실패하면 None = **판별 불가**(필터 미적용)."""
    if len(candidates) < 2:
        return None
    blocks = {n: b for n, b in _paragraph_blocks(act_text) if n in candidates}
    if len(blocks) < 2:
        return None
    try:
        from client.rag import embed_text
        import math

        def _cos(a, b):
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            if not na or not nb:
                return 0.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        qv = embed_text(query)
        scored = {n: _cos(qv, embed_text(b)) for n, b in blocks.items()}
    except Exception as e:  # noqa: BLE001 — 임베딩 실패는 필터를 끌 뿐
        logger.debug(f"[medical-law] target paragraph detection skipped: {e}")
        return None
    if not scored:
        return None
    top = max(scored.values())
    return {n for n, s in scored.items() if s >= top - margin}


def _drop_unrelated_penalty(hits):
    """검색된 **어느 행위 조문도 처벌하지 않는** 벌칙 청크를 근거에서 뺀다(v2.124.94).

    벌칙 조문은 "제N조…를 위반한 자는 X"라는 조번호 목록이라, 질의와 임베딩 유사도는
    높지만 **이 질의를 처벌할 수 없는** 조문이 상위에 앉는다(08-10 실측: 무면허 질의
    근거에 제89조가, 리베이트 질의에 제90조가 들어온다 — 둘 다 해당 행위를 처벌하지 않는다).

    🔑 **행위 조문은 검색된 것 전부를 본다.** top-1만 보면 오판한다 —
    실측(41문항·벌칙 청크 28개): top-1 기준 미열거 **12** vs 전 행위조문 기준 **5**.
    7건은 "다른 행위 조문은 열거하는데 top-1만 안 하는" 경우였다.

    🔑 **열거하는 벌칙이 하나라도 남을 때만 버린다** — `_drop_paragraph_mismatch`와 같은
    불변식. 실측에서 "의료인의 결격사유는?"은 벌칙이 제66조 하나뿐인데 그것이 제8조를
    열거하지 않아, 이 가드가 없으면 **벌칙 근거가 통째로 사라진다**.

    ⚠️ 행위 조문이 **시행규칙**이면 법률 벌칙은 원리적으로 그 조번호를 열거하지 않는다
    (제14조·제2조의2). 그 경우 열거하는 벌칙이 0이 되어 위 불변식이 자동으로 무개입을
    만든다 — 조번호 체계가 다른 경우를 따로 분기할 필요가 없다.

    config `medlaw_unrelated_penalty_filter`(기본 true) 무재배포 토글."""
    try:
        if not load_config().get("medlaw_unrelated_penalty_filter", True):
            return hits
    except Exception:  # noqa: BLE001
        pass
    if not hits:
        return hits
    acts, penalties = [], []
    for h in hits:
        m = h.get("metadata") or {}
        if m.get("law_name") != "의료법" or not m.get("article_number"):
            continue
        if any(t in str(m.get("article_title") or "") for t in _PENALTY_TITLE_TERMS):
            penalties.append(h)
        else:
            acts.append(m["article_number"])
    if not acts or len(penalties) < 2:
        return hits
    related, unrelated = [], []
    for h in penalties:
        body = h.get("text") or ""
        if any(_listed_paragraphs(body, a)[0] for a in acts):
            related.append(h)
        else:
            unrelated.append(h)
    if not related or not unrelated:
        return hits          # 남을 것이 없으면 아무것도 버리지 않는다
    drop = {id(h) for h in unrelated}
    for h in unrelated:
        m = h.get("metadata") or {}
        logger.info("[medical-law] 무관 벌칙 근거 제외: %s(검색된 행위 조문 %s 중 어느 것도 미열거)",
                    m.get("article_number"), acts[:4])
    return [h for h in hits if id(h) not in drop]


def _drop_paragraph_mismatch(hits, query):
    """행위 조문의 **다른 항**만 처벌하는 벌칙 청크를 근거에서 뺀다(v2.124.94).

    08-10 검색 진단: "무면허 의료행위의 벌칙은?"에서 **제90조가 벡터 1순위**(0.6703)이고
    정답 제87조의2가 2위(0.6466)다. 격차 0.023은 노이즈 수준이며, 제90조를 끌어올리는 것은
    꼬리 한 구절("의료기관 개설자가 될 수 없는 자에게 **고용되어 의료행위를 한 자**",
    단독 0.6881 = 전문보다 높다)이다. 이건 **사무장병원** 조항인데 임베딩이 무면허와
    구분하지 못한다. 게다가 정답 조문에는 "무면허"라는 말이 아예 없고 `제27조제1항`이라는
    **조번호로만** 지칭돼 있다 ⇒ 표면 어휘로는 임베딩·재랭커 어느 쪽도 못 고친다.
    가릴 수 있는 정보는 **어느 항을 처벌하는가** 하나뿐이다.

    🔑 **양성 매칭이 남을 때만 버린다.** 대상 항을 처벌하는 벌칙 조문이 하나도 없으면
    아무것도 버리지 않는다 — 근거가 비는 경우가 원리적으로 생기지 않는다(근거 소실은
    오귀속보다 나쁘다). 조를 아예 열거하지 않는 벌칙은 여기서 건드리지 않는다 —
    조 단위 무관 판정은 `_drop_unrelated_penalty`가 맡는다(관심사 분리).

    config `medlaw_paragraph_filter`(기본 true) 무재배포 토글."""
    try:
        if not load_config().get("medlaw_paragraph_filter", True):
            return hits
    except Exception:  # noqa: BLE001
        pass
    if not hits:
        return hits
    act = None
    penalties = []
    for h in hits:
        m = h.get("metadata") or {}
        if m.get("law_name") != "의료법" or not m.get("article_number"):
            continue
        if any(t in str(m.get("article_title") or "") for t in _PENALTY_TITLE_TERMS):
            penalties.append(h)
        elif act is None:
            act = h
    if act is None or len(penalties) < 2:
        return hits
    art = (act.get("metadata") or {}).get("article_number")
    listed = {}
    for h in penalties:
        mentioned, paras = _listed_paragraphs(h.get("text") or "", art)
        if mentioned and paras:
            listed[id(h)] = paras
    if len(listed) < 2:
        return hits          # 항으로 가릴 게 없다
    candidates = set().union(*listed.values())
    targets = _target_paragraphs(query, act.get("text") or "", candidates)
    if not targets:
        return hits
    matched = {k for k, v in listed.items() if v & targets}
    if not matched:
        return hits          # 양성 매칭 없음 → 아무것도 버리지 않는다
    dropped = [h for h in penalties
               if id(h) in listed and id(h) not in matched]
    if not dropped:
        return hits
    drop_ids = {id(h) for h in dropped}
    for h in dropped:
        m = h.get("metadata") or {}
        logger.info("[medical-law] 항 불일치로 벌칙 근거 제외: %s(%s 중 %s항 처벌·대상 %s항)",
                    m.get("article_number"), art,
                    sorted(listed[id(h)]), sorted(targets))
    return [h for h in hits if id(h) not in drop_ids]


def _penalty_backref_chunks(question, legal_hits, cap=2):
    """처벌 의도 질의 + 검색된 **행위 조문** → 그 조번호를 역참조하는 벌칙류 조문(최대 cap).
    결정적(임베딩 무관). 실패·미매칭은 빈 리스트로 강등=기존 동작 불변.

    🔑 씨앗은 **벌칙이 아닌 조문**이어야 한다(v2.124.94). 역참조는 `행위조문 → 벌칙조문`
    방향인데 벌칙 조문을 씨앗으로 허용하면 `벌칙 → 같은 법의 다른 벌칙`으로 번진다.
    08-10 실측: "무면허 의료행위의 벌칙은?"(의료법 제27조)에 **약사법 제93조**가 검색
    노이즈로 딸려 오자, 그것을 씨앗으로 **약사법 제95조**까지 합류했고 답변이 그 형량을
    의료법 제90조의 것인 양 썼다("제90조 … 1년 이하의 징역 또는 1천만원 이하의 벌금"
    = 약사법 제95조의 형량). 조번호는 법령을 가로질러 충돌하므로 씨앗을 좁히지 않으면
    타법 형량이 근거에 상주한다. `_xref_focus_articles`가 초점에서 벌칙을 빼는 것과 같은 규칙."""
    try:
        if not load_config().get("medlaw_penalty_backref", True):
            return []
    except Exception:  # noqa: BLE001
        pass
    q = question or ""
    # 처벌 의도(직접) 또는 금지·의무형(간접) — 후자에서도 모델이 벌칙을 덧붙이므로
    # 근거를 붙여 창작을 차단한다(2026-08-04 태아 성감별 실증). config로 끌 수 있다.
    if not any(t in q for t in _PENALTY_INTENT_TERMS):
        try:
            duty_on = load_config().get("medlaw_backref_duty_intent", True)
        except Exception:  # noqa: BLE001
            duty_on = True
        if not (duty_on and _has_duty_intent(q)):
            return []
    import re
    seen = {h.get("id") for h in legal_hits}
    out = []
    for h in legal_hits:  # 검색 랭크 순 — 상위 조문의 역참조부터
        m = h.get("metadata") or {}
        law, art = m.get("law_name"), m.get("article_number")
        if not law or not art:
            continue
        if any(t in (m.get("article_title") or "") for t in _PENALTY_TITLE_TERMS):
            continue  # 벌칙을 씨앗으로 쓰면 같은 법의 다른 벌칙을 줄줄이 끌어온다(위 주석)
        # "제22조"가 "제22조의2" 참조에 오매칭되지 않도록 경계("의" 후행 금지)
        pat = re.compile(re.escape(art) + r"(?!의)")
        try:
            for pc in _penalty_article_chunks(law):
                if pc["id"] in seen or not pat.search(pc["text"]):
                    continue
                out.append(pc)
                seen.add(pc["id"])
                if len(out) >= cap:
                    return out
        except Exception as e:  # noqa: BLE001 — 법령 하나 실패해도 나머지 진행
            logger.debug(f"[medical-law] penalty backref skipped ({law}): {e}")
            continue
    return out


# 벌칙류 청크의 위반 조항 교차참조는 조·항·호 번호 목록이라 모델이 항 단위 대조에
# 실패하는 클래스 실측(07-19 재측정: 제87조의2②의 제27조제1항 목록이 캡 안에 온전한데도
# 무면허 벌칙을 제90조(제27조제2항 위반)로 오귀속 — 규칙 8 단독으론 미봉합). 청크
# **전문**에서 결정적으로(regex·LLM 불요) 위반 조항을 추출해 프롬프트에 주석으로 명시
# = 규칙 8이 대조할 재료 제공 + 캡에 잘린 목록도 매핑 보존.
import re as _re
_XREF_RE = _re.compile(r"제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?")
_PENALTY_HEAD_RE = _re.compile(r"^제\d+조(?:의\d+)?\((?:벌칙|과태료|자격정지)")


def _is_penalty_chunk(text, article_title=""):
    """벌칙·과태료·자격정지 청크 판정(표제 메타 우선, 없으면 본문 선두 패턴)."""
    if any(t in (article_title or "") for t in _PENALTY_TITLE_TERMS):
        return True
    return bool(_PENALTY_HEAD_RE.match((text or "").strip()[:40]))


_TITLE_MAP_CACHE = {}  # {law_name: (ts, {article_number: article_title})}


def _article_title_map(law_name):
    """법령의 조번호→표제 맵(TTL 캐시). 실패 시 빈 dict(주석은 번호만으로 강등)."""
    import time as _time
    cached = _TITLE_MAP_CACHE.get(law_name)
    if cached and _time.time() - cached[0] < _BACKREF_CACHE_TTL:
        return cached[1]
    out = {}
    try:
        from client.rag import get_collection
        col = get_collection(MEDLAW_COLLECTION)
        res = col.get(where={"law_name": law_name}, include=["metadatas"], limit=3000)
        for m in res.get("metadatas") or []:
            art, title = (m or {}).get("article_number"), (m or {}).get("article_title")
            if art and title and art not in out:
                out[art] = title
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[medical-law] title map skipped ({law_name}): {e}")
    _TITLE_MAP_CACHE[law_name] = (_time.time(), out)
    return out


_PARA_MARK_RE = _re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]")
# 호 시작("8. "·"3의2. ") — 앞이 숫자면 연도(`2015. 1.`)이므로 제외한다.
_HO_START_RE = _re.compile(r"(?<![0-9])(\d+(?:의\d+)?)\.\s")


def _paragraph_head(body, pos):
    """pos 앞쪽에서 가장 가까운 항 마커를 찾아 그 항의 **머리 문장**을 돌려준다.

    형량은 항 머리에 있다(`③ … 100만원 이하의 과태료를 부과한다`). 호만 되돌리면
    금액이 여전히 안 보이므로 반드시 함께 실어야 한다.
    """
    marks = [m for m in _PARA_MARK_RE.finditer(body) if m.start() < pos]
    if not marks:
        return ""
    st = marks[-1].start()
    seg = body[st:st + 200]
    cut = _re.search(r"[.。]|<개정|<신설", seg[1:])
    return (seg[:cut.end()] if cut else seg[:120]).strip()


def _focus_ho_tail(body, cap, focus, max_items=3):
    """캡에 잘려 안 보이는 **초점 호**를 항 헤더와 함께 되돌린 문자열(없으면 "").

    2026-08-18 실측(id479): "의료인의 명찰 착용 의무는?"에서 제92조 청크가 **1699자**인데
    생성 캡은 1500이고, 정답 `8. 제4조제5항…`이 **위치 1523**이다. 모델이 보는 것은 맨 위의
    `① … 300만원`뿐이라 명찰 과태료를 **300만원**이라 답한다(정답 ③8호 **100만원**,
    그것도 **시정명령 불이행 시**). 발생률 **3/40 = 7.5%**.

    🔑 **숫자를 요약해 주는 게 아니라 원문 조각을 제자리에 되돌려 놓는다.**
       v2.124.94가 기각한 형량 부기는 주석이 금액을 굵게 실어 모델이 그대로 따라 쓴 것이
       문제였다(금지 신호 6/12 → 12/12 악화). 여기서 모델이 읽는 것은 여전히 법령 원문이다.

    발동 조건이 좁다 — **캡에 잘렸고 + 검색된 행위 조문을 가리키는 호가 잘린 구간에 있을 때만**.
    실측 적용 표면: 평가 질의 40문항 중 **1건**(벌칙 청크 35개 중 1개).

    📌 캡 상향(v2.124.6 600→1500)으로는 이 클래스가 안 닫힌다 — 제21조는 4,091자다.
       캡 경쟁 대신 **필요한 조각만** 되돌린다.
    """
    if not body or not focus or len(body) <= cap:
        return ""
    out, seen = [], set()
    for base in sorted(focus):
        for m in _re.finditer(_re.escape(base) + r"(?:제\d+항)?", body):
            if m.start() < cap:
                continue                       # 이미 보이는 것은 되돌릴 필요가 없다
            starts = [h for h in _HO_START_RE.finditer(body) if h.start() <= m.start()]
            if not starts:
                continue
            hs = starts[-1]
            nxt = _HO_START_RE.search(body, m.end())
            ho = body[hs.start():(nxt.start() if nxt else len(body))].strip()
            ho = _re.split(r"[③④⑤⑥⑦⑧⑨⑩]", ho)[0].strip()
            key = (hs.group(1), ho[:20])
            if key in seen:
                continue
            seen.add(key)
            out.append((_paragraph_head(body, hs.start()), ho))
            if len(out) >= max_items:
                break
        if len(out) >= max_items:
            break
    if not out:
        return ""
    lines = ["", "[절단 구간에서 되돌린 조항 — 근거 원문 그대로]"]
    for head, ho in out:
        if head:
            lines.append("  " + head)
        lines.append("    " + ho)
    return "\n".join(lines) + "\n"

def _xref_focus_articles(rag_hits):
    """주석에서 남길 조번호 집합 — **검색된 행위 조문**(벌칙류가 아닌 조문)의 조번호.
    비어 있으면 필터링하지 않는다(기존 동작 유지=fail-open)."""
    focus = set()
    for h in (rag_hits or []):
        m = (h or {}).get("metadata") or {}
        title = m.get("article_title") or ""
        if any(t in title for t in _PENALTY_TITLE_TERMS):
            continue                      # 벌칙 조문 자신은 초점이 아니다
        art = (m.get("article_number") or "").replace(" ", "")
        if art:
            focus.add(art)
    return focus


def _penalty_xref_note(text, max_refs=20, title_map=None, focus=None):
    """벌칙류 청크 전문에서 위반 조항 교차참조 토큰을 순서 보존 추출한 주석 문자열.
    자기 조번호(청크 선두 조문)는 제외. 추출 0건이면 빈 문자열.
    title_map(조번호→표제)이 있으면 표제 병기 — 질의 행위와 조번호를 잇는 매핑 홉을
    컨텍스트 안에서 닫는다(실측: 금지 조문이 top_k 밖이면 모델이 번호만으론 매핑 실패).

    focus(조번호 집합)를 주면 **그 조를 가리키는 참조만** 남긴다. 2026-08-04 재진단:
    필요한 대응(제88조→제19조·제87조의2→제27조)은 주석 안에 **이미 있었으나 16~17개
    목록에 묻혀 있었고 방향이 반대**였다 — 모델이 필요한 건 `제19조→제88조`인데 주석은
    `제88조→[16개]`를 준다. 무면허는 제27조**제1항**과 **제5항**이 둘 다 실려 있어
    아무거나 골랐다(v2.124.9가 "항 귀속 관찰"로 남긴 잔여가 이것).
    ⇒ 검색된 행위 조문과 교집합만 남겨 목록을 1~2개로 줄인다. 교집합이 비면 필터링하지
       않는다(fail-open — 근거를 지우는 것보다 노이즈가 낫다)."""
    body = (text or "").strip()
    own = ""
    m = _re.match(r"제\d+조(?:의\d+)?", body)
    if m:
        own = m.group(0)
    refs, seen = [], set()
    focused = []
    for tok in _XREF_RE.findall(body):
        base = _re.match(r"제\d+조(?:의\d+)?", tok).group(0)
        if base == own or tok in seen:
            continue
        seen.add(tok)
        if focus and base in focus:
            focused.append((tok, base))
        # 표제는 조 단위 메타 — 항 붙은 토큰에 조 표제를 달면 항 구분이 뭉개져
        # 잘못된 매핑을 정당화(실측: "제27조제2항(무면허 의료행위 등 금지)"가
        # 무면허→제2항→제90조 오귀속을 유도) → 조-only 토큰에만 병기
        title = (title_map or {}).get(base) if tok == base else None
        refs.append(f"{tok}({title})" if title else tok)
        if len(refs) >= max_refs:
            break
    if focused:
        # 초점 조문이 잡히면 그것만 남기고, **방향을 뒤집어** 표기한다
        # (`행위조 → 이 벌칙조`가 모델이 실제로 필요한 형태다)
        items = []
        for tok, base in focused:
            title = (title_map or {}).get(base) if tok == base else None
            items.append(f"{tok}({title})" if title else tok)
        return ("  ※ 위 벌칙 조문은 **%s 위반**을 처벌한다(원문 자동 추출·항 번호까지 대조하라). "
                "다른 조항 목록은 이 질의와 무관하므로 인용하지 말 것.\n"
                % ", ".join(items))
    if not refs:
        return ""
    return ("  ※ 위 벌칙 조문이 나열하는 위반 조항(원문 자동 추출·항 번호까지 대조하라): "
            + ", ".join(refs) + "\n")


def _act_context_chunks(query, exclude_ids, cap=2):
    """처벌 의도 질의에서 행위 조문(금지·의무 본문)이 벌칙 청크에 밀려 top_k 탈락하는
    클래스 보정(07-19 실측: "무면허 의료행위의 벌칙은?" top5가 전부 벌칙류 —
    제27조 본문이 없어 모델이 항 단위 매핑을 파라메트릭 추측) — 벌칙 어휘를 제거한
    질의로 본 코퍼스 보조 검색, 벌칙류 아닌 상위 조문을 합류. 결정적·실패 시 빈 리스트.
    config `medlaw_act_context`(기본 true) 무재배포 토글."""
    try:
        if not load_config().get("medlaw_act_context", True):
            return []
    except Exception:  # noqa: BLE001
        pass
    q = query or ""
    if not any(t in q for t in _PENALTY_INTENT_TERMS):
        return []
    stripped = q
    for t in _PENALTY_INTENT_TERMS:
        stripped = stripped.replace(t, " ")
    stripped = _re.sub(r"[은는이가의를에]?\s*[?？]*\s*$", "", stripped.strip()).strip()
    if len(stripped) < 3:
        return []
    try:
        from client.rag import search as _search
        hits = _search(stripped, n_results=3, collection_name=MEDLAW_COLLECTION,
                       min_score=0.30)
        out = []
        for h in hits:
            if h.get("id") in exclude_ids:
                continue
            m = h.get("metadata") or {}
            if _is_penalty_chunk(h.get("text") or "", m.get("article_title") or ""):
                continue
            h2 = dict(h)
            h2["act_ctx"] = True
            out.append(h2)
            if len(out) >= cap:
                break
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[medical-law] act context skipped: {e}")
        return []


def _rag_law_search(query, top_k=5, min_score=0.4, notice_category=None, legal_min_score=None):
    """medical_law_knowledge(법령·고시) + 발간물 보조 컬렉션 벡터 검색 후 **권위 우선 병합**.
    - 본 코퍼스(법령·고시)와 보조(발간물)를 각각 질의.
    - tier penalty(발간물 -0.06 등)로 동등 관련도에서 법적 근거 우선.
    - 관련 법령·고시가 있으면 최대 2건은 인용에 보장(발간물이 전부 밀어내는 권위역전 방지).
    - **본 코퍼스 게이트 완화**(legal_min_score, 기본 min_score-0.07): 법령·고시·개정법률안은
      고권위라 얇은 의안(소수 청크)이 풍부한 발간물에 밀려 min_score 게이트에서 탈락하던 현상
      보정. 발간물(pub)은 min_score 유지. 잡음은 tier penalty·floor가 억제.
    notice_category 지정 시 category 필터(O5). 미가용 시 [] → SQLite 시드 폴백(회귀 0).
    보조 컬렉션 개별 실패는 격리."""
    try:
        from client.rag import search as _rag_search
        where = {"category": notice_category} if notice_category else None
        legal_gate = legal_min_score if legal_min_score is not None \
            else max(0.30, min_score - 0.07)
        # 과다 인출 후 dedup(아래) → top_k 절단. 본 코퍼스는 동일 원문이 여러 고시
        # 판본에 걸쳐 색인돼 있어(실측 2026-07: 중복 3,013슬롯/12,704, top-8의 15%가
        # 중복·최대 5/8) top_k만 뽑으면 dedup 후 법령 슬롯이 줄고 그 자리를 발간물이
        # 메워 권위가 낮아진다. 코퍼스는 판본 출처 보존을 위해 건드리지 않는다.
        legal = _rag_search(query, n_results=top_k * _OVERFETCH,
                            collection_name=MEDLAW_COLLECTION,
                            min_score=legal_gate, where=where)
        # rank-기반 법령 폴백: 자연어 질문은 법령 원문과 절대 유사도가 낮게 나와(실측:
        # "사본 발급 거부할 수 있는가" → 제21조가 rank-2인데 0.234로 게이트 0.33 탈락)
        # 정답 조문이 잘림. 법령 코퍼스는 전권위 큐레이션이라 게이트 미달이어도 상위
        # 랭크가 최선의 법적 근거 → 게이트 통과 2건 미만이면 top을 floor 0.20으로 보강.
        if len(legal) < 2:
            try:
                relaxed = _rag_search(query, n_results=top_k, collection_name=MEDLAW_COLLECTION,
                                      min_score=0.20, where=where)
                seen = {h.get("id") for h in legal}
                legal += [h for h in relaxed if h.get("id") not in seen][:2 - len(legal)]
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[medical-law] legal rank-fallback skipped: {e}")
        # 결정적 질의 확장 병합(본 코퍼스만): 원 질의가 용어 충돌로 정답 조문을 놓치는
        # 클래스(_DEFAULT_QUERY_EXPANSIONS 참조) 보정. 확장 hit의 score는 확장 질의
        # 기준이라 원 질의 hit보다 후순위로 덧붙이되, 병합 정렬(_adj)에서 자체 점수로
        # 상위 진입 — top_k 내 포함이 목적(전 청크가 프롬프트에 들어가므로 위치 비민감).
        for exp_q in _query_expansions(query):
            try:
                extra = _rag_search(exp_q, n_results=3, collection_name=MEDLAW_COLLECTION,
                                    min_score=legal_gate, where=where)
                seen = {h.get("id") for h in legal}
                legal += [h for h in extra if h.get("id") not in seen][:2]
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[medical-law] query expansion skipped ({exp_q}): {e}")
        pub = []
        for aux in MEDLAW_AUX_COLLECTIONS:
            try:
                pub += _rag_search(query, n_results=top_k, collection_name=aux,
                                   min_score=min_score, where=where)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[medical-law] aux collection {aux} unavailable: {e}")

        legal = _drop_form_chunks(legal)  # 법령: fail-safe 유지(근거 소실 방지)
        # 동일 원문 중복 제거 — 3단비교 파싱 중복(동일 조문 텍스트가 다른 id로 2회
        # 색인, 실측 의료법 제14조)이 top-K 슬롯을 이중 점유 → 첫 hit(고점수) 유지
        seen_texts = set()
        uniq = []
        for h in legal:
            t = (h.get("text") or "").strip()
            if t and t in seen_texts:
                continue
            seen_texts.add(t)
            uniq.append(h)
        legal = uniq[:top_k]        # 과다 인출분은 dedup 후 절단(슬롯 수 원상 복구)
        pub = [h for h in pub if not _is_form_chunk(h.get("text") or "")]  # 발간물: 서식 전량 제거 OK
        def _adj(h):  # tier-penalized score
            pen = _authority_of(h.get("metadata", {}).get("origin"))[1]
            return (h.get("score", 0) or 0) - pen

        merged = sorted(legal + pub, key=_adj, reverse=True)
        # 법적 근거 floor: 관련 법령·고시(본 코퍼스) 상위 최대 2건을 우선 포함 → 권위역전 방지
        floor = legal[:min(2, len(legal))]
        result = (floor + [h for h in merged if h not in floor])[:top_k]
        # 벌칙 역참조 보강(결정적): 처벌 의도 질의면 검색 조문을 역참조하는 벌칙류
        # 조문을 테일 슬롯에 보장 합류(floor 불변·top_k 유지)
        try:
            backref = _penalty_backref_chunks(query, legal)
            extra = [b for b in backref
                     if b.get("id") not in {r.get("id") for r in result}]
            if extra:
                keep = max(len(floor), top_k - len(extra))
                result = (result[:keep] + extra)[:max(top_k, len(floor))]
        except Exception as e:  # noqa: BLE001 — 보강 실패는 기존 결과로 강등
            logger.debug(f"[medical-law] penalty backref merge skipped: {e}")
        # 행위 조문 보강(결정적): 처벌 의도 질의면 벌칙 어휘 제거 질의로 행위 본문
        # 조문을 테일 합류(최대 2·기존 결과 불변 append) — 벌칙 주석의 조·항 번호를
        # 항 단위로 대조할 본문 재료 제공(벌칙 역참조의 대칭 방향)
        try:
            act = _act_context_chunks(query, {r.get("id") for r in result})
            if act:
                result = result + act
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[medical-law] act context merge skipped: {e}")
        # 무관·항 불일치 벌칙 근거 제외(결정적) — 행위 조문이 합류한 **뒤**에 판정해야
        # 가릴 재료(행위 조문 본문·조번호)가 갖춰진다. 실패는 원본 유지.
        # 순서: 조 단위(아예 처벌하지 않는 것) → 항 단위(같은 조의 다른 항만 처벌하는 것)
        try:
            result = _drop_unrelated_penalty(result)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[medical-law] unrelated penalty filter skipped: {e}")
        try:
            result = _drop_paragraph_mismatch(result, query)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[medical-law] paragraph mismatch filter skipped: {e}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[medical-law] rag search unavailable: {e}")
        return []


def _rag_citations(hits):
    """RAG hit → 근거인용 메타(응답용).
    law3dan(법령): article_number/title. khidi_notice(고시): doc_no를 식별자로 사용(조문필드 없음)."""
    out = []
    for h in hits:
        m = h.get("metadata", {})
        doc_no = m.get("doc_no", "")
        out.append({
            "law_name": m.get("law_name"),
            "law_level": m.get("law_level"),
            "article": m.get("article_number") or doc_no,  # 고시는 고시번호로 대체
            "title": m.get("article_title", ""),
            "doc_no": doc_no,
            "category": m.get("category", ""),
            "origin": m.get("origin", ""),
            "doc_type": m.get("doc_type", ""),  # 발간물(hira_pub) 종류 식별
            "authority": _authority_of(m.get("origin", ""))[0],  # 법령>고시>발간물 위계
            "effective_date": m.get("effective_date", ""),
            "score": h.get("score"),
            "snippet": (h.get("text") or "")[:200],
        })
    return out


def _ask_assemble(data: dict) -> dict:
    """/ask·/ask/stream 공용 — 시드 조문 + RAG 검색 + 컨텍스트/근거 조립."""
    question = data.get("question", "").strip()
    category = data.get("category", "")
    use_rag = data.get("use_rag", True)
    top_k = _safe_top_k(data.get("top_k"), default=5)

    # (1) 시드(SQLite) — 손 시드 조문 + 구조화 벌칙 메타
    conn = _get_db()
    if category:
        articles = conn.execute(
            "SELECT law_name, article_number, article_title, content, penalty FROM law_articles WHERE category=? LIMIT 5",
            (category,)).fetchall()
    else:
        articles = conn.execute(
            "SELECT law_name, article_number, article_title, content, penalty FROM law_articles "
            "WHERE content LIKE ? ESCAPE '\\' LIMIT 5",
            (_like(question[:20]),)).fetchall()

    # (2) RAG 벡터검색(medical_law_knowledge) — 미가용 시 [] (시드 폴백, 회귀 0)
    rag_hits = _rag_law_search(question, top_k=top_k,
                               notice_category=(data.get("notice_category") or None)) if use_rag else []

    context = ""
    if articles:
        context += "\n\n[관련 법률 조문(요약)]\n"
        for a in articles:
            context += f"\n{a['law_name']} {a['article_number']} ({a['article_title']}): {a['content']}"
            if a["penalty"]:
                context += f"\n  → 벌칙: {a['penalty']}"
    if rag_hits:
        context += ("\n\n[관련 법령 본문(검색)]\n"
                    "※ 근거 위계: 법령 > 고시 > 발간물(편람·가이드맵·사례집=실무참고, 법적 효력은 법령·고시 우선). "
                    "각 근거의 [권위] 라벨을 고려해 답하라.\n")
        cap = _chunk_cap()
        # 캡 절단 마커 — 무표시 절단이면 모델이 잘린 사실을 모른 채 후속 호·항을 기억으로
        # 완성한다(실증 eval id253: 제21조 4,091자 중 캡 밖 호를 서술). 잘림을 명시해
        # 규칙 5("보이지 않는 호는 확인되지 않음으로 표기")가 작동할 재료를 준다.
        _CUT_MARK = ("\n  …(근거 절단 — 이 조문 원문은 여기서 잘렸고 이후 내용이 더 있음. "
                     "잘린 부분을 기억으로 보완하지 말 것)")
        for h in rag_hits:
            m = h.get("metadata", {})
            origin = m.get("origin", "")
            auth = _authority_of(origin)[0]
            if origin == "hira_pub":
                # 발간물(법령집 등)의 law_name은 문서 제목 — 법령명으로 오인·축약 인용
                # 방지(실증 eval id200: 「국민건강보험법령집」 수록 마약류관리법 제32조를
                # "국민건강보험법 제32조"로 오귀속) → 라벨에 성격 명시(규칙 7과 쌍)
                pub_body = h.get("text") or ""
                context += (f"\n[권위:{auth}] [발간물({m.get('doc_type','')}) "
                            f"「{m.get('law_name','')}」 — 문서 제목이며 법령명 아님]\n"
                            f"{pub_body[:cap]}"
                            f"{_CUT_MARK if len(pub_body) > cap else ''}\n")
            else:
                body = h.get("text") or ""
                context += (f"\n[권위:{auth}] [{m.get('law_name')} {m.get('law_level')} "
                            f"{m.get('article_number') or m.get('doc_no','')}({m.get('article_title','')})]\n"
                            f"{body[:cap]}"
                            f"{_CUT_MARK if len(body) > cap else ''}\n")
                # 벌칙류 청크 = 위반 조항 교차참조 주석(전문 기준=캡 무관, 결정적 추출,
                # 조번호→표제 병기로 질의 행위↔조번호 매핑 홉을 컨텍스트 안에서 닫음)
                if _is_penalty_chunk(body, m.get("article_title") or ""):
                    _focus = _xref_focus_articles(rag_hits)
                    context += _penalty_xref_note(
                        body, title_map=_article_title_map(m.get("law_name") or ""),
                        focus=_focus)
                    # 캡에 잘려 안 보이는 초점 호를 항 헤더(=형량)와 함께 되돌린다.
                    # 주석이 아니라 **원문 조각**이다 — v2.124.94가 기각한 형량 부기와 다르다.
                    context += _focus_ho_tail(body, cap, _focus)

    legal_basis = [{"law": a["law_name"], "article": a["article_number"],
                    "title": a["article_title"], "penalty": a["penalty"]}
                   for a in articles] if articles else []
    return {"question": question, "category": category, "context": context,
            "rag_hits": rag_hits, "legal_basis": legal_basis}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@medical_law_bp.route("/api/medical-law/ask", methods=["POST"])
def api_ask():
    """의료법 질의응답 (RAG: 법령 3단비교 + KHIDI 고시 벡터검색 + 시드 병합, 근거인용)

    의료법 자문의 주 진입점. 근거는 세 갈래를 병합한다 — 법령 3단비교(법·시행령·시행규칙)
    + `medical_law_knowledge` 고시 벡터검색(`use_rag`) + 카테고리별 시드 조문(`category`).
    응답에는 인용 근거가 법령명 라벨과 함께 실린다.
    ⚠️ **자문 보조이며 법률 자문이 아니다** — 최종 판단은 사람이 한다.
    코퍼스 현황은 `GET /api/medical/catalog`의 `medical_law_knowledge` 항목에서 확인한다.
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string}
            category: {type: string, enum: [general, record, consent, emergency, infection, pharmacy], description: "시드 조문 필터"}
            use_rag: {type: boolean, default: true, description: "medical_law_knowledge 벡터검색 사용"}
            top_k: {type: integer, default: 5, description: "RAG 결과 수(≤10)"}
            notice_category: {type: string, enum: [신의료기술, 선별급여, 의료기기, 급여기준, 심사기준, infection, record], description: "고시 category 필터(선택)"}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        description: answer·legal_basis·citations(law_name/article/doc_no/category/score)·rag_used·disclaimer
        schema:
          type: object
          properties:
            answer: {type: string, description: "답변 본문"}
            legal_basis: {type: array, description: "시드 조문 근거"}
            citations: {type: array, description: "RAG 인용(법령명 라벨 포함)"}
            rag_used: {type: boolean, description: "벡터검색 근거 사용 여부"}
            model: {type: string}
            tokens: {type: integer}
            duration_ms: {type: integer}
            disclaimer: {type: string, description: "법률 자문이 아니라는 고지 — 화면에 반드시 노출할 것"}
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    category = data.get("category", "")
    model = data.get("model") or _default_model()
    use_rag = data.get("use_rag", True)
    top_k = _safe_top_k(data.get("top_k"), default=5)

    # 캐시 조회
    _ck = hashlib.md5(f"{question}|{category}|{model}|{use_rag}|{top_k}|{data.get('notice_category') or ''}".encode("utf-8", errors="replace")).hexdigest()
    _cached = _medical_law_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    asm = _ask_assemble(data)
    rag_hits, legal_basis = asm["rag_hits"], asm["legal_basis"]

    prompt = f"{question}{asm['context']}"
    response, tokens, duration_ms = _generate(model, prompt, _system_for(model))
    if _gen_failed(response):
        # 🔴 실패를 200으로 흘리지도, 캐시에 넣지도 않는다.
        # 넣으면 일과성 타임아웃 한 번이 TTL 1시간 동안 그 질문을 고정 오답으로 만든다
        # (라이브 실측: 스모크 3/3이 0.006초 캐시 히트로 같은 에러를 반환).
        return jsonify({"error": "generation_failed",
                        "message": "모델 생성에 실패했습니다. 잠시 후 다시 시도하세요.",
                        "citations": _rag_citations(rag_hits),
                        "rag_used": bool(rag_hits), "model": model}), 502
    # 근거에 있으나 답변이 빠뜨린 한정 단서를 원문 그대로 부기(폐루프 16)
    response = _append_clause_note(response, _evidence_text(rag_hits))

    # 이력 저장
    conn = _get_db()
    conn.execute("INSERT INTO law_queries (question, answer, category, model, tokens, duration_ms) VALUES (?,?,?,?,?,?)",
                 (question, response, category, model, tokens, duration_ms))
    conn.commit()

    resp = {
        "answer": response, "legal_basis": legal_basis,
        "citations": _rag_citations(rag_hits), "rag_used": bool(rag_hits),
        "model": model, "tokens": tokens, "duration_ms": duration_ms,
        "disclaimer": "이 정보는 법률 정보 제공이며, 법적 조언이 아닙니다. 구체적 사안은 법률 전문가와 상담하세요.",
    }
    try:
        if response and len(response) >= 50 and not response.startswith("Error:"):
            qr = compute_korean_quality(response, genre="medical")
            resp["quality_metrics"] = get_quality_summary(qr)
    except Exception:
        pass
    _medical_law_cache.set(_ck, resp)
    return jsonify(resp)


@medical_law_bp.route("/api/medical-law/ask/stream", methods=["POST"])
def api_ask_stream():
    """의료법 질의응답 NDJSON 스트리밍 — /ask와 동일 RAG, 토큰 단위 응답
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string}
            category: {type: string, enum: [general, record, consent, emergency, infection, pharmacy]}
            use_rag: {type: boolean, default: true}
            top_k: {type: integer, default: 5}
            notice_category: {type: string}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        description: "NDJSON 스트림 — {event:meta,citations,legal_basis,rag_used} → {event:token,token} × N → {event:done,answer,tokens,duration_ms,disclaimer}"
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    model = data.get("model") or _default_model()

    # RAG 조립은 스트림 시작 전(여기서 실패 시 HTTP 에러로 반환 가능)
    asm = _ask_assemble(data)

    def generate():
        t0 = time.time()
        try:
            yield json.dumps({"event": "meta", "legal_basis": asm["legal_basis"],
                              "citations": _rag_citations(asm["rag_hits"]),
                              "rag_used": bool(asm["rag_hits"]), "model": model},
                             ensure_ascii=False) + "\n"

            full, tokens = "", 0
            for tok, done, n in _stream_tokens(model, f"{question}{asm['context']}",
                                               _system_for(model)):
                if tok:
                    full += tok
                    yield json.dumps({"event": "token", "token": tok},
                                     ensure_ascii=False) + "\n"
                if done:
                    tokens = n

            answer = force_korean(full)
            # 단서 부기(폐루프 16) — 부기분은 토큰으로도 흘려보낸다. 안 그러면
            # 토큰을 이어붙인 클라이언트 텍스트와 done.answer가 어긋난다.
            with_note = _append_clause_note(answer, _evidence_text(asm["rag_hits"]))
            if with_note != answer:
                yield json.dumps({"event": "token", "token": with_note[len(answer):]},
                                 ensure_ascii=False) + "\n"
                answer = with_note
            duration_ms = int((time.time() - t0) * 1000)
            conn = _get_db()
            conn.execute("INSERT INTO law_queries (question, answer, category, model, tokens, duration_ms) VALUES (?,?,?,?,?,?)",
                         (question, answer, asm["category"], model, tokens, duration_ms))
            conn.commit()
            yield json.dumps({"event": "done", "answer": answer, "tokens": tokens,
                              "duration_ms": duration_ms,
                              "disclaimer": "이 정보는 법률 정보 제공이며, 법적 조언이 아닙니다. 구체적 사안은 법률 전문가와 상담하세요."},
                             ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            logger.error(f"[medical-law] ask/stream error: {e}")
            yield json.dumps({"event": "error", "message": str(e)[:200]}, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_REVIEW_DISCLAIMER = ("이 평가는 법률 정보 제공이며 법적 조언이 아닙니다. 비식별 정보 기반 자동 분석으로, "
                      "구체적 사안은 법률 전문가의 검토가 필요합니다.")


def _risk_level(score: float) -> str:
    if score >= 0.70:
        return "HIGH"
    if score >= 0.40:
        return "MEDIUM"
    return "LOW"


@medical_law_bp.route("/api/medical-law/review", methods=["POST"])
def api_review():
    """의료기관 행위 의료법령 이슈 체크 (위험밴드 + 근거인용 강제)

    의료기관의 특정 행위를 입력하면 관련 의료법령 이슈를 점검해 **위험밴드**와 함께 낸다.
    근거 인용이 강제되므로 응답에는 판단의 출처 조문이 함께 실린다.
    ⚠️ 자문 보조이며 법률 자문이 아니다.
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [action]
          properties:
            action: {type: string, description: "평가할 의료기관 행위(비식별 서술)"}
            context: {type: object, description: "행위 맥락(facility_type/department/date 등)"}
            notice_category: {type: string, description: "고시 category 필터(선택, O5)"}
            model: {type: string, default: "qwen2.5:14b"}
            top_k: {type: integer, default: 6}
    responses:
      200:
        description: risk_level(HIGH≥0.70/MED/LOW)·risk_score·has_evidence·issues·evidence_citations·recommendation·disclaimer. 근거없으면 위험상향금지(환각방지)
        schema:
          type: object
          properties:
            action: {}
            risk_level: {type: string}
            risk_score: {type: number}
            has_evidence: {type: boolean}
            issues: {type: array}
            evidence_citations: {type: array}
            recommendation: {type: string}
            reasoning: {type: string}
            model: {}
            tokens: {}
            duration_ms: {}
            disclaimer: {}
    """
    data = request.json or {}
    action = (data.get("action") or "").strip()
    if not action:
        return jsonify({"error": "action is required"}), 400

    ctx = data.get("context") or {}
    model = data.get("model", "qwen2.5:14b")
    top_k = _safe_top_k(data.get("top_k"), default=6)

    # 행위 → 관련 법령 RAG 검색 (notice_category 선택 필터, O5)
    hits = _rag_law_search(action, top_k=top_k, min_score=0.35,
                           notice_category=(data.get("notice_category") or None))
    if not hits:
        return jsonify({
            "action": action, "risk_level": "LOW", "risk_score": 0.0,
            "has_evidence": False, "issues": [], "evidence_citations": [],
            "recommendation": "관련 법령 근거를 찾지 못했습니다. 법률 전문가 검토를 권장합니다.",
            "reasoning": "근거 미발견 — 환각 방지를 위해 위험도를 상향하지 않습니다.",
            "disclaimer": _REVIEW_DISCLAIMER,
        })

    ctx_str = ""
    if ctx:
        ctx_str = "\n[행위 맥락] " + ", ".join(f"{k}={v}" for k, v in ctx.items() if v)
    ev = ""
    for i, h in enumerate(hits):
        m = h.get("metadata", {})
        ev += (f"\n[{i + 1}] {m.get('law_name')} {m.get('law_level')} "
               f"{m.get('article_number')}({m.get('article_title', '')}): {(h.get('text') or '')[:500]}")

    prompt = f"""다음 의료기관 행위가 의료법령상 이슈가 되는지 평가하세요.

[행위]
{action}{ctx_str}

[참조 법령(검색된 근거)]{ev}

위 참조 법령만 인용하여 평가하고 JSON으로만 출력:
{{
  "risk_score": 0.0~1.0 사이 숫자,
  "issues": [{{"law": "법령명 조문번호", "issue": "문제점", "severity": "high|medium|low"}}],
  "recommendation": "권고 사항",
  "reasoning": "판단 근거",
  "cited": [인용한 참조번호]
}}
근거가 불충분하면 risk_score를 낮추고 cited를 비우세요. JSON만 출력."""

    system = ("당신은 한국 의료법령 컴플라이언스 전문 AI입니다. 제공된 참조 법령만 인용하고 "
              "근거 없는 추측은 하지 마세요. 반드시 JSON으로만 답하세요.")
    out, tokens, duration_ms = _generate_json(model, prompt, system)

    if out.get("_error"):
        return jsonify({"error": "evaluation failed", "detail": out.get("_error")}), 502

    try:
        score = float(out.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))

    # 근거인용 강제(환각 방지): cited 인덱스 → 실제 hit 매핑
    citations = []
    for ci in (out.get("cited") or []):
        try:
            citations.append(_rag_citations([hits[int(ci) - 1]])[0])
        except (ValueError, IndexError, TypeError):
            continue
    has_evidence = bool(citations)
    if not has_evidence:
        score = min(score, 0.39)  # 근거 없으면 위험 상향 금지

    return jsonify({
        "action": action,
        "risk_level": _risk_level(score),
        "risk_score": round(score, 3),
        "has_evidence": has_evidence,
        "issues": out.get("issues", []),
        "evidence_citations": citations,
        "recommendation": out.get("recommendation", ""),
        "reasoning": out.get("reasoning", ""),
        "model": model, "tokens": tokens, "duration_ms": duration_ms,
        "disclaimer": _REVIEW_DISCLAIMER,
    })


_CORPUS_CACHE = {"ts": 0.0, "data": None}  # 60s TTL (전체 metadata 조회 비용 방어)


@medical_law_bp.route("/api/medical-law/corpus-status")
def api_corpus_status():
    """RAG 코퍼스 현황 — medical_law_knowledge 컬렉션 origin별 분포 + 증분 watermark
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
    """
    import time
    if _CORPUS_CACHE["data"] is not None and time.time() - _CORPUS_CACHE["ts"] < 60:
        return jsonify(_CORPUS_CACHE["data"])
    result = {"collection": MEDLAW_COLLECTION}
    try:
        from client.rag import get_collection
        col = get_collection(MEDLAW_COLLECTION)
        result["total_chunks"] = col.count()
        # 거리 공간 가드 — 본 컬렉션이 hnsw:space 미지정(L2 기본값)으로 생성되면
        # score=2cos−1로 압축돼 cosine 스케일인 발간물 컬렉션에 구조적으로 밀림
        # (2026-07-16 실측: 게이트 전멸→권위역전→근거 오염·조문 창작의 공통 뿌리)
        space = (col.metadata or {}).get("hnsw:space") or "l2(default)"
        result["distance_space"] = space
        if space != "cosine":
            result["space_warning"] = ("distance space가 cosine이 아님 — 법령 점수가 "
                                       "압축돼 게이트·권위 병합이 오작동. "
                                       "scripts/migrate_medlaw_cosine.py로 재구축 필요")
            logger.critical("[medical-law] %s distance space=%s (cosine 아님) — "
                            "검색 점수 스케일 왜곡", MEDLAW_COLLECTION, space)
        if result["total_chunks"]:
            from collections import Counter
            metas = col.get(include=["metadatas"]).get("metadatas", [])
            by_origin = Counter(m.get("origin") or "unknown" for m in metas)
            result["by_origin"] = dict(by_origin)
            result["distinct_sources"] = len({m.get("source_file") for m in metas})
            laws = {m.get("law_name") for m in metas if m.get("origin") == "law3dan"}
            result["law_documents"] = sorted(x for x in laws if x)
        # 보조 컬렉션(발간물) 현황 — 별도 컬렉션 분리 색인 가시화
        aux = {}
        for name in MEDLAW_AUX_COLLECTIONS:
            try:
                aux[name] = get_collection(name).count()
            except Exception:  # noqa: BLE001
                aux[name] = 0
        result["aux_collections"] = aux
        result["available"] = True
    except Exception as e:  # noqa: BLE001
        result["available"] = False
        result["error"] = str(e)
    try:
        from core.medlaw_processor import _load_state
        result["khidi_watermark"] = _load_state()
    except Exception:  # noqa: BLE001
        pass
    _CORPUS_CACHE["ts"] = time.time()
    _CORPUS_CACHE["data"] = result
    return jsonify(result)


@medical_law_bp.route("/api/medical-law/search", methods=["POST"])
def api_search():
    """법률 조문 검색
    ---
    tags: [Medical Law]
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
            limit: {type: integer, default: 10}
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            results: {type: array}
            count: {type: integer}
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    category = data.get("category")
    law_name = data.get("law_name")
    limit = min(int(data.get("limit", 10)), 50)

    conn = _get_db()
    where = "1=1"
    params = []
    if query:
        where += (" AND (content LIKE ? ESCAPE '\\' "
                  "OR article_title LIKE ? ESCAPE '\\')")
        params.extend([_like(query), _like(query)])
    if category:
        where += " AND category=?"
        params.append(category)
    if law_name:
        where += " AND law_name LIKE ? ESCAPE '\\'"
        params.append(_like(law_name))

    rows = conn.execute(
        f"SELECT * FROM law_articles WHERE {where} ORDER BY law_name, article_number LIMIT ?",
        params + [limit]).fetchall()

    return jsonify({"results": [dict(r) for r in rows], "count": len(rows)})


@medical_law_bp.route("/api/medical-law/sources")
def api_sources():
    """수집된 법률 목록
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            sources: {type: array}
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT law_name, COUNT(*) as article_count FROM law_articles GROUP BY law_name ORDER BY article_count DESC"
    ).fetchall()
    return jsonify({"sources": [dict(r) for r in rows]})


@medical_law_bp.route("/api/medical-law/stats")
def api_stats():
    """의료법 DB 통계
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            total_articles: {}
            by_category: {type: object}
            reportable_diseases: {}
            total_queries: {}
    """
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM law_articles").fetchone()[0]
    by_cat = conn.execute("SELECT category, COUNT(*) as cnt FROM law_articles GROUP BY category").fetchall()
    diseases = conn.execute("SELECT COUNT(*) FROM reportable_diseases").fetchone()[0]
    queries = conn.execute("SELECT COUNT(*) FROM law_queries").fetchone()[0]

    return jsonify({
        "total_articles": total,
        "by_category": {r["category"]: r["cnt"] for r in by_cat},
        "reportable_diseases": diseases,
        "total_queries": queries,
    })


@medical_law_bp.route("/api/medical-law/check/report", methods=["POST"])
def api_check_report():
    """감염병 신고 의무 확인
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            diagnosis: {type: string, description: "진단명"}
            icd_code: {type: string, description: "ICD-10 코드"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "diagnosis or icd_code required"}}
    """
    data = request.json or {}
    diagnosis = data.get("diagnosis", "").strip()
    icd_code = data.get("icd_code", "").strip().upper()

    if not diagnosis and not icd_code:
        return jsonify({"error": "diagnosis or icd_code required"}), 400

    conn = _get_db()
    result = None

    if icd_code:
        # ICD prefix 매칭
        prefix = icd_code.split(".")[0]
        rows = conn.execute("SELECT * FROM reportable_diseases").fetchall()
        for r in rows:
            codes = r["icd_codes"].split(",")
            if icd_code in codes or prefix in codes:
                result = dict(r)
                break

    if not result and diagnosis:
        row = conn.execute(
            "SELECT * FROM reportable_diseases WHERE disease_name LIKE ? ESCAPE '\\' "
            "OR disease_name_en LIKE ? ESCAPE '\\'",
            (_like(diagnosis), _like(diagnosis))).fetchone()
        if row:
            result = dict(row)

    if result:
        # 관련 법률 조문
        law_article = conn.execute(
            "SELECT * FROM law_articles WHERE category='infection' LIMIT 1").fetchone()

        return jsonify({
            "reportable": True,
            "disease": result["disease_name"],
            "disease_en": result["disease_name_en"],
            "class": result["class"],
            "report_deadline": result["report_deadline"],
            "report_to": result["report_to"],
            "icd_codes": result["icd_codes"],
            "law": "감염병의 예방 및 관리에 관한 법률 제11조",
            "penalty": "500만원 이하 벌금 (감염병예방법 제79조의4)",
        })

    return jsonify({
        "reportable": False,
        "message": f"'{diagnosis or icd_code}'는 현재 등록된 신고 대상 감염병에 해당하지 않습니다.",
        "note": "최신 감염병 목록은 질병관리청(kdca.go.kr) 확인 필요",
    })


@medical_law_bp.route("/api/medical-law/check/record", methods=["POST"])
def api_check_record():
    """의무기록 법적 적합성 검사
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [record]
          properties:
            record: {type: string, description: "의무기록 텍스트 (SOAP 등)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "record is required"}}
    """
    data = request.json or {}
    record = data.get("record", "").strip()
    if not record:
        return jsonify({"error": "record is required"}), 400

    # 의료법 제22조 기재사항 검사
    checklist = {
        "patient_info": any(k in record for k in ["나이", "연령", "성별", "age", "sex", "세 "]),
        "diagnosis": any(k in record for k in ["진단", "Assessment", "assessment", "A:", "진단명"]),
        "treatment_plan": any(k in record for k in ["계획", "Plan", "plan", "P:", "처방", "처치"]),
        "subjective": any(k in record for k in ["주관", "Subjective", "subjective", "S:", "호소", "증상"]),
        "objective": any(k in record for k in ["객관", "Objective", "objective", "O:", "검사", "소견", "V/S"]),
        "timestamp": any(k in record for k in ["날짜", "일시", "date", "202"]),
    }

    issues = []
    if not checklist["patient_info"]:
        issues.append({"severity": "warning", "law": "의료법 제22조",
                        "issue": "환자 기본 정보(나이/성별) 미기재", "field": "patient_info"})
    if not checklist["diagnosis"]:
        issues.append({"severity": "error", "law": "의료법 제22조",
                        "issue": "진단명 미기재", "field": "diagnosis"})
    if not checklist["treatment_plan"]:
        issues.append({"severity": "warning", "law": "의료법 제22조",
                        "issue": "치료 계획 미기재", "field": "treatment_plan"})

    return jsonify({
        "compliant": len(issues) == 0,
        "issues": issues,
        "checklist": checklist,
        "retention_period": "10년 (의료법 시행규칙 제15조)",
        "law_reference": "의료법 제22조 — 진료기록부 기재사항",
    })


# ---------------------------------------------------------------------------
# Phase 2: 동의서/개인정보/인폼드컨센트/보관/광고/과태료
# ---------------------------------------------------------------------------

@medical_law_bp.route("/api/medical-law/check/consent", methods=["POST"])
def api_check_consent():
    """동의서 요건 확인
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [procedure]
          properties:
            procedure: {type: string, description: "시술/수술명"}
            patient_age: {type: integer}
            is_emergency: {type: boolean, default: false}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    procedure = data.get("procedure", "").strip()
    if not procedure:
        return jsonify({"error": "procedure required"}), 400

    is_emergency = data.get("is_emergency", False)
    patient_age = data.get("patient_age")

    # 서면동의 필요 여부 판단
    consent_required_keywords = ["수술", "마취", "전신마취", "수혈", "시술",
                                  "내시경", "생검", "조직검사", "절개", "절제",
                                  "조영제", "침습", "카테터"]
    needs_written = any(k in procedure for k in consent_required_keywords)

    result = {
        "procedure": procedure,
        "written_consent_required": needs_written,
        "law": "의료법 제24조의2",
        "required_explanations": [
            "질병의 증상 및 진단명",
            "수술 등의 필요성, 방법 및 내용",
            "수술 등에 따라 전형적으로 발생이 예상되는 위험이나 부작용",
            "전문의의 성명",
            "수술 전후 환자가 준수하여야 할 사항",
        ],
        "violation_penalty": "300만원 이하 과태료 (의료법 제24조의2 제3항)",
    }

    if is_emergency:
        result["emergency_exception"] = "응급환자의 경우 설명·동의 사후 가능 (응급의료법 제9조 제2항)"
        result["written_consent_required"] = False

    if patient_age and patient_age < 19:
        result["minor_note"] = "미성년자의 경우 법정대리인(보호자)의 동의 필수"

    return jsonify(result)


@medical_law_bp.route("/api/medical-law/check/privacy", methods=["POST"])
def api_check_privacy():
    """개인정보 처리 적법성 검사
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [action]
          properties:
            action: {type: string, description: "처리 행위 (수집/이용/제공/파기)"}
            data_type: {type: string, description: "데이터 유형 (진료기록/처방전/영상 등)"}
            purpose: {type: string, description: "처리 목적"}
            has_consent: {type: boolean, default: false}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    action = data.get("action", "").strip()
    data_type = data.get("data_type", "").strip()
    purpose = data.get("purpose", "").strip()
    has_consent = data.get("has_consent", False)

    if not action:
        return jsonify({"error": "action required"}), 400

    # 의료정보는 민감정보 → 별도 동의 필요
    is_sensitive = any(k in (data_type + purpose) for k in
                       ["진료", "처방", "영상", "검사", "진단", "의무기록", "건강", "질병"])

    issues = []
    laws = []

    if is_sensitive and not has_consent:
        issues.append({
            "severity": "error",
            "issue": "민감정보(건강/의료 정보) 처리 시 별도 동의 필요",
            "law": "개인정보보호법 제23조",
        })

    if action in ("제공", "이전", "공유"):
        issues.append({
            "severity": "warning",
            "issue": "제3자 제공 시 정보주체 동의 또는 법적 근거 필요",
            "law": "개인정보보호법 제17조",
        })
        laws.append({"law": "개인정보보호법", "article": "제17조", "title": "개인정보의 제공"})

    if action == "파기":
        laws.append({"law": "개인정보보호법", "article": "제21조", "title": "개인정보의 파기"})

    laws.append({"law": "의료법", "article": "제19조", "title": "정보 누설 금지",
                 "penalty": "3년 이하 징역 또는 1천만원 이하 벌금"})
    laws.append({"law": "의료법", "article": "제21조", "title": "기록 열람 등"})

    return jsonify({
        "lawful": len([i for i in issues if i["severity"] == "error"]) == 0,
        "is_sensitive_data": is_sensitive,
        "issues": issues,
        "applicable_laws": laws,
        "retention_note": "의무기록: 10년 보관 의무 (의료법 시행규칙 제15조) — 보관 기간 내 파기 금지",
    })


@medical_law_bp.route("/api/medical-law/check/emergency", methods=["POST"])
def api_check_emergency():
    """응급의료 의무 준수 확인
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [situation]
          properties:
            situation: {type: string, description: "응급 상황 설명"}
            actions_taken: {type: array, items: {type: string}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    situation = data.get("situation", "").strip()
    if not situation:
        return jsonify({"error": "situation required"}), 400

    actions = data.get("actions_taken", [])

    obligations = [
        {"duty": "응급의료 거부 금지", "law": "응급의료법 제6조",
         "penalty": "3년 이하 징역 또는 3천만원 이하 벌금"},
        {"duty": "응급의료 중단 금지", "law": "응급의료법 제10조",
         "penalty": "3년 이하 징역 또는 3천만원 이하 벌금"},
        {"duty": "설명·동의 (사후 가능)", "law": "응급의료법 제9조",
         "note": "의식 없는 환자 등은 사후 동의 허용"},
        {"duty": "이송 의무", "law": "응급의료법 제11조",
         "note": "자체 처치 불가 시 적절한 의료기관으로 이송"},
    ]

    # 키워드 기반 위반 가능성 분석
    issues = []
    refuse_kw = ["거부", "거절", "거부했", "돌려보", "진료 안"]
    if any(k in situation for k in refuse_kw):
        issues.append({"severity": "critical", "issue": "응급환자 진료 거부 의심",
                        "law": "응급의료법 제6조", "penalty": "3년 이하 징역 또는 3천만원 이하 벌금"})

    stop_kw = ["중단", "중지", "포기", "치료 안"]
    if any(k in situation for k in stop_kw):
        issues.append({"severity": "critical", "issue": "응급의료 중단 의심",
                        "law": "응급의료법 제10조"})

    return jsonify({
        "compliant": len(issues) == 0,
        "issues": issues,
        "obligations": obligations,
        "note": "응급의료종사자의 응급환자에 대한 의무를 요약한 것입니다.",
    })


@medical_law_bp.route("/api/medical-law/guide/informed-consent", methods=["POST"])
def api_guide_informed_consent():
    """인폼드 컨센트 가이드 (LLM)
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [procedure]
          properties:
            procedure: {type: string, description: "시술/수술명"}
            patient_age: {type: integer}
            risk_factors: {type: array, items: {type: string}}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    procedure = data.get("procedure", "").strip()
    if not procedure:
        return jsonify({"error": "procedure required"}), 400

    patient_age = data.get("patient_age")
    risk_factors = data.get("risk_factors", [])
    model = data.get("model", "qwen2.5:14b")

    risk_str = ", ".join(risk_factors) if risk_factors else "없음"

    prompt = f"""'{procedure}'에 대한 인폼드 컨센트(설명 의무) 가이드를 작성하세요.

환자 나이: {patient_age or '미상'}
위험요인: {risk_str}

다음 항목을 포함하세요:
1. 시술/수술의 필요성과 방법
2. 전형적으로 예상되는 위험과 부작용 (5개 이상)
3. 이 환자의 위험요인에 따른 추가 위험
4. 대안 치료법
5. 시술 전후 주의사항

JSON으로 출력:
{{
  "procedure": "{procedure}",
  "typical_risks": ["위험1", "위험2"],
  "patient_specific_risks": ["환자별 위험"],
  "alternatives": ["대안1"],
  "pre_procedure": ["전 주의사항"],
  "post_procedure": ["후 주의사항"]
}}

JSON만 출력."""

    system = "의료법 전문가이자 임상 전문가입니다. 인폼드 컨센트에 필요한 정보를 의료법 제24조의2에 따라 작성하세요. 한국어로 작성."

    response, tokens, duration_ms = _generate(model, prompt, system, {"temperature": 0.3, "num_predict": 1200})

    # JSON 파싱
    result = {}
    try:
        clean = response.strip()
        if "```" in clean:
            clean = clean.split("```")[1].strip()
            if clean.startswith("json"):
                clean = clean[4:].strip()
        result = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        result = {"raw_guide": response}

    result["legal_requirement"] = "의료법 제24조의2 — 수술 등의 설명 의무"
    result["required_explanations"] = [
        "질병의 증상 및 진단명",
        "수술 등의 필요성, 방법 및 내용",
        "전형적으로 발생이 예상되는 위험이나 부작용",
        "전문의의 성명",
        "수술 전후 환자가 준수하여야 할 사항",
    ]
    result["violation_penalty"] = "300만원 이하 과태료"
    result["metadata"] = {"tokens": tokens, "duration_ms": duration_ms, "model": model}

    return jsonify(result)


@medical_law_bp.route("/api/medical-law/guide/retention", methods=["POST"])
def api_guide_retention():
    """기록 보관 기간 안내
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            record_type: {type: string, description: "기록 유형 (진료기록부/처방전/검사기록 등)"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    record_type = data.get("record_type", "").strip()

    retention_rules = [
        {"type": "진료기록부", "period": "10년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "조산기록부", "period": "5년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "간호기록부", "period": "5년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "처방전", "period": "2년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "수술기록", "period": "10년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "검사 소견 기록", "period": "5년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "방사선 사진 및 소견서", "period": "5년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "진단서 등의 부본", "period": "3년", "law": "의료법 시행규칙 제15조 제1항"},
        {"type": "전자의무기록", "period": "원본과 동일 (10년/5년)", "law": "의료법 제22조의2"},
        {"type": "동의서", "period": "10년 (수술 기록과 동일)", "law": "의료법 제24조의2"},
    ]

    if record_type:
        matched = [r for r in retention_rules if record_type in r["type"] or r["type"] in record_type]
        if matched:
            return jsonify({"record_type": record_type, "rules": matched})

    return jsonify({
        "record_type": record_type or "(전체)",
        "rules": retention_rules,
        "note": "의료법 시행규칙 제15조에 따른 보관 기간. 전자의무기록도 동일 적용.",
    })


@medical_law_bp.route("/api/medical-law/guide/advertisement", methods=["POST"])
def api_guide_advertisement():
    """의료 광고 적법성 검토 (LLM)
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [ad_content]
          properties:
            ad_content: {type: string, description: "광고 문구"}
            ad_medium: {type: string, description: "매체 (인터넷/SNS/TV/옥외)"}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    ad_content = data.get("ad_content", "").strip()
    if not ad_content:
        return jsonify({"error": "ad_content required"}), 400

    model = data.get("model", "qwen2.5:14b")
    ad_medium = data.get("ad_medium", "인터넷")

    # 금지 키워드 검사 (의료법 제56조, 제57조)
    prohibited_keywords = {
        "치료 보장": "치료 결과 보장 표현 금지 (의료법 제56조 제2항 제3호)",
        "100%": "치료 효과 보장 표현 금지",
        "완치": "완치 보장은 허위·과대 광고",
        "최고": "비교·우수 표현 금지 (의료법 제56조 제2항 제2호)",
        "최초": "최초/유일 등 사실 검증 불가 표현",
        "무료": "진료비 할인·면제 광고 제한",
        "환자 체험기": "환자 경험담 활용 제한 (의료법 제56조 제2항 제5호)",
        "전후 사진": "수술 전후 사진 제한",
    }

    auto_issues = []
    for keyword, reason in prohibited_keywords.items():
        if keyword in ad_content:
            auto_issues.append({"keyword": keyword, "issue": reason, "severity": "error"})

    prompt = f"""의료 광고 문구의 적법성을 검토하세요.

[광고 문구]
{ad_content}

[매체] {ad_medium}

의료법 제56조(의료광고의 금지 등)에 따라 검토:
1. 허위·과대 광고 여부
2. 비교·우수 표현 여부
3. 치료 보장 표현 여부
4. 환자 체험기 활용 여부
5. 심의 필요 여부

JSON으로 출력:
{{
  "lawful": true/false,
  "issues": [{{"issue": "문제점", "severity": "error/warning", "law": "관련 조문"}}],
  "suggestions": ["수정 제안"]
}}

JSON만 출력."""

    response, tokens, duration_ms = _generate(model, prompt, MEDICAL_LAW_SYSTEM,
                                               {"temperature": 0.3, "num_predict": 800})

    llm_result = {}
    try:
        clean = response.strip()
        if "```" in clean:
            clean = clean.split("```")[1].strip()
            if clean.startswith("json"):
                clean = clean[4:].strip()
        llm_result = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        llm_result = {"raw_review": response}

    # 자동 검사 + LLM 결과 병합
    all_issues = auto_issues + llm_result.get("issues", [])

    return jsonify({
        "lawful": len([i for i in all_issues if i.get("severity") == "error"]) == 0,
        "issues": all_issues,
        "suggestions": llm_result.get("suggestions", []),
        "review_required": ad_medium in ("TV", "라디오", "신문") or "의료법인" in ad_content,
        "review_note": "TV/라디오/신문 광고는 사전 심의 필요 (의료법 제57조)",
        "metadata": {"tokens": tokens, "duration_ms": duration_ms},
    })


@medical_law_bp.route("/api/medical-law/calculate/penalty", methods=["POST"])
def api_calculate_penalty():
    """위반 시 벌칙/과태료 계산
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [violation]
          properties:
            violation: {type: string, description: "위반 내용"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    violation = data.get("violation", "").strip()
    if not violation:
        return jsonify({"error": "violation required"}), 400

    conn = _get_db()
    # 위반 내용에서 관련 조문 검색
    articles = conn.execute(
        "SELECT law_name, article_number, article_title, content, penalty "
        "FROM law_articles WHERE penalty IS NOT NULL AND "
        "(content LIKE ? ESCAPE '\\' OR article_title LIKE ? ESCAPE '\\') LIMIT 5",
        (_like(violation[:15]), _like(violation[:15]))
    ).fetchall()

    if not articles:
        # 전체 벌칙 조문 조회
        articles = conn.execute(
            "SELECT law_name, article_number, article_title, content, penalty "
            "FROM law_articles WHERE penalty IS NOT NULL ORDER BY law_name LIMIT 10"
        ).fetchall()

    penalties = []
    for a in articles:
        penalties.append({
            "law": a["law_name"],
            "article": a["article_number"],
            "title": a["article_title"],
            "penalty": a["penalty"],
        })

    return jsonify({
        "violation": violation,
        "applicable_penalties": penalties,
        "note": "실제 양형은 위반의 정도, 고의성, 피해 규모 등에 따라 달라집니다.",
    })


# ---------------------------------------------------------------------------
# Phase 2: 추론체인 자동 연동
# ---------------------------------------------------------------------------

@medical_law_bp.route("/api/medical-law/auto-check", methods=["POST"])
def api_auto_check():
    """추론체인/자동의무기록 결과 통합 법적 검사
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            record: {type: string, description: "의무기록 (SOAP 등)"}
            diagnoses: {type: array, items: {type: object}, description: "감별진단 목록"}
            prescriptions: {type: array, items: {type: string}, description: "처방 약물"}
            procedures: {type: array, items: {type: string}, description: "시술/수술"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    record = data.get("record", "")
    diagnoses = data.get("diagnoses", [])
    prescriptions = data.get("prescriptions", [])
    procedures = data.get("procedures", [])

    results = {"checks": [], "overall_compliant": True}

    # 1. 의무기록 기재사항 검사
    if record:
        checklist = {
            "patient_info": any(k in record for k in ["나이", "연령", "성별", "age", "sex", "세 "]),
            "diagnosis": any(k in record for k in ["진단", "Assessment", "A:", "진단명"]),
            "treatment_plan": any(k in record for k in ["계획", "Plan", "P:", "처방"]),
            "subjective": any(k in record for k in ["주관", "Subjective", "S:", "호소"]),
            "objective": any(k in record for k in ["객관", "Objective", "O:", "검사", "V/S"]),
        }
        record_issues = []
        if not checklist["diagnosis"]:
            record_issues.append("진단명 미기재 (의료법 제22조)")
        if not checklist["patient_info"]:
            record_issues.append("환자 기본 정보 미기재")
        if not checklist["treatment_plan"]:
            record_issues.append("치료 계획 미기재")

        results["checks"].append({
            "type": "record_compliance",
            "law": "의료법 제22조",
            "compliant": len(record_issues) == 0,
            "issues": record_issues,
            "checklist": checklist,
        })
        if record_issues:
            results["overall_compliant"] = False

    # 2. 감염병 신고 의무 검사
    if diagnoses:
        conn = _get_db()
        reportable_found = []
        for dx in diagnoses:
            icd = dx.get("icd10", dx.get("icd_code", "")).upper()
            name = dx.get("diagnosis_ko", dx.get("diagnosis", ""))
            if icd:
                prefix = icd.split(".")[0]
                rows = conn.execute("SELECT * FROM reportable_diseases").fetchall()
                for r in rows:
                    codes = r["icd_codes"].split(",")
                    if icd in codes or prefix in codes:
                        reportable_found.append({
                            "diagnosis": name,
                            "icd": icd,
                            "disease": r["disease_name"],
                            "class": r["class"],
                            "deadline": r["report_deadline"],
                        })

        if reportable_found:
            results["checks"].append({
                "type": "reportable_disease",
                "law": "감염병예방법 제11조",
                "compliant": False,  # 신고 필요 → action needed
                "action_required": True,
                "diseases": reportable_found,
                "penalty": "500만원 이하 벌금",
            })

    # 3. 시술/수술 동의서 확인
    if procedures:
        consent_needed = []
        consent_keywords = ["수술", "마취", "전신마취", "수혈", "내시경", "생검", "절제", "절개"]
        for proc in procedures:
            if any(k in proc for k in consent_keywords):
                consent_needed.append(proc)

        if consent_needed:
            results["checks"].append({
                "type": "informed_consent",
                "law": "의료법 제24조의2",
                "compliant": False,  # 동의서 필요 → 확인 필요
                "action_required": True,
                "procedures_requiring_consent": consent_needed,
                "penalty": "300만원 이하 과태료",
            })

    return jsonify(results)


@medical_law_bp.route("/api/medical-law/history")
def api_history():
    """상담 이력
    ---
    tags: [Medical Law]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: limit, type: integer, default: 20}
      - {in: query, name: category, type: string}
    responses:
      200:
        schema:
          type: object
    """
    limit = min(int(request.args.get("limit", 20)), 100)
    category = request.args.get("category")

    conn = _get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM law_queries WHERE category=? ORDER BY created_at DESC LIMIT ?",
            (category, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM law_queries ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    return jsonify({"queries": [dict(r) for r in rows], "count": len(rows)})
