"""P6-1 Phase 0 — `logprobs` 통과 배선 회귀. 순수 단위(HTTP 0).

🔑 **가장 중요한 고정은 "기본값에서 페이로드가 안 바뀐다"는 것**이다. 이게 깨지면
v2.125.13 이관 동일성 회귀(75건)가 의미를 잃는다 — 그 회귀들은 "종전 페이로드를
그대로 적은 것"이고, 여기에 키가 하나 더 붙으면 전부 다른 요청이 된다.
"""

import pytest

from core import model_router as M


@pytest.fixture()
def adapter():
    return M.OllamaAdapter(base="http://127.0.0.1:11434")


def test_기본값은_페이로드에_키를_안_싣는다(adapter):
    p = adapter._payload(model="m", prompt="p", stream=False)
    assert "logprobs" not in p and "top_logprobs" not in p


def test_logprobs_False_명시도_안_싣는다(adapter):
    p = adapter._payload(model="m", prompt="p", stream=False, logprobs=False, top_logprobs=5)
    assert "logprobs" not in p and "top_logprobs" not in p


def test_요청하면_싣는다(adapter):
    p = adapter._payload(model="m", prompt="p", stream=False, logprobs=True, top_logprobs=3)
    assert p["logprobs"] is True and p["top_logprobs"] == 3


def test_top_logprobs_미지정이면_그_키만_없다(adapter):
    p = adapter._payload(model="m", prompt="p", stream=False, logprobs=True)
    assert p["logprobs"] is True and "top_logprobs" not in p


def test_스트림_페이로드는_손대지_않았다(adapter):
    """범위 최소화 — 평가 경로는 비스트림이다."""
    a = adapter._payload(model="m", prompt="p", stream=True)
    assert "logprobs" not in a


class _Adapter:
    """logprobs 인자를 **모르는** 어댑터(외부 provider 형상)."""
    name = "legacy"
    is_local = True

    def __init__(self):
        self.calls = []

    def generate(self, *, model, prompt, system=None, options=None, schema=None,
                 images=None, think=None, keep_alive=None, timeout=60):
        self.calls.append({"model": model})
        return M.Result(text="ok", model=model, provider=self.name, ok=True)


def test_미지원_어댑터는_확률없이_진행한다(monkeypatch):
    """🔑 **거절이 아니라 폴백**. 미지원이 평가를 멈추면 안 된다(fail-open)."""
    ad = _Adapter()
    monkeypatch.setattr(M, "resolve", lambda task, routing=None: {"provider": "x", "model": "m"})
    monkeypatch.setattr(M, "check_policy", lambda spec, is_local: None)
    r = M.generate("eval.verifier", "p", adapter=ad, logprobs=True, top_logprobs=5)
    assert r.ok and r.text == "ok"
    assert len(ad.calls) == 1


def test_미지원_어댑터_TypeError는_logprobs_요청시에만_삼킨다(monkeypatch):
    """logprobs를 안 걸었는데 난 TypeError는 **진짜 배선 오류**라 던져야 한다."""
    class Broken(_Adapter):
        def generate(self, **kw):
            raise TypeError("real wiring bug")

    monkeypatch.setattr(M, "resolve", lambda task, routing=None: {"provider": "x", "model": "m"})
    monkeypatch.setattr(M, "check_policy", lambda spec, is_local: None)
    with pytest.raises(TypeError):
        M.generate("eval.verifier", "p", adapter=Broken())
