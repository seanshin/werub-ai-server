"""글쓰기 캐시 — **필터를 통과 못 할 응답을 담지 않는다** (2026-08-12 실결함).

🔴 증상: `/api/writing/draft`가 **0.0초에 0자**를 반환했다. 프롬프트를 **한 글자만** 바꾸자
   3.8초·493자·한글 74%가 나왔다 — 캐시 히트가 확정적 증거다
   (0.00N초 응답 = 캐시 히트, [[feedback_dont_cache_failures]]).

근본 원인: `_generate`의 캐시 가드가 `if response:`뿐이었다. 그런데 **호출자는 그 값을
그대로 쓰지 않는다** — `force_korean`을 한 번 더 태운다(5곳). 비한국어 응답은 필터가
**통째로 비우므로**, "원문이 비어 있지 않다"는 이유로 캐시하면 그 뒤로 **영원히 빈 초안**이
나간다. 실측: 영문 문장 → `force_korean` 후 **0자**.

📌 **오늘 세 번째 같은 클래스**다:
  1. `/ask` 실패 캐시(v2.124.85) — 타임아웃 1회가 TTL 1시간 고정 오답
  2. `_medical_generate` 깨진 JSON 고정 — "에러가 아니면 성공"으로 봤다
  3. 이 건 — "비어 있지 않으면 성공"으로 봤다

🔑 **캐시 가드는 소비자가 필요로 하는 조건으로 걸어야 한다.** 저장하는 쪽이 아는 조건이
   아니라, 그 값을 쓰는 쪽이 통과시켜야 하는 조건이다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.korean import force_korean


# ── 전제 확인(탐지기 검증) ──────────────────────────────────────────────────
def test_filter_empties_english():
    """이 전제가 무너지면 아래 가드의 의미도 달라진다 — 먼저 못 박는다."""
    assert force_korean("Dear team, the annual checkup starts next week.").strip() == ""


def test_filter_keeps_korean():
    assert force_korean("안녕하세요. 건강검진 안내드립니다.").strip()


# ── 캐시 가드 ───────────────────────────────────────────────────────────────
class _Res:
    def __init__(self, text):
        self.text, self.ok, self.tokens, self.duration_ms = text, True, 300, 10


def _run(monkeypatch, text, prompt):
    """`_generate`를 두 번 호출하고 **실제 생성 횟수**를 돌려준다."""
    from routes import writing as W
    calls = {"n": 0}

    def fake_generate(task, p, **kw):
        calls["n"] += 1
        return _Res(text)

    monkeypatch.setattr("core.model_router.generate", fake_generate)
    monkeypatch.setattr(W, "_get_session", lambda: (None, "http://t"))
    out = [W._generate("m", prompt, "sys") for _ in range(2)]
    return out, calls["n"]


def test_non_korean_response_is_not_cached(monkeypatch):
    """🔴 핵심 — 필터가 비울 응답은 담지 않아야 재시도가 가능하다."""
    out, n = _run(monkeypatch, "Dear team, the annual checkup starts next week.",
                  "고유프롬프트-영문-%d" % id(monkeypatch))
    assert n == 2, "비한국어 응답이 캐시되면 그 뒤로 영원히 빈 초안이 나간다"


def test_korean_response_is_cached(monkeypatch):
    """캐시 이득은 그대로 — 정상 응답은 한 번만 생성한다."""
    out, n = _run(monkeypatch, "안녕하세요. 사내 건강검진 안내드립니다.",
                  "고유프롬프트-한글-%d" % id(monkeypatch))
    assert n == 1 and out[0][0] == out[1][0]


def test_empty_response_is_not_cached(monkeypatch):
    """종전 가드도 유지 — 빈 응답은 여전히 캐시하지 않는다."""
    _out, n = _run(monkeypatch, "", "고유프롬프트-빈-%d" % id(monkeypatch))
    assert n == 2


def test_bracket_remnant_is_not_cached(monkeypatch):
    """🔴 두 번째로 뚫린 자리 — 필터 후 `'[]\n[]'`(5자)가 남아 **비어 있지 않다**고 통과했다.
    비어 있지 않은 것과 **쓸 수 있는 한국어**인 것은 다르다."""
    out, n = _run(monkeypatch, "[]\n[]  Dear team, the checkup starts next week.",
                  "고유프롬프트-괄호-%d" % id(monkeypatch))
    assert n == 2, "한글이 하나도 없는 잔해가 캐시되면 그 뒤로 그것만 나간다"


def test_cacheable_is_pure():
    """판정은 순수 함수라 단독으로 검증된다 — 가드를 또 약하게 걸지 않으려면 필요하다."""
    from routes.writing import _cacheable
    assert _cacheable("안녕하세요. 사내 건강검진 안내드립니다.") is True
    assert _cacheable("Dear team, the annual checkup starts next week.") is False
    assert _cacheable("[]\n[]") is False
    assert _cacheable("") is False
    assert _cacheable(None) is False


def test_guard_wired_in_source():
    """배선이 빠지면 이 결함만 조용히 돌아온다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "routes/writing.py"), encoding="utf-8") as f:
        s = f.read()
    assert "if _cacheable(response):" in s
    assert "if response:\n            _writing_cache.set" not in s
    assert "if response and force_korean(response).strip():" not in s, "약한 가드 잔재"


# ── 반환 경로 — 캐시를 막은 것과 **같은 조건의 두 번째 지점** ────────────────
# 2026-08-14: 캐시는 `'[]\n[]'`를 안 담게 됐는데, 호출자는 여전히 그것을 200과 함께 받았다.
# 6회 중 1회 재현 · retry_count=0 · risk_band=low (품질 밴드는 한국어인 글을 전제한 지표라
# 한국어가 아닐 때 오히려 침묵한다).

def test_usable_draft_is_pure():
    from routes.writing import _usable_draft
    assert _usable_draft(
        "안녕하세요. 사내 건강검진 안내드립니다. 올해 검진은 3월부터 진행되며 "
        "부서별 일정은 추후 공지드리겠습니다.") is True
    assert _usable_draft("[]\n[]") is False, "비어 있지 않을 뿐 한글이 하나도 없다"
    assert _usable_draft("회사") is False, "🔴 세 번째로 뚫린 자리 — 한글일 뿐 초안이 아니다"
    assert _usable_draft("Dear team, the checkup starts next week.") is False
    assert _usable_draft("   \n  ") is False
    assert _usable_draft("") is False
    assert _usable_draft(None) is False


def test_usable_draft_floor_is_not_tuned_to_the_gate():
    """하한을 게이트(min_len 120)에 맞추면 **게이트만** 통과시킨다.
    실측 붕괴 2~5자와 정상 초안 464~528자 사이에서, 어느 쪽에도 붙지 않은 값이어야 한다."""
    from routes.writing import _MIN_DRAFT_CHARS
    assert 5 < _MIN_DRAFT_CHARS < 120


def test_draft_return_path_guarded_in_source():
    """세 가지가 함께 있어야 한다 — 하나만 빠져도 무성 실패가 돌아온다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "routes/writing.py"), encoding="utf-8") as f:
        s = f.read()
    # ① 한국어가 아니면 재생성이 걸린다 (risk_band=low 구간을 덮는다)
    assert 'risk_band"] == "high" or not _usable_draft(response)' in s
    # ② 빈 재생성이 멀쩡한 초안을 덮어쓰지 않는다
    assert 'startswith("Error:") and _usable_draft(response2)' in s
    # ③ 끝까지 실패하면 200이 아니라 502로 알린다
    assert '"draft generation produced no usable Korean text"' in s
