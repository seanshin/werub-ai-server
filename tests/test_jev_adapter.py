"""P6-1 Phase 1 — Jev 어댑터 회귀. **실제 네트워크 호출 0**(세션 스텁).

고정하는 것:
  - **fail-open 불변식**: 미설정·미도달·캡 초과·파싱 실패가 전부 None이고 예외가 없다
  - 비용 가드(일일 토큰 캡)가 실제로 호출을 막는다
  - 버전 고정(`jev-latest` 금지)
  - state 캡은 **거절이 아니라 자르기**(공급사 약점 #5)
"""

import pytest

import core.jev as J

#: 픽스처가 패치하기 **전**의 진짜 가드 — 가드 테스트가 이걸 되돌려 쓴다.
_REAL_GUARD = J._caller_allowed


class _Resp:
    def __init__(self, status=200, body=None, boom=False, headers=None):
        self.status_code, self._body, self._boom = status, body or {}, boom
        self.headers = headers or {}

    def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._body


class _Session:
    """호출을 기록하는 스텁. 네트워크로 나가지 않는다.

    `resp`에 리스트를 주면 호출마다 하나씩 소비한다(재시도 검증용).
    """

    def __init__(self, resp=None, raise_exc=None):
        self.calls, self._exc = [], raise_exc
        self._seq = list(resp) if isinstance(resp, list) else None
        self._resp = None if self._seq is not None else resp

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        if self._exc:
            raise self._exc
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else _Resp(500)
        return self._resp or _Resp(200, {"answers": {"c1": {"type": "noul", "noul": 0.8}}})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """설정·키·사용량을 테스트마다 격리 — 라이브 config.json을 읽지 않는다."""
    cfg = dict(J._DEFAULTS)
    cfg["jev_enabled"] = True
    monkeypatch.setattr(J, "_cfg", lambda k: cfg[k])
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    # 호출자 가드는 여기서 통과시키고 **별도 테스트로 직접 검증**한다 —
    # 허용 목록에 `tests.`를 넣으면 목록이 운영 사실과 달라진다.
    monkeypatch.setattr(J, "_caller_allowed", lambda: True)
    J._usage.update(day=None, tokens=0)
    return cfg


def _ask(sess, **kw):
    return J.ask_noul("근거 문서", {"c1": "이 주장이 근거만으로 지지되는가"}, session=sess, **kw)


# --- 정상 경로 -----------------------------------------------------------------

def test_정상_응답은_확률을_준다():
    s = _Session()
    assert _ask(s) == {"c1": 0.8}


def test_버전을_고정해_보낸다():
    """`jev-latest` 금지 — 판정자가 조용히 바뀌면 추세선이 끊긴다."""
    s = _Session()
    _ask(s)
    assert s.calls[0]["json"]["model"] == "jev-1.13.0"
    assert "latest" not in s.calls[0]["json"]["model"]


def test_noul_타입으로_보낸다():
    s = _Session()
    _ask(s)
    q = s.calls[0]["json"]["questions"]["c1"]
    assert q["type"] == "noul" and "지지" in q["instructions"]


def test_Bearer를_싣는다():
    s = _Session()
    _ask(s)
    assert s.calls[0]["headers"]["Authorization"] == "Bearer sk-test"


# --- fail-open 불변식 -----------------------------------------------------------

def test_비활성이면_호출조차_안_한다(_isolate):
    _isolate["jev_enabled"] = False
    s = _Session()
    assert _ask(s) is None and s.calls == []


def test_키_미설정이면_호출_안_한다(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    s = _Session()
    assert _ask(s) is None and s.calls == []


def test_HTTP_오류는_None(caplog):
    s = _Session(resp=_Resp(500))
    assert _ask(s) is None


def test_타임아웃_예외는_None이고_안_던진다():
    s = _Session(raise_exc=TimeoutError("timeout"))
    assert _ask(s) is None          # 예외가 새어 나오면 배치가 멈춘다


def test_429는_재시도_후_None(_isolate, monkeypatch):
    """공급사 레이트가 '동적 조정 중'이라 예고 없이 바뀐다. 짧게 한 번 더 시도하고 포기."""
    slept = []
    monkeypatch.setattr(J.time, "sleep", slept.append)
    s = _Session(resp=[_Resp(429), _Resp(429)])
    assert _ask(s) is None
    assert len(s.calls) == 2 and slept == [1.0]


def test_429_후_성공하면_값을_준다(_isolate, monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda *_: None)
    s = _Session(resp=[_Resp(429), _Resp(200, {"answers": {"c1": {"noul": 0.61}}})])
    assert _ask(s) == {"c1": 0.61}
    assert len(s.calls) == 2


def test_깨진_JSON은_None():
    assert _ask(_Session(resp=_Resp(200, boom=True))) is None


def test_answers_부재는_None():
    assert _ask(_Session(resp=_Resp(200, {"x": 1}))) is None


def test_noul이_범위_밖이면_버린다():
    assert _ask(_Session(resp=_Resp(200, {"answers": {"c1": {"noul": 1.7}}}))) is None


def test_bool은_확률이_아니다():
    """`True`는 int 하위형이라 그냥 통과하면 1.0으로 실린다."""
    assert _ask(_Session(resp=_Resp(200, {"answers": {"c1": {"noul": True}}}))) is None


def test_빈_questions는_호출_안_한다():
    s = _Session()
    assert J.ask_noul("근거", {}, session=s) is None and s.calls == []


# --- 가드 ----------------------------------------------------------------------

def test_state_캡은_거절이_아니라_자르기(_isolate):
    """공급사 약점 #5 — state가 크면 정확도가 떨어진다. 자르되 계속 진행."""
    _isolate["jev_state_max_chars"] = 10
    s = _Session()
    J.ask_noul("가" * 500, {"c1": "q"}, session=s)
    assert len(s.calls[0]["json"]["state"]) == 10


def test_일일_캡을_넘으면_호출을_막는다(_isolate):
    _isolate["jev_daily_token_cap"] = 5
    s = _Session()
    assert J.ask_noul("아" * 400, {"c1": "q"}, session=s) is None
    assert s.calls == []            # 캡은 호출 **전에** 막아야 비용이 안 난다


def test_캡이_0이면_전부_차단(_isolate):
    _isolate["jev_daily_token_cap"] = 0
    assert _ask(_Session()) is None


def test_사용량이_누적된다():
    s = _Session()
    _ask(s); before = J.usage()["tokens"]
    _ask(s)
    assert J.usage()["tokens"] > before


# --- 🔴 PHI 경계: 호출자 가드 --------------------------------------------------

def test_허용목록은_평가층뿐이다():
    """의료·상담·STT가 목록에 있으면 **PHI가 외부로 나갈 수 있다**."""
    assert set(J._ALLOWED_CALLERS) == {
        "core.faithfulness", "scripts.eval_faithfulness", "scripts.eval_jev_gate"}
    joined = " ".join(J._ALLOWED_CALLERS)
    for banned in ("medical", "counseling", "stt", "scribe", "vision", "triage"):
        assert banned not in joined


def test_허용목록에_테스트가_없다():
    """목록이 운영 사실과 같아야 가드가 무엇을 막는지 증명된다."""
    assert not any("test" in a for a in J._ALLOWED_CALLERS)


def test_비허용_호출자는_거절되고_호출이_안_나간다(monkeypatch):
    """진짜 가드를 되돌려 놓고 부르면(=이 테스트 모듈이 호출자) 막혀야 한다."""
    monkeypatch.setattr(J, "_caller_allowed", _REAL_GUARD)
    monkeypatch.setattr(J, "_cfg", lambda k: dict(J._DEFAULTS, jev_enabled=True)[k])
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    s = _Session()
    assert J.ask_noul("근거", {"c1": "q"}, session=s) is None
    assert s.calls == []


def test_거절은_error로_남는다(monkeypatch, caplog):
    """배선 오류라 조용히 넘어가면 안 된다."""
    import logging
    monkeypatch.setattr(J, "_caller_allowed", _REAL_GUARD)
    monkeypatch.setattr(J, "_cfg", lambda k: dict(J._DEFAULTS, jev_enabled=True)[k])
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    with caplog.at_level(logging.ERROR, logger="core.jev"):
        J.ask_noul("근거", {"c1": "q"}, session=_Session())
    assert any("비허용 호출자" in r.message for r in caplog.records)


# --- 재시도: 경계가 있어야 한다 -------------------------------------------------

def test_Retry_After를_존중한다(_isolate, monkeypatch):
    slept = []
    monkeypatch.setattr(J.time, "sleep", slept.append)
    s = _Session(resp=[_Resp(503, headers={"Retry-After": "4"}),
                       _Resp(200, {"answers": {"c1": {"noul": 0.5}}})])
    assert _ask(s) == {"c1": 0.5}
    assert slept == [4.0]


def test_Retry_After가_상한을_넘으면_기다리지_않고_포기(_isolate, monkeypatch):
    """🔑 평가 배치는 **끝나야 하는 작업**이다. '10분 뒤에 오라'면 기다리는 게 아니라
    축을 null로 두고 다음 배치가 이어받는다."""
    _isolate["jev_retry_max_wait"] = 15.0
    slept = []
    monkeypatch.setattr(J.time, "sleep", slept.append)
    s = _Session(resp=[_Resp(429, headers={"Retry-After": "600"}), _Resp(200)])
    assert _ask(s) is None
    assert slept == [] and len(s.calls) == 1


def test_재시도_0이면_한_번만_부른다(_isolate):
    _isolate["jev_retry_on_rate_limit"] = 0
    s = _Session(resp=[_Resp(429), _Resp(200)])
    assert _ask(s) is None and len(s.calls) == 1


def test_400은_재시도하지_않는다(_isolate, monkeypatch):
    """계약 문제라 다시 불러도 같은 답이 온다 — 재시도는 비용만 쓴다."""
    monkeypatch.setattr(J.time, "sleep", lambda *_: None)
    s = _Session(resp=[_Resp(400), _Resp(200)])
    assert _ask(s) is None and len(s.calls) == 1


def test_일일_캡에서는_기다리지_않는다(_isolate, monkeypatch):
    """🔑 캡은 우리 예산이자 피해 상한이다. 자정까지 블로킹하면 배치가 멎는다
    (이 레포는 요청 경로 블로킹으로 스레드가 고갈된 이력이 있다)."""
    _isolate["jev_daily_token_cap"] = 1
    slept = []
    monkeypatch.setattr(J.time, "sleep", slept.append)
    s = _Session()
    assert J.ask_noul("아" * 400, {"c1": "q"}, session=s) is None
    assert slept == [] and s.calls == []


def test_깨진_Retry_After는_백오프로_떨어진다(_isolate, monkeypatch):
    """HTTP-date 형식은 파싱하지 않는다(표면 최소화) — 기본 백오프."""
    slept = []
    monkeypatch.setattr(J.time, "sleep", slept.append)
    s = _Session(resp=[_Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                       _Resp(200, {"answers": {"c1": {"noul": 0.3}}})])
    assert _ask(s) == {"c1": 0.3}
    assert slept == [1.0]
