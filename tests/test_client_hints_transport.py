"""client.call hints 전송 구조 복구 (v2.125.24).

종전 core/orchestrator/client.call은 hints를 인자로 받고도 전송하지 않아, 워커가
form/body 기본값으로 낙착했다(STT language가 무성 ko로 떨어진 X7의 구조 원인).
처치=_encode가 hints를 multipart면 form data로, json이면 body에 병합(payload 우선).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.orchestrator import client  # noqa: E402


# ── _encode: multipart(audio/image/file) ───────────────────────────────────

def test_hints_go_to_form_for_multipart():
    files, data, json_body, fh = client._encode(b"AUDIO", "audio",
                                                 hints={"language": "en"})
    assert files["audio"][0] == "audio.wav"
    assert data == {"language": "en"}      # 워커가 request.form.get("language")로 읽는다
    assert json_body is None and fh is None


def test_no_hints_empty_form():
    files, data, json_body, fh = client._encode(b"X", "image", hints=None)
    assert data == {}                      # 힌트 없으면 빈 form(회귀 없음)


def test_none_and_nonscalar_hints_excluded():
    files, data, _, _ = client._encode(b"X", "audio",
                                       hints={"language": "ko", "initial_prompt": None,
                                              "hotwords": ["용어1", "용어2"], "n": 3})
    # None 제외 · list(비-스칼라) 제외 · 스칼라는 문자열화
    assert data == {"language": "ko", "n": "3"}


def test_hints_merge_into_json_body_payload_wins():
    files, data, json_body, fh = client._encode({"data": "x", "language": "ja"}, "json",
                                                hints={"language": "en", "extra": "v"})
    assert files is None and data is None
    assert json_body["language"] == "ja"   # payload 우선(setdefault)
    assert json_body["extra"] == "v"       # payload에 없는 hint는 병합


# ── call: requests에 data가 실리는지 ────────────────────────────────────────

class _Resp:
    status_code = 200

    def json(self):
        return {"available": True, "text": "ok"}


def test_call_passes_data_to_requests(monkeypatch):
    from core.orchestrator import nodes, registry
    monkeypatch.setattr(registry, "capability", lambda cap: registry.Capability(
        name=cap, resource="heavy", worker_route="/worker/stt", payload_kind="audio"))
    monkeypatch.setattr(nodes, "imac_conf",
                        lambda: {"url": "http://x", "token": "t", "delegate": ["stt"]})
    captured = {}

    import requests

    def _fake_post(url, files=None, data=None, json=None, headers=None, timeout=None):
        captured.update(url=url, data=data, files=files, headers=headers)
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
    out = client.call(nodes.IMAC, "stt", b"AUDIO", hints={"language": "en"})
    assert out == {"available": True, "text": "ok"}
    assert captured["data"] == {"language": "en"}      # 종전엔 전송 자체가 없었다
    assert captured["headers"]["X-Worker-Token"] == "t"
