"""`quality.validated` 표식 — 마지막 시도 성공을 실패로 표시하던 모호성 처치 (v2.125.73).

종전 `generate_with_validation`은 **마지막 시도 통과**와 **전 시도 실패**에 똑같이 `retries=max_retries`를 돌려줘,
호출자 7곳의 `validated = retries < 2`가 마지막 시도 성공을 `false`로 냈다(진짜 실패와 구분 불가). 최종 실패는 로그도 없었다.
"""
import logging

import pytest

from core.quality import generate_with_validation, generate_with_validation_ex


def _seq(*responses):
    it = iter(responses)
    return lambda p, s, o: (next(it), 1, 1)


ok_if_json = [lambda r: (r.startswith("{"), "JSON 아님")]


@pytest.mark.parametrize("responses,retries,passed", [
    (("{}",), 0, True),                       # 첫 시도 통과
    (("x", "{}"), 1, True),                   # 두 번째 통과
    (("x", "y", "{}"), 2, True),              # 🔑 마지막 시도 통과 — 종전엔 실패와 같은 값
    (("x", "y", "z"), 2, False),              # 전 시도 실패
])
def test_ex_distinguishes_last_attempt_success(responses, retries, passed):
    r = generate_with_validation_ex(_seq(*responses), "p", "s", validators=ok_if_json, max_retries=2)
    assert (r[3], r[4]) == (retries, passed)
    assert r[0] == responses[-1]


def test_no_validators_is_passed():
    assert generate_with_validation_ex(_seq("아무거나"), "p", "s")[3:] == (0, True)


def test_final_failure_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="ollama-admin"):
        generate_with_validation_ex(_seq("x", "y", "z"), "p", "s", validators=ok_if_json, max_retries=2)
    assert any("after 3 attempts" in r.getMessage() for r in caplog.records)   # 종전: 최종 실패 로그 없음


def test_wrapper_keeps_old_4tuple_contract():
    a = generate_with_validation(_seq("x", "y", "{}"), "p", "s", validators=ok_if_json, max_retries=2)
    b = generate_with_validation(_seq("x", "y", "z"), "p", "s", validators=ok_if_json, max_retries=2)
    assert len(a) == len(b) == 4 and a[3] == b[3] == 2   # 호환 래퍼는 종전 그대로(그래서 retries로 판단하면 안 된다)


def test_no_caller_uses_retries_as_validity():
    """`validated = retries < N` 패턴이 다시 생기면 잡는다."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    hits = [f"{p.relative_to(root)}:{i+1}" for p in (root / "routes").glob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines())
            if re.search(r'"validated":\s*retries\s*<', line)]
    assert hits == []


# ── 라우트 단위: 마지막 시도 성공이면 validated=true ─────────────────────────
@pytest.fixture
def client():
    import app as A                                            # noqa: PLC0415
    c = A.app.test_client()
    return lambda path, **kw: c.post(path, environ_base={"REMOTE_ADDR": "127.0.0.1"}, **kw)


def test_explain_last_attempt_success_is_validated(client, monkeypatch):
    from routes import medical as M
    q = ["설명 텍스트", "또 텍스트", '{"explanation": "고혈압은 혈압이 높은 상태입니다.", "key_points": ["염분"]}']
    monkeypatch.setattr(M, "_medical_generate", lambda *a, **k: (q.pop(0), 5, 10))
    monkeypatch.setattr(M, "_require_tenant", lambda: ({"id": "t-test", "name": "t"}, None))
    monkeypatch.setattr(M, "_save_consultation", lambda *a, **k: "cid")
    d = client("/api/medical/explain", json={"diagnosis": "고혈압"}).get_json()
    assert d["quality"]["retries"] == 2 and d["quality"]["validated"] is True   # 구: validated false


def test_code_generate_without_validators_does_not_crash(client, monkeypatch):
    # validator 없는 분기(비-Python)에서 `passed` 미정의 NameError가 나지 않는다
    from routes import coding as C
    monkeypatch.setattr(C, "_code_generate", lambda *a, **k: ("```js\nconsole.log(1)\n```", 5, 10))
    r = client("/api/code/generate", json={"prompt": "print 1", "language": "javascript"})
    assert r.status_code == 200 and r.get_json()["quality"]["validated"] is True
