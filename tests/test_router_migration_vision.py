"""D1 router 이관 — routes/vision.py 10곳 (v2.125.13). 이관은 변경이 아니다: 와이어 페이로드가 종전 직접 호출과 **동일**해야 한다.

종전 페이로드(2026-08-19 커밋 ccad2e1 기준 `requests.post(f"{_OLLAMA_BASE}/api/generate", json=…)`)를 여기 **그대로 적어**
어댑터가 실제로 `requests.post`에 넘기는 본문·타임아웃·stream 플래그와 비교한다. 스텁은 `requests.post` 층에 꽂는다 —
vision.py의 `_ExcSession`(예외 타입 보존)과 스트리밍 `session=requests`가 둘 다 이 층으로 나가므로 이관 코드 전체가 실행된다.

응답 계약도 그대로다: 200+빈 응답→폴백 1회→502 · Timeout→504 · ConnectionError→503 · 404→MODEL_NOT_FOUND · 5xx→502 LLM_ERROR ·
SSE 이벤트 형식(token/done/error). 라우팅 표는 가짜로 대체하고, 실제 표 등록은 마지막 테스트가 따로 확인한다.
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import base64
import io
import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_router  # noqa: E402
from routes import vision as V  # noqa: E402

# ── 가짜 라우팅 표: vision.py가 쓰는 task 전부(모델은 호출자가 `model=`로 덮어쓰므로 표 값은 참고용) ──
_TASKS = {
    "vision.describe": "qwen3-vl:8b",
    "vision.medical_classify": V.MEDICAL_VISION_MODEL,
    "vision.medical_analyze": V.MEDICAL_VISION_MODEL,
    "vision.medical_stream": V.MEDICAL_VISION_MODEL,
    "vision.medical_report": V.MEDICAL_VISION_MODEL,
    "vision.dual_read_primary": V.MEDICAL_VISION_MODEL,
    "vision.dual_read_secondary": "medgemma:4b",
}
_ROUTING = {"tasks": {t: {"model": m} for t, m in _TASKS.items()},
            "defaults": {"policy": "local_only", "provider": "ollama"}}

_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
_B64 = base64.b64encode(_PNG).decode("utf-8")
_URL = f"{V._OLLAMA_BASE}/api/generate"


class _Resp:
    """requests.Response 흉내 — 비스트림은 `.json()`, 스트림은 `.iter_lines()`."""
    def __init__(self, status=200, body=None, lines=None):
        self.status_code = status
        self._b = body if body is not None else {}
        self._lines = lines or []
        self.text = json.dumps(self._b, ensure_ascii=False)

    def json(self):
        return self._b

    def iter_lines(self):
        for ln in self._lines:
            yield ln if isinstance(ln, bytes) else json.dumps(ln, ensure_ascii=False).encode("utf-8")

    def close(self):
        pass


class _Post:
    """`requests.post` 대체 — 보낸 (url, json, timeout, stream)을 순서대로 기록. `script`는 호출 순서대로 응답 또는 예외
    (마지막 항목은 반복)."""
    def __init__(self, *script):
        self.script, self.sent = list(script), []

    def __call__(self, url, json=None, timeout=None, stream=False):
        self.sent.append({"url": url, "json": json, "timeout": timeout, "stream": stream})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def wire(monkeypatch):
    """가짜 라우팅 표 + `requests.post` 스텁 설치. `wire(*script)` → _Post."""
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: _ROUTING)

    def _install(*script):
        p = _Post(*script)
        monkeypatch.setattr(V.requests, "post", p)
        return p
    return _install


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(V.vision_bp)
    return app.test_client()


def _img(key="image"):
    return {key: (io.BytesIO(_PNG), "x.png", "image/png")}


def _sse_events(text):
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(json.loads(block[len("data: "):]))
    return out


# ── 종전 프롬프트 리터럴(analyze·stream 공용 / report / dual-read) ────────────────────────────
def _analyze_prompt(modality, body_part, clinical_info, language):
    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."
    return f"""{lang_instruction}

영상 종류: {modality}
촬영 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '제공되지 않음'}

이 의료 영상을 분석하고 다음 형식으로 보고서를 작성하세요:

## 소견 (Findings)
영상에서 관찰되는 모든 소견을 체계적으로 기술하세요.

## 판독 소견 (Impression)
주요 진단 또는 감별 진단을 기술하세요.

## 권고 사항 (Recommendations)
추가 검사나 추적 관찰이 필요한 경우 권고하세요."""


def _report_prompt_comparison(modality, body_part, clinical_info, language):
    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."
    return f"""{lang_instruction}

영상 종류: {modality} | 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '없음'}

두 장의 의료 영상이 제공됩니다. 첫 번째는 현재 영상, 두 번째는 이전 영상입니다.

1. 현재 영상 소견
2. 이전 영상 대비 변화
3. 종합 판독 소견
4. 권고 사항"""


def _dual_prompt(modality, clinical_info, language):
    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."
    return f"""{lang_instruction}
영상 종류: {modality}
임상 정보: {clinical_info or '없음'}

이 의료 영상을 분석하세요:
1. 소견 (Findings)
2. 판독 소견 (Impression)
3. 권고 사항 (Recommendations)"""


# ── 1. 일반 비전 /api/vision/describe — 사고형 기본 모델(예산 4096·think False) ─────────────
def test_describe_thinking_model_payload_identical(wire, client):
    post = wire(_Resp(200, {"response": "A cat on a desk."}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 200 and r.get_json() == {"description": "A cat on a desk.", "model": "qwen3-vl:8b"}
    assert len(post.sent) == 1
    assert post.sent[0] == {"url": _URL, "timeout": 180, "stream": False, "json": {
        # 종전: _vision_payload("qwen3-vl:8b", "P", [b64], 500) → 사고형이라 4096으로 올리고 think False 동반
        "model": "qwen3-vl:8b", "prompt": "P", "images": [_B64], "stream": False,
        "options": {"num_predict": 4096, "repeat_penalty": 1.1}, "think": False}}


# ── 2. 200+빈 응답 → 폴백 minicpm-v(예산 200·think 없음) → 그래도 비면 502 ───────────────────
def test_describe_empty_response_falls_back_then_502(wire, client):
    # v2.125.60: 사고형 기본 모델이 비면 같은 모델 1회 재시도 → 그래도 비면 폴백(사전 등록 (3))
    post = wire(_Resp(200, {"response": "", "done_reason": "length", "thinking": "..."}),
                _Resp(200, {"response": "", "done_reason": "length", "thinking": "..."}),
                _Resp(200, {"response": "   "}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 502
    assert r.get_json() == {"error": "vision model 'minicpm-v:latest' returned empty response",
                            "model": "minicpm-v:latest"}
    assert [s["json"]["model"] for s in post.sent] == ["qwen3-vl:8b", "qwen3-vl:8b", "minicpm-v:latest"]
    assert post.sent[1] == post.sent[0]                     # 재시도는 같은 페이로드
    assert post.sent[2] == {"url": _URL, "timeout": 90, "stream": False, "json": {
        # 종전: _vision_payload(VISION_FALLBACK_MODEL, "P", [b64], 200) → 비사고형은 그대로·think 키 없음
        "model": "minicpm-v:latest", "prompt": "P", "images": [_B64], "stream": False,
        "options": {"num_predict": 200, "repeat_penalty": 1.1}}}


def test_describe_fallback_success_after_empty(wire, client):
    post = wire(_Resp(200, {"response": ""}), _Resp(200, {"response": ""}), _Resp(200, {"response": "fallback text"}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 200 and r.get_json() == {"description": "fallback text", "model": "minicpm-v:latest"}
    assert len(post.sent) == 3


def test_describe_retry_same_model_recovers_before_fallback(wire, client):
    """사고 폭주 꼬리는 독립 사건 — 재시도가 살리면 폴백(minicpm)으로 가지 않는다."""
    post = wire(_Resp(200, {"response": "", "done_reason": "length", "thinking": "x" * 13985}),
                _Resp(200, {"response": "이수경 영업팀장"}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 200 and r.get_json()["model"] == "qwen3-vl:8b"
    assert [s["json"]["model"] for s in post.sent] == ["qwen3-vl:8b", "qwen3-vl:8b"]


def test_describe_explicit_non_thinking_model_no_retry(wire, client):
    """재시도는 사고형 기본 모델에만 — 명시한 다른 모델의 빈 응답은 그대로 502."""
    post = wire(_Resp(200, {"response": ""}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en", "model": "minicpm-v:latest"})
    assert r.status_code == 502 and len(post.sent) == 1


# ── 3. 전송 예외 계약: Timeout→504(폴백까지) · ConnectionError→503 — 어댑터가 삼킨 예외의 **타입**이 살아야 한다 ──
def test_describe_timeout_after_fallback_maps_504(wire, client):
    post = wire(requests.exceptions.Timeout("t1"), requests.exceptions.Timeout("t2"))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 504
    body = r.get_json()
    assert body["code"] == "TIMEOUT" and body["detail"] == "t2"
    assert [s["json"]["model"] for s in post.sent] == ["qwen3-vl:8b", "minicpm-v:latest"]
    assert [s["timeout"] for s in post.sent] == [180, 90]


def test_describe_explicit_model_connection_error_maps_503(wire, client):
    post = wire(requests.exceptions.ConnectionError("down"))
    r = client.post("/api/vision/describe",
                    data={**_img(), "prompt": "P", "language": "en", "model": "minicpm-v:latest"})
    assert r.status_code == 503 and r.get_json()["code"] == "LLM_UNAVAILABLE"
    assert len(post.sent) == 1     # 명시 모델이 폴백 모델이면 재시도 없음(종전과 같다)


# ── 4. HTTP 오류 계약: 기본 모델 5xx→폴백 · 폴백 404→MODEL_NOT_FOUND · 5xx→502 LLM_ERROR(detail 복원) ──
def test_describe_http_error_on_default_falls_back(wire, client):
    post = wire(_Resp(500, {"error": "boom"}), _Resp(200, {"response": "ok"}))
    r = client.post("/api/vision/describe", data={**_img(), "prompt": "P", "language": "en"})
    assert r.status_code == 200 and r.get_json()["model"] == "minicpm-v:latest"
    assert len(post.sent) == 2


def test_describe_http_error_bodies_identical(wire, client):
    wire(_Resp(404, {"error": "model not found"}))
    r = client.post("/api/vision/describe",
                    data={**_img(), "prompt": "P", "language": "en", "model": "minicpm-v:latest"})
    assert r.status_code == 404
    assert r.get_json() == {"error": "model 'minicpm-v:latest' not found — run 'ollama pull minicpm-v:latest'",
                            "code": "MODEL_NOT_FOUND", "model": "minicpm-v:latest"}

    wire(_Resp(500, {"error": "boom"}))
    r = client.post("/api/vision/describe",
                    data={**_img(), "prompt": "P", "language": "en", "model": "minicpm-v:latest"})
    assert r.status_code == 502
    assert r.get_json() == {"error": "LLM engine error (HTTP 500)", "code": "LLM_ERROR",
                            "status_code": 500, "detail": "boom"}


# ── 5. precheck 분류 — keep_alive 10m·num_predict 100 (Flask 컨텍스트 불요) ─────────────────
def test_medical_classify_payload_identical(wire):
    post = wire(_Resp(200, {"response": 'x {"is_medical": true, "modality": "xray", "body_part": "chest", "confidence": 0.9}'}))
    out = V._classify_medical_image("IMG")
    assert out == {"is_medical": True, "modality": "xray", "modality_confidence": 0.9,
                   "body_part": "chest", "body_part_confidence": 0.9}
    assert post.sent[0] == {"url": _URL, "timeout": 30, "stream": False, "json": {
        "model": V.MEDICAL_VISION_MODEL, "prompt": V._CLASSIFICATION_PROMPT, "images": ["IMG"],
        "stream": False, "keep_alive": "10m", "options": {"num_predict": 100, "temperature": 0.1, "repeat_penalty": 1.1}}}


def test_medical_classify_error_contract(wire):
    wire(requests.exceptions.ConnectionError("down"))
    assert V._classify_medical_image("IMG")["error"] == "down"
    wire(_Resp(503, {"error": "busy"}))
    assert V._classify_medical_image("IMG")["error"] == "Ollama HTTP 503"


# ── 6. 의료 영상 분석(비스트림) — images/system/options ─────────────────────────────────────
def test_medical_analyze_payload_identical(wire, client):
    post = wire(_Resp(200, {"response": "## Findings\nnormal\n## Impression\nnone\n## Recommendations\nnone",
                            "eval_count": 7, "eval_duration": 5_000_000}))
    r = client.post("/api/vision/medical/analyze",
                    data={**_img(), "modality": "xray", "body_part": "chest", "language": "en"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["model"] == V.MEDICAL_VISION_MODEL and body["tokens"] == 7 and body["duration_ms"] == 5
    assert body["findings"] == "normal" and body["impression"] == "none"
    assert post.sent[0] == {"url": _URL, "timeout": 120, "stream": False, "json": {
        "model": V.MEDICAL_VISION_MODEL,
        "prompt": _analyze_prompt("xray", "chest", "", "en"),
        "system": V.MEDICAL_IMAGING_SYSTEM,
        "images": [_B64],
        "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.3, "repeat_penalty": 1.1}}}


def test_medical_analyze_error_contracts(wire, client):
    wire(_Resp(200, {"response": ""}))
    r = client.post("/api/vision/medical/analyze", data={**_img(), "language": "en"})
    assert r.status_code == 502 and r.get_json()["code"] == "EMPTY_RESPONSE"

    wire(requests.exceptions.Timeout("slow"))
    r = client.post("/api/vision/medical/analyze", data={**_img(), "language": "en"})
    assert r.status_code == 504 and r.get_json()["code"] == "TIMEOUT"

    wire(_Resp(500, {"error": "oom"}))
    r = client.post("/api/vision/medical/analyze", data={**_img(), "language": "en"})
    assert r.status_code == 502 and r.get_json() == {"error": "LLM engine error (HTTP 500)", "code": "LLM_ERROR",
                                                     "status_code": 500, "detail": "oom"}


# ── 7. 스트리밍 SSE — stream True·keep_alive 10m, 이벤트 형식(token/done) 그대로 ─────────────
def test_medical_stream_payload_and_sse_identical(wire, client):
    post = wire(_Resp(200, lines=[
        {"response": "nor", "done": False}, b"",
        {"response": "mal", "done": False},
        {"response": "", "done": True, "eval_count": 9, "done_reason": "stop"},
    ]))
    r = client.post("/api/vision/medical/analyze/stream",
                    data={**_img(), "modality": "ct", "body_part": "brain", "language": "en"})
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    events = _sse_events(r.get_data(as_text=True))
    assert events[:2] == [{"type": "token", "token": "nor"}, {"type": "token", "token": "mal"}]
    done = events[2]
    assert isinstance(done.pop("duration_ms"), int)
    assert done == {"type": "done", "model": V.MEDICAL_VISION_MODEL, "modality": "ct", "body_part": "brain",
                    "tokens": 9, "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."}
    assert len(events) == 3
    assert post.sent[0] == {"url": _URL, "timeout": 120, "stream": True, "json": {
        "model": V.MEDICAL_VISION_MODEL,
        "prompt": _analyze_prompt("ct", "brain", "", "en"),
        "system": V.MEDICAL_IMAGING_SYSTEM,
        "images": [_B64],
        "stream": True,
        "keep_alive": "10m",
        "options": {"num_predict": 1024, "temperature": 0.3, "repeat_penalty": 1.1}}}


def test_medical_stream_error_event(wire, client):
    wire(requests.exceptions.ConnectionError("down"))
    r = client.post("/api/vision/medical/analyze/stream", data={**_img(), "language": "en"})
    assert _sse_events(r.get_data(as_text=True)) == [{"type": "error", "error": "down"}]


# ── 8. 비교 판독 — 이미지 2장·num_predict 1500·timeout 180 ────────────────────────────────
def test_medical_report_two_images_payload_identical(wire, client):
    post = wire(_Resp(200, {"response": "report body", "eval_count": 2, "eval_duration": 3_000_000}))
    r = client.post("/api/vision/medical/report",
                    data={**_img("current_image"), **_img("previous_image"),
                          "modality": "xray", "body_part": "chest", "clinical_info": "cough", "language": "en"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["comparison"] is True and body["report"] == "report body" and body["tokens"] == 2
    assert post.sent[0] == {"url": _URL, "timeout": 180, "stream": False, "json": {
        "model": V.MEDICAL_VISION_MODEL,
        "prompt": _report_prompt_comparison("xray", "chest", "cough", "en"),
        "system": V.MEDICAL_IMAGING_SYSTEM,
        "images": [_B64, _B64],
        "stream": False,
        "options": {"num_predict": 1500, "temperature": 0.3, "repeat_penalty": 1.1}}}


# ── 9. 이중 판독 — 두 호출 페이로드 + 예외는 항목별 error(종전 계약) ─────────────────────────
def test_dual_read_payloads_and_per_read_error(wire, client):
    post = wire(_Resp(200, {"response": "A", "eval_count": 3, "total_duration": 2_000_000}),
                requests.exceptions.ConnectionError("down"))
    r = client.post("/api/vision/medical/dual-read",
                    data={**_img(), "modality": "xray", "clinical_info": "cough", "language": "en"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["primary_read"] == {"model": V.MEDICAL_VISION_MODEL, "findings": "A", "tokens": 3, "elapsed_ms": 2}
    assert body["secondary_read"] == {"model": "medgemma:4b", "error": "down"}
    assert body["comparison"]["dual_read_complete"] is False
    prompt = _dual_prompt("xray", "cough", "en")
    assert post.sent[0] == {"url": _URL, "timeout": 120, "stream": False, "json": {
        "model": V.MEDICAL_VISION_MODEL, "prompt": prompt, "system": V.MEDICAL_IMAGING_SYSTEM,
        "images": [_B64], "stream": False, "options": {"num_predict": 1024, "temperature": 0.3, "repeat_penalty": 1.1}}}
    assert post.sent[1] == {"url": _URL, "timeout": 120, "stream": False, "json": {
        "model": "medgemma:4b", "prompt": prompt,
        "system": "You are a medical imaging specialist. Provide detailed findings.",
        "images": [_B64], "stream": False, "options": {"num_predict": 1024, "temperature": 0.3, "repeat_penalty": 1.1}}}


def test_dual_read_http_error_surfaces(wire, client):
    """v2.125.15 무성 실패 일괄: HTTP 오류는 전송 예외와 같은 error 형태 —
    종전엔 findings ""·tokens 0으로 흘렀다(200인데 빈 결과 클래스)."""
    wire(_Resp(500, {"error": "boom"}))
    r = client.post("/api/vision/medical/dual-read", data={**_img(), "language": "en"})
    body = r.get_json()
    assert body["primary_read"]["model"] == V.MEDICAL_VISION_MODEL
    assert "error" in body["primary_read"] and "findings" not in body["primary_read"]


# ── 10. `_vision_payload` 계약은 그대로(기존 test_vision_thinking_budget과 같은 축) ──────────
def test_router_generate_uses_vision_payload_verbatim(wire):
    post = wire(_Resp(200, {"response": "x"}))
    p = V._vision_payload("qwen3-vl:8b", "설명", ["b64"], 500)
    res, exc = V._router_generate("vision.describe", p, timeout=180)
    assert exc is None and res.ok and res.raw == {"response": "x"}
    # D2(v2.125.14)부터 "verbatim"은 options의 repeat_penalty 핀 제외 그대로 — 호출자 dict(p)에는 핀이 없다.
    assert p == {"model": "qwen3-vl:8b", "prompt": "설명", "images": ["b64"],
                 "stream": False, "options": {"num_predict": 4096}, "think": False}
    assert post.sent[0]["json"] == {"model": "qwen3-vl:8b", "prompt": "설명", "images": ["b64"],
                                    "stream": False,
                                    "options": {"num_predict": 4096, "repeat_penalty": 1.1}, "think": False}


# ── 11. 실제 라우팅 표 등록 — 미등록 task는 라우터가 UnknownTask로 즉시 실패시킨다(비전 전 엔드포인트 500) ──
def test_tasks_registered_in_routing_table():
    """⚠️ config/model_routing.json에 vision.* 7 task가 없으면 실패한다 — 이관 코드가 살아 있는 표를 요구한다."""
    r = model_router.load_routing()
    missing = [t for t in _TASKS if t not in r["tasks"]]
    assert not missing, f"config/model_routing.json에 미등록: {missing}"
    for t in _TASKS:
        assert model_router.resolve(t)["policy"] == "local_only", t
