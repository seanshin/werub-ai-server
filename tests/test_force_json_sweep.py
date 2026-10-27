"""force_json 폴백 무성 실패 전수 처치 — v2.125.72 (2026-10-08 · v2.125.71 예측 3종의 같은 클래스 쓸기 #7).

`force_json`은 실패해도 `{fallback_key: 원문}`, `force_json_array`는 `[]`를 돌려줘 호출자가 성공/실패를 못 갈랐다.
- 주 결과(A) → 명시 오류(502 / 워크플로 단계 `error`)
- 하위 단계(C) → 엔드포인트는 살리고 항목 단위 실패 표식
- 의도된 저하(B: deep-analysis·explain) → 동작 유지 + `parse_ok` 표식(추가 필드)
"""
import io
import json

import pytest

NON_JSON = "죄송합니다. 다음과 같이 분석됩니다. 환자의 상태는 양호합니다."


# ── 판정 함수 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("[]", []), ("{}", {}), ('{"a": 1}', {"a": 1}), ('설명\n```json\n[{"x": 1}]\n```', [{"x": 1}]),
    (NON_JSON, None), ("", None), (None, None),
])
def test_parse_json_strict(text, expected):
    from core.quality import parse_json_strict
    assert parse_json_strict(text) == expected


def test_parse_json_array_strict_keeps_legit_empty():
    from core.quality import parse_json_array_strict
    assert parse_json_array_strict("[]") == []                 # 정상 '없음'은 성공
    assert parse_json_array_strict('{"a": 1}') == [{"a": 1}]   # 종전 force_json_array 규칙
    assert parse_json_array_strict(NON_JSON) is None


# ── 공용 픽스처 ─────────────────────────────────────────────────────────────
@pytest.fixture
def app_client():
    import app as A                                            # noqa: PLC0415
    c = A.app.test_client()

    def post(path, **kw):
        return c.post(path, environ_base={"REMOTE_ADDR": "127.0.0.1"}, **kw)
    return post


@pytest.fixture
def med(monkeypatch):
    """routes.medical 생성 함수·테넌트·저장을 가짜로 — 생성 응답은 queue로 지정."""
    import app  # noqa: F401,PLC0415
    from routes import medical as M
    st = {"queue": [], "saved": []}

    def fake_gen(model, prompt, system, options=None, timeout=None, action="", accept=None):
        return (st["queue"].pop(0) if st["queue"] else st["default"]), 11, 100
    monkeypatch.setattr(M, "_medical_generate", fake_gen)
    monkeypatch.setattr(M, "_require_tenant", lambda: ({"id": "t-test", "name": "t"}, None))
    monkeypatch.setattr(M, "_save_consultation", lambda *a, **k: st["saved"].append(a[1]) or "cid")
    st["default"] = NON_JSON
    return st


@pytest.fixture
def madv(monkeypatch):
    import app  # noqa: F401,PLC0415
    from routes import medical_advanced as MA
    st = {"queue": [], "default": NON_JSON}

    def fake_gen(*a, **kw):
        return (st["queue"].pop(0) if st["queue"] else st["default"]), 11, 100
    monkeypatch.setattr(MA, "_medical_generate", fake_gen)
    return st


# ── A: 주 결과 → 502 ────────────────────────────────────────────────────────
def test_auto_record_ner_unparsed_is_502(app_client, madv):
    r = app_client("/api/medical/auto-record", json={"conversation": "환자: 기침이 나요"})
    assert r.status_code == 502 and r.get_json()["code"] == "generation_failed"   # 구: 200 {"soap": {"response": …}}


def test_auto_record_stream_unparsed_is_error_event(app_client, madv):
    r = app_client("/api/medical/auto-record/stream", json={"conversation": "환자: 기침이 나요"})
    events = [json.loads(l[6:]) for l in r.get_data(as_text=True).splitlines() if l.startswith("data: ")]
    assert any(e.get("event") == "error" and e.get("status") == 502 for e in events), events
    assert not any(e.get("event") == "complete" for e in events)


def test_drug_analysis_step1_unparsed_is_502(app_client, madv):
    r = app_client("/api/medical/drug-analysis", json={"medications": ["warfarin", "aspirin"], "depth": "quick"})
    assert r.status_code == 502


def test_drug_analysis_step2_unparsed_is_section_failure(app_client, madv):
    madv["queue"] = ['{"interactions": [{"pair": "warfarin+aspirin", "severity": "high"}]}', NON_JSON]
    r = app_client("/api/medical/drug-analysis", json={"medications": ["warfarin", "aspirin"], "depth": "standard"})
    d = r.get_json()
    assert r.status_code == 200 and d["interactions"]["interactions"][0]["severity"] == "high"
    assert d["recommendations"] is None and "recommendations_error" in d          # 구: {"response": 원문}


def test_summarize_unparsed_is_502_and_not_saved(app_client, med):
    r = app_client("/api/medical/summarize", json={"text": "환자는 3일 전부터 기침과 발열이 있다."})
    assert r.status_code == 502                                                   # 구: 200 {"raw": …}
    assert med["saved"] == []                                                     # 실패가 이력·과금에 안 남는다


def test_summarize_split_meta_unparsed_is_section_failure(app_client, med):
    med["default"] = "S: 기침 3일"            # SOAP 절은 텍스트 그대로 쓴다 — 메타만 JSON 계약
    r = app_client("/api/medical/summarize", json={"text": "환자는 3일 전부터 기침과 발열이 있다.", "split_soap": True})
    d = r.get_json()
    assert r.status_code == 200 and d["urgency"] is None and "meta_error" in d    # 구: urgency "medium"(지어냄)


def test_analyze_unparsed_is_502(app_client, med):
    r = app_client("/api/medical/analyze", json={"symptoms": ["발열", "기침"], "age": 40})
    assert r.status_code == 502 and med["saved"] == []                            # 구: 200 differential []


def test_analyze_legit_empty_is_200(app_client, med):
    med["default"] = "[]"
    r = app_client("/api/medical/analyze", json={"symptoms": ["발열"], "age": 40})
    assert r.status_code == 200 and r.get_json()["differential_diagnosis"] == []


# ── C: 항목 단위 ────────────────────────────────────────────────────────────
def test_batch_unparsed_record_is_failed(app_client, med):
    med["queue"] = [NON_JSON, '{"summary": "ok"}']
    r = app_client("/api/medical/batch", json={"action": "summarize",
                                               "records": [{"id": "a", "text": "기침"}, {"id": "b", "text": "발열"}]})
    rows = {x["id"]: x for x in r.get_json()["results"]}
    assert {rows["a"]["status"], rows["b"]["status"]} == {"failed", "completed"}  # 구: 둘 다 completed


def test_cross_validate_unparsed_model_excluded(app_client, madv):
    madv["queue"] = [NON_JSON, '{"diagnoses": [{"name": "폐렴"}], "urgency": "high"}']
    r = app_client("/api/medical/cross-validate", json={"patient_text": "발열 기침", "models": ["m1", "m2"]})
    d = r.get_json()
    errs = [k for k, v in d["cross_validation"]["model_results"].items() if "error" in v]
    assert r.status_code == 200 and len(errs) == 1                                # 구: 둘 다 유효(diagnoses [])


# ── B: 저하 유지 + 표식 ─────────────────────────────────────────────────────
def test_deep_analysis_keeps_text_with_parse_ok_false(app_client, madv):
    r = app_client("/api/medical/deep-analysis", json={"patient_text": "발열 기침"})
    d = r.get_json()
    assert r.status_code == 200 and d["parse_ok"] is False and d["analysis"]["analysis"] == NON_JSON


def test_explain_keeps_text_with_parse_ok_false(app_client, med):
    r = app_client("/api/medical/explain", json={"diagnosis": "고혈압"})
    d = r.get_json()
    assert r.status_code == 200 and d["parse_ok"] is False and NON_JSON in d["explanation"]


# ── 워크플로 단계 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("ep,body", [("/api/medical/summarize", {"text": "기침"}),
                                     ("/api/medical/analyze", {"symptoms": ["발열"]})])
def test_orchestrator_direct_steps_fail_on_unparsed(med, ep, body):
    from routes import orchestrator as O
    fn = O._get_direct_call_map()[ep]
    out = fn(body, {"id": "t-test"})
    assert "error" in out                                                         # 구: 완료로 다음 단계에 흘렀다


# ── 쓰기·코딩 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/api/writing/seo", "/api/writing/evaluate"])
def test_writing_unparsed_is_502(app_client, monkeypatch, path):
    from routes import writing as W
    monkeypatch.setattr(W, "_generate", lambda *a, **k: (NON_JSON, 5, 10))
    r = app_client(path, json={"text": "오늘은 블로그 글쓰기에 대해 이야기해 보겠습니다. " * 3})
    assert r.status_code == 502 and r.get_json()["code"] == "MODEL_OUTPUT_UNPARSED"


def test_code_review_unparsed_is_502_and_not_cached(app_client, monkeypatch):
    from routes import coding as C
    monkeypatch.setattr(C, "_code_generate", lambda *a, **k: (NON_JSON, 5, 10))
    sets = []
    monkeypatch.setattr(C._coding_cache, "set", lambda k, v: sets.append(k))
    monkeypatch.setattr(C._coding_cache, "get", lambda k: None)
    r = app_client("/api/code/review", json={"code": "def f(x):\n    return x/0\n", "language": "python"})
    assert r.status_code == 502 and sets == []                                    # 구: 200 issues [] + 캐시


def test_code_review_legit_no_issues_is_200(app_client, monkeypatch):
    from routes import coding as C
    monkeypatch.setattr(C, "_code_generate", lambda *a, **k: ("[]", 5, 10))
    monkeypatch.setattr(C._coding_cache, "get", lambda k: None)
    monkeypatch.setattr(C._coding_cache, "set", lambda k, v: None)
    r = app_client("/api/code/review", json={"code": "def f(x):\n    return x\n", "language": "python"})
    assert r.status_code == 200 and r.get_json()["issues"] == []
