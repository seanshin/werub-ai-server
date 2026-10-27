"""의료 생성 캐시 — **깨진 출력을 고정하지 않는다**.

🔴 2026-08-10 실측(계약 스모크가 잡음): `/api/medical/predict/adverse-drug`가
구조화 필드 없이 **3/3 결정적으로** 실패했다. 원인은 두 겹이었다.

  ① 재기동 직후 **콜드 캐시 상태의 첫 생성**이 깨진 JSON을 냈다
     (`{ "medications_analyzed": [...],  {  "drug": …` — 키 없는 중괄호).
     14b는 캐시 콜드/웜에 따라 긴 생성이 갈린다(v2.124.20에서 규명한 그 성질).
  ② `_medical_generate`가 그것을 **캐시에 저장**했다. 가드가
     `not response.startswith("Error")`뿐이라 "에러가 아니면 성공"으로 봤다.

⇒ 일과성 결함이 TTL 동안 **고정 오답**이 됐다. 같은 프롬프트에서 나이만 70→71로
   바꾸면(=캐시 키가 달라지면) 즉시 정상이었다 — 그것이 결정적 증거다.

📌 `/ask` 실패 캐시(v2.124.85)와 **같은 클래스**가 이 경로에 남아 있었다.
   한 곳에서 고친 결함은 같은 클래스 전 경로를 훑어야 한다.

이 파일이 고정하는 것:
  ① 깨진 JSON은 캐시되지 않는다(재시도가 통해야 한다)
  ② 정상 JSON은 종전대로 캐시된다(캐시 이득을 잃지 않는다)
  ③ `accept` 미지정 경로는 **동작 불변**(비-JSON 경로를 깨지 않는다)
  ④ JSON 계약 경로에 실제로 배선돼 있다
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re

import pytest


# ── ① 판정 규칙 (순수) ──────────────────────────────────────────────────────
def _acceptable(text):
    from routes.medical import _json_acceptable
    return _json_acceptable(text)


def test_broken_json_is_not_acceptable():
    """실제로 캐시를 오염시킨 그 출력 형태."""
    broken = ('{  "medications_analyzed": ["파록세틴", "셀레길린"],  '
              '{  "drug": "파록세틴",  "common_adr": [{"reaction": "구토"}]')
    assert _acceptable(broken) is False


def test_valid_json_is_acceptable():
    assert _acceptable('{"individual_risks": [], "overall_risk": "high"}') is True


def test_fenced_json_is_acceptable():
    """```json 펜스는 `force_json`이 복구한다 — 복구되면 캐시해도 된다."""
    assert _acceptable('```json\n{"overall_risk": "low"}\n```') is True


def test_empty_is_not_acceptable():
    assert _acceptable("") is False
    assert _acceptable(None) is False


def test_prose_is_not_acceptable():
    assert _acceptable("죄송합니다. 분석을 완료하지 못했습니다.") is False


# ── ②③ 캐시 거동 ───────────────────────────────────────────────────────────
class _Sess:
    """호출마다 다른 응답을 주는 세션 — 캐시 히트 여부를 관측한다."""

    def __init__(self, responses):
        self._r = list(responses)
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        body = self._r[min(self.calls - 1, len(self._r) - 1)]

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"response": body, "eval_count": 5, "total_duration": 1_000_000}
        return _R()


def _run(monkeypatch, responses, accept, prompt):
    from routes import medical as M
    sess = _Sess(responses)
    monkeypatch.setattr(M, "_ollama_session", sess)
    monkeypatch.setattr(M, "_OLLAMA_BASE", "http://t:11434")
    out = []
    for _ in range(2):
        out.append(M._medical_generate(model="m", prompt=prompt, system="s",
                                       action="t", accept=accept)[0])
    return out, sess.calls


def test_broken_output_is_retried_not_frozen(monkeypatch):
    """🔴 핵심 — 첫 응답이 깨졌으면 **두 번째 호출이 새로 생성**돼야 한다."""
    from routes.medical import _json_acceptable
    prompt = "고유프롬프트-깨짐-%d" % id(monkeypatch)
    out, calls = _run(monkeypatch, ['{"a": ,}', '{"overall_risk":"low"}'],
                      _json_acceptable, prompt)
    assert calls == 2, "깨진 응답이 캐시되면 재시도가 원리적으로 불가능하다"
    assert out[1] == '{"overall_risk":"low"}'


def test_valid_output_is_cached(monkeypatch):
    """캐시 이득은 그대로 — 정상 응답은 한 번만 생성한다."""
    from routes.medical import _json_acceptable
    prompt = "고유프롬프트-정상-%d" % id(monkeypatch)
    out, calls = _run(monkeypatch, ['{"overall_risk":"low"}', '{"other":1}'],
                      _json_acceptable, prompt)
    assert calls == 1 and out[0] == out[1]


def test_no_accept_keeps_legacy_behavior(monkeypatch):
    """accept 미지정(비-JSON 경로)은 종전대로 무엇이든 캐시한다."""
    prompt = "고유프롬프트-레거시-%d" % id(monkeypatch)
    out, calls = _run(monkeypatch, ["그냥 산문 응답", "다른 응답"], None, prompt)
    assert calls == 1 and out[0] == out[1]


def test_error_response_still_not_cached(monkeypatch):
    """종전 가드도 유지 — Error 응답은 여전히 캐시하지 않는다."""
    prompt = "고유프롬프트-에러-%d" % id(monkeypatch)
    out, calls = _run(monkeypatch, ["Error: boom", '{"ok":1}'], None, prompt)
    assert calls == 2


# ── ④ 배선 — JSON 계약 경로에 실제로 걸려 있는가 ────────────────────────────
def test_json_contract_routes_are_wired():
    """`force_json`으로 파싱하는 경로는 `accept`를 넘겨야 한다.

    배선이 빠지면 그 경로만 조용히 옛 거동으로 남는다(이 결함이 정확히 그랬다).

    산문을 만드는 호출은 바로 위에 `# not-json-contract` 표시로 제외한다 —
    근접 탐지는 뒤따르는 남의 `force_json`을 물 수 있다(첫 구현이 그랬다).
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    missing = []
    for fn in ("routes/medical_advanced.py", "routes/medical.py", "routes/orchestrator.py"):
        src = open(os.path.join(root, fn), encoding="utf-8").read()
        for m in re.finditer(r"_medical_generate\((.{0,600}?)\)\s*\n(.{0,300}?)force_json",
                             src, re.S):
            blk = m.group(1)
            if "accept=" not in blk and "not-json-contract" not in src[max(0, m.start() - 200):m.start()]:
                head = blk.replace("\n", " ")[:70]
                missing.append(f"{fn}: {head}")
    assert not missing, "JSON 계약인데 accept 미배선:\n  " + "\n  ".join(missing)


def test_adr_route_specifically_wired():
    """스모크가 잡은 바로 그 경로."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "routes/medical_advanced.py"), encoding="utf-8").read()
    i = src.index('action="predict_adr"')
    assert "accept=_json_acceptable" in src[i:i + 200]
