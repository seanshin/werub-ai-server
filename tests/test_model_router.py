"""task → 모델 라우팅 — 라우팅·정책·어댑터 회귀 (기획 우선순위 4 / Phase 1-a·b·c·f).

배경(2026-08-10 실측): Ollama 직접 호출 **56파일** · 모델명 하드코딩 141/62/32/22회.
기존 추상화 셋은 목적이 다르다(`dispatch`=노드 · `aux_infer`=사이드카 · `client/rag`=검색)
— **"어느 모델"을 정하는 지점이 없었다.**

이 파일이 고정하는 불변식:
  ① 미등록 task는 **조용히 기본값으로 흐르지 않는다**(오타 → 다른 모델 = 무성 사고)
  ② `local_only` task는 외부 provider가 **거부**한다(PHI·규제 경계, fail-closed)
  ③ 어댑터는 현재 프로덕션 페이로드를 **그대로** 만든다(이관은 변경이 아니다)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest

from core import model_router as gw


ROUTING = {
    "defaults": {"policy": "local_only", "provider": "ollama"},
    "tasks": {
        "medical_law.answer": {"model": "qwen2.5:14b",
                               "options": {"temperature": 0.3, "num_predict": 1024}},
        "story.polish": {"model": "qwen2.5:7b", "policy": "any"},
        "rag.embed": {"model": "bge-m3", "ctx": 2048, "requires_reindex": True},
    },
}


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeSession:
    """페이로드를 붙잡아 두는 세션 — 어댑터가 **무엇을 보내는지** 본다."""

    def __init__(self, payload=None):
        self.calls = []
        self._payload = payload or {"response": "안녕", "eval_count": 7,
                                    "total_duration": 2_000_000, "done_reason": "stop"}

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResp(self._payload)


def _adapter(session=None):
    return gw.OllamaAdapter(base="http://x:11434", session=session or _FakeSession())


# ── ① 미등록 task ────────────────────────────────────────────────────────────
def test_unknown_task_raises_not_default():
    """오타가 조용히 다른 모델을 타면 그 사실이 응답 어디에도 안 남는다."""
    with pytest.raises(gw.UnknownTask):
        gw.resolve("medical_law.anwser", ROUTING)     # 오타


def test_unknown_task_message_lists_known():
    with pytest.raises(gw.UnknownTask) as e:
        gw.resolve("nope", ROUTING)
    assert "medical_law.answer" in str(e.value)


def test_missing_routing_file_makes_everything_unknown(tmp_path):
    """라우팅 파일이 없으면 **전부 미등록** — 조용한 기본값보다 명시 실패가 낫다."""
    r = gw.load_routing(str(tmp_path / "nope.json"))
    with pytest.raises(gw.UnknownTask):
        gw.resolve("medical_law.answer", r)


# ── 라우팅 해석 ──────────────────────────────────────────────────────────────
def test_resolve_merges_defaults():
    s = gw.resolve("medical_law.answer", ROUTING)
    assert s["model"] == "qwen2.5:14b"
    assert s["provider"] == "ollama" and s["policy"] == "local_only"


def test_task_policy_overrides_default():
    assert gw.resolve("story.polish", ROUTING)["policy"] == "any"


# ── ② 정책 fail-closed ───────────────────────────────────────────────────────
def test_local_only_blocks_remote_provider():
    """의료·PHI·규제 경로가 프론티어로 번지지 않게 하는 마지막 방어선."""
    with pytest.raises(gw.PolicyViolation):
        gw.check_policy(gw.resolve("medical_law.answer", ROUTING), provider_is_local=False)


def test_local_only_allows_local_provider():
    gw.check_policy(gw.resolve("medical_law.answer", ROUTING), provider_is_local=True)


def test_any_policy_allows_remote():
    gw.check_policy(gw.resolve("story.polish", ROUTING), provider_is_local=False)


def test_generate_enforces_policy_end_to_end():
    class Remote(gw.OllamaAdapter):
        is_local = False
    with pytest.raises(gw.PolicyViolation):
        gw.generate("medical_law.answer", "q", routing=ROUTING,
                    adapter=Remote(session=_FakeSession()))


def test_unknown_provider_is_explicit():
    with pytest.raises(ValueError):
        gw.get_adapter("litellm")           # Phase 4 전까지 미구현


# ── ③ 어댑터 페이로드 = 현행 프로덕션 경로 ──────────────────────────────────
def test_payload_matches_production_shape():
    """D2(v2.125.14)부터 어댑터 options에 `repeat_penalty 1.1` 핀이 합류한다
    (core/engine_defaults — 엔진 기본값 의존 탈피·A/B N=40 판정값)."""
    s = _FakeSession()
    _adapter(s).generate(model="m", prompt="p", system="sys",
                         options={"temperature": 0.3, "num_predict": 1024})
    j = s.calls[0]["json"]
    assert j["model"] == "m" and j["prompt"] == "p" and j["system"] == "sys"
    assert j["stream"] is False
    assert j["options"] == {"temperature": 0.3, "num_predict": 1024,
                            "repeat_penalty": 1.1}
    assert s.calls[0]["url"].endswith("/api/generate")


def test_optional_fields_absent_when_not_given():
    """안 준 것을 보내면 동작이 바뀐다 — 특히 `think`는 비-thinking 모델을 **거절**시킨다."""
    s = _FakeSession()
    _adapter(s).generate(model="m", prompt="p")
    j = s.calls[0]["json"]
    for k in ("think", "keep_alive", "format", "images", "system"):
        assert k not in j


def test_schema_is_sent_as_format():
    s = _FakeSession()
    _adapter(s).generate(model="m", prompt="p", schema={"type": "object"})
    assert s.calls[0]["json"]["format"] == {"type": "object"}


def test_think_and_keep_alive_pass_through():
    s = _FakeSession()
    _adapter(s).generate(model="m", prompt="p", think=False, keep_alive="-1")
    j = s.calls[0]["json"]
    assert j["think"] is False and j["keep_alive"] == "-1"


def test_result_is_normalized():
    r = _adapter().generate(model="m", prompt="p")
    assert r.ok is True and r.text == "안녕" and r.tokens == 7
    assert r.duration_ms == 2 and r.model == "m" and r.provider == "ollama"


def test_failure_returns_result_not_raise():
    """실패는 예외가 아니라 Result에 실린다(기존 `_gen_failed` 규율)."""
    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("engine down")
    r = gw.OllamaAdapter(session=Boom()).generate(model="m", prompt="p")
    assert r.ok is False and "engine down" in r.error and r.text == ""


def test_gateway_does_not_post_process_text():
    """한국어 후처리는 **seam 위**다 — §1-6에서 필터가 내용을 지운 사고가 있었다."""
    s = _FakeSession({"response": "English only output", "eval_count": 1})
    assert _adapter(s).generate(model="m", prompt="p").text == "English only output"


# ── 임베딩(§1-2·§6-1f) ───────────────────────────────────────────────────────
def test_embed_pins_num_ctx():
    """전역 8192를 먹으면 KV 4.5GB → 상주 14b와 겹쳐 RAG가 500이 됐다."""
    s = _FakeSession({"embeddings": [[0.1, 0.2]]})
    gw.embed("rag.embed", ["a"], routing=ROUTING, adapter=_adapter(s))
    j = s.calls[0]["json"]
    assert j["options"]["num_ctx"] == 2048 and j["model"] == "bge-m3"
    assert s.calls[0]["url"].endswith("/api/embed")


def test_embed_failure_returns_empty_not_raise():
    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("down")
    assert gw.embed("rag.embed", ["a"], routing=ROUTING,
                    adapter=gw.OllamaAdapter(session=Boom())) == []


def test_embed_task_declares_reindex_cost():
    """임베딩 교체는 **전 코퍼스 재색인**을 요구한다 — 표가 그 사실을 말해야 한다."""
    assert gw.resolve("rag.embed", ROUTING)["requires_reindex"] is True


# ── 라우팅 노출(규제 트랙 계약) ──────────────────────────────────────────────
def test_routing_table_exposes_policy_and_reindex():
    rows = {r["task"]: r for r in gw.routing_table(ROUTING)}
    assert rows["medical_law.answer"]["policy"] == "local_only"
    assert rows["rag.embed"]["requires_reindex"] is True
    assert rows["story.polish"]["policy"] == "any"


# ── 배포 중인 라우팅 파일 ────────────────────────────────────────────────────
def test_shipped_routing_is_valid_and_documented():
    r = gw.load_routing()
    assert "_doc" in r, "정책 의미를 파일 안에 적어 둘 것(읽는 사람이 여기만 본다)"
    assert r["tasks"], "빈 표면 전부 미등록이 된다"
    for task in r["tasks"]:
        s = gw.resolve(task, r)
        assert s.get("model"), task
        assert s["policy"] in (gw.LOCAL_ONLY, gw.ANY), task


def test_shipped_task_names_follow_convention():
    """`<domain>.<role>` — 규약이 없으면 141개 하드코딩이 141개 task로 흩어진다."""
    for task in gw.load_routing()["tasks"]:
        assert "." in task, f"규약 위반: {task}"


def test_medical_tasks_are_local_only():
    """🔴 의료·PHI 경로가 실수로 `any`가 되면 프론티어로 번진다."""
    r = gw.load_routing()
    for task in r["tasks"]:
        if task.split(".")[0] in ("medical_law", "medical", "eval", "rag"):
            assert gw.resolve(task, r)["policy"] == gw.LOCAL_ONLY, task


def test_migration_progress_is_tracked():
    """이관된 호출자 목록을 **명시로 고정**한다.

    이관(Phase 1-d)은 단계별 검증(스모크 64/64·배치 노이즈 밴드·회귀)을 통과할 때만
    한다. 새 파일이 라우터를 쓰기 시작하면 이 테스트가 실패하고, **그때 이 목록을
    갱신하면서 그 단계의 검증을 했는지 스스로 확인**하게 된다.

    ⚠️ 목록을 늘리기 전에 물을 것: 그 도메인의 배치를 돌렸는가? 스모크는 통과했는가?
    """
    MIGRATED = {
        "routes/medical_law.py",     # 1단계 · 비스트리밍 /ask (v2.124.96)
        # 2단계 · 평가 judge 2종 (v2.124.96). 검증=동등성 회귀 23 + 이관 후 40문항 배치.
        # ⚠️ 평가 경로는 **강등하지 않는다** — 프로덕션과 규율이 다르다.
        "core/faithfulness.py",
        "core/llm_judge.py",
        # 3단계 · `/ask/stream` (v2.124.97) — medical_law.py에 이미 포함
        # 4단계 · counseling·writing·korean (v2.124.98).
        # ⚠️ counseling은 골든셋(스모크 19체크)이 있어 회귀를 볼 눈이 있다.
        #    writing·korean은 **아직 골든셋이 없다** — 이관은 페이로드 동등성으로만
        #    보증되고, 답변 품질 회귀는 못 본다. 골든셋 확보가 남은 일이다.
        "routes/counseling.py",
        "routes/writing.py",
        "routes/korean.py",
        "core/korean_spellcheck.py",
        # 5단계 · orchestrator·novel (v2.124.99).
        # ⚠️ 두 도메인 다 **골든셋이 없다** — 이관은 페이로드 동등성으로만 보증된다.
        #    orchestrator는 스모크 일부(medlaw·dur 라우팅)가 간접 커버, novel은 없음.
        "routes/orchestrator.py",
        "routes/novel.py",
        # 6단계 · 임베딩 (v2.124.99). 🔴 **값 동결** — 표의 requires_reindex가 비용을 남긴다.
        # coding.py는 이관이 곧 §1-2 OOM 봉합이었다(이 경로만 num_ctx가 없었다).
        "client/rag.py",
        "routes/coding.py",
        # D1 슬라이스 1 (v2.125.12) · 측정·판정 경로 — 페이로드 동일성 회귀
        # tests/test_router_migration_slice1.py. 인벤토리: docs/01-plan/references/direct-ollama-calls-2026-08-19.md
        "core/retrieval_eval.py",
        "core/selfcheck.py",
        # D1 슬라이스 2 (v2.125.12) · 단일 호출 경로
        "routes/rag.py",
        "routes/notice.py",
        "routes/medical_advanced.py",
        # D1 슬라이스 3 (v2.125.13) · 병렬 5그룹 — 회귀 tests/test_router_migration_{game,vision,chat_story_triage,hr_medical,core_app}.py
        "routes/game.py",
        "routes/vision.py",
        # routes/chat.py — v2.125.15에서 삭제(미등록 레거시·import 불가·복구 경로는 git 이력)
        "routes/story.py",
        "routes/triage.py",
        "routes/hr_law.py",
        "routes/medical.py",
        "routes/medical_cli.py",
        "core/tool_executor.py",
        "core/websocket_handler.py",
        "core/chat_memory.py",
        "core/meeting_pipeline.py",
        "core/meeting_vision.py",
        # D2 (v2.125.14) · 이관 호출자가 아니라 **핀 모듈 자체** — 어댑터 페이로드
        # 빌더가 쓰는 engine_defaults가 독스트링에서 model_router를 언급한다.
        "core/engine_defaults.py",
        # 일반 AI 표면 (v2.125.34) · 신설부터 라우터 경유(직접 호출 이력 없음) —
        # 회귀 tests/test_general_ai.py(분리 계약·무성 실패·스트림).
        "routes/general_ai.py",
        # OCR 폴백 (v2.125.37) · 신설부터 라우터 경유(task medlaw.ocr) —
        # 회귀 tests/test_medlaw_ocr_fallback.py.
        "core/medlaw_ocr.py",
    }
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = set()
    for sub in ("routes", "core", "client"):
        for dirpath, _dirs, files in os.walk(os.path.join(root, sub)):
            for fn in files:
                if not fn.endswith(".py") or fn == "model_router.py":
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    if "model_router" in f.read():
                        hits.add(os.path.relpath(p, root))
    assert hits == MIGRATED, (
        f"이관 목록이 바뀌었다. 새로 늘었다면 그 단계의 검증(스모크·배치·회귀)을 "
        f"마친 뒤 MIGRATED를 갱신할 것.\n  실제={sorted(hits)}\n  기대={sorted(MIGRATED)}")
