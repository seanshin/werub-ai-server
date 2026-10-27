"""생성 실패 처리 회귀 — 실패를 **캐시하지도, 200으로 흘리지도** 않는다.

라이브 실측(2026-08-05): 일과성 타임아웃 1회가 `_medical_law_cache`(TTL 1h)에 박혀
통제군 스모크가 **3/3 같은 에러를 0.006초에** 반환했다. GPU·모델은 정상이었고 직접
생성은 3~7초였다 — 즉 **한 번의 실패가 한 시간짜리 고정 오답**이 되어 있었다.

동시에 응답 본문이 `HTTPConnectionPool(host='localhost', port=11434)`를 그대로 실어
내부 주소를 노출했다(v2.124.25 에러 위생과 같은 클래스가 이 경로에만 남아 있었다).

여기서 고정하는 것:
  ① 실패 문자열에 예외 상세(호스트·포트·라이브러리 문자열)가 들어가지 않는다
  ② 실패는 `_gen_failed`로 판별 가능하다(기존 `startswith("Error:")` 호출자 호환)
  ③ 성공 응답을 실패로 오판하지 않는다
"""
import pytest

from routes import medical_law as ML


class _BoomSession:
    """업스트림이 죽은 상태를 흉내낸다 — 예외 문자열에 내부 주소가 들어 있다."""

    def post(self, *a, **kw):
        raise OSError("HTTPConnectionPool(host='localhost', port=11434): Read timed out.")


@pytest.fixture
def boom(monkeypatch):
    monkeypatch.setattr(ML, "_ollama_session", _BoomSession())
    monkeypatch.setattr(ML, "_think_off", lambda m: {})
    monkeypatch.setattr(ML, "_keep_alive_for", lambda m: {})


def test_failure_does_not_leak_internal_address(boom):
    resp, tokens, ms = ML._generate("qwen2.5:14b", "질문", "시스템")
    for leak in ("HTTPConnectionPool", "localhost", "11434", "Read timed out"):
        assert leak not in resp, f"내부 상세 노출: {leak}"


def test_failure_is_detectable(boom):
    resp, _, _ = ML._generate("qwen2.5:14b", "질문", "시스템")
    assert ML._gen_failed(resp)
    # 기존 호출자(story/novel/writing)가 쓰는 판별식과도 호환
    assert resp.startswith("Error:")


def test_failure_returns_zero_tokens(boom):
    resp, tokens, ms = ML._generate("qwen2.5:14b", "질문", "시스템")
    assert tokens == 0 and ms == 0


def test_failure_message_is_korean_and_actionable(boom):
    resp, _, _ = ML._generate("qwen2.5:14b", "질문", "시스템")
    assert "실패" in resp


@pytest.mark.parametrize("ok", [
    "진료기록부의 보존기간은 10년입니다.",
    "",                       # 빈 응답은 실패 표식이 아니다(별개 클래스)
    "Errors 라는 단어로 시작하는 정상 답변은 아니다",
])
def test_success_not_misjudged(ok):
    assert not ML._gen_failed(ok)


def test_non_string_not_misjudged():
    assert not ML._gen_failed(None)
    assert not ML._gen_failed({"answer": "x"})
