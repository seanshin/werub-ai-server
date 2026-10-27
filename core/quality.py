"""응답 품질 유틸리티 — JSON 파싱, 검증, 재시도"""
import json
import re
import logging
import time

logger = logging.getLogger("ollama-admin")


def force_json(response, fallback_key="response"):
    """LLM 응답에서 JSON 강제 추출. 실패 시 fallback dict 반환."""
    if not response or not isinstance(response, str):
        return {fallback_key: response} if response else {}

    response = response.strip()

    # 1. 직접 파싱
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # 2. ```json 블록 추출
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. { } 객체 추출
    match = re.search(r'(\{[\s\S]*\})', response)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. [ ] 배열 추출
    match = re.search(r'(\[[\s\S]*\])', response)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 5. Fallback
    return {fallback_key: response}


def force_json_array(response):
    """LLM 응답에서 JSON 배열 강제 추출."""
    result = force_json(response, fallback_key="raw")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "raw" in result:
        return []
    return [result]


_UNPARSED = "__unparsed_sentinel__"


def parse_json_strict(response):
    """`force_json`과 같은 복구 규칙으로 파싱하되 **실패면 None**(v2.125.72).

    🔴 `force_json`은 실패해도 `{fallback_key: 원문}` dict를, `force_json_array`는 `[]`를 돌려준다 —
    호출자가 성공과 실패를 못 가르고 **200 + 원문 감싼 dict / 빈 목록**("이슈 없음"으로 읽힌다)이 나갔다
    (2026-10-08 전수: routes 20곳 중 주 결과 무성 실패 ~10곳). 실패 판별은 센티넬 키로 한다
    (`routes.medical._json_acceptable`과 같은 방식). ⚠️ 정상 JSON인 `[]`·`{}`는 **성공**이다 — 캐시 수용 판정과 다르다.
    """
    if not response or not isinstance(response, str):
        return None
    parsed = force_json(response, _UNPARSED)
    if isinstance(parsed, dict) and set(parsed.keys()) == {_UNPARSED}:
        return None
    return parsed


def parse_json_array_strict(response):
    """`force_json_array`의 엄격판 — 실패면 None, 객체 하나면 [객체](종전 규칙과 같다)."""
    parsed = parse_json_strict(response)
    if parsed is None:
        return None
    return parsed if isinstance(parsed, list) else [parsed]


def validate_fields(data, required_fields):
    """필수 필드 존재 확인. (passed, missing_fields)"""
    if not isinstance(data, dict):
        return False, required_fields
    missing = [f for f in required_fields if f not in data or data[f] is None]
    return len(missing) == 0, missing


def generate_with_validation_ex(generate_fn, prompt, system, validators=None,
                                 max_retries=2, options=None):
    """LLM 생성 + 검증 + 자동 재시도 — **검증 통과 여부를 함께** 돌려준다(v2.125.73).

    Returns: (response, tokens, duration_ms, retries, passed)
      - retries = 첫 시도 이후 재시도 횟수(0..max_retries)
      - passed  = 마지막 응답이 모든 validator를 통과했는가(validator가 없으면 True)

    🔴 종전 `generate_with_validation`은 **마지막 시도에서 통과**해도, **전 시도 실패**여도 똑같이
       `retries=max_retries`를 돌려줬다 → 호출자 7곳의 `validated = retries < 2`가 **마지막 시도 성공을 실패로**
       표시했고 진짜 실패와 구분되지 않았다(2026-10-08 확인). 최종 실패는 로그도 남지 않았다.
    """
    total_tokens = 0
    total_duration = 0
    reason = ""
    response = ""

    for attempt in range(max_retries + 1):
        response, tokens, duration = generate_fn(prompt, system, options)
        total_tokens += tokens
        total_duration += duration

        if not validators:
            return response, total_tokens, total_duration, 0, True

        all_passed = True
        for validator in validators:
            passed, reason = validator(response)
            if not passed:
                all_passed = False
                if attempt < max_retries:
                    logger.warning(f"Quality validation failed (attempt {attempt+1}): {reason}")
                    system = system + f"\n\n[중요: 이전 응답 문제: {reason}. 정확한 형식으로 다시 출력하세요.]"
                break

        if all_passed:
            return response, total_tokens, total_duration, attempt, True

    logger.warning("Quality validation failed after %d attempts — returning last response (validated=false): %s",
                   max_retries + 1, reason)
    return response, total_tokens, total_duration, max_retries, False


def generate_with_validation(generate_fn, prompt, system, validators=None,
                              max_retries=2, options=None):
    """호환 래퍼 — `(response, tokens, duration_ms, retries)`(종전 반환 형태 그대로).

    ⚠️ 여기서 `retries`만으로 성공/실패를 판단하지 말 것(마지막 시도 성공과 전 시도 실패가 같은 값이다).
       통과 여부가 필요하면 `generate_with_validation_ex`의 `passed`를 쓴다.
    """
    response, tokens, duration, retries, _passed = generate_with_validation_ex(
        generate_fn, prompt, system, validators=validators, max_retries=max_retries, options=options)
    return response, tokens, duration, retries

def validate_json_response(response):
    """응답이 유효한 JSON인지 검증"""
    try:
        json.loads(response)
        return True, "valid JSON"
    except Exception:
        # Try extracting
        result = force_json(response)
        if "response" not in result and "raw" not in result:
            return True, "extracted JSON"
        return False, "JSON 형식이 아닙니다. JSON만 출력하세요."


def validate_json_array_response(response):
    """응답이 JSON 배열인지 검증"""
    arr = force_json_array(response)
    if arr and len(arr) > 0:
        return True, f"array with {len(arr)} items"
    return False, "JSON 배열 형식이 아닙니다. [{{...}}] 형식으로 출력하세요."


def validate_soap_fields(response):
    """SOAP 필드 검증"""
    data = force_json(response, "raw")
    if "summary" in data:
        data = data["summary"]
    required = ["subjective", "objective", "assessment", "plan"]
    passed, missing = validate_fields(data, required)
    if passed:
        return True, "SOAP complete"
    return False, f"SOAP 필드 누락: {missing}. subjective, objective, assessment, plan 모두 포함하세요."


# ---------------------------------------------------------------------------
# ICD-10 / KCD Code Validation (v2.37.0)
# ---------------------------------------------------------------------------

def validate_icd_codes(icd_codes, db_path=None):
    """ICD-10 코드 목록을 KCD DB로 유효성 검증

    Args:
        icd_codes: list[str] — LLM이 생성한 ICD 코드 배열
        db_path: clinic_codes.db 경로 (None → 자동 탐색)

    Returns:
        {
            "valid": [{"code": "J06.9", "name": "급성 상기도감염"}],
            "invalid": ["Z99.99"],
            "total": 2,
            "valid_count": 1,
            "accuracy": 0.5
        }
    """
    import sqlite3
    import os

    if not icd_codes or not isinstance(icd_codes, list):
        return {"valid": [], "invalid": [], "total": 0, "valid_count": 0, "accuracy": 1.0}

    # Find clinic_codes.db
    if db_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base, "clinic_codes.db")

    if not os.path.exists(db_path):
        logger.warning(f"clinic_codes.db not found at {db_path}")
        return {"valid": [], "invalid": list(icd_codes), "total": len(icd_codes),
                "valid_count": 0, "accuracy": 0.0, "error": "DB not found"}

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")  # ingest 동시 진행 시 BUSY 즉시실패 방어
        valid = []
        invalid = []

        for code in icd_codes:
            code = str(code).strip().upper()
            if not code:
                continue
            cursor = conn.execute(
                "SELECT code, name_ko FROM kcd_codes WHERE code = ?", (code,))
            row = cursor.fetchone()
            if row:
                valid.append({"code": row[0], "name": row[1]})
            else:
                # Try fuzzy: remove trailing .0, try prefix
                base_code = code.split(".")[0]
                safe_base = base_code.replace("%", "").replace("_", "")
            cursor = conn.execute(
                "SELECT code, name_ko FROM kcd_codes WHERE code LIKE ? LIMIT 1",
                (safe_base + "%",))
            row = cursor.fetchone()
            if row:
                valid.append({"code": row[0], "name": row[1], "corrected_from": code})
            else:
                invalid.append(code)

        conn.close()
        total = len(valid) + len(invalid)
        return {
            "valid": valid,
            "invalid": invalid,
            "total": total,
            "valid_count": len(valid),
            "accuracy": round(len(valid) / total, 2) if total > 0 else 1.0,
        }
    except Exception as e:
        logger.error(f"ICD validation error: {e}")
        return {"valid": [], "invalid": list(icd_codes), "total": len(icd_codes),
                "valid_count": 0, "accuracy": 0.0, "error": str(e)}


# ---------------------------------------------------------------------------
# Drug Interaction Check (v2.37.0)
# ---------------------------------------------------------------------------

def check_drug_interactions(drug_names, db_path=None):
    """약물 목록에서 상호작용 검출

    Args:
        drug_names: list[str] — 약물명 (generic 또는 brand)
        db_path: clinic_codes.db 경로

    Returns:
        {
            "interactions": [
                {"drug_a": "Warfarin", "drug_b": "Aspirin", "severity": "major",
                 "description": "...", "management": "..."}
            ],
            "drugs_checked": 3,
            "interaction_count": 1,
            "has_major": True
        }
    """
    import sqlite3
    import os

    if not drug_names or len(drug_names) < 2:
        return {"interactions": [], "drugs_checked": len(drug_names or []),
                "interaction_count": 0, "has_major": False}

    if db_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base, "clinic_codes.db")

    if not os.path.exists(db_path):
        logger.warning(f"clinic_codes.db not found at {db_path}")
        return {"interactions": [], "drugs_checked": len(drug_names),
                "interaction_count": 0, "has_major": False, "error": "DB not found"}

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")  # ingest 동시 진행 시 BUSY 즉시실패 방어
        conn.row_factory = sqlite3.Row
        interactions = []

        # Normalize drug names (lowercase, escape SQL wildcards)
        def _safe(s):
            return s.strip().replace("%", "").replace("_", "")
        normalized = [_safe(d) for d in drug_names if d and d.strip()]

        # Check all pairs
        for i in range(len(normalized)):
            for j in range(i + 1, len(normalized)):
                a, b = normalized[i], normalized[j]
                if not a or not b:
                    continue
                # Search both directions + case-insensitive + partial match
                cursor = conn.execute("""
                    SELECT drug_a, drug_b, severity, description, mechanism,
                           clinical_effect, management, evidence_level
                    FROM drug_interactions
                    WHERE (LOWER(drug_a) LIKE ? AND LOWER(drug_b) LIKE ?)
                       OR (LOWER(drug_a) LIKE ? AND LOWER(drug_b) LIKE ?)
                """, (f"%{a.lower()}%", f"%{b.lower()}%",
                      f"%{b.lower()}%", f"%{a.lower()}%"))

                for row in cursor.fetchall():
                    interactions.append({
                        "drug_a": row["drug_a"],
                        "drug_b": row["drug_b"],
                        "severity": row["severity"],
                        "description": row["description"],
                        "mechanism": row["mechanism"],
                        "clinical_effect": row["clinical_effect"],
                        "management": row["management"],
                        "evidence_level": row["evidence_level"],
                        "source": "internal_db",
                    })

        conn.close()

        # 식약처 DUR 정본 병용금기 병합(권위 우선) — 한글 성분명 입력 시 매칭.
        # 내부 DB(LLM 시드)는 보조, DUR은 정본이므로 source로 구분해 함께 노출.
        try:
            from core import dur as _dur
            dres = _dur.check(list(drug_names))
            existing = {tuple(sorted((i["drug_a"], i["drug_b"]))) for i in interactions}
            for a in dres.get("contraindications", []):
                key = tuple(sorted((a["drug_a"], a["drug_b"])))
                if key in existing:
                    continue
                interactions.append({
                    "drug_a": a["drug_a"], "drug_b": a["drug_b"],
                    "severity": "contraindicated", "description": a["reason"],
                    "mechanism": "", "clinical_effect": "", "management": "병용금기 — 병용 회피",
                    "evidence_level": "authoritative", "source": "식약처DUR",
                })
        except Exception as e:  # noqa: BLE001
            logger.warning(f"DUR merge in interaction check: {e}")

        has_major = any(i["severity"] in ("major", "contraindicated") for i in interactions)

        return {
            "interactions": interactions,
            "drugs_checked": len(normalized),
            "interaction_count": len(interactions),
            "has_major": has_major,
        }
    except Exception as e:
        logger.error(f"Drug interaction check error: {e}")
        return {"interactions": [], "drugs_checked": len(drug_names),
                "interaction_count": 0, "has_major": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Code Syntax Validation (v2.37.0)
# ---------------------------------------------------------------------------

def extract_code_block(response, language=None):
    """LLM 응답에서 코드 블록 추출

    Args:
        response: LLM 응답 텍스트
        language: 힌트 언어 (python, javascript 등)

    Returns:
        str: 추출된 코드 (블록 없으면 전체 응답)
    """
    if not response:
        return ""

    # ```language ... ``` 블록 추출
    patterns = [
        r'```(?:' + (language or r'\w*') + r')\s*\n([\s\S]*?)```',
        r'```\s*\n([\s\S]*?)```',
    ]
    for pat in patterns:
        match = re.search(pat, response)
        if match:
            return match.group(1).strip()

    return response.strip()


def validate_python_syntax(code):
    """Python 코드 구문 검증 (ast.parse)

    Returns:
        (passed: bool, detail: str)
    """
    import ast
    try:
        ast.parse(code)
        return True, "syntax OK"
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg}"


def validate_code_syntax(response, language="python"):
    """generate_with_validation 호환 코드 검증기

    Args:
        response: LLM 응답 (코드 블록 포함)
        language: 언어 (현재 python만 지원)

    Returns:
        (passed: bool, reason: str)
    """
    code = extract_code_block(response, language)
    if not code:
        return True, "no code block found (skip validation)"

    if language and language.lower() in ("python", "py"):
        return validate_python_syntax(code)

    # 지원하지 않는 언어는 통과
    return True, f"syntax validation not supported for {language}"


def make_code_validator(language="python"):
    """특정 언어의 코드 검증기 생성 (generate_with_validation 호환)"""
    def validator(response):
        return validate_code_syntax(response, language)
    return validator


# ---------------------------------------------------------------------------
# Code Security Scan (v2.38.0)
# ---------------------------------------------------------------------------

SECURITY_PATTERNS = [
    # Python
    {"pattern": r'\beval\s*\(', "severity": "critical", "rule": "eval-usage",
     "message": "eval() 사용 — 임의 코드 실행 위험", "languages": ["python"]},
    {"pattern": r'\bexec\s*\(', "severity": "critical", "rule": "exec-usage",
     "message": "exec() 사용 — 임의 코드 실행 위험", "languages": ["python"]},
    {"pattern": r'subprocess\.(?:call|run|Popen)\s*\(.*shell\s*=\s*True',
     "severity": "critical", "rule": "shell-injection",
     "message": "subprocess shell=True — 명령어 인젝션 위험", "languages": ["python"]},
    {"pattern": r'os\.system\s*\(', "severity": "high", "rule": "os-system",
     "message": "os.system() — subprocess 사용 권장", "languages": ["python"]},
    {"pattern": r'pickle\.loads?\s*\(', "severity": "high", "rule": "pickle-deserialize",
     "message": "pickle 역직렬화 — 임의 코드 실행 위험", "languages": ["python"]},
    {"pattern": r'__import__\s*\(', "severity": "high", "rule": "dynamic-import",
     "message": "__import__() — 동적 모듈 로딩 위험", "languages": ["python"]},
    # SQL Injection (all languages)
    {"pattern": r'f["\'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP).*\{',
     "severity": "critical", "rule": "sql-injection",
     "message": "f-string SQL — SQL 인젝션 위험. 파라미터 바인딩 사용", "languages": ["python"]},
    {"pattern": r'["\'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP).*["\']\s*%\s*',
     "severity": "critical", "rule": "sql-injection-format",
     "message": "% 포맷 SQL — SQL 인젝션 위험. 파라미터 바인딩 사용", "languages": ["python"]},
    {"pattern": r'\.format\s*\(.*(?:SELECT|INSERT|UPDATE|DELETE)',
     "severity": "critical", "rule": "sql-injection-format2",
     "message": ".format() SQL — SQL 인젝션 위험", "languages": ["python"]},
    # XSS / Injection (web)
    {"pattern": r'innerHTML\s*=', "severity": "high", "rule": "xss-innerhtml",
     "message": "innerHTML 직접 할당 — XSS 위험", "languages": ["javascript", "typescript"]},
    {"pattern": r'dangerouslySetInnerHTML', "severity": "high", "rule": "xss-react",
     "message": "dangerouslySetInnerHTML — XSS 위험", "languages": ["javascript", "typescript"]},
    # Secrets
    {"pattern": r'(?:password|secret|api_key|token)\s*=\s*["\'][^"\']{8,}',
     "severity": "high", "rule": "hardcoded-secret",
     "message": "하드코딩된 비밀값 — 환경 변수 사용 권장", "languages": []},
]


def scan_code_security(code, language="python"):
    """코드 보안 패턴 스캔

    Args:
        code: 소스 코드 문자열
        language: 프로그래밍 언어

    Returns:
        {
            "issues": [{"rule": str, "severity": str, "message": str, "line": int}],
            "issue_count": int,
            "has_critical": bool
        }
    """
    if not code:
        return {"issues": [], "issue_count": 0, "has_critical": False}

    lang = language.lower() if language else ""
    issues = []

    for pat in SECURITY_PATTERNS:
        # Language filter (empty = all languages)
        if pat["languages"] and lang not in pat["languages"]:
            continue

        for match in re.finditer(pat["pattern"], code, re.IGNORECASE):
            # Find line number
            line_num = code[:match.start()].count("\n") + 1
            issues.append({
                "rule": pat["rule"],
                "severity": pat["severity"],
                "message": pat["message"],
                "line": line_num,
                "match": match.group()[:60],
            })

    # Deduplicate by rule+line
    seen = set()
    unique = []
    for i in issues:
        key = (i["rule"], i["line"])
        if key not in seen:
            seen.add(key)
            unique.append(i)

    return {
        "issues": unique,
        "issue_count": len(unique),
        "has_critical": any(i["severity"] == "critical" for i in unique),
    }
