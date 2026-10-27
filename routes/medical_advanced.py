"""Medical Advanced AI Blueprint — Phase 4: 추론 체인 + 자동 의무기록 + 멀티모달 강화 + 예측 분석

API Endpoints:
  POST /api/medical/reasoning/chain          — 5단계 추론 체인 (동기)
  POST /api/medical/reasoning/chain/stream   — 5단계 추론 체인 (SSE 스트리밍)
  POST /api/medical/auto-record              — 진료 대화 → 자동 의무기록
  POST /api/medical/auto-record/stream       — 자동 의무기록 (SSE)
  POST /api/vision/medical/structured-report — 구조화된 영상 판독 보고서
  POST /api/medical/predict/readmission      — 재입원 위험도 예측
  POST /api/medical/predict/complication     — 합병증 위험도 예측
  POST /api/medical/predict/adverse-drug     — 약물 부작용 예측
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response
from core.rate_limit import heavy_limit
from core.security import require_auth
from core import dur as dur_mod
from core.korean import force_korean, force_korean_json
from core.korean_humanize import compute_korean_quality, get_quality_summary

logger = logging.getLogger("ollama-admin")

medical_advanced_bp = Blueprint("medical_advanced", __name__)


def _sse_generation_guard(gen):
    """SSE 스트림의 생성 실패를 `error` 이벤트로 드러낸다(A11·2026-09-27).

    헤더가 나간 뒤라 app_errorhandler가 닿지 않는다 — 종전엔 실패가 `"Error: …"` 문자열로
    다음 단계 프롬프트에 섞여 들어가 **정상 완료처럼** 흘렀다.
    """
    from routes.medical import MedicalGenerationError, medical_generation_error_event
    try:
        yield from gen
    except MedicalGenerationError as e:
        yield f"data: {json.dumps(medical_generation_error_event(e), ensure_ascii=False)}\n\n"

# Injected via init
_ollama_session = None
_OLLAMA_BASE = None
_medical_generate = None


def _json_acceptable(text):
    """JSON 계약 경로의 **캐시 수용** 판정 — 정본은 `routes/medical._json_acceptable`.

    깨진 출력을 캐시하면 일과성 결함이 TTL 동안 고정 오답이 된다(2026-08-10 ADR 실측).
    여기서는 위임만 한다 — 판정 규칙이 두 곳에 생기면 곧 갈라진다.
    """
    from routes.medical import _json_acceptable as _impl
    return _impl(text)




def _require_json(response, action, model, *, array=False):
    """주 결과의 JSON 계약 강제 — 파싱 못 하면 `MedicalGenerationError` 502(v2.125.72).

    `_parse_prediction`(v2.125.71·예측 3종)을 같은 클래스 전 경로로 넓힌 것. 차이: 판정이 `parse_json_strict`라
    정상 JSON `[]`·`{}`는 성공으로 본다(캐시 수용 판정 `_json_acceptable`은 빈 것을 거절한다 — 용도가 다르다).
    ⚠️ 로그에 응답 내용은 남기지 않는다(PHI). SSE 경로에서는 `_sse_generation_guard`가 error 이벤트로 바꾼다.
    """
    from core.quality import parse_json_strict, parse_json_array_strict
    v = (parse_json_array_strict if array else parse_json_strict)(response)
    if v is None:
        from routes.medical import MedicalGenerationError
        logger.warning("[medical] %s 구조 미충족 → 502: model=%s len=%d", action, model, len(response or ""))
        raise MedicalGenerationError(f"{action}: model output is not the promised JSON", status=502)
    return force_korean_json(v)

def _parse_prediction(response, action, model):
    """예측 3종(readmission·complication·adverse-drug)의 JSON 계약 강제 — 못 지키면 502(v2.125.71).

    🔴 종전엔 모델 출력이 JSON이 아니어도 `force_json`이 `{"response": 원문}`으로 감싸 **200**으로 나갔다
       (반복 클래스 #1). 2026-10-07 HIS 실측: complication 13:51:14 200 + 구조 미충족 · 14일 로그 8건
       (readmission 7·complication 1)이 같은 경로로 '성공'처리됐다. Swagger가 약속한 `prediction` 객체가 아니다.
    판정은 캐시 수용과 **같은 함수**(`_json_acceptable` — 센티넬로 force_json 폴백을 가른다)를 쓴다.
    ⚠️ 로그에 응답 **내용을 남기지 않는다** — 예측 출력은 환자 정보를 되풀이할 수 있다(HIS PHI 비저장 합의). 길이만.
    재시도는 넣지 않았다(효과 미확인 — 필요하면 발생률을 재고 사전 등록 후).
    """
    if not _json_acceptable(response):
        from routes.medical import MedicalGenerationError
        logger.warning("[medical] %s 구조 미충족 → 502: model=%s len=%d", action, model, len(response or ""))
        raise MedicalGenerationError(f"{action}: model output is not the promised JSON", status=502)
    from core.quality import force_json
    return force_korean_json(force_json(response))


_BASE_DIR = None


def init_medical_advanced(*, ollama_session, ollama_base, medical_generate_fn, base_dir):
    global _ollama_session, _OLLAMA_BASE, _medical_generate, _BASE_DIR
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _medical_generate = medical_generate_fn
    _BASE_DIR = base_dir

    from core.reasoning_chain import init_reasoning_chain
    init_reasoning_chain(
        ollama_session=ollama_session,
        ollama_base=ollama_base,
        medical_generate_fn=medical_generate_fn,
    )


# ===========================================================================
# P4-1: 의료 추론 체인 (Chain of Thought)
# ===========================================================================

@medical_advanced_bp.route("/api/medical/reasoning/chain", methods=["POST"])
@heavy_limit("20/minute")
@require_auth
def api_reasoning_chain():
    """5단계 의료 추론 체인 — 증상→감별진단→순위화→검사→평가
    ---
    tags: [Medical - Advanced AI]
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
            model: {type: string, description: "LLM 모델 (기본: heavy)"}
            specialty: {type: string, description: "전문과 (내과/외과/소아과/응급의학과 등)"}
            patient_info:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string, enum: [M, F]}
                conditions: {type: array, items: {type: string}}
                medications: {type: array, items: {type: string}}
    responses:
      200:
        description: 추론 체인 결과
        schema:
          type: object
          properties:
            chain_id: {type: string}
            steps: {type: array}
            final_assessment: {type: object}
            recommended_tests: {type: object}
            validation: {type: object}
            total_tokens: {type: integer}
            total_duration_ms: {type: integer}
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    from core.reasoning_chain import run_reasoning_chain
    from routes.medical import _get_medical_model
    model = _get_medical_model("reasoning_chain", data.get("model"))
    result = run_reasoning_chain(
        patient_text,
        model=model,
        specialty=data.get("specialty"),
        patient_info=data.get("patient_info"),
    )
    return jsonify(result)


@medical_advanced_bp.route("/api/medical/reasoning/chain/stream", methods=["POST"])
@heavy_limit("20/minute")
@require_auth
def api_reasoning_chain_stream():
    """5단계 의료 추론 체인 (SSE 스트리밍) — 단계별 실시간 결과
    ---
    tags: [Medical - Advanced AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_text]
          properties:
            patient_text: {type: string}
            model: {type: string}
            specialty: {type: string}
            patient_info: {type: object}
    responses:
      200:
        description: SSE 스트리밍 (text/event-stream)
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    from core.reasoning_chain import run_reasoning_chain_stream
    from routes.medical import _get_medical_model
    model = _get_medical_model("reasoning_chain", data.get("model"))

    def generate():
        yield from _sse_generation_guard(run_reasoning_chain_stream(
            patient_text,
            model=model,
            specialty=data.get("specialty"),
            patient_info=data.get("patient_info"),
        ))

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@medical_advanced_bp.route("/api/medical/reasoning/ensemble", methods=["POST"])
@heavy_limit("20/minute")
@require_auth
def api_reasoning_ensemble():
    """Multi-Model Ensemble 추론 체인 — 다중 모델 교차 검증
    ---
    tags: [Medical - Advanced AI]
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
            chain_mode: {type: string, enum: [single, multi, consensus], default: multi, description: "single=단일모델 5단계(스왑0·속도우선), multi=단계별 전문모델, consensus=3모델 투표"}
            model: {type: string, description: "single 모드 단일 모델(미지정 시 diagnosis 1순위 werub-medical:7b). 예: qwen2.5:14b 지정 시 상주모델 재사용=스왑0"}
            depth: {type: string, enum: [full, triage], default: full, description: "single 모드 단계 깊이. full=5단계, triage=3단계(normalize→differential→assessment, ranking·tests 생략·컨시어지 속도). multi/consensus는 무시"}
            specialty: {type: string, description: "전문과"}
            patient_info:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string, enum: [M, F]}
                conditions: {type: array, items: {type: string}}
                medications: {type: array, items: {type: string}}
    responses:
      200:
        description: 앙상블 추론 결과
        schema:
          type: object
          properties:
            chain_id: {type: string}
            chain_mode: {type: string}
            steps: {type: array}
            final_assessment: {type: object}
            consensus: {type: object}
            models_used: {type: object}
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    chain_mode = data.get("chain_mode", "multi")
    if chain_mode not in ("single", "multi", "consensus"):
        return jsonify({"error": "chain_mode must be 'single', 'multi' or 'consensus'"}), 400

    from core.reasoning_chain import run_ensemble_chain
    from routes.medical import _get_medical_model
    # single 모드만 모델 선택(스왑 제거). 기본 diagnosis 1순위, model=user 오버라이드 가능.
    single_model = _get_medical_model("reasoning_chain", data.get("model"), patient_text) if chain_mode == "single" else None
    depth = data.get("depth", "full")
    if depth not in ("full", "triage"):
        return jsonify({"error": "depth must be 'full' or 'triage'"}), 400
    result = run_ensemble_chain(
        patient_text,
        chain_mode=chain_mode,
        patient_info=data.get("patient_info"),
        model=single_model,
        depth=depth,
    )
    return jsonify(result)


@medical_advanced_bp.route("/api/medical/reasoning/ensemble/stream", methods=["POST"])
@require_auth
def api_reasoning_ensemble_stream():
    """Multi-Model Ensemble 추론 체인 (SSE 스트리밍) — 단계별 실시간 결과

    /ensemble 과 동일 로직·동일 모델 경로를 단계별 SSE로 방출한다.
    비스트림 블롭(30~90s) 대기를 제거 → 컨시어지 정밀분석 체감 지연 ↓.
    ---
    tags: [Medical - Advanced AI]
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
            chain_mode: {type: string, enum: [single, multi, consensus], default: multi, description: "single=단일모델 5단계(스왑0·속도우선)"}
            model: {type: string, description: "single 모드 단일 모델(미지정 시 werub-medical:7b)"}
            depth: {type: string, enum: [full, triage], default: full, description: "single 모드 단계: triage=3단계(컨시어지 속도)"}
            patient_info: {type: object}
    responses:
      200:
        description: SSE 스트리밍 (text/event-stream) — chain_start/step_start/step_complete/chain_complete
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    chain_mode = data.get("chain_mode", "multi")
    if chain_mode not in ("single", "multi", "consensus"):
        return jsonify({"error": "chain_mode must be 'single', 'multi' or 'consensus'"}), 400

    from core.reasoning_chain import run_ensemble_chain_stream
    from routes.medical import _get_medical_model
    single_model = _get_medical_model("reasoning_chain", data.get("model"), patient_text) if chain_mode == "single" else None
    depth = data.get("depth", "full")
    if depth not in ("full", "triage"):
        return jsonify({"error": "depth must be 'full' or 'triage'"}), 400

    def generate():
        yield from _sse_generation_guard(run_ensemble_chain_stream(
            patient_text,
            chain_mode=chain_mode,
            patient_info=data.get("patient_info"),
            model=single_model,
            depth=depth,
        ))

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ===========================================================================
# P4-3: 자동 의무기록 생성 (NER → SOAP)
# ===========================================================================

AUTO_RECORD_NER_SYSTEM = """당신은 의료 NER(Named Entity Recognition) AI입니다. 반드시 한국어로 답변하세요.

진료 대화 텍스트에서 의료 엔터티를 추출하세요.

[추출 대상]
1. 증상(symptom): 환자 호소 증상
2. 소견(finding): 의사 관찰 소견
3. 진단(diagnosis): 진단명 + ICD-10
4. 약물(medication): 약물명 + 용량 + 용법
5. 검사(test): 검사명 + 결과
6. 시술(procedure): 시술/처치명
7. 활력징후(vital): BP/HR/BT/RR/SpO2

반드시 JSON으로만 출력:
{
  "entities": {
    "symptoms": [{"text": "원문", "normalized": "의학용어", "onset": "시점", "severity": "mild/moderate/severe"}],
    "findings": [{"text": "원문", "normalized": "의학용어", "location": "부위"}],
    "diagnoses": [{"text": "원문", "icd10": "코드", "certainty": "confirmed/suspected/ruled_out"}],
    "medications": [{"name": "약물명", "dose": "용량", "route": "경로", "frequency": "빈도", "duration": "기간"}],
    "tests": [{"name": "검사명", "result": "결과", "normal_range": "정상범위", "abnormal": true/false}],
    "procedures": [{"name": "시술명", "detail": "상세"}],
    "vitals": {"bp": null, "hr": null, "bt": null, "rr": null, "spo2": null}
  },
  "chief_complaint": "주호소 한줄 요약"
}"""

AUTO_RECORD_SOAP_SYSTEM = """당신은 의무기록 자동 생성 AI입니다. 반드시 한국어로 작성하세요.

추출된 NER 데이터를 SOAP 의무기록으로 변환하세요.

[SOAP 규칙]
- Subjective: 환자 호소 증상, 병력 (entities.symptoms 기반)
- Objective: 활력징후, 검사결과, 신체검사 소견 (entities.vitals + findings + tests 기반)
- Assessment: 진단/감별진단 + ICD-10 (entities.diagnoses 기반)
- Plan: 처방, 검사 계획, 추적관찰 (entities.medications + procedures 기반)

[추가]
- 빠진 필수 항목은 "미확인" 표시
- 의학적으로 정확하고 간결하게

반드시 JSON으로만 출력:
{
  "soap": {
    "subjective": "...",
    "objective": "...",
    "assessment": "...",
    "plan": "..."
  },
  "icd_codes": ["I10", "E11.9"],
  "prescriptions": [{"drug": "약물명", "dose": "용량", "route": "경로", "frequency": "빈도", "duration": "기간"}],
  "orders": [{"type": "lab/imaging/referral", "name": "오더명", "priority": "urgent/routine"}],
  "urgency": "low/medium/high/critical",
  "completeness": {"score": 0-100, "missing": ["빠진 항목"]}
}"""


@medical_advanced_bp.route("/api/medical/auto-record", methods=["POST"])
@require_auth
def api_auto_record():
    """진료 대화 → 자동 의무기록 생성 (NER + SOAP)
    ---
    tags: [Medical - Advanced AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [conversation]
          properties:
            conversation: {type: string, description: "진료 대화 텍스트 (의사-환자 대화)"}
            model: {type: string, description: "LLM 모델"}
            specialty: {type: string, description: "전문과"}
            patient_info:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string}
                conditions: {type: array, items: {type: string}}
    responses:
      200:
        description: 자동 생성된 의무기록
        schema:
          type: object
          properties:
            record_id: {type: string}
            entities: {type: object}
            soap: {type: object}
            icd_codes: {type: array}
            prescriptions: {type: array}
    """
    data = request.json or {}
    conversation = data.get("conversation", "").strip()
    if not conversation:
        return jsonify({"error": "conversation is required"}), 400

    from routes.medical import _get_medical_model, SPECIALTY_HINTS
    model = _get_medical_model("draft", data.get("model"))
    specialty = data.get("specialty")
    opts = {"temperature": 0.2, "top_p": 0.9}

    # 환자 정보 보강
    context = conversation
    patient_info = data.get("patient_info")
    if patient_info:
        info_parts = []
        if patient_info.get("age"):
            info_parts.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_parts.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_parts.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_parts:
            context = f"[환자 정보] {', '.join(info_parts)}\n\n[진료 대화]\n{conversation}"

    record_id = str(uuid.uuid4())
    start = time.time()

    # Step 1: NER
    ner_response, ner_tokens, ner_ms = _medical_generate(
        model=model, prompt=context,
        system=AUTO_RECORD_NER_SYSTEM, options=opts, action="auto_record_ner",
    accept=_json_acceptable)
    ner_data = _require_json(ner_response, "auto_record_ner", model)

    # Step 2: SOAP 생성
    soap_prompt = f"""[추출된 엔터티]
{json.dumps(ner_data, ensure_ascii=False, indent=2)}

[원문 대화]
{conversation}"""

    if specialty and specialty in SPECIALTY_HINTS:
        soap_prompt += SPECIALTY_HINTS[specialty]

    soap_response, soap_tokens, soap_ms = _medical_generate(
        model=model, prompt=soap_prompt,
        system=AUTO_RECORD_SOAP_SYSTEM, options=opts, action="auto_record_soap",
    accept=_json_acceptable)
    soap_data = _require_json(soap_response, "auto_record_soap", model)

    # 환각 검증
    from core.medical_validator import validate_medical_response
    validation = validate_medical_response(soap_data, action="draft")

    total_ms = int((time.time() - start) * 1000)

    return jsonify({
        "record_id": record_id,
        "entities": ner_data.get("entities", ner_data),
        "chief_complaint": ner_data.get("chief_complaint", ""),
        "soap": soap_data.get("soap", soap_data),
        "icd_codes": soap_data.get("icd_codes", []),
        "prescriptions": soap_data.get("prescriptions", []),
        "orders": soap_data.get("orders", []),
        "urgency": soap_data.get("urgency", "medium"),
        "completeness": soap_data.get("completeness", {}),
        "validation": validation,
        "model": model,
        "tokens": ner_tokens + soap_tokens,
        "duration_ms": total_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@medical_advanced_bp.route("/api/medical/auto-record/stream", methods=["POST"])
@require_auth
def api_auto_record_stream():
    """진료 대화 → 자동 의무기록 (SSE 스트리밍)
    ---
    tags: [Medical - Advanced AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [conversation]
          properties:
            conversation: {type: string}
            model: {type: string}
            specialty: {type: string}
            patient_info: {type: object}
    responses:
      200:
        description: SSE 스트리밍
    """
    data = request.json or {}
    conversation = data.get("conversation", "").strip()
    if not conversation:
        return jsonify({"error": "conversation is required"}), 400

    from routes.medical import _get_medical_model, SPECIALTY_HINTS
    model = _get_medical_model("draft", data.get("model"))
    specialty = data.get("specialty")
    opts = {"temperature": 0.2, "top_p": 0.9}

    context = conversation
    patient_info = data.get("patient_info")
    if patient_info:
        info_parts = []
        if patient_info.get("age"):
            info_parts.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_parts.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_parts.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_parts:
            context = f"[환자 정보] {', '.join(info_parts)}\n\n[진료 대화]\n{conversation}"

    record_id = str(uuid.uuid4())

    def _emit(event_type, payload):
        return f"data: {json.dumps({'event': event_type, 'record_id': record_id, **payload}, ensure_ascii=False)}\n\n"

    def generate():
        yield _emit("start", {"model": model})

        # Step 1: NER
        yield _emit("step_start", {"step": "ner"})
        ner_response, ner_tokens, ner_ms = _medical_generate(
            model=model, prompt=context,
            system=AUTO_RECORD_NER_SYSTEM, options=opts, action="auto_record_ner",
        accept=_json_acceptable)
        ner_data = _require_json(ner_response, "auto_record_ner", model)
        yield _emit("step_complete", {"step": "ner", "result": ner_data, "tokens": ner_tokens, "duration_ms": ner_ms})

        # Step 2: SOAP
        yield _emit("step_start", {"step": "soap"})
        soap_prompt = f"[추출된 엔터티]\n{json.dumps(ner_data, ensure_ascii=False, indent=2)}\n\n[원문 대화]\n{conversation}"
        if specialty and specialty in SPECIALTY_HINTS:
            soap_prompt += SPECIALTY_HINTS[specialty]

        soap_response, soap_tokens, soap_ms = _medical_generate(
            model=model, prompt=soap_prompt,
            system=AUTO_RECORD_SOAP_SYSTEM, options=opts, action="auto_record_soap",
        accept=_json_acceptable)
        soap_data = _require_json(soap_response, "auto_record_soap", model)
        yield _emit("step_complete", {"step": "soap", "result": soap_data, "tokens": soap_tokens, "duration_ms": soap_ms})

        # Validation
        from core.medical_validator import validate_medical_response
        validation = validate_medical_response(soap_data, action="draft")

        yield _emit("complete", {
            "soap": soap_data.get("soap", soap_data),
            "entities": ner_data.get("entities", ner_data),
            "icd_codes": soap_data.get("icd_codes", []),
            "prescriptions": soap_data.get("prescriptions", []),
            "validation": validation,
            "total_tokens": ner_tokens + soap_tokens,
        })

    return Response(_sse_generation_guard(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ===========================================================================
# P4-2: 멀티모달 영상 판독 강화 — 구조화된 보고서
# ===========================================================================

STRUCTURED_REPORT_SYSTEM = """당신은 영상의학과 전문의 수준의 AI 판독 보조 시스템입니다. 반드시 한국어로 답변하세요.

의료 영상을 분석하고 구조화된 판독 보고서를 작성하세요.

[보고서 구조]
1. 기술적 적합성 (Technical adequacy)
2. 소견 (Findings) - 부위별 체계적 기술
3. 비교 소견 (이전 영상 있을 경우)
4. 판독 소견 (Impression)
5. 등급 분류 (해당 시: BI-RADS, Lung-RADS, TI-RADS, LI-RADS)
6. 권고 사항 (Recommendation)

[등급 시스템 적용 기준]
- 유방: BI-RADS 0-6
- 폐: Lung-RADS 1-4X
- 갑상선: TI-RADS TR1-TR5
- 간: LI-RADS LR-1 ~ LR-5

반드시 JSON으로만 출력:
{
  "technical_adequacy": {"adequate": true/false, "limitations": ["제한사항"]},
  "findings": [{"location": "부위", "description": "소견", "significance": "normal/benign/indeterminate/suspicious/malignant"}],
  "comparison": {"available": false, "changes": []},
  "impression": "종합 판독 소견",
  "grading": {"system": "BI-RADS/Lung-RADS/TI-RADS/LI-RADS/없음", "grade": "등급", "description": "등급 설명"},
  "recommendation": {"action": "권고 조치", "urgency": "routine/soon/urgent", "follow_up": "추적 시기"},
  "confidence": 0.0-1.0,
  "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
}"""


# ---------------------------------------------------------------------------
# Research / Literature API (v2.47.0) — OpenBioLLM, PMC-LLaMA, BioMistral
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM = """You are a biomedical research expert. When given a query:
1. Provide evidence-based information from medical literature
2. Cite relevant studies, guidelines, or systematic reviews when possible
3. Distinguish between established evidence and emerging research
4. Note the level of evidence (meta-analysis > RCT > cohort > case report > expert opinion)
5. Provide practical clinical implications
Respond in the same language as the query."""

@medical_advanced_bp.route("/api/medical/research", methods=["POST"])
@require_auth
def api_medical_research():
    """Biomedical literature research query — uses OpenBioLLM/PMC-LLaMA/BioMistral
    ---
    tags: [Medical - Research]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: "연구 질의 (한국어/영어)"}
            model: {type: string, description: "모델 (기본: 도메인 라우팅)"}
            detail_level: {type: string, enum: [brief, standard, comprehensive], default: standard}
    responses:
      200:
        description: 연구 결과
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("research", data.get("model"))

    detail = data.get("detail_level", "standard")
    length_hint = {"brief": "Respond concisely in 200 words.", "standard": "", "comprehensive": "Provide a thorough review in 800+ words."}

    system = RESEARCH_SYSTEM + ("\n" + length_hint.get(detail, "") if length_hint.get(detail) else "")

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt=query, system=system,
        options={"temperature": 0.3, "num_predict": 2048}, action="research",
    )

    response = force_korean(response)
    resp = {
        "response": response, "model": model, "tokens": tokens,
        "duration_ms": duration_ms, "detail_level": detail,
    }
    try:
        if response and len(response) >= 50:
            qr = compute_korean_quality(response, genre="medical")
            resp["quality_metrics"] = get_quality_summary(qr)
    except Exception:
        pass
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Patient Education API (v2.47.0) — MediChat-Llama3
# ---------------------------------------------------------------------------

PATIENT_EDUCATION_SYSTEM = """You are a compassionate medical educator explaining health information to patients.
Rules:
1. Use simple, everyday language — avoid medical jargon
2. If medical terms must be used, explain them in parentheses
3. Be reassuring but honest
4. Structure your response: What is it? → Why does it happen? → What to do? → When to seek help
5. Respond in the same language as the query
6. Add a brief disclaimer at the end"""

@medical_advanced_bp.route("/api/medical/patient-education", methods=["POST"])
@require_auth
def api_patient_education():
    """Patient-friendly medical explanation — uses MediChat-Llama3
    ---
    tags: [Medical - Patient]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [topic]
          properties:
            topic: {type: string, description: "설명할 의학 주제/진단/약물"}
            patient_age: {type: integer, description: "환자 나이 (설명 수준 조절)"}
            lang: {type: string, default: ko, description: "응답 언어"}
            model: {type: string}
    responses:
      200:
        description: 환자용 의학 설명
    """
    data = request.json or {}
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("patient_education", data.get("model"))

    age = data.get("patient_age")
    lang = data.get("lang", "ko")
    prompt = topic
    if age:
        prompt = f"환자 나이: {age}세\n질문: {topic}" if lang == "ko" else f"Patient age: {age}\nTopic: {topic}"

    system = PATIENT_EDUCATION_SYSTEM
    if lang == "ko":
        system += "\n반드시 한국어로 답하세요."

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt=prompt, system=system,
        options={"temperature": 0.4, "num_predict": 1024}, action="patient_education",
    )

    if lang == "ko":
        response = force_korean(response)
    resp = {
        "response": response, "model": model, "tokens": tokens,
        "duration_ms": duration_ms, "lang": lang,
    }
    try:
        if lang == "ko" and response and len(response) >= 50:
            qr = compute_korean_quality(response, genre="medical")
            resp["quality_metrics"] = get_quality_summary(qr)
    except Exception:
        pass
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Clinical QA API (v2.48.0) — Med42 8B
# ---------------------------------------------------------------------------

CLINICAL_QA_SYSTEM = """You are an expert physician answering clinical questions from other medical professionals.
Provide evidence-based, concise answers with:
1. Direct answer to the question
2. Key clinical points (3-5 bullets)
3. Relevant guidelines or scoring systems (name only, no fabricated citations)
4. Practical clinical pearls
Respond in the same language as the question."""

@medical_advanced_bp.route("/api/medical/clinical-qa", methods=["POST"])
@require_auth
def api_clinical_qa():
    """Clinical QA for medical professionals — uses Med42 8B
    ---
    tags: [Medical - Clinical QA]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string, description: "임상 질의"}
            context:
              type: object
              properties:
                specialty: {type: string}
                patient_age: {type: integer}
                patient_sex: {type: string}
            model: {type: string}
            lang: {type: string, default: ko}
    responses:
      200:
        description: 임상 QA 응답
        schema:
          type: object
          properties:
            answer: {}
            model: {}
            tokens: {}
            duration_ms: {}
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("clinical_qa", data.get("model"), input_text=question)

    ctx = data.get("context", {})
    prompt = question
    if ctx:
        ctx_parts = []
        if ctx.get("specialty"): ctx_parts.append(f"전문과: {ctx['specialty']}")
        if ctx.get("patient_age"): ctx_parts.append(f"환자 나이: {ctx['patient_age']}세")
        if ctx.get("patient_sex"): ctx_parts.append(f"환자 성별: {ctx['patient_sex']}")
        if ctx_parts:
            prompt = "[임상 맥락]\n" + "\n".join(ctx_parts) + "\n\n[질문]\n" + question

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt=prompt, system=CLINICAL_QA_SYSTEM,
        options={"temperature": 0.3, "num_predict": 1024}, action="clinical_qa",
    )

    response = force_korean(response)
    return jsonify({
        "answer": response, "model": model, "tokens": tokens,
        "duration_ms": duration_ms,
    })


# ---------------------------------------------------------------------------
# Drug Analysis API (v2.48.0) — meditron + Med42 + OpenBioLLM
# ---------------------------------------------------------------------------

DRUG_INTERACTION_SYSTEM = """You are a clinical pharmacology expert. Analyze the given medications for a specific patient.
Provide:
1. Drug-drug interactions (severity: high/medium/low, mechanism)
2. Contraindications given patient conditions
3. Dose appropriateness
Respond in JSON:
{"interactions": [{"drugs": [...], "severity": "high", "mechanism": "...", "recommendation": "..."}],
 "contraindications": [{"drug": "...", "condition": "...", "risk": "..."}],
 "dose_check": [{"drug": "...", "status": "appropriate|adjust|caution", "note": "..."}]}"""

DRUG_RECOMMENDATION_SYSTEM = """You are a clinical pharmacist. Based on the interaction analysis, provide:
1. Alternative medications for problematic drugs
2. Monitoring parameters
3. Patient counseling points
Respond in JSON:
{"alternatives": [{"current": "...", "alternative": "...", "reason": "..."}],
 "monitoring": [{"parameter": "...", "frequency": "...", "target": "..."}],
 "counseling": ["point1", "point2"]}"""

@medical_advanced_bp.route("/api/medical/drug-analysis", methods=["POST"])
@require_auth
def api_drug_analysis():
    """Multi-model drug analysis — meditron + Med42 + OpenBioLLM
    ---
    tags: [Medical - Drug Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [medications]
          properties:
            medications: {type: array, items: {type: string}, description: "약물 목록"}
            patient:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string}
                conditions: {type: array, items: {type: string}}
                weight_kg: {type: number}
            depth: {type: string, enum: [quick, standard, comprehensive], default: standard}
            model: {type: string}
    responses:
      200:
        description: 약물 분석 결과
    """
    data = request.json or {}
    meds = data.get("medications", [])
    if not meds:
        return jsonify({"error": "medications array is required"}), 400

    patient = data.get("patient", {})
    depth = data.get("depth", "standard")

    patient_context = f"약물: {', '.join(meds)}"
    if patient:
        if patient.get("age"): patient_context += f"\n나이: {patient['age']}세"
        if patient.get("sex"): patient_context += f"\n성별: {patient['sex']}"
        if patient.get("conditions"): patient_context += f"\n기저질환: {', '.join(patient['conditions'])}"

    # 식약처 DUR 정본 교차검증 — LLM 환각 방지(권위 데이터 우선 주입). 환자 컨텍스트 반영.
    dur_authoritative = None
    try:
        dur_authoritative = dur_mod.check(meds, age=patient.get("age"),
                                          pregnant=patient.get("pregnant"))
        if dur_authoritative.get("has_contraindication"):
            cis = "; ".join(f"{a['drug_a']}+{a['drug_b']}({a['reason']})"
                            for a in dur_authoritative["contraindications"])
            patient_context += f"\n\n[식약처 DUR 정본 병용금기 — 반드시 반영하세요]\n{cis}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[drug-analysis] DUR cross-check: {e}")

    models_used = []
    total_tokens = 0
    total_ms = 0

    # Step 1: 상호작용 + 금기 스크리닝 (meditron)
    from routes.medical import _get_medical_model
    model1 = _get_medical_model("clinical_drug", data.get("model"))
    r1, t1, d1 = _medical_generate(
        model=model1, prompt=patient_context, system=DRUG_INTERACTION_SYSTEM,
        options={"temperature": 0.2, "num_predict": 1024}, action="drug_analysis_interaction",
    accept=_json_acceptable)
    models_used.append(model1)
    total_tokens += t1; total_ms += d1

    interaction_result = _require_json(r1, "drug_analysis_interaction", model1)

    result = {"interactions": interaction_result, "depth": depth, "models_used": models_used}

    if depth in ("standard", "comprehensive"):
        # Step 2: 임상 권고 + 대안 (Med42)
        model2 = _get_medical_model("clinical_qa", data.get("model"))
        step2_prompt = f"{patient_context}\n\n[분석 결과]\n{r1}\n\n대안 약물과 모니터링 권고를 제시하세요."
        r2, t2, d2 = _medical_generate(
            model=model2, prompt=step2_prompt, system=DRUG_RECOMMENDATION_SYSTEM,
            options={"temperature": 0.3, "num_predict": 1024}, action="drug_analysis_recommendation",
        accept=_json_acceptable)
        models_used.append(model2)
        total_tokens += t2; total_ms += d2
        # 하위 절 — 1단계(상호작용)는 이미 성공했으니 엔드포인트를 죽이지 않고 절 단위로 표시한다(v2.125.72).
        #   종전엔 `{"response": 원문}`이 권고처럼 나갔다.
        from core.quality import parse_json_strict
        rec = parse_json_strict(r2)
        if rec is None:
            logger.warning("[medical] drug_analysis_recommendation 구조 미충족 — 절 단위 실패: model=%s len=%d", model2, len(r2 or ""))
            result["recommendations"] = None
            result["recommendations_error"] = "model output is not the promised JSON"
        else:
            result["recommendations"] = force_korean_json(rec)

    if depth == "comprehensive":
        # Step 3: 근거 의견 (OpenBioLLM)
        model3 = _get_medical_model("research", data.get("model"))
        step3_prompt = f"Provide biomedical evidence context for these drug interactions:\n{patient_context}\nDo NOT cite specific paper titles or PMIDs. Only describe evidence types (guideline, meta-analysis, RCT level)."
        r3, t3, d3 = _medical_generate(
            model=model3, prompt=step3_prompt,
            system="You are a biomedical research expert. Provide evidence-based context without fabricating citations.",
            options={"temperature": 0.3, "num_predict": 512}, action="drug_analysis_evidence",
        )
        models_used.append(model3)
        total_tokens += t3; total_ms += d3
        result["evidence"] = {"statement": r3, "verified": False, "disclaimer": "AI 참고 의견이며, 실제 가이드라인 확인이 필요합니다."}

    result["total_tokens"] = total_tokens
    result["total_duration_ms"] = total_ms
    result["models_used"] = models_used
    # 정본 DUR 교차검증 결과 동봉(LLM 분석과 별개의 권위 근거)
    result["dur_authoritative"] = dur_authoritative
    return jsonify(result)


@medical_advanced_bp.route("/api/vision/medical/structured-report", methods=["POST"])
@require_auth
def api_vision_structured_report():
    """구조화된 의료 영상 판독 보고서 (BI-RADS/Lung-RADS 등급 포함)
    ---
    tags: [Medical - Advanced AI, Vision]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
        description: "의료 영상 파일"
      - name: previous_image
        in: formData
        type: file
        required: false
        description: "이전 영상 (비교 분석용)"
      - name: modality
        in: formData
        type: string
        required: false
        description: "영상 종류 (xray/ct/mri/ultrasound/mammography)"
      - name: body_part
        in: formData
        type: string
        required: false
        description: "촬영 부위"
      - name: clinical_info
        in: formData
        type: string
        required: false
        description: "임상 정보"
    responses:
      200:
        description: 구조화된 판독 보고서
        schema:
          type: object
          properties:
            report: {}
            comparison: {}
            model: {type: string}
            modality: {}
            body_part: {}
            tokens: {}
            duration_ms: {}
    """
    import base64
    import requests as http_req

    # 이미지 검증 (vision.py의 로직 재사용)
    if "image" not in request.files:
        return jsonify({"error": "image file required", "code": "MISSING_IMAGE"}), 400

    img_file = request.files["image"]
    img_bytes = img_file.read()
    if len(img_bytes) > 20 * 1024 * 1024:
        return jsonify({"error": "image too large (max 20MB)", "code": "FILE_TOO_LARGE"}), 400
    img_b64 = base64.b64encode(img_bytes).decode()

    images = [img_b64]
    comparison = False

    if "previous_image" in request.files:
        prev_file = request.files["previous_image"]
        prev_bytes = prev_file.read()
        if len(prev_bytes) <= 20 * 1024 * 1024:
            images.append(base64.b64encode(prev_bytes).decode())
            comparison = True

    modality = request.form.get("modality", "unknown")
    body_part = request.form.get("body_part", "unknown")
    clinical_info = request.form.get("clinical_info", "")

    # 등급 시스템 자동 선택
    grading_hint = ""
    if modality == "mammography" or body_part == "breast":
        grading_hint = "\n이 영상은 유방 영상입니다. BI-RADS 등급을 반드시 포함하세요."
    elif body_part in ("chest", "lung"):
        grading_hint = "\n이 영상은 폐 영상입니다. Lung-RADS 등급을 적용하세요 (해당 시)."
    elif body_part == "thyroid":
        grading_hint = "\n이 영상은 갑상선 영상입니다. TI-RADS 등급을 적용하세요."
    elif body_part == "liver":
        grading_hint = "\n이 영상은 간 영상입니다. LI-RADS 등급을 적용하세요 (해당 시)."

    prompt = f"""영상 종류: {modality} | 부위: {body_part}
임상 정보: {clinical_info or '없음'}
비교 영상: {'있음 (두 번째 이미지)' if comparison else '없음'}{grading_hint}

의료 영상을 분석하고 구조화된 판독 보고서를 JSON으로 작성하세요."""

    # v2.125.12 D1 슬라이스 2 — task 라우터 경유(페이로드 동일: images 포함). 실패 계약 유지:
    # 예외→502 MODEL_ERROR · HTTP≠200→502 status. (어댑터는 둘 다 Result.ok=False로 돌려준다)
    from core import model_router
    res = model_router.generate(
        "medical.structured_report", prompt, model="medgemma:4b", system=STRUCTURED_REPORT_SYSTEM,
        images=images, options={"num_predict": 2000, "temperature": 0.2}, timeout=180,
        adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=http_req))
    if not res.ok:
        logger.error("Structured report error: %s", res.error)
        if res.status:
            return jsonify({"error": "model returned error", "code": "MODEL_ERROR", "status": res.status}), 502
        return jsonify({"error": f"model error: {res.error}", "code": "MODEL_ERROR"}), 502

    result = res.raw
    response_text = res.text

    # v2.125.72: 모델 오류는 이미 502인데 **파싱 실패는 `{"response": 원문}`이 report로 200**이었다 — 같은 형식 502로.
    from core.quality import parse_json_strict
    parsed_report = parse_json_strict(response_text)
    if parsed_report is None:
        logger.warning("[vision] structured-report 구조 미충족 → 502: len=%d", len(response_text or ""))
        return jsonify({"error": "model output is not the promised JSON", "code": "MODEL_OUTPUT_UNPARSED"}), 502
    report = force_korean_json(parsed_report)

    return jsonify({
        "report": report,
        "comparison": comparison,
        "model": "medgemma:4b",
        "modality": modality,
        "body_part": body_part,
        "tokens": result.get("eval_count", 0),
        "duration_ms": result.get("eval_duration", 0) // 1000000 if result.get("eval_duration") else 0,
    })


# ===========================================================================
# P4-4: 예측 분석 (Predictive Analytics)
# ===========================================================================

PREDICT_READMISSION_SYSTEM = """당신은 재입원 위험도 예측 AI입니다. 반드시 한국어로 답변하세요.

환자 데이터를 분석하여 30일 내 재입원 위험도를 HOSPITAL score 방법론으로 평가하세요.

[HOSPITAL Score 항목]
- Hemoglobin < 12 g/dL: +1
- Oncology discharge: +2
- Sodium < 135 mEq/L: +1
- Procedure during admission: +1
- Index admission Type (urgent/emergency): +1
- Number of admissions in past year: +2 (≥5) or +1 (2-4)
- Length of stay ≥ 5 days: +2

[위험 분류]
- 0-4점: 저위험 (5-10% 재입원율)
- 5-6점: 중위험 (15-25% 재입원율)
- 7+점: 고위험 (30%+ 재입원율)

반드시 JSON으로만 출력:
{
  "risk_level": "low/medium/high",
  "score": 0,
  "score_breakdown": [{"item": "항목", "value": "값", "points": 0, "rationale": "근거"}],
  "estimated_readmission_rate": "0-100%",
  "risk_factors": ["주요 위험요인"],
  "preventive_measures": ["예방 조치"],
  "recommended_follow_up": "권고 추적 일정",
  "disclaimer": "AI 참고용 예측이며, 임상 판단을 대체하지 않습니다."
}"""

PREDICT_COMPLICATION_SYSTEM = """당신은 합병증 위험도 예측 AI입니다. 반드시 한국어로 답변하세요.

환자의 진단 및 상태 정보를 기반으로 주요 합병증 발생 위험도를 평가하세요.

[분석 항목]
1. 현재 진단의 알려진 합병증 목록
2. 환자 위험요인 (나이, 기저질환, 약물)
3. 각 합병증 발생 확률 추정
4. 조기 감지 징후 (warning signs)
5. 예방 전략

반드시 JSON으로만 출력:
{
  "primary_diagnosis": "주진단",
  "complications": [
    {
      "name": "합병증명",
      "probability": "low/medium/high",
      "timeframe": "예상 발생 시기",
      "warning_signs": ["조기 징후"],
      "prevention": ["예방법"],
      "monitoring": "모니터링 방법"
    }
  ],
  "overall_risk": "low/medium/high",
  "key_monitoring_parameters": ["핵심 모니터링 항목"],
  "disclaimer": "AI 참고용 예측이며, 임상 판단을 대체하지 않습니다."
}"""

PREDICT_ADR_SYSTEM = """당신은 약물 부작용(ADR) 예측 AI입니다. 반드시 한국어로 답변하세요.

환자의 약물 목록과 상태 정보를 분석하여 약물 부작용 위험도를 평가하세요.

[분석 항목]
1. 각 약물의 주요 부작용 (흔한 + 심각한)
2. 약물 간 상호작용
3. 환자 특성별 위험 (연령, 신장/간 기능, 임신)
4. 중복 약효 (therapeutic duplication)
5. 모니터링 권장 사항

반드시 JSON으로만 출력:
{
  "medications_analyzed": ["약물 목록"],
  "individual_risks": [
    {
      "drug": "약물명",
      "common_adr": [{"reaction": "부작용", "frequency": "빈도"}],
      "serious_adr": [{"reaction": "심각 부작용", "risk_level": "low/medium/high"}],
      "patient_specific_risk": "환자 특이 위험"
    }
  ],
  "interactions": [
    {
      "drugs": ["약물A", "약물B"],
      "severity": "minor/moderate/major/contraindicated",
      "effect": "상호작용 효과",
      "management": "관리 방법"
    }
  ],
  "duplications": [{"drugs": ["약물A", "약물B"], "class": "약효군"}],
  "overall_risk": "low/medium/high",
  "monitoring_plan": [{"parameter": "모니터링 항목", "frequency": "빈도", "target": "목표 범위"}],
  "disclaimer": "AI 참고용 분석이며, 약사/의사의 판단을 대체하지 않습니다."
}"""


@medical_advanced_bp.route("/api/medical/predict/readmission", methods=["POST"])
@require_auth
def api_predict_readmission():
    """30일 재입원 위험도 예측 (HOSPITAL Score)
    ---
    tags: [Medical - Predictive]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_data]
          properties:
            patient_data:
              type: object
              description: "환자 데이터 (진단, 검사, 입원 정보 등 자유 텍스트)"
              properties:
                diagnosis: {type: string}
                lab_results: {type: string}
                admission_info: {type: string}
                past_admissions: {type: integer}
                length_of_stay: {type: integer}
            model: {type: string}
    responses:
      200:
        description: 재입원 위험도 예측 결과
        schema:
          type: object
          properties:
            prediction: {}
            model: {}
            tokens: {}
            duration_ms: {}
            timestamp: {}
    """
    data = request.json or {}
    patient_data = data.get("patient_data")
    if not patient_data:
        return jsonify({"error": "patient_data is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("predict_readmission", data.get("model"))

    prompt = json.dumps(patient_data, ensure_ascii=False, indent=2) if isinstance(patient_data, dict) else str(patient_data)

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt=prompt,
        system=PREDICT_READMISSION_SYSTEM,
        options={"temperature": 0.2}, action="predict_readmission",
    accept=_json_acceptable)
    result = _parse_prediction(response, "predict_readmission", model)

    return jsonify({
        "prediction": result,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@medical_advanced_bp.route("/api/medical/predict/complication", methods=["POST"])
@require_auth
def api_predict_complication():
    """합병증 위험도 예측
    ---
    tags: [Medical - Predictive]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [diagnosis]
          properties:
            diagnosis: {type: string, description: "주진단"}
            patient_info:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string}
                conditions: {type: array, items: {type: string}}
                medications: {type: array, items: {type: string}}
                lab_results: {type: string}
            model: {type: string}
    responses:
      200:
        description: 합병증 예측 결과
        schema:
          type: object
          properties:
            prediction: {}
            model: {}
            tokens: {}
            duration_ms: {}
            timestamp: {}
    """
    data = request.json or {}
    diagnosis = data.get("diagnosis", "").strip()
    if not diagnosis:
        return jsonify({"error": "diagnosis is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("predict_complication", data.get("model"))

    prompt_parts = [f"주진단: {diagnosis}"]
    pi = data.get("patient_info", {})
    if pi.get("age"):
        prompt_parts.append(f"나이: {pi['age']}세")
    if pi.get("sex"):
        prompt_parts.append(f"성별: {pi['sex']}")
    if pi.get("conditions"):
        prompt_parts.append(f"기저질환: {', '.join(pi['conditions'])}")
    if pi.get("medications"):
        prompt_parts.append(f"복용약물: {', '.join(pi['medications'])}")
    if pi.get("lab_results"):
        prompt_parts.append(f"검사결과: {pi['lab_results']}")

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt="\n".join(prompt_parts),
        system=PREDICT_COMPLICATION_SYSTEM,
        options={"temperature": 0.2}, action="predict_complication",
    accept=_json_acceptable)
    result = _parse_prediction(response, "predict_complication", model)

    return jsonify({
        "prediction": result,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@medical_advanced_bp.route("/api/medical/predict/adverse-drug", methods=["POST"])
@require_auth
def api_predict_adverse_drug():
    """약물 부작용(ADR) 위험도 예측
    ---
    tags: [Medical - Predictive]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [medications]
          properties:
            medications:
              type: array
              items: {type: string}
              description: "약물 목록"
            patient_info:
              type: object
              properties:
                age: {type: integer}
                sex: {type: string}
                weight: {type: number}
                conditions: {type: array, items: {type: string}}
                allergies: {type: array, items: {type: string}}
                renal_function: {type: string}
                hepatic_function: {type: string}
            model: {type: string}
    responses:
      200:
        description: 약물 부작용 예측 결과
        schema:
          type: object
          properties:
            prediction: {}
            model: {}
            tokens: {}
            duration_ms: {}
            timestamp: {}
    """
    data = request.json or {}
    medications = data.get("medications", [])
    if not medications:
        return jsonify({"error": "medications array is required"}), 400

    from routes.medical import _get_medical_model
    model = _get_medical_model("predict_adverse_drug", data.get("model"))

    prompt_parts = [f"약물 목록: {', '.join(medications)}"]
    pi = data.get("patient_info", {})
    if pi.get("age"):
        prompt_parts.append(f"나이: {pi['age']}세")
    if pi.get("sex"):
        prompt_parts.append(f"성별: {pi['sex']}")
    if pi.get("weight"):
        prompt_parts.append(f"체중: {pi['weight']}kg")
    if pi.get("conditions"):
        prompt_parts.append(f"기저질환: {', '.join(pi['conditions'])}")
    if pi.get("allergies"):
        prompt_parts.append(f"알레르기: {', '.join(pi['allergies'])}")
    if pi.get("renal_function"):
        prompt_parts.append(f"신기능: {pi['renal_function']}")
    if pi.get("hepatic_function"):
        prompt_parts.append(f"간기능: {pi['hepatic_function']}")

    # DB 기반 약물 상호작용도 병행 확인
    from core.medical_validator import check_drug_interaction_warnings
    db_check = check_drug_interaction_warnings({"medications": medications})

    response, tokens, duration_ms = _medical_generate(
        model=model, prompt="\n".join(prompt_parts),
        system=PREDICT_ADR_SYSTEM,
        options={"temperature": 0.2}, action="predict_adr",
    accept=_json_acceptable)
    result = _parse_prediction(response, "predict_adr", model)

    # DB 상호작용 결과 병합
    if not db_check[0] and db_check[2].get("interactions"):
        if isinstance(result, dict):
            db_interactions = db_check[2]["interactions"]
            result["db_verified_interactions"] = db_interactions

    return jsonify({
        "prediction": result,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Physiology Simulation (werub-physio-v1) — 0D 다계통 생리 시뮬 + 커플링 (R1, 환자 아바타)
#   순수 CPU(GPU/LLM 무경합)·비식별·미저장. 트윈 0D(Windkessel/renal_pk) 격상 + 계통간 커플링 2개.
# ---------------------------------------------------------------------------

def _physiology_payload(data):
    """입력 비식별 검증 — 환자식별자(name/mrn/birth 등) 거부, 생리 파라미터만 통과."""
    forbidden = {"name", "patient_name", "mrn", "patient_id", "birth", "birthdate",
                 "ssn", "rrn", "phone", "address"}
    present = forbidden.intersection({str(k).lower() for k in (data or {}).keys()})
    if present:
        return None, f"식별자 입력 금지(비식별만): {sorted(present)}"
    return data or {}, None


@medical_advanced_bp.route("/api/medical/physiology", methods=["POST"])
@require_auth
def api_physiology():
    """0D 다계통 생리 시뮬 (werub-physio-v1) — 비식별 파라미터 → 계통별 시계열 + 커플링
    ---
    tags: [Medical - Physiology]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            age: {type: integer}
            sex: {type: string, enum: [male, female]}
            conditions: {type: array, items: {type: string}}
            vitals:
              type: object
              properties:
                hr: {type: number}
                sbp: {type: number}
                dbp: {type: number}
                rr: {type: number}
                spo2: {type: number}
                temp: {type: number}
            meds: {type: array, items: {type: string}}
            egfr: {type: number, description: "eGFR(mL/min/1.73) — 신장-약동-심혈관 커플링 입력"}
            weight_kg: {type: number, description: "선택 — 미전달 시 성/연령 표준 추정 폴백"}
            height_cm: {type: number}
            horizon_sec: {type: number, default: 60}
            tick_ms: {type: number, default: 200}
    responses:
      200:
        description: "series{hr,map,sbp,dbp,rr,spo2,glucose_mgdl,cardiac_output_lmin} + drug_response[] + coupling[]"
        schema: {type: object}
    """
    from core import physiology
    data, err = _physiology_payload(request.json)
    if err:
        return jsonify({"error": err}), 400
    try:
        t0 = time.time()
        result = physiology.simulate(data)
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        logger.error("physiology simulate failed: %s", e)
        return jsonify({"error": "생리 시뮬 실패", "detail": str(e)[:200]}), 500


@medical_advanced_bp.route("/api/medical/physiology/stream", methods=["POST"])
@require_auth
def api_physiology_stream():
    """생리 시뮬 NDJSON 스트리밍 — meta → frame(틱별) → done. 아바타 실시간 매핑용.
    ---
    tags: [Medical - Physiology]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema: {type: object}
    responses:
      200: {description: "NDJSON 스트림 (application/x-ndjson)"}
    """
    from core import physiology
    data, err = _physiology_payload(request.json)
    if err:
        return jsonify({"error": err}), 400
    try:
        result = physiology.simulate(data)
    except Exception as e:  # noqa: BLE001
        logger.error("physiology stream failed: %s", e)
        return jsonify({"error": "생리 시뮬 실패", "detail": str(e)[:200]}), 500

    def generate():
        series = result["series"]
        channels = list(series.keys())
        meta = {k: result[k] for k in ("model", "horizon_sec", "tick_ms", "n_points",
                                       "coupling", "drug_response", "inputs_resolved", "disclaimer")}
        meta["type"] = "meta"
        meta["channels"] = channels
        yield json.dumps(meta, ensure_ascii=False) + "\n"
        dt = result["tick_ms"] / 1000.0
        for i in range(result["n_points"]):
            frame = {"type": "frame", "i": i, "t": round(i * dt, 3),
                     "values": {c: series[c][i] for c in channels}}
            yield json.dumps(frame, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


# ---------------------------------------------------------------------------
# Cross-Validation API (v2.58.0) — 다중 모델 교차 검증
# ---------------------------------------------------------------------------

@medical_advanced_bp.route("/api/medical/cross-validate", methods=["POST"])
@require_auth
def api_cross_validate():
    """의료 진단 교차 검증 — 2~3개 모델로 독립 분석 후 일치도 평가
    ---
    tags: [Medical - Validation]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_text]
          properties:
            patient_text: {type: string, description: "환자 증상/진료 기록"}
            patient_info: {type: object, properties: {age: {type: integer}, sex: {type: string}, conditions: {type: array}}}
            models: {type: array, items: {type: string}, description: "검증 모델 (기본: werub-medical + Med42 + phi4-reasoning)"}
    responses:
      200:
        description: 교차 검증 결과
        schema:
          type: object
          properties:
            cross_validation: {type: object}
            total_tokens: {}
            total_duration_ms: {type: integer}
            timestamp: {}
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    patient_info = data.get("patient_info")
    models = data.get("models") or ["werub-medical:7b", "thewindmom/llama3-med42-8b", "phi4-reasoning"]

    context = patient_text
    if patient_info:
        info_parts = []
        if patient_info.get("age"):
            info_parts.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_parts.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_parts.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_parts:
            context += "\n\n[환자 정보]\n" + "\n".join(info_parts)

    cv_system = """당신은 전문 의사입니다. 감별진단을 수행하세요.
JSON 출력: {"diagnoses": [{"name": "진단명", "icd_code": "ICD-10", "probability": "high/medium/low", "reasoning": "근거"}], "recommended_tests": ["검사명"], "urgency": "low/medium/high/critical"}"""

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.quality import force_json

    total_start = time.time()
    model_results = {}
    total_tokens = 0

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for m in models[:3]:
            f = pool.submit(
                _medical_generate, m, context, cv_system,
                {"temperature": 0.2, "num_predict": 1500}, "cross_validate"
            )
            futures[f] = m

        for f in as_completed(futures, timeout=60):
            model_name = futures[f]
            try:
                resp, tokens, dur = f.result()
                from core.quality import parse_json_strict
                raw_parsed = parse_json_strict(resp)
                if raw_parsed is None or not isinstance(raw_parsed, dict):
                    # v2.125.72: 종전엔 `diagnoses: []`로 '진단 없음'처럼 세져 valid_count에 들고 일치도를 끌어내렸다.
                    logger.warning("[medical] cross_validate 구조 미충족 — 모델 단위 실패: model=%s len=%d", model_name, len(resp or ""))
                    model_results[model_name] = {"error": "model output is not the promised JSON",
                                                 "tokens": tokens, "duration_ms": dur}
                    total_tokens += tokens
                    continue
                parsed = force_korean_json(raw_parsed)
                model_results[model_name] = {
                    "diagnoses": parsed.get("diagnoses", []),
                    "recommended_tests": parsed.get("recommended_tests", []),
                    "urgency": parsed.get("urgency", "medium"),
                    "tokens": tokens, "duration_ms": dur,
                }
                total_tokens += tokens
            except Exception as e:
                model_results[model_name] = {"error": str(e)[:200]}

    # 일치도 분석
    all_dx = {}
    valid_count = len([r for r in model_results.values() if "error" not in r])
    for mn, r in model_results.items():
        if "error" in r:
            continue
        for dx in r.get("diagnoses", []):
            name = dx.get("name", "").strip()
            if not name:
                continue
            if name not in all_dx:
                all_dx[name] = {"count": 0, "models": [], "icd_codes": set()}
            all_dx[name]["count"] += 1
            all_dx[name]["models"].append(mn)
            if dx.get("icd_code"):
                all_dx[name]["icd_codes"].add(dx["icd_code"])

    consensus = [{
        "diagnosis": name,
        "agreement": f"{info['count']}/{valid_count}",
        "models": info["models"],
        "icd_codes": list(info["icd_codes"]),
        "confidence": "high" if info["count"] >= 2 else "low",
    } for name, info in sorted(all_dx.items(), key=lambda x: -x[1]["count"])]

    agreement_rate = 0
    if consensus and valid_count >= 2:
        agreement_rate = round(sum(1 for c in consensus if c["confidence"] == "high") / max(len(consensus), 1) * 100)

    return jsonify({
        "cross_validation": {
            "models_used": list(model_results.keys()),
            "model_results": model_results,
            "consensus_diagnoses": consensus,
            "agreement_rate": agreement_rate,
        },
        "total_tokens": total_tokens,
        "total_duration_ms": int((time.time() - total_start) * 1000),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# MedGemma 심층 분석 API (v2.58.0)
# ---------------------------------------------------------------------------

@medical_advanced_bp.route("/api/medical/deep-analysis", methods=["POST"])
@require_auth
def api_deep_analysis():
    """MedGemma 27B 심층 분석 — 복잡 케이스 정밀 분석 (야간/비피크 권장)
    ---
    tags: [Medical - Deep Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_text]
          properties:
            patient_text: {type: string}
            patient_info: {type: object}
            analysis_type: {type: string, enum: [comprehensive, differential, treatment_plan, literature_review], default: comprehensive}
            model: {type: string, default: "medgemma:27b"}
    responses:
      200:
        description: 심층 분석 결과
        schema:
          type: object
          properties:
            analysis: {}
            analysis_type: {}
            model: {}
            tokens: {}
            duration_ms: {type: integer}
            note: {type: string}
            timestamp: {}
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    analysis_type = data.get("analysis_type", "comprehensive")
    model = data.get("model", "medgemma:27b")
    patient_info = data.get("patient_info")

    context = patient_text
    if patient_info:
        info_parts = []
        for k, label in [("age", "Age"), ("sex", "Sex"), ("conditions", "PMH"), ("medications", "Meds"), ("labs", "Labs")]:
            v = patient_info.get(k)
            if v:
                info_parts.append(f"{label}: {', '.join(v) if isinstance(v, list) else str(v)}")
        if info_parts:
            context += "\n\nPatient: " + ", ".join(info_parts)

    type_prompts = {
        "comprehensive": "Comprehensive analysis: 1)Summary 2)Differential(top5+ICD-10) 3)Workup 4)Treatment 5)Prognosis 6)Red Flags 7)Education. JSON 7 keys.",
        "differential": "Exhaustive DDx: primary(3), secondary(5), must_not_miss. Each: ICD-10, probability, evidence. JSON.",
        "treatment_plan": "Treatment plan: immediate, pharmacological(dosages), non-pharm, monitoring, timeline, complications, follow-up. JSON.",
        "literature_review": "Evidence review: guidelines, trials, practices, controversies. JSON 4 keys.",
    }

    prompt = f"{type_prompts.get(analysis_type, type_prompts['comprehensive'])}\n\nCase:\n{context}"
    da_system = "Senior specialist (20+ years). Evidence-based. ICD-10. JSON output."

    start = time.time()
    response, tokens, duration_ms = _medical_generate(
        model=model, prompt=prompt, system=da_system,
        options={"temperature": 0.2, "num_predict": 4096}, action="deep_analysis",
    accept=_json_acceptable)

    # v2.125.72: Swagger `analysis: {}`(타입 없음)라 자유 텍스트 저하는 **유지**하되 성공/저하를 표시한다(추가 필드 —
    #   기존 호출자 무영향). 종전엔 `{"analysis": 원문}`이 구조화 결과와 구분 없이 나갔다.
    from core.quality import force_json, parse_json_strict
    parse_ok = parse_json_strict(response) is not None
    if not parse_ok:
        logger.warning("[medical] deep_analysis 구조 미충족 — 자유 텍스트로 저하: model=%s len=%d", model, len(response or ""))
    result = force_korean_json(force_json(response, "analysis"))

    return jsonify({
        "analysis": result,
        "parse_ok": parse_ok,
        "analysis_type": analysis_type,
        "model": model,
        "tokens": tokens,
        "duration_ms": int((time.time() - start) * 1000),
        "note": "MedGemma 27B — GPU 부하 높음, 야간/비피크 권장",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ── DUR 병용금기 (식약처 정본 미러 — 비식별·자문 보조) ───────────────────────
@medical_advanced_bp.route("/api/medical/dur/check", methods=["POST"])
@require_auth
def api_dur_check():
    """처방 약물 리스트의 DUR 점검(8유형) — 식약처 DUR 정본 데이터 기반.

    병용금기 + 단일성분 금기/주의(임부·연령·노인·효능군중복·용량·투여기간) + 서방정분할주의.
    각 약물은
    성분명·DUR성분코드(D000)·품목일련번호·품목명 중 하나로 식별(비식별). 선택적 환자 컨텍스트
    age(세)·pregnant(bool)로 임부금기·연령금기·노인주의를 게이팅(미제공 시 해당 유형 생략).
    실시간 차단이 아닌 자문 보조 — 최종 판단은 처방의·약사(disclaimer 강제).
    응답: contraindications(병용금기)·pregnancy_warnings·age_warnings·elderly_warnings·
    duplicate_therapy(효능군중복)·advisories(용량/기간 정보성).
    ---
    tags: [Medical - DUR]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [drugs]
          properties:
            drugs: {type: array, items: {type: string}, description: "약물 성분명/코드 목록(최대 50)"}
            age: {type: integer, description: "환자 나이(세, 선택) — 연령금기·노인주의 점검"}
            pregnant: {type: boolean, description: "임신 여부(선택) — 임부금기 점검"}
    responses:
      200:
        description: DUR 점검 결과(병용금기 + 환자 컨텍스트 금기/주의)
        schema:
          type: object
          properties:
            input_count: {type: integer}
            resolved:
              type: array
              description: "약물별 DUR성분코드 해석 결과"
              items:
                type: object
                properties:
                  input: {description: "원본 입력"}
                  codes: {type: array, items: {type: string}, description: "해석된 DUR성분코드(D000)"}
                  names: {type: array, items: {type: string}}
                  via: {type: string, description: "해석 경로(name/ingredient_code/product_serial 등)"}
                  resolved: {type: boolean}
            patient_context: {type: object, properties: {age: {type: integer}, pregnant: {type: boolean}}}
            contraindications:
              type: array
              description: "병용금기 쌍"
              items:
                type: object
                properties:
                  drug_a: {type: string}
                  drug_b: {type: string}
                  reason: {type: string, description: "금기내용", example: "세로토닌성 증후군"}
                  grade: {type: string}
                  notice_date: {type: string}
            count: {type: integer, description: "병용금기 건수"}
            has_contraindication: {type: boolean}
            pregnancy_warnings: {type: array, description: "임부금기(pregnant=true 시)", items: {type: object}}
            elderly_warnings: {type: array, description: "노인주의(age>=65 시)", items: {type: object}}
            age_warnings: {type: array, description: "연령금기(age 제공 시)", items: {type: object}}
            duplicate_therapy: {type: array, description: "효능군중복(같은 효능군 2종 이상)", items: {type: object}}
            advisories: {type: array, description: "용량주의·투여기간주의(정보성)", items: {type: object}}
            split_cautions: {type: array, description: "서방정분할주의(제품 단위)", items: {type: object}}
            has_warning: {type: boolean, description: "금기/주의 하나라도 있으면 true"}
            dur_types_checked: {type: array, items: {type: string}, description: "점검한 8유형"}
            disclaimer: {type: string}
      503: {description: "DUR 엔진 일시 불가"}
    """
    p = request.get_json(silent=True) or {}
    drugs = p.get("drugs")
    if not isinstance(drugs, list) or not drugs:
        return jsonify({"error": "drugs[] (성분명/코드 목록) required"}), 400
    if len(drugs) > 50:
        return jsonify({"error": "max 50 drugs"}), 400
    age = p.get("age")
    if age is None and isinstance(p.get("patient"), dict):
        age = p["patient"].get("age")
    pregnant = p.get("pregnant")
    if pregnant is None and isinstance(p.get("patient"), dict):
        pregnant = p["patient"].get("pregnant")
    try:
        return jsonify(dur_mod.check(drugs, age=age, pregnant=pregnant)), 200
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dur] check error: {e}")
        return jsonify({"error": "dur engine unavailable", "detail": str(e)[:200]}), 503


@medical_advanced_bp.route("/api/medical/dur/drug", methods=["POST"])
@medical_advanced_bp.route("/api/medical/dur/drug/<code>", methods=["GET"])
@require_auth
def api_dur_drug(code=None):
    """단일 약물의 병용금기 상대 성분 목록. GET /<code> 또는 POST {"drug": ...}.

    성분명 또는 DUR 성분코드(예: `D000`)로 그 약물의 **병용금기 상대 성분**을 조회한다.
    여러 약물의 조합을 한 번에 보려면 `POST /api/medical/dur/check`를 쓴다.
    정본은 식약처 DUR이며 갱신은 `POST /api/medical/dur/sync`.
    ---
    tags: [Medical - DUR]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: code
        required: false
        type: string
        description: "GET 경로변수 — 성분명 또는 DUR성분코드(D000)"
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            drug: {type: string, description: "성분명/코드", example: "파록세틴"}
    responses:
      200:
        description: "단일 약물 병용금기 프로파일"
        schema:
          type: object
          properties:
            codes: {type: array, items: {type: string}}
            names: {type: array, items: {type: string}}
            contraindicated_with:
              type: array
              description: "병용금기 상대 성분 목록"
              items:
                type: object
                properties:
                  code: {type: string}
                  name: {type: string}
                  reason: {type: string}
                  grade: {type: string}
                  notice_date: {type: string}
            contraindicated_count: {type: integer}
            disclaimer: {type: string}
    """
    drug = code
    if drug is None:
        p = request.get_json(silent=True) or {}
        drug = p.get("drug") or p.get("code") or p.get("name")
    if not drug:
        return jsonify({"error": "drug (성분명/코드) required"}), 400
    try:
        return jsonify(dur_mod.drug_profile(drug)), 200
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dur] drug error: {e}")
        return jsonify({"error": "dur engine unavailable", "detail": str(e)[:200]}), 503


@medical_advanced_bp.route("/api/medical/dur/stats", methods=["GET"])
@require_auth
def api_dur_stats():
    """DUR 적재 현황(병용금기 룰·단일유형별·품목·성분 수).

    DUR 정본 적재 현황. 유형별 건수가 기대치와 다르면 동기화 실패를 의심하고
    `GET /api/medical/dur/sync-status`로 소스별 상태를 확인한다.
    ---
    tags: [Medical - DUR]
    security: [{Bearer: []}]
    responses:
      200:
        description: "DUR 적재 현황"
        schema:
          type: object
          properties:
            interactions: {type: integer, description: "병용금기 룰 수", example: 1313}
            products: {type: integer, description: "품목→성분 색인 수"}
            distinct_ingredients: {type: integer}
            single_warnings:
              type: object
              description: "단일유형별 건수(임부금기/연령금기/노인주의/용량주의/투여기간주의/효능군중복/서방정분할주의)"
            dur_types: {type: array, items: {type: string}, description: "적재된 유형 목록"}
    """
    return jsonify(dur_mod.stats()), 200


# ── 의약품개요정보(e약은요) — 환자용 정보 정본 ───────────────────────────────
@medical_advanced_bp.route("/api/medical/drug-info", methods=["POST"])
@medical_advanced_bp.route("/api/medical/drug-info/<item_seq>", methods=["GET"])
@require_auth
def api_drug_info(item_seq=None):
    """환자용 의약품 정보(효능·사용법·주의사항·상호작용·부작용·보관법) — 식약처 e약은요 정본.

    GET /<item_seq> 또는 POST {"query": 제품명 또는 품목일련번호}. 제품명은 부분일치 검색.
    LLM 환각 없이 정본 정보로 자문(최종 판단 의사·약사).
    ---
    tags: [Medical - DrugInfo]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: item_seq
        required: false
        type: string
        description: "GET 경로변수 — 품목일련번호"
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            query: {type: string, description: "제품명(부분일치) 또는 품목일련번호", example: "타이레놀"}
    responses:
      200:
        description: "환자용 의약품 정보(빈 필드는 생략)"
        schema:
          type: object
          properties:
            found: {type: boolean}
            query: {type: string}
            match_count: {type: integer}
            matches:
              type: array
              items:
                type: object
                properties:
                  item_seq: {type: string, description: "품목일련번호"}
                  name: {type: string, description: "제품명"}
                  maker: {type: string, description: "업체명"}
                  efficacy: {type: string, description: "효능(efcyQesitm)"}
                  usage: {type: string, description: "사용법/복용법(useMethodQesitm)"}
                  warn: {type: string, description: "주의경고(atpnWarnQesitm)"}
                  precaution: {type: string, description: "주의사항(atpnQesitm)"}
                  interaction: {type: string, description: "상호작용(intrcQesitm)"}
                  side_effect: {type: string, description: "부작용(seQesitm)"}
                  storage: {type: string, description: "보관법(depositMethodQesitm)"}
                  image: {type: string, description: "낱알이미지 URL"}
            disclaimer: {type: string}
    """
    from core.drug_info import lookup as info_lookup
    q = item_seq
    if q is None:
        p = request.get_json(silent=True) or {}
        q = p.get("query") or p.get("name") or p.get("item_seq")
    if not q:
        return jsonify({"error": "query(제품명/품목일련번호) required"}), 400
    return jsonify(info_lookup(q)), 200


@medical_advanced_bp.route("/api/medical/drug-info/stats", methods=["GET"])
@require_auth
def api_drug_info_stats():
    """e약은요 적재 현황.

    e약은요(의약품 개요정보) 적재 현황. 최신성은 `GET /api/medical/catalog`에도 실린다.
    ---
    tags: [Medical - DrugInfo]
    security: [{Bearer: []}]
    responses:
      200:
        description: "적재 현황"
        schema:
          type: object
          properties:
            total: {type: integer, description: "총 품목 수", example: 4789}
            with_side_effect: {type: integer, description: "부작용 정보 보유 품목"}
    """
    from core.drug_info import stats as info_stats
    return jsonify(info_stats()), 200


# ── 의료 데이터 카탈로그 — 내부 시스템 연동용 단일 발견 지점 ─────────────────
@medical_advanced_bp.route("/api/medical/catalog", methods=["GET"])
@require_auth
def api_medical_catalog():
    """의료 데이터 카탈로그 — 보유 데이터셋·건수·최신성·**서빙 엔드포인트**를 한 번에.

    내부 시스템이 연동할 때 "무슨 의료 데이터가 있고 어떤 API로 꺼내는가"를 기계가 읽는
    형태로 낸다. 의료 엔드포인트는 26개 태그에 흩어져 있어(`Medical - DUR`·`Notice`·
    `Medical Law`…) 문서 통독 말고는 알 방법이 없었다 — 이 엔드포인트가 그 진입점이다.

    데이터셋별 **fail-open**: 한 저장소가 없거나 깨져도 나머지를 낸다(해당 항목은
    `available:false` + `error`). 집계만 노출하며 레코드 본문·PHI는 포함하지 않는다.
    최신성은 미측정 시 **null**이다(0으로 세지 않는다 — 결측을 0으로 세면 최신으로 보인다).
    ---
    tags: [Medical - Catalog]
    security: [{Bearer: []}]
    parameters:
      - in: query
        name: rag
        type: boolean
        required: false
        default: true
        description: "ChromaDB RAG 컬렉션 포함 여부(false면 SQLite 데이터셋만 — 더 빠름)"
    responses:
      200:
        description: "카탈로그 스냅샷"
        schema:
          type: object
          properties:
            generated_at: {type: string, example: "2026-08-31T08:30:00+00:00Z"}
            datasets:
              type: array
              description: "SQLite 데이터셋"
              items:
                type: object
                properties:
                  id: {type: string, example: "drug_master"}
                  label: {type: string, example: "약가 마스터(제품·표준코드)"}
                  source: {type: string, example: "HIRA 약제급여목록"}
                  store: {type: string, example: "notice.db"}
                  rows: {type: integer, example: 305522}
                  available: {type: boolean}
                  error: {type: string, description: "available=false일 때만"}
                  last_sync_at: {type: string, description: "미측정이면 null"}
                  last_sync_status: {type: string, example: "ok"}
                  age_days: {type: integer, description: "미측정이면 null"}
                  endpoints:
                    type: array
                    items: {type: string}
                    description: "이 데이터셋을 실제로 서빙하는 API"
            rag_collections:
              type: array
              description: "ChromaDB 컬렉션(행 대신 chunks)"
              items: {type: object}
            summary:
              type: object
              properties:
                datasets: {type: integer}
                datasets_available: {type: integer}
                datasets_unavailable: {type: integer}
                total_rows: {type: integer}
                stalest_age_days: {type: integer, description: "가장 오래된 수집 경과일(미측정 제외)"}
            sync_endpoints:
              type: object
              description: "갱신 트리거·상태 조회 경로"
    """
    from core.medical_catalog import build
    include_rag = (request.args.get("rag", "true").lower() != "false")
    return jsonify(build(include_rag=include_rag)), 200


# ── 외부 서비스(식약처 data.go.kr) 동기화 — DUR 8유형 + e약은요 ──────────────
@medical_advanced_bp.route("/api/medical/dur/sync-status", methods=["GET"])
@require_auth
def api_dur_sync_status():
    """식약처 외부 API 정본 동기화 현황(소스별 마지막 시각·건수·상태·에러).
    ---
    tags: [Medical - DUR]
    security: [{Bearer: []}]
    responses:
      200:
        description: "동기화 현황"
        schema:
          type: object
          properties:
            configured: {type: boolean, description: "서비스키 구성 여부"}
            running: {type: boolean, description: "동기화 진행 중"}
            last_sync_at: {type: string, description: "최종 동기화 시각(ISO)"}
            sources:
              type: array
              description: "소스별(DUR 8유형+e약은요) 상태"
              items:
                type: object
                properties:
                  source: {type: string, example: "병용금기"}
                  status: {type: string, description: "ok | skip | error", example: "ok"}
                  count: {type: integer}
                  last_sync_at: {type: string}
                  duration_s: {type: number}
                  last_error: {type: string}
    """
    from core.dur_sync import sync_status
    return jsonify(sync_status()), 200


@medical_advanced_bp.route("/api/medical/dur/sync", methods=["POST"])
@require_auth
def api_dur_sync():
    """식약처 정본 수동 갱신 트리거(백그라운드). 안전 갱신(전량 fetch 성공+건수 sane 시에만 교체).

    바디 불요. 진행 상황은 GET /api/medical/dur/sync-status 로 폴링. 서비스키 미구성 시 503.
    ---
    tags: [Medical - DUR]
    security: [{Bearer: []}]
    responses:
      202:
        description: "동기화 시작(백그라운드)"
        schema:
          type: object
          properties:
            ok: {type: boolean}
            message: {type: string}
      503: {description: "서비스키 미구성"}
    """
    import threading as _t

    from core.dur_sync import get_service_key, refresh_all
    if not get_service_key():
        return jsonify({"error": "no service key (config datago_api_key / env DUR_API_KEY)"}), 503
    _t.Thread(target=refresh_all, name="dur-sync-manual", daemon=True).start()
    return jsonify({"ok": True, "message": "동기화 시작(백그라운드). /dur/sync-status 로 확인"}), 202


# ── 아바타 R2 — 영상→장기 메시(glTF) 비동기 ──────────────────────────────────
@medical_advanced_bp.route("/api/vision/medical/organ-mesh", methods=["POST"])
@heavy_limit("6/minute")
@require_auth
def api_organ_mesh():
    """CT 영상(서명 URL) → 장기 메시(glTF .glb) 비동기 생성 — 아바타 R2.

    NIfTI.gz 또는 **DICOM ZIP(PACS/Orthanc 아카이브)** 서명 URL을 받아 백그라운드에서
    다운로드→(dicom_zip은 압축해제→DICOM 폴더)→TotalSegmentator(fast 3mm, lung 5엽,
    NIfTI/DICOM 폴더 직접 처리)→marching_cubes→trimesh→.glb→S3 presigned(짧은 TTL).
    202+폴링(콜백 미채택). **비식별**: glTF 지오메트리만(DICOM 태그 미임베드), temp 처리 후 폐기.
    동시성 1(VRAM 직렬화).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pixels_url]
          properties:
            pixels_url: {type: string, description: "트윈/PACS 임시 서명 URL(공인 https, NIfTI.gz 또는 DICOM ZIP)"}
            format: {type: string, enum: [nifti_gz, dicom_zip], default: nifti_gz, description: "nifti_gz(NIfTI.gz) 또는 dicom_zip(DICOM 시리즈 ZIP/PACS 아카이브)"}
            mode: {type: string, enum: [fast, full], default: fast, description: "fast=3mm(안전 기본)/full=1.5mm(OOM 위험, 기본 차단→fast)"}
            roi: {type: string, enum: [lung, heart, kidney, liver], default: lung, description: "장기 ROI(lung=폐 5엽/heart=심장/kidney=좌우신장/liver=간)"}
    responses:
      202:
        description: "잡 접수(비동기). poll_url로 폴링(5~10s 권장)"
        schema:
          type: object
          properties:
            job_id: {type: string, example: "avt-ab12cd34ef56"}
            status: {type: string, example: "pending"}
            poll_url: {type: string, example: "/api/vision/medical/organ-mesh/avt-..."}
      400: {description: "pixels_url 누락/비공인"}
    """
    from core.avatar_mesh import submit
    p = request.get_json(silent=True) or {}
    res = submit(p.get("pixels_url"), fmt=p.get("format", "nifti_gz"),
                 mode=p.get("mode", "fast"), roi=p.get("roi", "lung"))
    if res.get("error"):
        return jsonify(res), 400
    res["poll_url"] = f"/api/vision/medical/organ-mesh/{res['job_id']}"
    return jsonify(res), 202


@medical_advanced_bp.route("/api/vision/medical/organ-mesh/<job_id>", methods=["GET"])
@require_auth
def api_organ_mesh_job(job_id):
    """아바타 R2 메시 잡 상태/결과 폴링.

    status: pending→downloading→segmenting→uploading→done(또는 failed). done이면 mesh_url(presigned).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: job_id
        required: true
        type: string
    responses:
      200:
        description: "잡 상태/결과"
        schema:
          type: object
          properties:
            job_id: {type: string}
            status: {type: string, description: "pending|downloading|segmenting|uploading|done|failed"}
            mode: {type: string}
            roi: {type: string}
            mesh_url: {type: string, description: "done 시 glTF .glb presigned URL(짧은 TTL)"}
            meta: {type: object, description: "geometries/vertices/faces/organs/seconds"}
            error: {type: string}
      404: {description: "잡 없음"}
    """
    from core.avatar_mesh import get_job
    j = get_job(job_id)
    if not j:
        return jsonify({"error": "job not found"}), 404
    return jsonify({k: j.get(k) for k in
                    ("job_id", "status", "mode", "roi", "mesh_url", "meta", "error",
                     "created_at", "updated_at")}), 200


@medical_advanced_bp.route("/api/vision/medical/organ-mesh-status", methods=["GET"])
@require_auth
def api_organ_mesh_status():
    """아바타 R2 메시 파이프라인 현황(잡 상태별·격리 venv 가용).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
    """
    from core.avatar_mesh import stats
    return jsonify(stats()), 200


# ── 아바타 R3 — 심장 전기생리(EP): 표면 LAT(glTF _LAT) + 12-lead ECG 비동기 ───────
@medical_advanced_bp.route("/api/medical/cardiac-ep", methods=["POST"])
@heavy_limit("6/minute")
@require_auth
def api_cardiac_ep():
    """CT 영상(서명 URL) → 심장 EP: 표면 활성화맵(glTF _LAT) + 12-lead ECG — 아바타 R3.

    NIfTI.gz 서명 URL을 받아 백그라운드에서 다운로드→TotalSegmentator(heart)→표면 메시→
    **그 표면 정점 위에서** eikonal LAT(=측지거리/CV) + 현상학적 12-lead ECG 합성→
    glTF(.glb)에 per-vertex `_LAT`(ms) 임베드→S3 presigned. 202+폴링(organ-mesh 패턴).

    **정점 정렬 보장**: LAT가 export하는 그 메시 정점에서 계산되므로 트윈 재매핑 불요.
    **비식별**·temp 폐기·동시성 1(R2 메시와 VRAM 직렬화 공유). EP=CPU 대리모델(eikonal-
    surrogate, openCARP 아님). **자문 보조·진단 아님**(disclaimer 강제).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pixels_url]
          properties:
            pixels_url: {type: string, description: "트윈 임시 서명 URL(공인 https, NIfTI.gz)"}
            hr: {type: number, default: 75, description: "심박수 bpm(20~220) — ECG 비트 간격"}
            conditions: {type: array, items: {type: string}, description: "병태 프리셋(lbbb/rbbb/lvh/rvh/lqts) — 표면 대리 근사"}
    responses:
      202:
        description: "잡 접수(비동기). poll_url로 폴링(EP 대리=수초~수십초)"
        schema:
          type: object
          properties:
            job_id: {type: string, example: "ep-ab12cd34ef56"}
            status: {type: string, example: "pending"}
            poll_url: {type: string, example: "/api/medical/cardiac-ep/ep-..."}
      400: {description: "pixels_url 누락/비공인"}
    """
    from core.cardiac_ep import submit
    p = request.get_json(silent=True) or {}
    # engine: 선택(미전송=기본 eikonal-surrogate-v1.1). 트윈 계약 불변 — 후속 대리모델 드롭인용.
    res = submit(p.get("pixels_url"), hr=p.get("hr", 75), conditions=p.get("conditions"),
                 engine=p.get("engine"))
    if res.get("error"):
        return jsonify(res), 400
    res["poll_url"] = f"/api/medical/cardiac-ep/{res['job_id']}"
    return jsonify(res), 202


@medical_advanced_bp.route("/api/medical/cardiac-ep/<job_id>", methods=["GET"])
@require_auth
def api_cardiac_ep_job(job_id):
    """아바타 R3 심장 EP 잡 상태/결과 폴링.

    done이면 mesh_url(glTF +_LAT presigned) + ecg(12-lead 인라인 JSON) + meta(LAT 범위 등).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: job_id
        required: true
        type: string
    responses:
      200:
        description: "잡 상태/결과"
        schema:
          type: object
          properties:
            job_id: {type: string}
            status: {type: string, description: "pending|downloading|segmenting|uploading|done|failed"}
            mesh_url: {type: string, description: "done 시 glTF(_LAT 정점속성, ms) presigned URL"}
            lat: {type: object, description: "LAT JSON 폴백 {lat_ms[], min_ms, max_ms} — glTF _LAT와 동일 정점 순서"}
            ecg: {type: object, description: "12-lead ECG {leads:{I..V6}, fs_hz, duration_ms, approximate}"}
            meta: {type: object, description: "lat_min_ms/lat_max_ms/vertex_count/cv_mm_per_ms/engine 등"}
            error: {type: string}
      404: {description: "잡 없음"}
    """
    from core.cardiac_ep import get_job
    j = get_job(job_id)
    if not j:
        return jsonify({"error": "job not found"}), 404
    return jsonify({k: j.get(k) for k in
                    ("job_id", "status", "hr", "conditions", "mesh_url", "lat", "ecg", "meta",
                     "error", "created_at", "updated_at")}), 200


@medical_advanced_bp.route("/api/medical/cardiac-ep-status", methods=["GET"])
@require_auth
def api_cardiac_ep_status():
    """아바타 R3 심장 EP 파이프라인 현황(잡 상태별·격리 venv 가용·엔진).
    ---
    tags: [Medical - Avatar]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
    """
    from core.cardiac_ep import stats
    return jsonify(stats()), 200
