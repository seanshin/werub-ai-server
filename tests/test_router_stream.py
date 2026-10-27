"""스트리밍 이관 — 어댑터 스트림 인터페이스 (기획 Phase 1-d 3단계).

스트리밍은 비스트림보다 **틀릴 자리가 많다**:
  · 페이로드가 갈라진다 — v2.124.59에서 `/api/chat`만 `options`·`think`가 빠져
    호출자가 준 값이 200 응답과 함께 조용히 버려졌다
  · 커넥션이 샌다 — 클라이언트가 중간에 끊으면 `GeneratorExit`이 들어오는데,
    닫지 않으면 업스트림 커넥션이 풀에 남는다
  · 실패가 절반만 나간 응답으로 보인다 — 중간 오류를 삼키면 호출자가 error 이벤트를 못 낸다

이 파일이 고정하는 것:
  ① 스트림/비스트림 페이로드가 `stream` 말고는 **완전히 같다**
  ② 조기 종료·예외 어느 쪽이든 커넥션을 **닫는다**
  ③ HTTP 오류는 **던진다**(비스트림은 Result에 싣지만 스트림은 자리가 없다)
  ④ 정책(`local_only`)이 스트림에도 걸린다
  ⑤ 토글 off면 구 경로, 라우터 배선 실패는 강등 — 단 **생성 오류는 삼키지 않는다**
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch

import pytest

from core import model_router as mr
from routes import medical_law as ml


class _StreamResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status
        self.closed = 0

    def iter_lines(self):
        for ln in self._lines:
            yield ln.encode() if isinstance(ln, str) else ln

    def close(self):
        self.closed += 1

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


class _Sess:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, json=None, timeout=None, stream=False):
        self.calls.append({"url": url, "json": json, "stream": stream})
        return self.resp


def _lines(*toks, done_count=7):
    out = [json.dumps({"response": t}) for t in toks]
    out.append(json.dumps({"done": True, "eval_count": done_count}))
    return out


# ── ① 페이로드 동일성 ───────────────────────────────────────────────────────
def test_stream_and_nonstream_payloads_differ_only_by_stream():
    """🔑 두 경로가 각자 본문을 만들면 조용히 갈라진다(v2.124.59 클래스)."""
    kw = dict(model="m", prompt="p", system="s", options={"temperature": 0.3},
              schema={"type": "object"}, think=False, keep_alive="-1", images=["b64"])
    ad = mr.OllamaAdapter(base="http://t")   # D2(v2.125.14): _payload가 인스턴스 메서드가 됨(핀 배선)
    a = ad._payload(stream=False, **kw)
    b = ad._payload(stream=True, **kw)
    assert a["stream"] is False and b["stream"] is True
    a.pop("stream"); b.pop("stream")
    assert a == b


def test_stream_payload_carries_all_fields():
    sess = _Sess(_StreamResp(_lines("가", "나")))
    ad = mr.OllamaAdapter(base="http://t", session=sess)
    list(ad.stream(model="m", prompt="p", system="s", options={"temperature": 0.3},
                   keep_alive="-1", think=False))
    p = sess.calls[-1]["json"]
    assert p["stream"] is True and sess.calls[-1]["stream"] is True
    assert p["system"] == "s" and p["keep_alive"] == "-1" and p["think"] is False
    assert p["options"] == {"temperature": 0.3, "repeat_penalty": 1.1}  # D2 핀(engine_defaults)


# ── 조각 계약 ───────────────────────────────────────────────────────────────
def test_chunks_carry_text_then_done_with_tokens():
    resp = _StreamResp(_lines("가", "나", done_count=12))
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    chunks = list(ad.stream(model="m", prompt="p"))
    assert [c.text for c in chunks if c.text] == ["가", "나"]
    last = chunks[-1]
    assert last.done is True and last.tokens == 12


def test_blank_lines_are_skipped():
    resp = _StreamResp(["", json.dumps({"response": "가"}), "",
                        json.dumps({"done": True, "eval_count": 3})])
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    assert [c.text for c in ad.stream(model="m", prompt="p") if c.text] == ["가"]


# ── ② 커넥션 회수 ───────────────────────────────────────────────────────────
def test_connection_closed_on_normal_end():
    resp = _StreamResp(_lines("가"))
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    list(ad.stream(model="m", prompt="p"))
    assert resp.closed == 1


def test_connection_closed_on_early_client_exit():
    """🔴 클라이언트가 끊으면 GeneratorExit이 들어온다 — 닫지 않으면 커넥션이 샌다."""
    resp = _StreamResp(_lines("가", "나", "다"))
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    gen = ad.stream(model="m", prompt="p")
    next(gen)                      # 첫 토큰만 받고
    gen.close()                    # 중도 포기(= 클라이언트 조기 종료)
    assert resp.closed == 1


def test_connection_closed_on_malformed_line():
    """중간에 깨진 줄이 와도 닫고 나간다(예외는 올린다)."""
    resp = _StreamResp([json.dumps({"response": "가"}), "{깨진 JSON"])
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    with pytest.raises(Exception):
        list(ad.stream(model="m", prompt="p"))
    assert resp.closed == 1


# ── ③ HTTP 오류 ─────────────────────────────────────────────────────────────
def test_http_error_raises_and_closes():
    """비스트림은 Result에 싣지만 스트림은 실을 자리가 없다 — 던진다."""
    resp = _StreamResp([], status=503)
    ad = mr.OllamaAdapter(base="http://t", session=_Sess(resp))
    with pytest.raises(RuntimeError) as e:
        list(ad.stream(model="m", prompt="p"))
    assert "503" in str(e.value)
    assert resp.closed == 1


# ── ④ 정책 ──────────────────────────────────────────────────────────────────
def test_policy_applies_to_stream():
    """스트리밍만 정책을 안 보면 local_only 경계가 절반만 선다."""
    class Remote(mr.OllamaAdapter):
        is_local = False
    with pytest.raises(mr.PolicyViolation):
        list(mr.stream("medical_law.answer", "p", adapter=Remote()))


def test_unknown_task_raises_in_stream():
    with pytest.raises(mr.UnknownTask):
        list(mr.stream("없는.task", "p"))


# ── ⑤ 라우트 배선 ───────────────────────────────────────────────────────────
def test_route_stream_uses_caller_session():
    """🔴 어댑터가 자기 세션을 만들면 Fair Queue를 우회한다(2026-08-10 실측)."""
    sess = _Sess(_StreamResp(_lines("답")))
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_OLLAMA_BASE", "http://engine.test:9999"), \
         patch.object(ml, "_ollama_session", sess):
        out = list(ml._stream_tokens("m", "질문", "시스템"))
    assert sess.calls, "호출자의 세션을 쓰지 않았다 = Fair Queue 우회"
    assert sess.calls[-1]["url"] == "http://engine.test:9999/api/generate"
    assert [t for t, _d, _n in out if t] == ["답"]
    assert out[-1][1] is True


def test_route_stream_legacy_when_disabled():
    """토글 off면 구 경로 — 라우터를 아예 안 탄다(1-e 롤백)."""
    sess = _Sess(_StreamResp(_lines("답")))
    with patch.object(ml, "load_config", return_value={"model_router_enabled": False}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", sess), \
         patch("core.model_router.stream",
               side_effect=AssertionError("라우터를 타면 안 된다")):
        out = list(ml._stream_tokens("m", "질문", "시스템"))
    assert [t for t, _d, _n in out if t] == ["답"]


def test_route_stream_payload_matches_legacy():
    """이관은 변경이 아니다 — 두 경로가 같은 본문을 만든다.
    예외 하나(D2 v2.125.14): 라우터 경로 options에만 `repeat_penalty 1.1` 핀이
    합류한다(core/engine_defaults · 의도된 변경). 구 폴백 경로는 핀이 없다."""
    def run(enabled):
        sess = _Sess(_StreamResp(_lines("답")))
        with patch.object(ml, "load_config",
                          return_value={"model_router_enabled": enabled}), \
             patch.object(ml, "_keep_alive_for", return_value={"keep_alive": "-1"}), \
             patch.object(ml, "_ollama_session", sess):
            list(ml._stream_tokens("m", "질문", "시스템"))
        return sess.calls[-1]["json"]
    routed, legacy = run(True), run(False)
    assert routed["options"].pop("repeat_penalty") == 1.1   # D2 핀 — 라우터 경로에만
    assert routed == legacy


def test_route_stream_generation_error_is_not_swallowed():
    """🔴 생성 중 오류를 삼키면 **절반만 나간 응답이 정상으로 보인다**.
    강등은 라우터 **배선** 실패에만 적용된다."""
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", _Sess(_StreamResp([], status=500))), \
         pytest.raises(Exception):
        list(ml._stream_tokens("m", "질문", "시스템"))


def test_route_stream_closes_connection_on_early_exit():
    resp = _StreamResp(_lines("가", "나", "다"))
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", _Sess(resp)):
        gen = ml._stream_tokens("m", "질문", "시스템")
        next(gen)
        gen.close()
    assert resp.closed == 1, "조기 종료에서 커넥션이 샌다"
