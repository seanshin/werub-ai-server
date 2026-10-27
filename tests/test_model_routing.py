"""GET /api/models/routing 회귀 — 경로별 서빙 모델·digest 스냅샷 (규제 대응 R2 확장).

하드코딩 표가 아니라 실제 라우팅 함수를 호출해 조립하므로, 활성 설정(heavy 슬롯)·
규제 가드(medical_pin_public_models)가 응답에 즉시 반영되는 것이 계약이다."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routes.medical as medical  # noqa: E402
import routes.ollama as ollama  # noqa: E402


def _fake_http(digests=None, engine_ver="0.30.11"):
    """http_requests.get 대체 — /api/tags·/api/version 응답 모킹."""
    def _get(url, timeout=5):
        r = MagicMock()
        if url.endswith("/api/tags"):
            r.json.return_value = {"models": [
                {"name": k, "digest": v + "0" * 52} for k, v in (digests or {}).items()]}
        else:
            r.json.return_value = {"version": engine_ver}
        return r
    m = MagicMock()
    m.get.side_effect = _get
    return m


_DIGESTS = {"qwen2.5:14b": "7cdf5a0187d5", "qwen2.5:7b": "845dbda0ea48",
            "werub-medical:7b": "f440e49dd806", "werubmedv2:latest": "a7a9ca5feb75",
            "thewindmom/llama3-med42-8b:latest": "9605c7ec9e5d"}


def _build(cfg=None, active=None):
    with patch.object(ollama, "http_requests", _fake_http(_DIGESTS)), \
         patch.object(ollama, "_OLLAMA_BASE", "http://localhost:11434"), \
         patch.object(medical, "_load_config", lambda: cfg or {}), \
         patch.object(medical, "_load_active_model_config",
                      lambda: active or {"heavy_model": "werubmedv2",
                                         "light_model": "qwen2.5:7b"}), \
         patch("core.config.load_config", return_value=cfg or {}):
        return ollama.build_model_routing()


def test_routing_snapshot_structure():
    d = _build()
    r = d["routes"]
    assert d["engine"] == {"name": "ollama", "version": "0.30.11"}
    # DUR = LLM 미사용 결정적 DB
    assert r["/api/medical/dur/*"]["model"] is None
    assert "LLM 미사용" in r["/api/medical/dur/*"]["policy"]
    # /v1 = 호출자 통제
    assert "caller-specified" in r["/v1/chat/completions"]["policy"]
    # light 경로 digest 조인
    assert r["/api/medical/reference"]["model"] == "qwen2.5:7b"
    assert r["/api/medical/reference"]["digest"] == "845dbda0ea48"


def test_routing_reflects_active_heavy_slot_and_custom_flag():
    """활성 설정 heavy=werubmedv2가 응답에 그대로 드러나고 custom 플래그 표시."""
    r = _build()["routes"]
    assert r["/api/medical/summarize"]["model"] == "werubmedv2"
    assert r["/api/medical/summarize"]["custom"] is True
    stages = r["/api/chat/enhanced(medical)"]["pipeline"]
    assert stages[0]["model"] == "werub-medical:7b"
    assert stages[0]["custom"] is True
    assert stages[0]["digest"] == "f440e49dd806"   # :latest 폴백 조인


def test_routing_reflects_pin_guard():
    """가드 on이면 자동 라우팅·파이프라인 모두 공개 모델로 반영(감지 계약의 핵심)."""
    d = _build(cfg={"medical_pin_public_models": True})
    r = d["routes"]
    assert d["pin_public_models"] is True
    assert r["/api/medical/summarize"]["model"] == "qwen2.5:14b"
    assert r["/api/medical/summarize"]["custom"] is False
    assert all(s["custom"] is False
               for s in r["/api/chat/enhanced(medical)"]["pipeline"])


def test_digest_failure_degrades_to_null():
    with patch.object(ollama, "http_requests", MagicMock(get=MagicMock(side_effect=RuntimeError))), \
         patch.object(ollama, "_OLLAMA_BASE", "http://localhost:11434"), \
         patch.object(medical, "_load_config", lambda: {}), \
         patch.object(medical, "_load_active_model_config", lambda: {}), \
         patch("core.config.load_config", return_value={}):
        d = ollama.build_model_routing()
    assert d["routes"]["/api/medical/reference"]["digest"] is None
    assert d["engine"]["version"] is None


def test_models_digest_map_present(monkeypatch):
    """caller-specified 경로(/v1)용 모델명→digest 직접 조회 맵 (트윈 request4)."""
    import routes.ollama as O
    monkeypatch.setattr(O, "_ollama_digests",
                        lambda: {"qwen2.5:7b": "7cdf5a0187d5", "bad": ""})
    data = O.build_model_routing()
    assert data["models"]["qwen2.5:7b"] == "7cdf5a0187d5"
    assert "bad" not in data["models"]          # digest 없는 항목은 제외
