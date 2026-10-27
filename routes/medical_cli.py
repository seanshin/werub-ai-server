"""Medical CLI Blueprint — 병원 역할별 AI CLI 업무 자동화"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, date

import requests
from flask import Blueprint, request, jsonify

logger = logging.getLogger("ollama-admin")

medical_cli_bp = Blueprint("medical_cli", __name__)


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프 (SQL injection 방지)"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

_ollama_session = None
_OLLAMA_BASE = None
_BASE_DIR = None
_resolve_medical_tenant = None

_cli_db_local = threading.local()


def init_medical_cli(*, ollama_session, ollama_base, base_dir, resolve_tenant):
    global _ollama_session, _OLLAMA_BASE, _BASE_DIR, _resolve_medical_tenant
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    _resolve_medical_tenant = resolve_tenant


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_cli_db():
    conn = getattr(_cli_db_local, "conn", None)
    if conn is None:
        db_path = os.path.join(_BASE_DIR, "clinic_codes.db")
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _init_cli_schema(conn)
        _cli_db_local.conn = conn
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except Exception as e:
            logger.debug("cli DB close failed: %s", e)
        _cli_db_local.conn = None
        return _get_cli_db()
    return conn


def _init_cli_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kcd_codes (
            code TEXT PRIMARY KEY,
            name_ko TEXT NOT NULL,
            name_en TEXT,
            category TEXT,
            chapter TEXT,
            is_common INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS edi_codes (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            price INTEGER,
            insurance_type TEXT DEFAULT 'covered',
            copay_rate REAL DEFAULT 0.3,
            effective_date TEXT,
            metadata TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS departments (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_full TEXT,
            consultation_fee_new INTEGER,
            consultation_fee_return INTEGER
        );
        CREATE TABLE IF NOT EXISTS dx_order_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kcd_code TEXT,
            edi_codes TEXT DEFAULT '[]',
            drug_codes TEXT DEFAULT '[]',
            lab_codes TEXT DEFAULT '[]',
            description TEXT,
            frequency INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_kcd_name ON kcd_codes(name_ko);
        CREATE INDEX IF NOT EXISTS idx_edi_name ON edi_codes(name);
        CREATE TABLE IF NOT EXISTS cli_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT,
            role TEXT,
            command TEXT,
            intent TEXT,
            result TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cli_tenant ON cli_history(tenant_id, created_at DESC);
    """)


# ---------------------------------------------------------------------------
# Role definitions + intent registry
# ---------------------------------------------------------------------------

ROLES = {
    "reception": {
        "name": "원무(접수)",
        "intents": {
            "patient_register": {"desc": "환자 접수", "patterns": ["접수", "초진", "재진", "등록"]},
            "patient_search": {"desc": "환자 검색", "patterns": ["찾아", "검색", "조회", "누구"]},
            "insurance_check": {"desc": "보험 확인", "patterns": ["보험", "자격", "건강보험", "의료급여"]},
            "appointment_list": {"desc": "예약 목록", "patterns": ["예약", "목록", "스케줄", "오늘"]},
            "visit_type_code": {"desc": "방문 유형 코드", "patterns": ["코드", "수가", "진찰료"]},
            "department_info": {"desc": "진료과 정보", "patterns": ["진료시간", "진료과", "몇시"]},
            "queue_status": {"desc": "대기 현황", "patterns": ["대기", "몇명", "몇번째", "현황"]},
            "symptom_to_dept": {"desc": "증상→진료과 추천", "patterns": ["아프", "아파", "증상", "통증", "어디", "뭐가", "어떤 과"]},
        }
    },
    "billing": {
        "name": "원무(수납)",
        "intents": {
            "calculate_bill": {"desc": "진료비 계산", "patterns": ["진료비", "계산", "얼마", "수납"]},
            "insurance_claim_code": {"desc": "청구 코드", "patterns": ["코드", "수가", "EDI"]},
            "procedure_cost": {"desc": "시술 비용", "patterns": ["비용", "가격", "얼마"]},
            "unpaid_list": {"desc": "미수금 목록", "patterns": ["미수", "미납", "미수금"]},
            "non_covered_explain": {"desc": "비급여 안내", "patterns": ["비급여", "자비", "보험안됨"]},
            "copay_calculate": {"desc": "본인부담 계산", "patterns": ["본인부담", "자기부담", "부담금"]},
        }
    },
    "nurse": {
        "name": "간호사",
        "intents": {
            "vital_record": {"desc": "활력징후 기록", "patterns": ["바이탈", "활력", "혈압", "체온"]},
            "vital_alert": {"desc": "이상 바이탈", "patterns": ["이상", "비정상", "위험"]},
            "medication_check": {"desc": "투약 확인", "patterns": ["투약", "약물", "처방약"]},
            "drug_interaction": {"desc": "약물 상호작용", "patterns": ["상호작용", "같이", "병용"]},
            "nursing_note": {"desc": "간호 기록", "patterns": ["간호기록", "기록", "노트"]},
            "patient_education": {"desc": "환자 교육", "patterns": ["교육", "안내", "설명"]},
        }
    },
    "doctor": {
        "name": "의사",
        "intents": {
            "dx_code_search": {"desc": "진단 코드 검색", "patterns": ["코드", "KCD", "진단"]},
            "rx_suggest": {"desc": "처방 추천", "patterns": ["처방", "약", "추천", "치료"]},
            "order_set": {"desc": "오더 세트", "patterns": ["오더", "세트", "입원"]},
            "soap_auto": {"desc": "SOAP 자동 요약", "patterns": ["SOAP", "요약", "기록"]},
            "referral_draft": {"desc": "의뢰서 초안", "patterns": ["의뢰", "전원", "협진"]},
            "lab_interpret": {"desc": "검사 해석", "patterns": ["해석", "결과", "검사"]},
        }
    },
    "claims": {
        "name": "원무(청구)",
        "intents": {
            "claim_validate": {"desc": "청구 검증", "patterns": ["검증", "확인", "체크"]},
            "claim_bundle": {"desc": "묶음 코드", "patterns": ["묶음", "번들", "통합"]},
            "denial_reason": {"desc": "삭감 사유", "patterns": ["삭감", "거부", "반려"]},
            "appeal_draft": {"desc": "이의신청서", "patterns": ["이의", "신청", "항변"]},
            "edi_code_search": {"desc": "EDI 코드 검색", "patterns": ["EDI", "수가", "코드"]},
        }
    },
}

# ---------------------------------------------------------------------------
# Entity extraction (rule-based)
# ---------------------------------------------------------------------------

DEPT_MAP = {
    "내과": "IM", "외과": "GS", "정형외과": "OS", "산부인과": "OB",
    "소아과": "PD", "소아청소년과": "PD", "이비인후과": "ENT",
    "안과": "EY", "피부과": "DM", "비뇨기과": "UR", "비뇨의학과": "UR",
    "신경외과": "NS", "신경과": "NR", "정신건강의학과": "PS", "정신과": "PS",
    "재활의학과": "RM", "영상의학과": "RD", "마취통증의학과": "AN",
    "응급의학과": "EM", "가정의학과": "FM", "심장내과": "CV",
    "호흡기내과": "PU", "소화기내과": "GI", "내분비내과": "EN",
    "감염내과": "ID", "혈액종양내과": "HO", "류마티스내과": "RH",
    "흉부외과": "CS", "성형외과": "PS2", "치과": "DN",
}

VISIT_MAP = {"초진": "new", "재진": "return", "응급": "emergency"}


def _extract_entities(text):
    """규칙 기반 엔티티 추출"""
    entities = {}

    # 생년월일 (6자리)
    birth_match = re.search(r'\b(\d{6})(?:-?([1-4]))?\b', text)
    if birth_match:
        ymd = birth_match.group(1)
        sex_code = birth_match.group(2)
        year = int(ymd[:2])
        year = (1900 + year) if year >= 30 else (2000 + year)
        if sex_code:
            if sex_code in ("1", "3"):
                year = 1900 + int(ymd[:2]) if sex_code == "1" else 2000 + int(ymd[:2])
        entities["birth"] = f"{year}-{ymd[2:4]}-{ymd[4:6]}"
        entities["age"] = datetime.now().year - year
        if sex_code:
            entities["sex"] = "M" if sex_code in ("1", "3") else "F"

    # 혈압
    bp_match = re.search(r'(\d{2,3})\s*/\s*(\d{2,3})', text)
    if bp_match:
        entities["bp"] = {"systolic": int(bp_match.group(1)), "diastolic": int(bp_match.group(2))}

    # 체온
    temp_match = re.search(r'(\d{2}\.\d)\s*(?:도|°)?', text)
    if temp_match:
        val = float(temp_match.group(1))
        if 34.0 <= val <= 42.0:
            entities["temperature"] = val

    # 심박수 (혈압/체온 뒤의 단독 2-3자리 숫자)
    remaining = text
    if bp_match:
        remaining = remaining[bp_match.end():]
    hr_match = re.search(r'\b(\d{2,3})\b', remaining)
    if hr_match:
        val = int(hr_match.group(1))
        if 30 <= val <= 220:
            entities["heart_rate"] = val
            # 그 뒤의 숫자는 SpO2
            after = remaining[hr_match.end():]
            spo2_match = re.search(r'\b(\d{2,3})\b', after)
            if spo2_match:
                spo2 = int(spo2_match.group(1))
                if 50 <= spo2 <= 100:
                    entities["spo2"] = spo2

    # 진료과
    for dept_name, dept_code in DEPT_MAP.items():
        if dept_name in text:
            entities["department"] = dept_name
            entities["department_code"] = dept_code
            break

    # 방문 유형
    for visit_name, visit_code in VISIT_MAP.items():
        if visit_name in text:
            entities["visit_type"] = visit_name
            entities["visit_code"] = visit_code
            break

    # 환자 이름 (한글 2-4자, 숫자/진료과/방문유형 제외)
    clean = re.sub(r'\d+', '', text)
    for dept in DEPT_MAP:
        clean = clean.replace(dept, '')
    for visit in VISIT_MAP:
        clean = clean.replace(visit, '')
    clean = re.sub(r'접수|검색|찾아|코드|보험|확인|예약|대기|바이탈|처방|기록', '', clean)
    name_match = re.search(r'([가-힯]{2,4})', clean.strip())
    if name_match:
        entities["patient_name"] = name_match.group(1)

    # 증상 키워드
    symptom_map = {
        "머리": ("두통", "신경과", "NR"),
        "head": ("두통", "신경과", "NR"),
        "배": ("복통", "내과", "IM"),
        "속": ("소화불량", "소화기내과", "GI"),
        "가슴": ("흉통", "심장내과", "CV"),
        "허리": ("요통", "정형외과", "OS"),
        "무릎": ("관절통", "정형외과", "OS"),
        "어깨": ("어깨통증", "정형외과", "OS"),
        "목": ("경부통", "정형외과", "OS"),
        "눈": ("안과증상", "안과", "EY"),
        "귀": ("이비인후과증상", "이비인후과", "ENT"),
        "코": ("비과증상", "이비인후과", "ENT"),
        "목감기": ("상기도감염", "내과", "IM"),
        "감기": ("상기도감염", "내과", "IM"),
        "기침": ("기침", "내과", "IM"),
        "열": ("발열", "내과", "IM"),
        "피부": ("피부증상", "피부과", "DM"),
        "발진": ("발진", "피부과", "DM"),
        "소변": ("비뇨기증상", "비뇨의학과", "UR"),
        "생리": ("부인과증상", "산부인과", "OB"),
        "임신": ("임신", "산부인과", "OB"),
        "불안": ("불안", "정신건강의학과", "PS"),
        "우울": ("우울", "정신건강의학과", "PS"),
        "잠": ("수면장애", "정신건강의학과", "PS"),
        "어지러": ("어지러움", "신경과", "NR"),
        "저림": ("저림", "신경과", "NR"),
        "숨": ("호흡곤란", "호흡기내과", "PU"),
    }
    for keyword, (symptom, dept, dept_code) in symptom_map.items():
        if keyword in text:
            entities["symptom"] = symptom
            if "department" not in entities:
                entities["recommended_dept"] = dept
                entities["recommended_dept_code"] = dept_code
            break

    # 증상이 있으면 이름으로 잘못 추출된 것 방지
    if entities.get("symptom") and entities.get("patient_name"):
        name = entities["patient_name"]
        for keyword in symptom_map:
            if keyword in name:
                del entities["patient_name"]
                break

    return entities


# ---------------------------------------------------------------------------
# Intent detection (rules + LLM fallback)
# ---------------------------------------------------------------------------

def _detect_intent_rules(role, text, entities):
    """규칙 기반 의도 분류"""
    role_config = ROLES.get(role, {})
    intents = role_config.get("intents", {})

    scores = {}
    for intent_id, config in intents.items():
        score = 0
        for pattern in config.get("patterns", []):
            if pattern in text:
                score += 10

        # 엔티티 기반 부스트
        if intent_id == "patient_register" and entities.get("department") and (entities.get("visit_type") or entities.get("patient_name")):
            score += 20
        elif intent_id == "symptom_to_dept" and entities.get("symptom"):
            score += 30
        elif intent_id == "vital_record" and (entities.get("bp") or entities.get("temperature")):
            score += 30
        elif intent_id == "patient_search" and entities.get("patient_name") and not entities.get("department"):
            score += 15

        if score > 0:
            scores[intent_id] = score

    if scores:
        best = max(scores, key=scores.get)
        total = sum(scores.values())
        return best, min(scores[best] / max(total, 1), 1.0)

    return None, 0.0


def _detect_intent_llm(role, text, entities):
    """LLM 기반 의도 분류 (fallback)"""
    role_config = ROLES.get(role, {})
    intent_list = "\n".join([f"- {k}: {v['desc']}" for k, v in role_config.get("intents", {}).items()])

    prompt = f"""사용자 입력: "{text}"
추출된 정보: {json.dumps(entities, ensure_ascii=False)}

위 입력의 의도를 아래 목록에서 하나 선택하세요:
{intent_list}

JSON으로 응답: {{"intent": "의도명", "confidence": 0.0~1.0}}
JSON만 출력하세요."""

    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
        from core import model_router
        res = model_router.generate(
            "medical.cli_intent", prompt, model="qwen2.5:14b",
            system="당신은 의도 분류기입니다. JSON만 출력하세요.",
            options={"temperature": 0.1, "num_predict": 100}, timeout=30,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        if not res.ok:
            # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(debug 로그+미분류) — 종전엔 무로그로 흘렀다
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        raw = res.text
        match = re.search(r'\{[^}]+\}', raw)
        if match:
            d = json.loads(match.group())
            return d.get("intent"), d.get("confidence", 0.5)
    except Exception as e:
        logger.debug("intent classification failed: %s", e)
    return None, 0.0


# ---------------------------------------------------------------------------
# Vital assessment
# ---------------------------------------------------------------------------

VITAL_RANGES = {
    "sbp": {"normal": (90, 140), "warning": (140, 180), "critical": 180},
    "dbp": {"normal": (60, 90), "warning": (90, 110), "critical": 110},
    "hr": {"low": 50, "normal": (50, 100), "warning": (100, 150), "critical": 150},
    "bt": {"low": 35.5, "normal": (36.0, 37.5), "warning": (37.5, 39.0), "critical": 39.0},
    "spo2": {"critical": 90, "warning": 94, "normal": (95, 100)},
}


def _assess_vitals(entities):
    """활력징후 자동 판정"""
    findings = []
    level = "normal"  # normal → warning → critical → emergency

    bp = entities.get("bp")
    if bp:
        sbp, dbp = bp["systolic"], bp["diastolic"]
        if sbp >= 180 or dbp >= 110:
            findings.append({"item": "BP", "value": f"{sbp}/{dbp}", "level": "critical", "msg": "고혈압 위기 — 즉시 보고"})
            level = "critical"
        elif sbp >= 140 or dbp >= 90:
            findings.append({"item": "BP", "value": f"{sbp}/{dbp}", "level": "warning", "msg": "고혈압"})
            if level != "critical":
                level = "warning"
        elif sbp < 90 or dbp < 60:
            findings.append({"item": "BP", "value": f"{sbp}/{dbp}", "level": "critical", "msg": "저혈압 — 즉시 보고"})
            level = "critical"
        else:
            findings.append({"item": "BP", "value": f"{sbp}/{dbp}", "level": "normal", "msg": "정상"})

    bt = entities.get("temperature")
    if bt:
        if bt >= 39.0:
            findings.append({"item": "BT", "value": f"{bt}°C", "level": "critical", "msg": "고열"})
            level = "critical"
        elif bt >= 37.5:
            findings.append({"item": "BT", "value": f"{bt}°C", "level": "warning", "msg": "발열"})
            if level not in ("critical",):
                level = "warning"
        elif bt < 35.5:
            findings.append({"item": "BT", "value": f"{bt}°C", "level": "warning", "msg": "저체온"})
            if level not in ("critical",):
                level = "warning"
        else:
            findings.append({"item": "BT", "value": f"{bt}°C", "level": "normal", "msg": "정상"})

    hr = entities.get("heart_rate")
    if hr:
        if hr >= 150 or hr < 40:
            findings.append({"item": "HR", "value": f"{hr}bpm", "level": "critical", "msg": "즉시 보고"})
            level = "critical"
        elif hr >= 100:
            findings.append({"item": "HR", "value": f"{hr}bpm", "level": "warning", "msg": "빈맥"})
            if level not in ("critical",):
                level = "warning"
        elif hr < 50:
            findings.append({"item": "HR", "value": f"{hr}bpm", "level": "warning", "msg": "서맥"})
            if level not in ("critical",):
                level = "warning"
        else:
            findings.append({"item": "HR", "value": f"{hr}bpm", "level": "normal", "msg": "정상"})

    spo2 = entities.get("spo2")
    if spo2:
        if spo2 < 90:
            findings.append({"item": "SpO2", "value": f"{spo2}%", "level": "critical", "msg": "심한 저산소 — 즉시 산소 투여"})
            level = "critical"
        elif spo2 < 94:
            findings.append({"item": "SpO2", "value": f"{spo2}%", "level": "warning", "msg": "저산소 — 산소 투여 고려"})
            if level not in ("critical",):
                level = "warning"
        else:
            findings.append({"item": "SpO2", "value": f"{spo2}%", "level": "normal", "msg": "정상"})

    recommendations = []
    if level == "critical":
        recommendations = ["담당의 즉시 보고", "15분 후 재측정"]
        if spo2 and spo2 < 94:
            recommendations.append("산소 투여 시작 (목표 SpO2 ≥94%)")
    elif level == "warning":
        recommendations = ["30분 후 재측정", "담당의에게 보고 고려"]

    return {"findings": findings, "level": level, "recommendations": recommendations}


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _handle_patient_register(entities, tenant):
    """접수 처리"""
    name = entities.get("patient_name", "")
    dept = entities.get("department", "")
    dept_code = entities.get("department_code", "")
    visit = entities.get("visit_type", "초진")
    birth = entities.get("birth", "")
    age = entities.get("age", "")
    sex = entities.get("sex", "")

    # 진찰료 조회
    conn = _get_cli_db()
    dept_row = conn.execute("SELECT * FROM departments WHERE code=? OR name=?", (dept_code, dept)).fetchone()

    fee = 0
    if dept_row:
        fee = dept_row["consultation_fee_new"] if visit == "초진" else (dept_row["consultation_fee_return"] or 0)

    copay_rate = 0.3  # 건강보험 기본
    copay = int(fee * copay_rate) if fee else 0

    reg_id = f"R-{datetime.now().strftime('%Y-%m')}-{str(uuid.uuid4())[:4].upper()}"

    result = {
        "action": "register",
        "patient": {"name": name, "birth": birth, "age": age, "sex": sex or "미상"},
        "visit": {
            "department": dept, "department_code": dept_code,
            "visit_type": visit, "registration_id": reg_id,
        },
        "insurance": {
            "type": "건강보험",
            "consultation_fee": fee,
            "copay_rate": copay_rate,
            "copay": copay,
        },
    }

    # Display text
    sex_label = {"M": "남", "F": "여"}.get(sex, "")
    display = f"접수 준비\n환자: {name} ({sex_label}/{age}세)\n진료과: {dept} | {visit}"
    if fee:
        display += f"\n진찰료: {fee:,}원 (본인부담 {copay:,}원)"
    display += f"\n접수번호: {reg_id}"

    return result, display, True  # confirm_required


def _handle_vital_record(entities, tenant):
    """바이탈 기록"""
    assessment = _assess_vitals(entities)

    level_emoji = {"normal": "✅", "warning": "⚠️", "critical": "🔴"}
    level_msg = {"normal": "정상 범위", "warning": "주의 — 모니터링 강화", "critical": "이상 — 즉시 보고 필요"}

    display_lines = [f"{level_emoji.get(assessment['level'], '')} {level_msg.get(assessment['level'], '')}"]
    for f in assessment["findings"]:
        icon = {"normal": "✅", "warning": "⚠️", "critical": "🔴"}.get(f["level"], "")
        display_lines.append(f"  {icon} {f['item']} {f['value']}: {f['msg']}")

    if assessment["recommendations"]:
        display_lines.append("\n권장 조치:")
        for r in assessment["recommendations"]:
            display_lines.append(f"  · {r}")

    name = entities.get("patient_name", "")
    if name:
        display_lines.insert(0, f"환자: {name}")

    return {
        "action": "vital_record",
        "patient_name": name,
        "vitals": {k: v for k, v in entities.items() if k in ("bp", "temperature", "heart_rate", "spo2")},
        "assessment": assessment,
    }, "\n".join(display_lines), False


def _handle_code_search(entities, text, intent):
    """코드 검색 (KCD/EDI)"""
    conn = _get_cli_db()
    query = text.strip()

    # Remove known keywords
    for kw in ["코드", "수가", "EDI", "KCD", "진단", "검색", "찾아"]:
        query = query.replace(kw, "").strip()

    results = []

    if intent in ("dx_code_search", "insurance_claim_code", "visit_type_code"):
        # KCD search
        _q = _escape_like(query)
        rows = conn.execute(
            "SELECT code, name_ko, category FROM kcd_codes WHERE name_ko LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' ORDER BY is_common DESC, code LIMIT 10",
            (f"%{_q}%", f"%{_q}%")).fetchall()
        for r in rows:
            results.append({"type": "KCD", "code": r["code"], "name": r["name_ko"], "category": r["category"]})

    if intent in ("edi_code_search", "insurance_claim_code", "visit_type_code", "procedure_cost"):
        # EDI search
        _q2 = _escape_like(query)
        rows = conn.execute(
            "SELECT code, name, category, price, insurance_type FROM edi_codes WHERE name LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' ORDER BY code LIMIT 10",
            (f"%{_q2}%", f"%{_q2}%")).fetchall()
        for r in rows:
            results.append({
                "type": "EDI", "code": r["code"], "name": r["name"],
                "price": r["price"], "insurance": r["insurance_type"],
            })

    # Check dx_order_mapping
    if results and results[0]["type"] == "KCD":
        mapping = conn.execute(
            "SELECT * FROM dx_order_mapping WHERE kcd_code=? ORDER BY frequency DESC LIMIT 1",
            (results[0]["code"],)).fetchone()
        if mapping:
            results[0]["related_orders"] = {
                "edi_codes": json.loads(mapping["edi_codes"]) if mapping["edi_codes"] else [],
                "lab_codes": json.loads(mapping["lab_codes"]) if mapping["lab_codes"] else [],
                "drug_codes": json.loads(mapping["drug_codes"]) if mapping["drug_codes"] else [],
            }

    display_lines = []
    for r in results[:5]:
        line = f"  {r['type']} {r['code']}: {r['name']}"
        if r.get("price"):
            line += f" ({r['price']:,}원)"
        if r.get("insurance"):
            line += f" [{r['insurance']}]"
        display_lines.append(line)

    return {
        "action": "code_search",
        "query": query,
        "results": results,
        "count": len(results),
    }, "\n".join(display_lines) if display_lines else "검색 결과 없음", False




def _handle_calculate_bill(entities, text, tenant):
    """수납: KCD→매핑→EDI 합산→본인부담 자동 계산"""
    conn = _get_cli_db()
    # Extract KCD from text
    query = text.strip()
    for kw in ["진료비", "계산", "얼마", "수납", "코드", "수가"]:
        query = query.replace(kw, "").strip()

    # Search KCD
    # 단어별 검색
    search_terms = [t for t in query.split() if len(t) > 1]
    if search_terms:
        like_clause = ' AND '.join(["name_ko LIKE ? ESCAPE '\\'" for _ in search_terms])
        params = [f'%{_escape_like(t)}%' for t in search_terms]
        _q3 = _escape_like(query)
        kcd_row = conn.execute(f"SELECT code, name_ko FROM kcd_codes WHERE ({like_clause}) OR code LIKE ? ESCAPE '\\' ORDER BY is_common DESC LIMIT 1",
            params + [f'%{_q3}%']).fetchone()
    else:
        _q3 = _escape_like(query)
        kcd_row = conn.execute("SELECT code, name_ko FROM kcd_codes WHERE name_ko LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' ORDER BY is_common DESC LIMIT 1",
            (f'%{_q3}%', f'%{_q3}%')).fetchone()

    if not kcd_row:
        return {"action": "calculate_bill", "error": f"'{query}' 관련 질병 코드를 찾을 수 없습니다."}, f"'{query}' 코드 없음", False

    kcd_code = kcd_row["code"]
    kcd_name = kcd_row["name_ko"]

    # Get mapping
    mapping = conn.execute(
        "SELECT * FROM dx_order_mapping WHERE kcd_code=? ORDER BY frequency DESC LIMIT 1",
        (kcd_code,)).fetchone()

    items = []
    total = 0

    if mapping:
        import json as _json
        edi_codes = _json.loads(mapping["edi_codes"] or "[]")
        for edi_code in edi_codes:
            edi_row = conn.execute("SELECT code, name, price, insurance_type, copay_rate FROM edi_codes WHERE code=?",
                                   (edi_code,)).fetchone()
            if edi_row and edi_row["price"]:
                price = int(edi_row["price"])
                items.append({
                    "code": edi_row["code"], "name": edi_row["name"],
                    "price": price, "insurance": edi_row["insurance_type"],
                })
                total += price
        # Increment frequency
        conn.execute("UPDATE dx_order_mapping SET frequency = frequency + 1 WHERE id=?", (mapping["id"],))
        conn.commit()

    copay_rate = 0.3  # 건강보험 기본
    copay = int(total * copay_rate)
    insurance_pay = total - copay

    # Display
    lines = [f"진료비 계산: {kcd_name} ({kcd_code})"]
    lines.append("-" * 40)
    for item in items:
        lines.append(f"  {item['name']} ({item['code']}): {item['price']:,}원")
    lines.append("-" * 40)
    lines.append(f"  총 급여액: {total:,}원")
    lines.append(f"  본인부담 (30%): {copay:,}원")
    lines.append(f"  공단부담 (70%): {insurance_pay:,}원")
    lines.append(f"  수납액: {copay:,}원")

    return {
        "action": "calculate_bill",
        "kcd": {"code": kcd_code, "name": kcd_name},
        "items": items,
        "total": total,
        "copay_rate": copay_rate,
        "copay": copay,
        "insurance_pay": insurance_pay,
    }, "\n".join(lines), False


def _ko_to_en_drug(name):
    """한국어 약물명 → 영문 일반명"""
    db = _get_cli_db()
    _n = _escape_like(name)
    row = db.execute(
        "SELECT name_generic FROM drug_codes WHERE name_ko LIKE ? ESCAPE '\\' OR name_brand LIKE ? ESCAPE '\\' LIMIT 1",
        (f"%{_n}%", f"%{_n}%")).fetchone()
    return row["name_generic"] if row else name


def _handle_drug_interaction(entities, text, tenant):
    """간호: 약물 상호작용 DB 검색"""
    conn = _get_cli_db()
    # Extract drug names from text
    query = text.strip()
    for kw in ["상호작용", "같이", "병용", "약물", "확인", "가능"]:
        query = query.replace(kw, "").strip()

    words = [w.strip() for w in re.split(r'[,\s]+', query.replace('와 ', ' ').replace('과 ', ' ').replace('랑 ', ' ')) if w.strip() and len(w.strip()) > 1]

    # 한국어 약물명을 영문으로 변환
    words = [_ko_to_en_drug(w) for w in words]
    if len(words) < 2:
        # Search by single drug
        if words:
            drug = words[0]
            _d = _escape_like(drug)
            rows = conn.execute(
                "SELECT * FROM drug_interactions WHERE drug_a LIKE ? ESCAPE '\\' OR drug_b LIKE ? ESCAPE '\\' ORDER BY CASE severity WHEN 'major' THEN 1 WHEN 'moderate' THEN 2 ELSE 3 END LIMIT 10",
                (f"%{_d}%", f"%{_d}%")).fetchall()
            if rows:
                lines = [f"'{drug}' 관련 상호작용 {len(rows)}건:"]
                for r in rows:
                    icon = {"major": "🔴", "moderate": "⚠️", "minor": "🟢"}.get(r["severity"], "")
                    lines.append(f"  {icon} {r['drug_a']} + {r['drug_b']}: {r['description'][:60]}")
                return {"action": "drug_interaction", "drug": drug, "interactions": [dict(r) for r in rows]}, "\n".join(lines), False
            return {"action": "drug_interaction", "error": f"'{drug}' 관련 상호작용 정보 없음"}, f"'{drug}' 상호작용 정보 없음", False
        return {"action": "drug_interaction", "error": "약물명 2개를 입력하세요"}, "약물명 2개를 입력하세요 (예: 와파린 아스피린)", False

    # Check pair interaction
    drug_a, drug_b = words[0], words[1]
    # Try exact match first, then LIKE
    row = conn.execute(
        "SELECT * FROM drug_interactions WHERE (drug_a=? AND drug_b=?) OR (drug_a=? AND drug_b=?)",
        (drug_a, drug_b, drug_b, drug_a)).fetchone()
    if not row:
        _da, _db = _escape_like(drug_a), _escape_like(drug_b)
        row = conn.execute(
            "SELECT * FROM drug_interactions WHERE (drug_a LIKE ? ESCAPE '\\' AND drug_b LIKE ? ESCAPE '\\') OR (drug_a LIKE ? ESCAPE '\\' AND drug_b LIKE ? ESCAPE '\\')",
            (f"%{_da}%", f"%{_db}%", f"%{_db}%", f"%{_da}%")).fetchone()

    if row:
        icon = {"major": "🔴 위험", "moderate": "⚠️ 주의", "minor": "🟢 경미"}.get(row["severity"], "")
        lines = [
            f"{icon}: {row['drug_a']} + {row['drug_b']}",
            f"설명: {row['description']}",
            f"기전: {row['mechanism']}",
            f"영향: {row['clinical_effect']}",
            f"대처: {row['management']}",
        ]
        return {"action": "drug_interaction", "interaction": dict(row), "found": True}, "\n".join(lines), False

    return {
        "action": "drug_interaction",
        "drug_a": drug_a, "drug_b": drug_b,
        "found": False,
        "message": f"{drug_a} + {drug_b}: 등록된 상호작용 정보 없음 (안전하다는 의미 아님)"
    }, f"✅ {drug_a} + {drug_b}: 등록된 상호작용 없음\n(DB 미등록일 수 있으니 약사/의사 확인 권장)", False


def _handle_rx_suggest(entities, text, tenant):
    """의사: 가이드라인 기반 처방 추천"""
    conn = _get_cli_db()
    query = text.strip()
    for kw in ["처방", "추천", "약", "치료", "초치료", "1차"]:
        query = query.replace(kw, "").strip()

    # Find disease
    _q4 = _escape_like(query)
    kcd_row = conn.execute(
        "SELECT code, name_ko FROM kcd_codes WHERE name_ko LIKE ? ESCAPE '\\' ORDER BY is_common DESC LIMIT 1",
        (f"%{_q4}%",)).fetchone()

    if not kcd_row:
        return {"action": "rx_suggest", "error": f"'{query}' 질환을 찾을 수 없습니다."}, f"'{query}' 질환 없음", False

    # Find related drugs by category mapping
    kcd_name = kcd_row["name_ko"]
    kcd_code = kcd_row["code"]

    # Map disease category to drug category
    category_map = {
        "순환기": ["CCB", "ARB", "ACEi", "BB", "이뇨제", "스타틴"],
        "내분비": ["비구아나이드", "DPP-4i", "SGLT2i", "GLP-1RA", "설포닐우레아", "갑상선호르몬"],
        "소화기": ["PPI", "H2RA", "위장운동", "점막보호"],
        "호흡기": ["ICS+LABA", "LAMA", "SABA", "LTRA", "항히스타민"],
        "감염": ["페니실린", "세팔로스포린", "FQ", "마크로라이드"],
        "근골격": ["NSAID", "COX-2", "근이완", "비스포스포네이트", "요산저하"],
        "정신": ["SSRI", "SNRI", "벤조", "비정형항정신", "Z-수면제"],
        "신경": ["항경련", "항파킨슨", "편두통", "치매"],
        "피부": ["강력스테로이드", "약한스테로이드", "항진균외용"],
        "비뇨기": ["알파차단", "5-ARI", "과민방광"],
    }

    # Get disease category from KCD
    kcd_cat = conn.execute("SELECT category FROM kcd_codes WHERE code=?", (kcd_code,)).fetchone()
    disease_cat = kcd_cat["category"] if kcd_cat else ""

    drug_categories = category_map.get(disease_cat, [])
    drugs = []
    for cat in drug_categories[:6]:
        rows = conn.execute(
            "SELECT name_ko, name_generic, strength, frequency, route, category FROM drug_codes WHERE category=? AND is_common=1 ORDER BY name_ko LIMIT 2",
            (cat,)).fetchall()
        for r in rows:
            drugs.append(dict(r))

    if not drugs:
        # Fallback: search by disease name in drug names
        rows = conn.execute(
            "SELECT name_ko, name_generic, strength, frequency, category FROM drug_codes WHERE is_common=1 LIMIT 5"
        ).fetchall()
        drugs = [dict(r) for r in rows]

    lines = [f"처방 추천: {kcd_name} ({kcd_code})"]
    lines.append("-" * 40)
    for i, d in enumerate(drugs[:8]):
        lines.append(f"  {i+1}. {d['name_ko']} ({d['name_generic']}) {d.get('strength','')} {d.get('frequency','')}")
    if not drugs:
        lines.append("  (등록된 처방 추천 없음 — LLM 분석 필요)")

    return {
        "action": "rx_suggest",
        "disease": {"code": kcd_code, "name": kcd_name, "category": disease_cat},
        "suggested_drugs": drugs[:8],
    }, "\n".join(lines), False


def _handle_claim_validate(entities, text, tenant):
    """청구: 청구 코드 검증 — claim_rules DB 대조"""
    conn = _get_cli_db()
    query = text.strip()
    for kw in ["청구", "검증", "확인", "체크", "검사"]:
        query = query.replace(kw, "").strip()

    # Extract EDI codes from text or find by disease
    edi_codes = re.findall(r'[A-Z]{1,2}\d{3,4}[A-Z]?', query.upper())
    # 질환명/검사명으로도 코드 추출
    if not edi_codes:
        for word in query.split():
            if len(word) > 1:
                rows = conn.execute("SELECT code FROM edi_codes WHERE name LIKE ? ESCAPE '\\' LIMIT 3", (f'%{_escape_like(word)}%',)).fetchall()
                edi_codes.extend([r['code'] for r in rows])

    if not edi_codes:
        # Try to find by disease name
        kcd_row = conn.execute(
            "SELECT code FROM kcd_codes WHERE name_ko LIKE ? ESCAPE '\\' ORDER BY is_common DESC LIMIT 1",
            (f"%{_escape_like(query)}%",)).fetchone()
        if kcd_row:
            import json as _json
            mapping = conn.execute(
                "SELECT edi_codes FROM dx_order_mapping WHERE kcd_code=? LIMIT 1",
                (kcd_row["code"],)).fetchone()
            if mapping:
                edi_codes = _json.loads(mapping["edi_codes"] or "[]")

    if not edi_codes:
        return {"action": "claim_validate", "error": "검증할 코드가 없습니다. EDI 코드 또는 질환명을 입력하세요."}, "검증할 코드 없음", False

    # Check rules
    issues = []
    for code in edi_codes:
        rules = conn.execute(
            "SELECT * FROM claim_rules WHERE code_a=? OR code_b=?",
            (code, code)).fetchall()
        for rule in rules:
            # Check if the paired code is also in the list
            if rule["rule_type"] == "bundle" and rule["code_b"] and rule["code_b"] in edi_codes:
                issues.append({
                    "type": "bundle",
                    "codes": [rule["code_a"], rule["code_b"]],
                    "description": rule["description"],
                    "action": rule["action"],
                    "fix": rule["fix_suggestion"],
                })
            elif rule["rule_type"] == "limit":
                issues.append({
                    "type": "limit",
                    "code": rule["code_a"],
                    "description": rule["description"],
                    "action": rule["action"],
                })
            elif rule["rule_type"] == "excluded":
                if rule["code_b"] and rule["code_b"] in edi_codes:
                    issues.append({
                        "type": "excluded",
                        "codes": [rule["code_a"], rule["code_b"]],
                        "description": rule["description"],
                        "action": rule["action"],
                    })

    passed = len(edi_codes) - len(set(i.get("code", i.get("codes", [""])[0]) for i in issues))

    lines = [f"청구 검증: {len(edi_codes)}개 코드"]
    if issues:
        lines.append(f"⚠️ {len(issues)}건 주의사항:")
        for i, issue in enumerate(issues):
            icon = {"warn": "⚠️", "block": "🔴", "auto_fix": "🔧"}.get(issue["action"], "")
            lines.append(f"  {i+1}. {icon} [{issue['type']}] {issue['description']}")
            if issue.get("fix"):
                lines.append(f"     수정: {issue['fix']}")
    else:
        lines.append(f"✅ {len(edi_codes)}건 모두 정상")

    return {
        "action": "claim_validate",
        "codes_checked": edi_codes,
        "issues": issues,
        "passed": passed,
        "total": len(edi_codes),
    }, "\n".join(lines), False


def _handle_llm_action(role, intent, text, entities, tenant):
    """LLM 기반 액션 (처방 추천, 간호 기록, 이의신청 등)"""
    role_name = ROLES.get(role, {}).get("name", role)
    intent_desc = ROLES.get(role, {}).get("intents", {}).get(intent, {}).get("desc", intent)

    prompt = f"""역할: {role_name}
요청: {intent_desc}
입력: {text}
추출 정보: {json.dumps(entities, ensure_ascii=False)}

위 요청에 맞는 응답을 한국어로 작성하세요. 간결하고 실용적으로."""

    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
        from core import model_router
        res = model_router.generate(
            "medical.cli_action", prompt, model="qwen2.5:14b",
            system=f"당신은 병원 {role_name}을 지원하는 AI입니다. 정확하고 간결하게 답변하세요.",
            options={"temperature": 0.3, "num_predict": 1000}, timeout=60,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        if not res.ok:
            # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(error 응답) — 종전엔 빈 response가 정상 결과로 나갔다
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        response = res.text
        return {"action": intent, "response": response}, response[:500], False
    except Exception as e:
        return {"action": intent, "error": str(e)}, f"오류: {e}", False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _require_tenant_cli():
    tenant = _resolve_medical_tenant(request)
    if not tenant:
        return None, (jsonify({"error": "X-Medical-Key required"}), 401)
    return tenant, None


@medical_cli_bp.route("/api/medical/cli/execute", methods=["POST"])
def cli_execute():
    """Execute CLI command

    임상 명령을 해석해 실행한다(`clinic_codes.db`의 청구 규칙·삭감 사유를 참조).
    의도가 불확실하면 `POST /api/medical/cli/suggest`로 후보를 먼저 받는다.
    ---
    tags: [Medical - CLI]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [role, command]
          properties:
            role: {type: string, enum: [reception, billing, nurse, doctor, claims]}
            command: {type: string, description: "자연어 명령"}
            context: {type: object}
    responses:
      200:
        description: CLI 실행 결과
        schema:
          type: object
          properties:
            intent: {type: string}
            confidence: {type: number}
            detection: {type: string, description: "의도 판별 방식"}
            entities: {type: object, description: "명령에서 추출한 엔티티"}
            result: {type: object}
            display: {type: object}
            confirm_required: {type: boolean, description: "true면 실행 전 사용자 확인이 필요하다"}
            suggestions: {type: array, items: {type: string}}
    """
    tenant, err = _require_tenant_cli()
    if err:
        return err

    data = request.json or {}
    role = data.get("role", "reception")
    command = data.get("command", "").strip()

    if not command:
        return jsonify({"error": "command is required"}), 400
    if role not in ROLES:
        return jsonify({"error": f"invalid role. available: {list(ROLES.keys())}"}), 400

    # 1. Entity extraction
    entities = _extract_entities(command)

    # 2. Intent detection (rules first, LLM fallback)
    intent, confidence = _detect_intent_rules(role, command, entities)
    detection_method = "rules"
    if not intent or confidence < 0.3:
        intent_llm, conf_llm = _detect_intent_llm(role, command, entities)
        if intent_llm and conf_llm > (confidence or 0):
            intent = intent_llm
            confidence = conf_llm
            detection_method = "llm"

    if not intent:
        # Unknown intent — use LLM general response
        result, display, confirm = _handle_llm_action(role, "general", command, entities, tenant)
        intent = "general"
        confidence = 0.5
    # 3. Execute action
    elif intent == "patient_register":
        result, display, confirm = _handle_patient_register(entities, tenant)
    elif intent == "vital_record":
        result, display, confirm = _handle_vital_record(entities, tenant)
    elif intent == "symptom_to_dept":
        symptom = entities.get("symptom", "알 수 없는 증상")
        dept = entities.get("recommended_dept", "")
        dept_code = entities.get("recommended_dept_code", "")
        display = f"추천 진료과: {dept}"
        if symptom:
            display = f"증상: {symptom} | 추천 진료과: {dept}"


        result = {
            "action": "symptom_to_dept",
            "symptom": symptom,
            "recommended_department": dept,
            "recommended_department_code": dept_code,
            "department": dept,
            "department_code": dept_code,
        }
        # Check if patient name exists
        name = entities.get("patient_name", "")
        if name:
            result["patient_name"] = name
            display = f"환자: {name} | {display}"
        confirm = True
    elif intent in ("dx_code_search", "insurance_claim_code", "visit_type_code",
                     "edi_code_search", "procedure_cost"):
        result, display, confirm = _handle_code_search(entities, command, intent)
    elif intent == "calculate_bill":
        result, display, confirm = _handle_calculate_bill(entities, command, tenant)
    elif intent == "copay_calculate":
        result, display, confirm = _handle_calculate_bill(entities, command, tenant)
    elif intent == "drug_interaction":
        result, display, confirm = _handle_drug_interaction(entities, command, tenant)
    elif intent == "rx_suggest":
        result, display, confirm = _handle_rx_suggest(entities, command, tenant)
    elif intent in ("claim_validate", "claim_bundle"):
        result, display, confirm = _handle_claim_validate(entities, command, tenant)
    else:
        result, display, confirm = _handle_llm_action(role, intent, command, entities, tenant)

    # 4. Save history
    conn = _get_cli_db()
    conn.execute(
        "INSERT INTO cli_history (tenant_id, role, command, intent, result) VALUES (?,?,?,?,?)",
        (tenant["id"], role, command, intent, json.dumps(result, ensure_ascii=False)[:2000]))
    conn.commit()

    # 5. Suggestions
    role_intents = ROLES.get(role, {}).get("intents", {})
    suggestions = [v["desc"] for k, v in list(role_intents.items())[:3] if k != intent]

    return jsonify({
        "intent": intent,
        "confidence": round(confidence, 2),
        "detection": detection_method,
        "entities": entities,
        "result": result,
        "display": display,
        "confirm_required": confirm,
        "suggestions": suggestions,
    })


@medical_cli_bp.route("/api/medical/cli/suggest", methods=["POST"])
def cli_suggest():
    """Input autocomplete/suggestions
    ---
    tags: [Medical - CLI]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            role: {type: string}
            partial: {type: string, description: "부분 입력"}
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant_cli()
    if err:
        return err

    data = request.json or {}
    role = data.get("role", "reception")
    partial = data.get("partial", "").strip()

    suggestions = []

    # Recent commands
    conn = _get_cli_db()
    recent = conn.execute(
        "SELECT DISTINCT command FROM cli_history WHERE tenant_id=? AND role=? AND command LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT 5",
        (tenant["id"], role, f"%{_escape_like(partial)}%")).fetchall()
    for r in recent:
        suggestions.append({"type": "recent", "text": r["command"]})

    # Intent suggestions
    role_config = ROLES.get(role, {})
    for intent_id, config in role_config.get("intents", {}).items():
        if partial.lower() in config["desc"].lower() or any(p in partial for p in config.get("patterns", [])):
            suggestions.append({"type": "intent", "text": config["desc"], "intent": intent_id})

    return jsonify({"suggestions": suggestions[:10]})


@medical_cli_bp.route("/api/medical/cli/intents/<role>")
def cli_intents(role):
    """List available intents for a role
    ---
    tags: [Medical - CLI]
    parameters:
      - {name: role, in: path, type: string, required: true}
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            role: {}
            role_name: {}
            intents: {}
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
    """
    if role not in ROLES:
        return jsonify({"error": f"invalid role. available: {list(ROLES.keys())}"}), 400

    role_config = ROLES[role]
    intents = []
    for k, v in role_config.get("intents", {}).items():
        intents.append({"id": k, "description": v["desc"], "keywords": v.get("patterns", [])})

    return jsonify({"role": role, "role_name": role_config["name"], "intents": intents})


@medical_cli_bp.route("/api/medical/cli/history")
def cli_history():
    """CLI command history
    ---
    tags: [Medical - CLI]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant_cli()
    if err:
        return err

    limit = min(int(request.args.get("limit", 50)), 200)
    role = request.args.get("role")

    conn = _get_cli_db()
    if role:
        rows = conn.execute(
            "SELECT role, command, intent, result, created_at FROM cli_history WHERE tenant_id=? AND role=? ORDER BY created_at DESC LIMIT ?",
            (tenant["id"], role, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, command, intent, result, created_at FROM cli_history WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
            (tenant["id"], limit)).fetchall()

    return jsonify({"history": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# Code search APIs
# ---------------------------------------------------------------------------

@medical_cli_bp.route("/api/medical/code/kcd", methods=["POST"])
def code_kcd():
    """Search KCD (disease) codes

    KCD 상병코드를 질의어로 검색한다(`limit` 상한 50).
    상병에서 처방·검사로 이어지는 매핑은 `POST /api/medical/code/mapping`을 쓴다.
    ---
    tags: [Medical - Code]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string}
            limit: {type: integer, default: 10}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "query required"}}
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    limit = min(data.get("limit", 10), 50)
    conn = _get_cli_db()
    _q5 = _escape_like(query)
    rows = conn.execute(
        "SELECT code, name_ko, name_en, category FROM kcd_codes WHERE name_ko LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' OR name_en LIKE ? ESCAPE '\\' ORDER BY is_common DESC, code LIMIT ?",
        (f"%{_q5}%", f"%{_q5}%", f"%{_q5}%", limit)).fetchall()

    return jsonify({"results": [dict(r) for r in rows], "count": len(rows)})


@medical_cli_bp.route("/api/medical/code/edi", methods=["POST"])
def code_edi():
    """Search EDI (procedure/fee) codes

    EDI 청구코드(행위·수가)를 질의어로 검색한다.
    코드가 어떤 고시 유형에 걸리는지는 `GET /api/notice/fee-codes/{code}`가 해석한다.
    ---
    tags: [Medical - Code]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string}
            limit: {type: integer, default: 10}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "query required"}}
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    limit = min(data.get("limit", 10), 50)
    conn = _get_cli_db()
    _q6 = _escape_like(query)
    rows = conn.execute(
        "SELECT code, name, category, price, insurance_type, copay_rate FROM edi_codes WHERE name LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' ORDER BY code LIMIT ?",
        (f"%{_q6}%", f"%{_q6}%", limit)).fetchall()

    return jsonify({"results": [dict(r) for r in rows], "count": len(rows)})


@medical_cli_bp.route("/api/medical/code/mapping", methods=["POST"])
def code_mapping():
    """Disease → procedure/lab/drug mapping

    상병(진단)에서 처방·검사·약제로 이어지는 매핑을 낸다(`dx_order_mapping`).
    ---
    tags: [Medical - Code]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [kcd_code]
          properties:
            kcd_code: {type: string, description: "KCD 질병 코드"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "kcd_code required"}}
      404:
        description: "대상을 찾을 수 없음"
    """
    data = request.json or {}
    kcd = data.get("kcd_code", "").strip()
    if not kcd:
        return jsonify({"error": "kcd_code required"}), 400

    conn = _get_cli_db()

    # Get disease info
    disease = conn.execute("SELECT * FROM kcd_codes WHERE code=?", (kcd,)).fetchone()
    if not disease:
        return jsonify({"error": f"KCD code {kcd} not found"}), 404

    # Get mapping
    mapping = conn.execute(
        "SELECT * FROM dx_order_mapping WHERE kcd_code=? ORDER BY frequency DESC LIMIT 1",
        (kcd,)).fetchone()

    result = {
        "kcd": {"code": disease["code"], "name": disease["name_ko"], "category": disease["category"]},
        "mapping": None,
    }

    if mapping:
        # Resolve EDI codes
        edi_details = []
        for edi_code in json.loads(mapping["edi_codes"] or "[]"):
            edi_row = conn.execute("SELECT code, name, price, insurance_type FROM edi_codes WHERE code=?", (edi_code,)).fetchone()
            if edi_row:
                edi_details.append(dict(edi_row))

        result["mapping"] = {
            "description": mapping["description"],
            "edi_codes": edi_details,
            "lab_codes": json.loads(mapping["lab_codes"] or "[]"),
            "drug_codes": json.loads(mapping["drug_codes"] or "[]"),
            "frequency": mapping["frequency"],
        }

        # Increment frequency
        conn.execute("UPDATE dx_order_mapping SET frequency = frequency + 1 WHERE id=?", (mapping["id"],))
        conn.commit()

    return jsonify(result)
