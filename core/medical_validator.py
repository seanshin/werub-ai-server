"""의료 환각 감지 — LLM 응답의 의료 팩트 검증 (P2-1)"""
import json
import logging
import os
import re
import sqlite3

logger = logging.getLogger("ollama-admin")

_BASE_DIR = None


def init_medical_validator(base_dir):
    global _BASE_DIR
    _BASE_DIR = base_dir


def _get_clinic_db():
    db_path = os.path.join(_BASE_DIR, "clinic_codes.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")  # ingest 동시 진행 시 SQLITE_BUSY 즉시실패 방어
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 개별 검증기
# ---------------------------------------------------------------------------

def check_drug_names(response_data):
    """약물명이 DB에 존재하는지 확인"""
    conn = _get_clinic_db()
    if not conn:
        return True, "DB unavailable", {}
    try:
        drugs_mentioned = set()
        text = json.dumps(response_data, ensure_ascii=False) if isinstance(response_data, dict) else str(response_data)

        # Extract drug-like words (영문 대문자 시작 or 한글 ~제/~정/~캡슐)
        drugs_mentioned.update(re.findall(r'\b([A-Z][a-z]+(?:_[A-Z][a-z]+)*)\b', text))
        drugs_mentioned.update(re.findall(r'([가-힣]+(?:정|캡슐|주|시럽|산|제))', text))

        if not drugs_mentioned:
            return True, "no drugs to check", {}

        verified = []
        unknown = []
        for drug in drugs_mentioned:
            row = conn.execute(
                "SELECT name_generic FROM drug_codes WHERE name_generic=? OR name_brand=? OR name_ko=? LIMIT 1",
                (drug, drug, drug)).fetchone()
            if row:
                verified.append(drug)
            else:
                # Fuzzy: LIKE search
                row = conn.execute(
                    "SELECT name_generic FROM drug_codes WHERE name_generic LIKE ? OR name_ko LIKE ? LIMIT 1",
                    (f"%{drug}%", f"%{drug}%")).fetchone()
                if row:
                    verified.append(drug)
                else:
                    unknown.append(drug)

        if unknown:
            return False, f"확인 불가 약물: {', '.join(unknown[:5])}", {"unknown_drugs": unknown}
        return True, "all drugs verified", {"verified": len(verified)}
    finally:
        conn.close()


def check_drug_interaction_warnings(response_data):
    """응답에 포함된 약물 조합의 상호작용 자동 검출 (내부 DB 보조 + 식약처 DUR 정본).

    영문 성분명 토큰을 추출해 ①내부 DB(LLM 시드, source=internal_db) ②식약처 DUR 정본
    병용금기(a_name_en 매칭, source=식약처DUR)로 점검. 정본 DUR은 clinic DB 미가용 시에도 동작.
    """
    text = json.dumps(response_data, ensure_ascii=False) if isinstance(response_data, dict) else str(response_data)
    drugs = re.findall(r'\b([A-Z][a-z]+(?:_[A-Z][a-z]+)*)\b', text)
    drug_set = list(set(d for d in drugs if len(d) > 3))

    # 🔴 2026-08-04 봉합: 위 정규식은 **영문 토큰만** 잡는다. 한국 의료 환경에서 쓰는 API인데
    #   한글 약물명(와파린·아미오다론·파록세틴)이 통째로 누락돼 DUR 조회가 빈 목록으로 돌았다.
    #   실증: `medications=["파록세틴","셀레길린"]` → 여기서는 **0건**인데
    #        `/api/medical/dur/check` 같은 입력은 **병용금기 1건**을 정확히 잡는다(29ms).
    #   ⇒ `medications` 원본 목록을 그대로 합류시킨다. DUR의 `_resolve_one`이 한글명·salt명·
    #      품목코드를 해석하므로 여기서 별도 정규화를 하지 않는다(해석 책임 단일화).
    if isinstance(response_data, dict):
        meds = response_data.get("medications") or []
        if isinstance(meds, str):
            meds = [meds]
        for m in meds:
            if isinstance(m, str) and m.strip() and m.strip() not in drug_set:
                drug_set.append(m.strip())

    interactions = []

    # ① 내부 DB(LLM 시드) — 보조
    conn = _get_clinic_db()
    if conn:
        try:
            for i in range(len(drug_set)):
                for j in range(i + 1, len(drug_set)):
                    row = conn.execute(
                        "SELECT * FROM drug_interactions WHERE (drug_a=? AND drug_b=?) OR (drug_a=? AND drug_b=?)",
                        (drug_set[i], drug_set[j], drug_set[j], drug_set[i])).fetchone()
                    if row and row["severity"] == "major":
                        interactions.append({
                            "drug_a": row["drug_a"], "drug_b": row["drug_b"],
                            "severity": "major", "management": row["management"],
                            "source": "internal_db",
                        })
        finally:
            conn.close()

    # ② 식약처 DUR 정본 병용금기(권위) — 영문 성분명도 a_name_en으로 매칭
    try:
        from core import dur as _dur
        existing = {tuple(sorted((i["drug_a"], i["drug_b"]))) for i in interactions}
        for a in _dur.check(drug_set).get("contraindications", []):
            key = tuple(sorted((a["drug_a"], a["drug_b"])))
            if key in existing:
                continue
            interactions.append({
                "drug_a": a["drug_a"], "drug_b": a["drug_b"],
                "severity": "contraindicated", "management": a["reason"],
                "source": "식약처DUR",
            })
    except Exception:  # noqa: BLE001
        pass

    if interactions:
        names = [f"{i['drug_a']}+{i['drug_b']}" for i in interactions]
        return False, f"주요 약물 상호작용 감지: {', '.join(names)}", {"interactions": interactions}
    return True, "no major interactions", {}


def check_dosage_range(response_data):
    """용량 표기가 비정상적 범위인지 검사"""
    text = json.dumps(response_data, ensure_ascii=False) if isinstance(response_data, dict) else str(response_data)

    # Extract dosage patterns: 숫자 + mg/g/mcg/unit
    dosages = re.findall(r'(\d+(?:\.\d+)?)\s*(mg|g|mcg|µg|unit|IU|mL)', text, re.IGNORECASE)

    warnings = []
    for amount_str, unit in dosages:
        amount = float(amount_str)
        unit_lower = unit.lower()

        # 비정상적 범위 감지
        if unit_lower == "mg" and amount > 5000:
            warnings.append(f"{amount}mg: 비정상적 고용량")
        elif unit_lower == "g" and amount > 10:
            warnings.append(f"{amount}g: 비정상적 고용량")
        elif unit_lower in ("mcg", "µg") and amount > 1000:
            warnings.append(f"{amount}{unit}: 비정상적 고용량 (mg 혼동?)")

    if warnings:
        return False, f"용량 경고: {'; '.join(warnings[:3])}", {"dosage_warnings": warnings}
    return True, "dosage ranges OK", {}


def check_icd_consistency(response_data):
    """ICD 코드와 진단명이 일치하는지 확인"""
    if not isinstance(response_data, dict):
        return True, "not a dict", {}

    icd_codes = response_data.get("icd_codes", [])
    if not icd_codes:
        # Check nested
        summary = response_data.get("summary", {})
        if isinstance(summary, dict):
            assessment = summary.get("assessment", "")
            icd_codes = re.findall(r'[A-Z]\d{2}(?:\.\d{1,2})?', str(assessment))

    if not icd_codes:
        return True, "no ICD codes", {}

    conn = _get_clinic_db()
    if not conn:
        return True, "DB unavailable", {}
    try:
        invalid = []
        for code in icd_codes[:10]:
            code = str(code).strip().upper()
            row = conn.execute("SELECT code FROM kcd_codes WHERE code=?", (code,)).fetchone()
            if not row:
                # Prefix match
                base = code.split(".")[0]
                row = conn.execute("SELECT code FROM kcd_codes WHERE code LIKE ? LIMIT 1", (base + "%",)).fetchone()
                if not row:
                    invalid.append(code)

        if invalid:
            return False, f"유효하지 않은 ICD 코드: {', '.join(invalid)}", {"invalid_codes": invalid}
        return True, "ICD codes valid", {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 통합 검증 함수
# ---------------------------------------------------------------------------

_ACTION_VALIDATORS = {
    "summarize": [check_icd_consistency],
    "analyze": [check_icd_consistency, check_dosage_range],
    "draft": [check_drug_names, check_drug_interaction_warnings, check_dosage_range],
    "explain": [],
    "reference": [],
}


def validate_medical_response(response, action="", raw_text=""):
    """의료 응답 종합 검증 — 환각 감지

    Returns:
        {
            "passed": bool,
            "checks": [{"name": "...", "passed": bool, "message": "...", "details": {...}}],
            "warnings": ["..."],
        }
    """
    from core.quality import force_json
    data = force_json(response) if isinstance(response, str) else response

    validators = _ACTION_VALIDATORS.get(action, [check_icd_consistency, check_dosage_range])
    results = []
    warnings = []
    all_passed = True

    for validator in validators:
        try:
            passed, message, details = validator(data)
            results.append({
                "name": validator.__name__,
                "passed": passed,
                "message": message,
                "details": details,
            })
            if not passed:
                all_passed = False
                warnings.append(message)
        except Exception as e:
            logger.debug("Validator %s error: %s", validator.__name__, e)
            results.append({"name": validator.__name__, "passed": True, "message": f"skipped: {e}", "details": {}})

    return {
        "passed": all_passed,
        "checks": results,
        "warnings": warnings,
    }
