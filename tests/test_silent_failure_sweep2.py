"""무성 실패 일괄 2차(v2.125.16) — HTTP 오류가 **기존 실패 분기로** 간다.

1차(v2.125.15)는 릴리즈 노트가 열거한 8지점만 닫았다. 🔴**인벤토리를 남의 목록으로 만든 것이
잘못**이었고(`계수도 탐지기다`와 같은 클래스), 직접 grep하니 같은 패턴이 **7곳 더** 있었다:
writing 2 · orchestrator 2 · triage 2 · chat_memory 1.

여기서 고정하는 계약:
  ① writing `_generate`: HTTP 오류 → `Error:` 반환 + **브레이커 failure**
     (종전엔 ""를 주면서 success를 기록 — 실패를 성공으로 세면 브레이커가 안 열린다)
  ② orchestrator general_chat: HTTP 오류 → **안내문**(종전엔 빈 응답이 사용자에게 나가고 세션에 저장)
  ③ chat_memory: HTTP 오류 → 던짐(종전엔 빈 요약 저장 + **원본 삭제** = 이력 소실)
  ④ triage: 결과는 종전과 같되(폴백) 실패가 로그에 남는다 — 호출자 계약은 유지
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import model_router


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class _Sess:
    """HTTP 오류를 돌려주는 세션 — 전송 예외가 아니라 **4xx/5xx 응답**이 핵심이다."""
    def __init__(self, status=500, body=None):
        self.status, self.body = status, body or {"error": "boom"}
        self.sent = []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json))
        return _Resp(self.status, self.body)


@pytest.fixture
def routing(monkeypatch):
    table = {"tasks": {t: {"model": "m"} for t in
                       ("writing.default", "orchestrator.general_chat",
                        "orchestrator.workflow_step", "chat.memory_summary",
                        "triage.extract_symptoms", "triage.guidance")},
             "defaults": {"policy": "local_only", "provider": "ollama"}}
    monkeypatch.setattr(model_router, "load_routing", lambda *a, **k: table)
    return table


# ── ① writing: 브레이커가 실패를 실패로 센다 ────────────────────────────────
def test_writing_http_error_records_breaker_failure(routing, monkeypatch):
    from routes import writing as W

    class _Breaker:
        def __init__(self):
            self.ok = self.fail = 0

        def can_execute(self):
            return True

        def record_success(self):
            self.ok += 1

        def record_failure(self):
            self.fail += 1

    br = _Breaker()
    from core import llm_resilience
    monkeypatch.setattr(llm_resilience, "ollama_breaker", br)
    monkeypatch.setattr(W, "_get_session", lambda: (_Sess(), "http://t:11434"))
    monkeypatch.setattr(model_router, "get_adapter",
                        lambda *a, **k: model_router.OllamaAdapter(session=_Sess()))
    text, tokens, _ = W._generate("m", "고유-무성실패-2차-%d" % id(br), "s")
    assert text.startswith("Error:") and tokens == 0
    assert (br.ok, br.fail) == (0, 1), "HTTP 오류를 success로 세면 브레이커가 열리지 않는다"


# ── ② orchestrator: general_chat·workflow_step 두 지점 ─────────────────────
# 두 지점 다 라우트 함수(`orch_chat`) 깊숙한 분기라 엔드투엔드 진입에 테넌트·세션·intent 설정이
# 필요하다. **skip은 아무것도 지키지 않으므로** 소스 계약으로 고정한다(chat_memory와 같은 방식) —
# 되돌리면 여기서 빨개진다.
def test_orchestrator_http_error_joins_failure_branches():
    import inspect
    from routes import orchestrator as O
    src = inspect.getsource(O.orch_chat)
    assert '_res.text if _res.ok else ""' not in src, \
        "general_chat: HTTP 오류가 빈 응답으로 나가면 사용자가 빈 답을 받고 세션에도 저장된다"
    assert "죄송합니다. 잠시 후 다시 시도해주세요." in src      # 실패 시 가야 할 분기가 살아 있다
    whole = inspect.getsource(O)
    assert '"response": _res.text if _res.ok else ""' not in whole, \
        "workflow_step: HTTP 오류가 빈 응답으로 다음 단계에 흘러가면 안 된다"


# ── ②-b triage: 결과는 그대로, 실패는 로그로 ────────────────────────────────
def test_triage_http_error_falls_back_but_is_not_silent():
    import inspect
    from routes import triage as T
    src = inspect.getsource(T)
    assert 'res.text if res.ok else ""' not in src
    # 호출자 계약(항상 리스트/문자열 반환)은 유지 — 폴백 자체는 설계다
    assert '["기타증상"]' in src


# ── ③ chat_memory: 빈 요약 + 원본 삭제 = 이력 소실 ─────────────────────────
def test_chat_memory_http_error_raises_before_deleting_originals(routing, monkeypatch):
    from core import chat_memory as CM
    monkeypatch.setattr(model_router, "get_adapter",
                        lambda *a, **k: model_router.OllamaAdapter(session=_Sess()))
    import inspect
    body = inspect.getsource(CM.compress_chat_history)
    assert 'res.text if res.ok else ""' not in body, \
        "HTTP 오류를 빈 요약으로 흘리면 원본 삭제와 겹쳐 대화 이력이 사라진다"
    assert "if not res.ok:" in body and "raise RuntimeError" in body


# ── ④ 소스 계약: 이 클래스가 다시 들어오는 것을 막는다 ──────────────────────
def test_no_silent_ok_default_returns_in_swept_files():
    """`res.text if res.ok else ""` 형태가 되돌아오면 여기서 잡는다.

    허용 예외 2곳: `routes/rag.py`(실패 문구를 **본문에 실어 드러내는** v2.125.12 처치) —
    빈 문자열이 아니라 사유 문자열이므로 무성 실패가 아니다.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for sub in ("routes", "core"):
        for dirpath, _d, files in os.walk(os.path.join(root, sub)):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if 'ok else ""' in line.replace("_res", "res"):
                            offenders.append("%s:%d" % (os.path.relpath(p, root), i))
    assert offenders == [], "무성 실패 패턴 재유입: %s" % offenders
