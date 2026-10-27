"""D2 엔진 기본값 핀 (`core/engine_defaults`) — v2.125.14.

고정하는 전제:
  ① 핀 값 = 1.1 (A/B N=40 판정 · v2.125.9 §5-b) — 값이 바뀌면 여기가 먼저 빨개진다
  ② 호출자 명시가 이긴다(setdefault) — A/B 하네스 양 팔 값을 핀이 덮으면 측정이 깨진다
  ③ Modelfile 자체 값 모델(qwen3:8b=1 등)은 핀하지 않는다 — 블랭킷 setdefault 금지(기획서 §6)
  ④ /api/show 실패 = 핀 생략(fail-safe) · 실패는 짧게 캐시(60s)
  ⑤ 호출자 dict를 제자리 수정하지 않는다 — 라우팅 표 options 오염 방지
  ⑥ 라우터 페이로드 빌더 두 곳(_payload/_chat_payload)이 모두 핀을 통과한다

⚠️ conftest의 autouse 픽스처가 `_fetch_modelfile_params`를 "자체 값 없음"으로 스텁한다.
   이 파일은 분기별로 그 위에 자기 스텁을 다시 얹는다(monkeypatch 후승자 우선).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from core import engine_defaults as ed
from core import model_router as mr


# ── 순수 파서 ────────────────────────────────────────────────────────────────
def test_parse_detects_repeat_penalty_line():
    assert ed._parse_has_repeat_penalty("num_ctx 4096\nrepeat_penalty 1\nstop <|im_end|>")


def test_parse_ignores_other_params_and_substrings():
    # 부분문자열 오매칭 금지(v2.125.6 `의무` 클래스) — 토큰 단위 비교여야 한다
    assert not ed._parse_has_repeat_penalty("num_ctx 4096\nstop repeat_penalty")
    assert not ed._parse_has_repeat_penalty("repeat_last_n 64")
    assert not ed._parse_has_repeat_penalty("")


# ── 핀 결정 ──────────────────────────────────────────────────────────────────
def test_pin_added_when_model_relies_on_engine_default():
    out = ed.pin_options("qwen2.5:14b", {"temperature": 0})
    assert out == {"temperature": 0, "repeat_penalty": 1.1}


def test_pin_value_is_the_ab_verdict():
    assert ed.REPEAT_PENALTY == 1.1


def test_caller_explicit_value_wins():
    out = ed.pin_options("qwen2.5:14b", {"repeat_penalty": 1.0})
    assert out["repeat_penalty"] == 1.0


def test_no_pin_when_modelfile_has_own_value(monkeypatch):
    monkeypatch.setattr(ed, "_fetch_modelfile_params",
                        lambda model, base=None: "repeat_penalty 1\nnum_ctx 4096")
    ed._cache.clear()
    out = ed.pin_options("qwen3:8b", {"temperature": 0})
    assert "repeat_penalty" not in out


def test_no_pin_when_show_fails(monkeypatch):
    def _boom(model, base=None):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(ed, "_fetch_modelfile_params", _boom)
    ed._cache.clear()
    out = ed.pin_options("qwen2.5:14b", {"temperature": 0})
    assert out == {"temperature": 0}
    # 실패는 캐시된다(짧게) — 같은 모델 재호출이 /api/show를 다시 때리지 않는다
    monkeypatch.setattr(ed, "_fetch_modelfile_params",
                        lambda model, base=None: (_ for _ in ()).throw(AssertionError("재조회")))
    assert ed.pin_options("qwen2.5:14b", None) == {}


def test_lookup_cached_per_model(monkeypatch):
    calls = []
    def _fetch(model, base=None):
        calls.append(model)
        return ""
    monkeypatch.setattr(ed, "_fetch_modelfile_params", _fetch)
    ed._cache.clear()
    ed.pin_options("qwen2.5:14b", None)
    ed.pin_options("qwen2.5:14b", {"temperature": 0})
    ed.pin_options("qwen2.5:7b", None)
    assert calls == ["qwen2.5:14b", "qwen2.5:7b"]


def test_caller_dict_not_mutated():
    src = {"temperature": 0}
    out = ed.pin_options("qwen2.5:14b", src)
    assert src == {"temperature": 0} and out is not src


def test_empty_model_never_pins():
    assert ed.pin_options("", None) == {}


# ── 라우터 배선 — 두 페이로드 빌더 모두 핀을 통과한다(⑥) ─────────────────────
class _Resp:
    def __init__(self, body):
        self._b, self.status_code = body, 200
        self.text = json.dumps(body)

    def json(self):
        return self._b


class _Sess:
    def __init__(self):
        self.sent = []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json))
        return _Resp({"response": "r", "message": {"content": "r"},
                      "eval_count": 1, "total_duration": 1_000_000})


def _routing():
    return {"tasks": {"t.gen": {"model": "qwen2.5:14b"}},
            "defaults": {"policy": "local_only", "provider": "ollama"}}


def test_generate_payload_carries_pin():
    s = _Sess()
    ad = mr.OllamaAdapter(base="http://t:11434", session=s)
    mr.generate("t.gen", "P", routing=_routing(), adapter=ad)
    url, payload = s.sent[-1]
    assert url.endswith("/api/generate")
    assert payload["options"] == {"repeat_penalty": 1.1}


def test_chat_payload_carries_pin():
    s = _Sess()
    ad = mr.OllamaAdapter(base="http://t:11434", session=s)
    mr.chat("t.gen", [{"role": "user", "content": "P"}], routing=_routing(), adapter=ad)
    url, payload = s.sent[-1]
    assert url.endswith("/api/chat")
    assert payload["options"] == {"repeat_penalty": 1.1}


def test_stream_payload_carries_pin():
    class _StreamSess(_Sess):
        def post(self, url, json=None, timeout=None, stream=False):
            self.sent.append((url, json))
            class _R:
                status_code = 200
                def iter_lines(self):
                    yield b'{"response":"r","done":true,"eval_count":1}'
                def close(self):
                    pass
            return _R()
    s = _StreamSess()
    ad = mr.OllamaAdapter(base="http://t:11434", session=s)
    list(mr.stream("t.gen", "P", routing=_routing(), adapter=ad))
    assert s.sent[-1][1]["options"] == {"repeat_penalty": 1.1}


def test_embed_path_is_not_pinned():
    """embed는 핀 대상이 아니다 — repeat_penalty는 생성 전용."""
    s = _Sess()

    class _EmbedResp(_Resp):
        pass
    ad = mr.OllamaAdapter(base="http://t:11434", session=s)
    ad.embed(model="bge-m3", texts=["x"], ctx=1024)
    url, payload = s.sent[-1]
    assert url.endswith("/api/embed")
    assert payload["options"] == {"num_ctx": 1024}
