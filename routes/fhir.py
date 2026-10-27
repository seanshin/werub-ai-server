"""FHIR-Lite 래퍼 Blueprint — 기존 의료 API 응답을 FHIR R4 JSON으로 변환

HIS/EMR 시스템이 FHIR R4 표준으로 AI 결과를 소비할 수 있도록 래퍼 레이어를 제공합니다.
기존 API를 직접 호출한 뒤 결과를 FHIR Bundle/Resource로 변환합니다.

Endpoints:
  POST /api/fhir/DiagnosticReport       — 추론 체인 → FHIR DiagnosticReport
  POST /api/fhir/DocumentReference       — 자동 의무기록 → FHIR DocumentReference
  POST /api/fhir/RiskAssessment          — 예측 분석 → FHIR RiskAssessment
  POST /api/fhir/Observation/drug-check  — 약물 상호작용 → FHIR Observation
  POST /api/fhir/Condition/search        — KCD 코드 검색 → FHIR Condition
  GET  /api/fhir/metadata                — CapabilityStatement (서버 지원 리소스)
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from core.security import require_auth

logger = logging.getLogger("ollama-admin")

fhir_bp = Blueprint("fhir", __name__)

FHIR_VERSION = "4.0.1"
SYSTEM_URL = "https://example.org/ollama/fhir"


# ---------------------------------------------------------------------------
# FHIR Resource Helpers
# ---------------------------------------------------------------------------

def _fhir_id():
    return str(uuid.uuid4())


def _fhir_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _fhir_meta(profile=None):
    meta = {"versionId": "1", "lastUpdated": _fhir_now()}
    if profile:
        meta["profile"] = [profile]
    return meta


def _fhir_bundle(entries, bundle_type="collection"):
    return {
        "resourceType": "Bundle",
        "id": _fhir_id(),
        "meta": _fhir_meta(),
        "type": bundle_type,
        "timestamp": _fhir_now(),
        "total": len(entries),
        "entry": entries,
    }


def _fhir_entry(resource):
    return {
        "fullUrl": f"urn:uuid:{resource.get('id', _fhir_id())}",
        "resource": resource,
    }


def _coding(system, code, display=None):
    c = {"system": system, "code": code}
    if display:
        c["display"] = display
    return c


# ---------------------------------------------------------------------------
# FHIR CapabilityStatement
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/metadata")
@require_auth
def fhir_metadata():
    """FHIR CapabilityStatement — 서버 지원 리소스 목록
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    responses:
      200:
        description: FHIR CapabilityStatement
        schema:
          type: object
          properties:
            resourceType: {type: string}
            id: {}
            status: {type: string}
            date: {}
            kind: {type: string}
            software: {type: object}
            fhirVersion: {}
            format: {type: array}
            rest: {type: array}
    """
    return jsonify({
        "resourceType": "CapabilityStatement",
        "id": _fhir_id(),
        "status": "active",
        "date": _fhir_now(),
        "kind": "instance",
        "software": {
            "name": "WeRU.B Medical AI FHIR Server",
            "version": "1.0.0",
        },
        "fhirVersion": FHIR_VERSION,
        "format": ["json"],
        "rest": [{
            "mode": "server",
            "resource": [
                {"type": "DiagnosticReport", "interaction": [{"code": "create"}],
                 "documentation": "추론 체인 → FHIR DiagnosticReport"},
                {"type": "DocumentReference", "interaction": [{"code": "create"}],
                 "documentation": "자동 의무기록 → FHIR DocumentReference"},
                {"type": "RiskAssessment", "interaction": [{"code": "create"}],
                 "documentation": "예측 분석 → FHIR RiskAssessment"},
                {"type": "Observation", "interaction": [{"code": "create"}],
                 "documentation": "약물 상호작용 → FHIR Observation"},
                {"type": "Condition", "interaction": [{"code": "search-type"}],
                 "documentation": "KCD 코드 검색 → FHIR Condition"},
            ],
        }],
    })


# ---------------------------------------------------------------------------
# DiagnosticReport — 추론 체인 결과
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/DiagnosticReport", methods=["POST"])
@require_auth
def fhir_diagnostic_report():
    """추론 체인 → FHIR DiagnosticReport (감별진단 + 검사 권고)
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_text]
          properties:
            patient_text: {type: string, description: "환자 호소 자유 텍스트"}
            patient_id: {type: string, description: "FHIR Patient ID (선택)"}
            encounter_id: {type: string, description: "FHIR Encounter ID (선택)"}
            specialty: {type: string}
            patient_info: {type: object}
            model: {type: string}
    responses:
      200:
        description: FHIR DiagnosticReport Bundle
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    # 추론 체인 실행
    from core.reasoning_chain import run_reasoning_chain
    chain = run_reasoning_chain(
        patient_text,
        model=data.get("model"),
        specialty=data.get("specialty"),
        patient_info=data.get("patient_info"),
    )

    # FHIR 변환
    report_id = _fhir_id()
    patient_ref = f"Patient/{data.get('patient_id', 'unknown')}"
    encounter_ref = f"Encounter/{data.get('encounter_id', 'unknown')}" if data.get("encounter_id") else None

    # 감별진단 → Observation entries
    observations = []
    steps = chain.get("steps", [])
    ranking_step = next((s for s in steps if s["step"] == "ranking"), None)
    if ranking_step and isinstance(ranking_step.get("result"), list):
        for dx in ranking_step["result"]:
            obs_id = _fhir_id()
            obs = {
                "resourceType": "Observation",
                "id": obs_id,
                "meta": _fhir_meta(),
                "status": "preliminary",
                "category": [{"coding": [_coding(
                    "http://terminology.hl7.org/CodeSystem/observation-category",
                    "exam", "Exam")]}],
                "code": {"coding": [_coding(
                    "http://hl7.org/fhir/sid/icd-10",
                    dx.get("icd10", ""),
                    dx.get("diagnosis_ko", ""))],
                    "text": dx.get("diagnosis_ko", "")},
                "subject": {"reference": patient_ref},
                "valueString": dx.get("clinical_pearls", ""),
                "interpretation": [{"coding": [_coding(
                    f"{SYSTEM_URL}/confidence",
                    dx.get("confidence", "medium"),
                    f"Score: {dx.get('score', 0)}")]}],
            }
            observations.append(obs)

    # 최종 평가 → DiagnosticReport
    assessment = chain.get("final_assessment", {})
    primary = assessment.get("primary_diagnosis", {}) if isinstance(assessment, dict) else {}

    conclusion_parts = []
    if isinstance(primary, dict) and primary.get("diagnosis_ko"):
        conclusion_parts.append(f"1차 진단: {primary['diagnosis_ko']} ({primary.get('icd10', '')})")
        conclusion_parts.append(f"신뢰도: {primary.get('confidence', '')}")
    if isinstance(assessment, dict):
        conclusion_parts.append(f"긴급도: {assessment.get('urgency', '')}")
        if assessment.get("immediate_actions"):
            conclusion_parts.append(f"즉시 조치: {', '.join(assessment['immediate_actions'][:3])}")

    report = {
        "resourceType": "DiagnosticReport",
        "id": report_id,
        "meta": _fhir_meta("http://hl7.org/fhir/StructureDefinition/DiagnosticReport"),
        "status": "preliminary",
        "category": [{"coding": [_coding(
            "http://terminology.hl7.org/CodeSystem/v2-0074",
            "RAD", "AI Clinical Analysis")]}],
        "code": {"coding": [_coding(
            f"{SYSTEM_URL}/procedure",
            "reasoning-chain", "AI 5-Step Reasoning Chain")],
            "text": "AI 5단계 추론 체인 감별진단"},
        "subject": {"reference": patient_ref},
        "issued": _fhir_now(),
        "conclusion": "\n".join(conclusion_parts),
        "conclusionCode": [],
        "result": [{"reference": f"Observation/{obs['id']}"} for obs in observations],
    }

    if encounter_ref:
        report["encounter"] = {"reference": encounter_ref}

    if isinstance(primary, dict) and primary.get("icd10"):
        report["conclusionCode"].append({
            "coding": [_coding("http://hl7.org/fhir/sid/icd-10",
                               primary["icd10"], primary.get("diagnosis_ko", ""))]
        })

    # 검사 권고 → ServiceRequest 리소스
    service_requests = []
    tests = chain.get("recommended_tests", {})
    immediate_tests = tests.get("immediate", []) if isinstance(tests, dict) else []
    for t in immediate_tests[:5]:
        sr = {
            "resourceType": "ServiceRequest",
            "id": _fhir_id(),
            "meta": _fhir_meta(),
            "status": "draft",
            "intent": "proposal",
            "priority": "urgent" if t.get("priority") == "urgent" else "routine",
            "code": {"text": t.get("test", "")},
            "subject": {"reference": patient_ref},
            "reasonCode": [{"text": t.get("purpose", "")}],
        }
        service_requests.append(sr)

    # Bundle 구성
    entries = [_fhir_entry(report)]
    for obs in observations:
        entries.append(_fhir_entry(obs))
    for sr in service_requests:
        entries.append(_fhir_entry(sr))

    bundle = _fhir_bundle(entries, "collection")
    bundle["_werub"] = {
        "chain_id": chain.get("chain_id"),
        "model": chain.get("model"),
        "total_tokens": chain.get("total_tokens"),
        "total_duration_ms": chain.get("total_duration_ms"),
    }

    return jsonify(bundle)


# ---------------------------------------------------------------------------
# DocumentReference — 자동 의무기록
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/DocumentReference", methods=["POST"])
@require_auth
def fhir_document_reference():
    """자동 의무기록 → FHIR DocumentReference (SOAP + NER)
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [conversation]
          properties:
            conversation: {type: string, description: "진료 대화 텍스트"}
            patient_id: {type: string}
            encounter_id: {type: string}
            specialty: {type: string}
            patient_info: {type: object}
            model: {type: string}
    responses:
      200:
        description: FHIR DocumentReference
    """
    data = request.json or {}
    conversation = data.get("conversation", "").strip()
    if not conversation:
        return jsonify({"error": "conversation is required"}), 400

    # 자동 의무기록 API 호출 (내부)
    import requests as http_req
    from core.config import get_self_base
    base = get_self_base()
    try:
        r = http_req.post(f"{base}/api/medical/auto-record", json={
            "conversation": conversation,
            "model": data.get("model"),
            "specialty": data.get("specialty"),
            "patient_info": data.get("patient_info"),
        }, timeout=120)
        record = r.json()
    except Exception as e:
        return jsonify({"error": f"auto-record failed: {e}"}), 502

    patient_ref = f"Patient/{data.get('patient_id', 'unknown')}"

    # SOAP → FHIR DocumentReference
    soap = record.get("soap", {})
    soap_text = ""
    if isinstance(soap, dict):
        for section in ("subjective", "objective", "assessment", "plan"):
            val = soap.get(section, "")
            soap_text += f"[{section.upper()}]\n{val}\n\n"

    import base64
    doc = {
        "resourceType": "DocumentReference",
        "id": _fhir_id(),
        "meta": _fhir_meta("http://hl7.org/fhir/StructureDefinition/DocumentReference"),
        "status": "current",
        "type": {"coding": [_coding(
            "http://loinc.org", "11506-3", "Progress note")]},
        "category": [{"coding": [_coding(
            "http://loinc.org", "11506-3", "SOAP Note")]}],
        "subject": {"reference": patient_ref},
        "date": _fhir_now(),
        "author": [{"display": "WeRU.B Medical AI"}],
        "description": record.get("chief_complaint", "AI 자동 생성 의무기록"),
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "language": "ko",
                "data": base64.b64encode(soap_text.encode()).decode(),
                "title": "SOAP Note (AI Generated)",
            },
        }],
        "context": {},
    }

    if data.get("encounter_id"):
        doc["context"]["encounter"] = [{"reference": f"Encounter/{data['encounter_id']}"}]

    # ICD 코드 → context.related
    icd_codes = record.get("icd_codes", [])
    if icd_codes:
        doc["context"]["related"] = [
            {"reference": f"Condition/{code}", "display": code}
            for code in icd_codes[:5]
        ]

    # 처방 → MedicationRequest
    med_requests = []
    for rx in record.get("prescriptions", [])[:10]:
        mr = {
            "resourceType": "MedicationRequest",
            "id": _fhir_id(),
            "meta": _fhir_meta(),
            "status": "draft",
            "intent": "proposal",
            "medicationCodeableConcept": {"text": rx.get("drug", "")},
            "subject": {"reference": patient_ref},
            "dosageInstruction": [{
                "text": f"{rx.get('dose', '')} {rx.get('route', '')} {rx.get('frequency', '')}",
                "timing": {"code": {"text": rx.get("frequency", "")}},
                "route": {"text": rx.get("route", "")},
                "doseAndRate": [{"doseQuantity": {"value": 0, "unit": rx.get("dose", "")}}],
            }],
        }
        if rx.get("duration"):
            mr["dosageInstruction"][0]["timing"]["repeat"] = {"boundsPeriod": {"start": _fhir_now()}}
        med_requests.append(mr)

    entries = [_fhir_entry(doc)]
    for mr in med_requests:
        entries.append(_fhir_entry(mr))

    bundle = _fhir_bundle(entries)
    bundle["_werub"] = {
        "record_id": record.get("record_id"),
        "model": record.get("model"),
        "tokens": record.get("tokens"),
        "duration_ms": record.get("duration_ms"),
        "completeness": record.get("completeness"),
        "validation": record.get("validation"),
    }

    return jsonify(bundle)


# ---------------------------------------------------------------------------
# RiskAssessment — 예측 분석
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/RiskAssessment", methods=["POST"])
@require_auth
def fhir_risk_assessment():
    """예측 분석 → FHIR RiskAssessment (재입원/합병증/ADR)
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [type]
          properties:
            type: {type: string, enum: [readmission, complication, adverse-drug], description: "예측 유형"}
            patient_id: {type: string}
            patient_data: {type: object, description: "readmission용 환자 데이터"}
            diagnosis: {type: string, description: "complication용 주진단"}
            medications: {type: array, items: {type: string}, description: "adverse-drug용 약물 목록"}
            patient_info: {type: object}
            model: {type: string}
    responses:
      200:
        description: FHIR RiskAssessment
    """
    data = request.json or {}
    pred_type = data.get("type", "").strip()
    if pred_type not in ("readmission", "complication", "adverse-drug"):
        return jsonify({"error": "type must be readmission/complication/adverse-drug"}), 400

    import requests as http_req
    from core.config import get_self_base
    base = get_self_base()

    # 적절한 예측 API 호출
    endpoint_map = {
        "readmission": "/api/medical/predict/readmission",
        "complication": "/api/medical/predict/complication",
        "adverse-drug": "/api/medical/predict/adverse-drug",
    }
    payload = {}
    if pred_type == "readmission":
        payload = {"patient_data": data.get("patient_data", {}), "model": data.get("model")}
    elif pred_type == "complication":
        payload = {"diagnosis": data.get("diagnosis", ""), "patient_info": data.get("patient_info", {}), "model": data.get("model")}
    elif pred_type == "adverse-drug":
        payload = {"medications": data.get("medications", []), "patient_info": data.get("patient_info", {}), "model": data.get("model")}

    try:
        r = http_req.post(f"{base}{endpoint_map[pred_type]}", json=payload, timeout=120)
        result = r.json()
    except Exception as e:
        return jsonify({"error": f"prediction failed: {e}"}), 502

    prediction = result.get("prediction", {})
    patient_ref = f"Patient/{data.get('patient_id', 'unknown')}"

    # FHIR RiskAssessment
    risk_level = prediction.get("risk_level") or prediction.get("overall_risk", "medium")
    probability_map = {"low": 0.1, "medium": 0.4, "high": 0.7}

    method_map = {
        "readmission": ("HOSPITAL Score", "30일 재입원 위험도"),
        "complication": ("Clinical Assessment", "합병증 발생 위험도"),
        "adverse-drug": ("Drug Interaction Analysis", "약물 부작용 위험도"),
    }

    ra = {
        "resourceType": "RiskAssessment",
        "id": _fhir_id(),
        "meta": _fhir_meta("http://hl7.org/fhir/StructureDefinition/RiskAssessment"),
        "status": "final",
        "subject": {"reference": patient_ref},
        "occurrenceDateTime": _fhir_now(),
        "method": {"coding": [_coding(
            f"{SYSTEM_URL}/method",
            pred_type,
            method_map[pred_type][0])],
            "text": method_map[pred_type][1]},
        "prediction": [{
            "outcome": {"text": method_map[pred_type][1]},
            "qualitativeRisk": {"coding": [_coding(
                "http://terminology.hl7.org/CodeSystem/risk-probability",
                risk_level, risk_level)]},
            "probabilityDecimal": probability_map.get(risk_level, 0.5),
        }],
        "note": [],
    }

    # 상세 정보를 note에 추가
    if isinstance(prediction, dict):
        for key in ("risk_factors", "preventive_measures", "monitoring_plan", "key_monitoring_parameters"):
            items = prediction.get(key, [])
            if items and isinstance(items, list):
                ra["note"].append({"text": f"[{key}] {'; '.join(str(i) for i in items[:5])}"})
        if prediction.get("disclaimer"):
            ra["note"].append({"text": prediction["disclaimer"]})

    # 합병증 목록 → 개별 prediction entries
    complications = prediction.get("complications", [])
    for comp in complications[:5]:
        if isinstance(comp, dict):
            ra["prediction"].append({
                "outcome": {"text": comp.get("name", "")},
                "qualitativeRisk": {"coding": [_coding(
                    "http://terminology.hl7.org/CodeSystem/risk-probability",
                    comp.get("probability", "medium"),
                    comp.get("probability", ""))]},
                "whenRange": {"low": {"value": 0, "unit": comp.get("timeframe", "")}},
            })

    bundle = _fhir_bundle([_fhir_entry(ra)])
    bundle["_werub"] = {
        "prediction_type": pred_type,
        "model": result.get("model"),
        "tokens": result.get("tokens"),
        "duration_ms": result.get("duration_ms"),
    }

    return jsonify(bundle)


# ---------------------------------------------------------------------------
# Observation — 약물 상호작용
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/Observation/drug-check", methods=["POST"])
@require_auth
def fhir_drug_check():
    """약물 상호작용 → FHIR Observation Bundle
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [medications]
          properties:
            medications: {type: array, items: {type: string}}
            patient_id: {type: string}
    responses:
      200:
        description: FHIR Observation Bundle (약물 상호작용)
        schema:
          type: object
          properties:
            resourceType: {type: string}
            id: {}
            meta: {}
            type: {}
            timestamp: {}
            total: {type: integer}
            entry: {}
    """
    data = request.json or {}
    medications = data.get("medications", [])
    if not medications:
        return jsonify({"error": "medications required"}), 400

    from core.medical_validator import check_drug_interaction_warnings
    _, message, details = check_drug_interaction_warnings({"medications": medications})

    patient_ref = f"Patient/{data.get('patient_id', 'unknown')}"
    interactions = details.get("interactions", [])

    observations = []
    for ix in interactions:
        obs = {
            "resourceType": "Observation",
            "id": _fhir_id(),
            "meta": _fhir_meta(),
            "status": "final",
            "category": [{"coding": [_coding(
                "http://terminology.hl7.org/CodeSystem/observation-category",
                "laboratory", "Drug Interaction Check")]}],
            "code": {"coding": [_coding(
                "http://loinc.org", "69463-4", "Drug interaction")],
                "text": f"{ix['drug_a']} + {ix['drug_b']}"},
            "subject": {"reference": patient_ref},
            "valueString": ix.get("management", ""),
            "interpretation": [{"coding": [_coding(
                f"{SYSTEM_URL}/severity",
                ix.get("severity", "unknown"),
                ix.get("severity", ""))]}],
        }
        observations.append(obs)

    # 상호작용 없으면 정상 Observation
    if not observations:
        obs = {
            "resourceType": "Observation",
            "id": _fhir_id(),
            "meta": _fhir_meta(),
            "status": "final",
            "code": {"coding": [_coding("http://loinc.org", "69463-4", "Drug interaction")],
                     "text": "약물 상호작용 검사"},
            "subject": {"reference": patient_ref},
            "valueString": "주요 상호작용 없음",
        }
        observations.append(obs)

    return jsonify(_fhir_bundle([_fhir_entry(o) for o in observations]))


# ---------------------------------------------------------------------------
# Condition — KCD 코드 검색
# ---------------------------------------------------------------------------

@fhir_bp.route("/api/fhir/Condition/search", methods=["POST"])
@require_auth
def fhir_condition_search():
    """KCD 코드 검색 → FHIR Condition Bundle
    ---
    tags: [FHIR - Interoperability]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: "검색어 (한글 질병명 또는 ICD-10 코드)"}
            patient_id: {type: string}
    responses:
      200:
        description: FHIR Condition Bundle
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    import requests as http_req
    from core.config import get_self_base
    try:
        r = http_req.post(f"{get_self_base()}/api/medical/code/kcd",
                          json={"query": query}, timeout=10)
        results = r.json().get("results", [])
    except Exception:
        results = []

    patient_ref = f"Patient/{data.get('patient_id', 'unknown')}"

    conditions = []
    for item in results[:10]:
        cond = {
            "resourceType": "Condition",
            "id": _fhir_id(),
            "meta": _fhir_meta(),
            "clinicalStatus": {"coding": [_coding(
                "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "active", "Active")]},
            "code": {
                "coding": [_coding(
                    "http://hl7.org/fhir/sid/icd-10",
                    item.get("code", ""),
                    item.get("name_ko", item.get("name", "")))],
                "text": item.get("name_ko", item.get("name", "")),
            },
            "subject": {"reference": patient_ref},
        }
        if item.get("category"):
            cond["category"] = [{"text": item["category"]}]
        conditions.append(cond)

    bundle = _fhir_bundle([_fhir_entry(c) for c in conditions], "searchset")
    return jsonify(bundle)
