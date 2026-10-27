"""예측 3종의 JSON 계약 — 못 지키면 502 (v2.125.71 · 2026-10-07 HIS 문의).

종전: 모델 출력이 JSON이 아니어도 `force_json`이 `{"response": 원문}`으로 감싸 **200**(반복 클래스 #1).
HIS 실측 13:51:14 complication 200 + 구조 미충족 · 14일 로그 8건(readmission 7·complication 1).
"""
import logging

import pytest

ENDPOINTS = [
    ("/api/medical/predict/readmission", {"patient_data": {"diagnosis": "심부전", "length_of_stay": 7}}),
    ("/api/medical/predict/complication", {"diagnosis": "제2형 당뇨", "patient_info": {"age": 70, "sex": "M"}}),
    ("/api/medical/predict/adverse-drug", {"medications": ["warfarin", "aspirin"], "patient_info": {"age": 80}}),
]
PHI_MARK = "환자홍길동주민번호900101"   # 로그에 응답 내용이 새면 이 문자열이 보인다


@pytest.fixture
def client(monkeypatch):
    import app as A                                     # noqa: PLC0415
    from routes import medical_advanced as MA
    out = {"text": ""}
    monkeypatch.setattr(MA, "_medical_generate", lambda **kw: (out["text"], 42, 1000))
    c = A.app.test_client()

    class C:
        def post(self, path, body, text):
            out["text"] = text
            return c.post(path, json=body, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return C()


@pytest.mark.parametrize("path,body", ENDPOINTS, ids=[p.rsplit("/", 1)[1] for p, _ in ENDPOINTS])
def test_non_json_output_is_502_not_200(client, path, body, caplog):
    with caplog.at_level(logging.WARNING, logger="ollama-admin"):
        r = client.post(path, body, f"죄송합니다. {PHI_MARK} 환자의 위험도는 높은 편입니다.")
    assert r.status_code == 502, r.get_json()                       # 구: 200 {"prediction": {"response": …}}
    assert r.get_json()["code"] == "generation_failed"
    log = "\n".join(x.getMessage() for x in caplog.records)
    assert "구조 미충족 → 502" in log
    assert PHI_MARK not in log                                       # 응답 내용은 로그에 남기지 않는다


@pytest.mark.parametrize("path,body", ENDPOINTS, ids=[p.rsplit("/", 1)[1] for p, _ in ENDPOINTS])
@pytest.mark.parametrize("text", ['{"risk_level": "high", "score": 7}',
                                  '분석 결과입니다.\n```json\n{"risk_level": "low", "score": 1}\n```'],
                         ids=["plain-json", "fenced-json"])
def test_json_output_still_200(client, path, body, text):
    r = client.post(path, body, text)
    assert r.status_code == 200
    d = r.get_json()
    assert d["prediction"]["risk_level"] in ("high", "low") and d["tokens"] == 42


@pytest.mark.parametrize("path,body", ENDPOINTS, ids=[p.rsplit("/", 1)[1] for p, _ in ENDPOINTS])
def test_empty_output_is_502(client, path, body):
    assert client.post(path, body, "").status_code == 502
