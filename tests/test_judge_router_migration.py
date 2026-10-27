"""평가 judge 경로 이관 — 동등성 + **정본 단일화** (기획 Phase 1-d 2단계).

의료법(1단계)과 **규율이 다르다**:
  · 프로덕션 경로 = 라우터가 죽으면 구 경로로 **강등**(서비스가 멈추면 안 된다)
  · 평가 경로   = **강등하지 않는다**. 측정 도구가 조용히 다른 구성으로 돌아가면
                  그 배치의 수치를 믿을 수 없다(v2.124.85~89에서 다섯 번 겪은 클래스)

이 파일이 고정하는 것:
  ① 페이로드 동등성(모델·system·format·options·프롬프트)
  ② judge 모델 정본은 `core/judge_config.py` **하나** — 라우팅 표가 값을 또 갖지 않는다
  ③ HTTP 오류가 **빈 응답으로 흐르지 않는다**(종전 `raise_for_status()`와 같은 층)
  ④ verifier는 judge와 **다른 체크포인트**여야 한다(교차검증 이종성)
  ⑤ eval task도 `local_only` — 답변·근거에 PHI가 실릴 수 있다
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch

import pytest

from core import faithfulness as F
from core import llm_judge as Q
from core import model_router as mr


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.text = json.dumps(payload)

    def json(self):
        return self._p


class _Capture:
    def __init__(self, response="{}", status=200):
        self.calls = []
        self._r, self._s = response, status

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _Resp({"response": self._r, "eval_count": 3, "total_duration": 1_000_000},
                     status=self._s)


def _adapter(cap):
    return mr.OllamaAdapter(base="http://t:11434", session=cap)


# ── ① 페이로드 동등성 ───────────────────────────────────────────────────────
def test_faith_judge_payload_matches_legacy():
    """이관 전 페이로드: model/system/prompt/stream/format/options."""
    cap = _Capture(response='{"claims":["주장"]}')
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        out = F._default_ask_json("SYS", "PROMPT", F._CLAIMS_SCHEMA)
    p = cap.calls[-1]["json"]
    assert p["system"] == "SYS" and p["prompt"] == "PROMPT"
    assert p["stream"] is False and p["format"] == F._CLAIMS_SCHEMA
    assert p["options"] == {"temperature": 0, "num_predict": 1024, "repeat_penalty": 1.1}  # D2 핀(engine_defaults)
    assert out == {"claims": ["주장"]}


def test_quality_judge_payload_matches_legacy():
    cap = _Capture(response='{"accuracy":{"score":4,"reason":"r"}}')
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        out = Q._default_ask_json("SYS", "PROMPT", {"type": "object"})
    p = cap.calls[-1]["json"]
    assert p["options"] == {"temperature": 0, "num_predict": 1024, "repeat_penalty": 1.1}  # D2 핀(engine_defaults)
    assert p["stream"] is False and p["system"] == "SYS"
    assert out["accuracy"]["score"] == 4


def test_verifier_payload_matches_legacy():
    cap = _Capture(response='{"supported":true}')
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        assert F._qwen_entail_check("근거", "주장") is True
    p = cap.calls[-1]["json"]
    assert p["options"] == {"temperature": 0, "num_predict": 32, "repeat_penalty": 1.1}  # D2 핀(engine_defaults)
    assert p["format"] == F._ENTAIL_SCHEMA and p["system"] == F._ENTAIL_SYS
    assert "[근거 문서]" in p["prompt"] and "[주장]" in p["prompt"]


def test_minicheck_payload_matches_legacy():
    cap = _Capture(response="Yes")
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        assert F._default_minicheck("근거", "주장") is True
    p = cap.calls[-1]["json"]
    assert p["options"] == {"temperature": 0, "num_predict": 4, "repeat_penalty": 1.1}  # D2 핀(engine_defaults)
    assert "format" not in p and "system" not in p     # 종전에도 둘 다 없었다
    assert p["prompt"].startswith("Document: ")


def test_minicheck_no_and_garbage():
    cap = _Capture(response="No")
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        assert F._default_minicheck("근거", "주장") is False
    cap2 = _Capture(response="아마도?")
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap2)), \
         pytest.raises(ValueError):
        F._default_minicheck("근거", "주장")


def test_caller_model_override_still_wins():
    cap = _Capture(response="{}")
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        F._default_ask_json("s", "p", {}, model="explicit:model")
    assert cap.calls[-1]["json"]["model"] == "explicit:model"


# ── ② judge 모델 정본은 한 곳 ───────────────────────────────────────────────
def test_routing_table_reads_judge_from_judge_config():
    """🔑 표에 값을 또 적으면 config로 judge를 바꿔도 `/api/models/routing`이
    옛 값을 광고한다 — 규제 트랙이 그 엔드포인트로 모델 교체를 감지하기로 한 계약이다."""
    with patch("core.judge_config.judge_model", side_effect=lambda role: f"판정용:{role}"):
        spec = mr.resolve("eval.faithfulness_judge")
    assert spec["model"] == "판정용:faithfulness"
    assert spec["model_source"] == "judge_config:faithfulness"


def test_routing_table_publishes_resolved_model():
    with patch("core.judge_config.judge_model", return_value="바뀐judge"):
        rows = {r["task"]: r for r in mr.routing_table()}
    assert rows["eval.quality_judge"]["model"] == "바뀐judge"
    assert rows["eval.quality_judge"]["model_source"] == "judge_config:quality"


def test_judge_config_change_reaches_the_call():
    """config를 바꾸면 **실제 호출 모델**이 따라와야 한다(표가 가로채면 안 된다)."""
    cap = _Capture(response="{}")
    with patch("core.judge_config.judge_model", return_value="새judge:1b"), \
         patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)):
        F._default_ask_json("s", "p", {})
    assert cap.calls[-1]["json"]["model"] == "새judge:1b"


def test_unknown_resolver_is_loud():
    """오타 난 resolver가 조용히 기본 모델을 타면 어느 모델로 쟀는지 알 수 없다."""
    routing = {"tasks": {"x.y": {"model_from": "없는소스:role"}}, "defaults": {}}
    with pytest.raises(mr.UnknownResolver):
        mr.resolve("x.y", routing)


def test_literal_model_still_supported():
    """model_from이 없는 task는 종전대로 표의 값을 쓴다."""
    spec = mr.resolve("eval.verifier")
    assert spec["model"] == "qwen2.5:7b" and spec.get("model_source") is None


# ── ③ HTTP 오류를 삼키지 않는다 ─────────────────────────────────────────────
def test_http_error_raises_not_empty_response():
    """🔴 종전 `raise_for_status()`가 하던 일. 이 층이 없으면 500이
    `response` 없는 JSON → **빈 문자열** → 주장 0개로 조용히 흐른다."""
    cap = _Capture(response="{}", status=500)
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)), \
         pytest.raises(RuntimeError) as e:
        F._default_ask_json("s", "p", {})
    assert "500" in str(e.value)


def test_http_error_in_quality_judge_raises():
    cap = _Capture(response="{}", status=503)
    with patch.object(mr, "get_adapter", lambda *a, **k: _adapter(cap)), \
         pytest.raises(RuntimeError):
        Q._default_ask_json("s", "p", {})


def test_adapter_marks_http_error_not_ok():
    cap = _Capture(response="{}", status=404)
    r = _adapter(cap).generate(model="m", prompt="p")
    assert r.ok is False and r.status == 404 and "404" in r.error


def test_adapter_ok_path_carries_status():
    cap = _Capture(response="답")
    r = _adapter(cap).generate(model="m", prompt="p")
    assert r.ok is True and r.status == 200 and r.text == "답"


def test_eval_path_does_not_degrade_on_router_failure():
    """평가는 **강등하지 않는다** — 조용히 다른 구성으로 재는 것이 최악이다."""
    with patch.object(mr, "generate", side_effect=RuntimeError("router broken")), \
         pytest.raises(RuntimeError):
        F._default_ask_json("s", "p", {})


# ── ④ 교차검증 이종성 ───────────────────────────────────────────────────────
def test_verifier_differs_from_judge():
    """verifier가 judge와 같아지면 교차검증이 자기 자신을 확인하는 셈이 된다."""
    judge = mr.resolve("eval.faithfulness_judge")["model"]
    assert mr.resolve("eval.verifier")["model"] != judge


# ── ⑤ 정책 — 평가에도 PHI가 실린다 ──────────────────────────────────────────
@pytest.mark.parametrize("task", ["eval.faithfulness_judge", "eval.quality_judge",
                                  "eval.verifier", "eval.verifier_minicheck"])
def test_eval_tasks_are_local_only(task):
    """채점 입력은 **답변과 근거 전문**이다 — 환자 맥락이 실릴 수 있어 외부로 못 보낸다."""
    assert mr.resolve(task)["policy"] == mr.LOCAL_ONLY


def test_external_provider_rejected_for_judge():
    class Remote(mr.OllamaAdapter):
        is_local = False
    with pytest.raises(mr.PolicyViolation):
        mr.generate("eval.quality_judge", "p", adapter=Remote())


# ── 집계는 이관과 무관하게 불변 ─────────────────────────────────────────────
def test_scoring_unchanged_end_to_end():
    """이관은 변경이 아니다 — 같은 judge 응답이면 같은 점수.

    ⚠️ 비지지 주장은 **개별 재판정**을 한 번 더 받는다(v2.124.75 — 일괄 번호 판정이
    정답 주장에 남의 판정을 붙이던 오염 봉합). 스텁이 그 3번째 호출을 흉내내지 않으면
    판정이 뒤집혀 이 테스트가 이관 결함으로 오인된다.
    """
    calls = {"n": 0}

    def ask(system, prompt, schema, model=None, timeout=180):
        calls["n"] += 1
        if calls["n"] == 1:                       # 주장 분해
            return {"claims": ["주장1", "주장2"]}
        if calls["n"] == 2:                       # 일괄 판정
            return {"verdicts": [{"i": 1, "verdict": "supported"},
                                 {"i": 2, "verdict": "unsupported"}]}
        return {"verdicts": [{"i": 1, "verdict": "unsupported"}]}   # 개별 재판정

    res = F.score("답변", "근거", ask_json=ask)
    assert res["faithfulness"] == 0.5 and res["n_claims"] == 2
    assert calls["n"] == 3, "비지지 개별 재판정이 돌아야 한다"
