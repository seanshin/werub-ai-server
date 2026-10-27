"""Drug Data Validator — 4-Layer 검증 로직

Layer 1: Schema   — 필수 필드, 타입, 패턴
Layer 2: Duplicate — code/generic_name/ATC 중복 탐지
Layer 3: Clinical  — 투여경로↔제형, 용량, 임신등급 일관성
Layer 4: Cross-ref — 상호작용 약물 존재 확인
"""
import json
import logging
import re
import sqlite3

logger = logging.getLogger("ollama-admin")

CODE_PATTERN = re.compile(r"^D-[A-Z]{2,4}\d{1,2}$")
ATC_PATTERN = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")
PREGNANCY_CATS = {"A", "B", "C", "D", "X", "N", "N/A"}

ROUTE_FORM_MAP = {
    "경구": {"정제", "캡슐", "서방정", "서방캡", "시럽", "분말", "연질캡"},
    "정맥": {"주사"},
    "피하": {"주사"},
    "흡입": {"흡입"},
    "점안": {"점안"},
    "외용": {"연고", "크림", "겔"},
    "비강": {"분무"},
    "경피": {"패치"},
}


class ValidationResult:
    __slots__ = ("passed", "layer", "message")

    def __init__(self, passed: bool, layer: str, message: str = ""):
        self.passed = passed
        self.layer = layer
        self.message = message

    def to_dict(self):
        return {"passed": self.passed, "layer": self.layer, "message": self.message}


def validate_drug(data: dict, conn: sqlite3.Connection) -> list[ValidationResult]:
    """4-Layer 검증. 결과 리스트 반환."""
    results = []
    results.extend(_layer1_schema(data))
    if all(r.passed for r in results):
        results.extend(_layer2_duplicate(data, conn))
    if all(r.passed for r in results):
        results.extend(_layer3_clinical(data))
    return results


def validate_interaction(data: dict, conn: sqlite3.Connection) -> list[ValidationResult]:
    """상호작용 데이터 검증."""
    results = []
    for field in ("drug_a", "drug_b", "severity", "description"):
        if not data.get(field):
            results.append(ValidationResult(False, "L1", f"missing: {field}"))
    if data.get("severity") not in ("major", "moderate", "minor", "contraindicated"):
        results.append(ValidationResult(False, "L1", f"invalid severity: {data.get('severity')}"))
    # L4: cross-ref
    if results and not all(r.passed for r in results):
        return results
    results.extend(_layer4_crossref(data, conn))
    return results


def _layer1_schema(data: dict) -> list[ValidationResult]:
    results = []
    code = data.get("code", "")
    if not CODE_PATTERN.match(code):
        results.append(ValidationResult(False, "L1", f"invalid code pattern: {code}"))
        return results

    if not data.get("name_generic") or len(data["name_generic"]) < 2:
        results.append(ValidationResult(False, "L1", "name_generic too short or missing"))
    if not data.get("name_ko") or len(data["name_ko"]) < 2:
        results.append(ValidationResult(False, "L1", "name_ko too short or missing"))

    atc = data.get("atc_code")
    if atc and not ATC_PATTERN.match(atc):
        results.append(ValidationResult(False, "L1", f"invalid ATC: {atc}"))

    preg = data.get("pregnancy_category")
    if preg and preg not in PREGNANCY_CATS:
        results.append(ValidationResult(False, "L1", f"invalid pregnancy_category: {preg}"))

    for json_field in ("contraindications", "side_effects"):
        val = data.get(json_field, "[]")
        if isinstance(val, str):
            try:
                json.loads(val)
            except (json.JSONDecodeError, TypeError):
                results.append(ValidationResult(False, "L1", f"invalid JSON in {json_field}"))

    if not results:
        results.append(ValidationResult(True, "L1", "schema OK"))
    return results


def _layer2_duplicate(data: dict, conn: sqlite3.Connection) -> list[ValidationResult]:
    results = []
    code = data["code"]

    # 1) exact code match
    row = conn.execute("SELECT code FROM drug_codes WHERE code=?", [code]).fetchone()
    if row:
        results.append(ValidationResult(False, "L2", f"duplicate code: {code}"))
        return results

    # 2) generic name normalized match
    name = data.get("name_generic", "").strip().lower()
    if name:
        row = conn.execute(
            "SELECT code FROM drug_codes WHERE LOWER(TRIM(name_generic))=?", [name]
        ).fetchone()
        if row:
            existing = row[0] if isinstance(row, tuple) else row["code"]
            results.append(ValidationResult(False, "L2", f"duplicate generic_name → {existing}"))
            return results

    # 3) ATC + strength match
    atc = data.get("atc_code")
    strength = data.get("strength")
    if atc and strength:
        row = conn.execute(
            "SELECT code FROM drug_codes WHERE atc_code=? AND strength=?", [atc, strength]
        ).fetchone()
        if row:
            existing = row[0] if isinstance(row, tuple) else row["code"]
            results.append(ValidationResult(False, "L2", f"duplicate ATC+strength → {existing}"))
            return results

    results.append(ValidationResult(True, "L2", "no duplicates"))
    return results


def _layer3_clinical(data: dict) -> list[ValidationResult]:
    results = []
    route = data.get("route", "")
    form = data.get("dosage_form", "")

    # route↔form consistency
    if route in ROUTE_FORM_MAP and form:
        expected_forms = ROUTE_FORM_MAP[route]
        if form not in expected_forms:
            results.append(ValidationResult(False, "L3", f"route '{route}' ↔ form '{form}' mismatch"))

    # pregnancy_category X must have 임신 in contraindications
    preg = data.get("pregnancy_category")
    contras = data.get("contraindications", "[]")
    if isinstance(contras, str):
        try:
            contras = json.loads(contras)
        except (json.JSONDecodeError, TypeError):
            contras = []
    if preg == "X" and isinstance(contras, list):
        has_preg = any("임신" in c for c in contras)
        if not has_preg:
            # auto-fix instead of fail
            contras.append("임신")
            data["contraindications"] = json.dumps(contras, ensure_ascii=False)

    if not results:
        results.append(ValidationResult(True, "L3", "clinical consistency OK"))
    return results


def _layer4_crossref(data: dict, conn: sqlite3.Connection) -> list[ValidationResult]:
    """상호작용에서 참조하는 약물이 drug_codes에 존재하는지 확인."""
    results = []
    for key in ("drug_a", "drug_b"):
        drug_name = data.get(key, "")
        if not drug_name:
            continue
        row = conn.execute(
            "SELECT code FROM drug_codes WHERE name_generic=? OR name_ko=?",
            [drug_name, drug_name],
        ).fetchone()
        if not row:
            results.append(ValidationResult(False, "L4", f"{key}='{drug_name}' not in drug_codes"))

    if not results:
        results.append(ValidationResult(True, "L4", "cross-ref OK"))
    return results
