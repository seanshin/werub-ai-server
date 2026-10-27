"""임베딩 이관 — **값 동결**과 `num_ctx` 불변식 (기획 Phase 1-d 6단계).

임베딩은 생성과 성질이 다르다. 모델을 바꾸면 **저장된 벡터가 무의미해진다**:
  · medical_law 13,002 + medical_pub 15,285 + werub_docs + edu_docs 전량 재색인
  · 차원이 달라지면 저장 임베딩 재사용 자체가 불가능(v2.124.8은 거리공간만 바꿔 재구축)

⇒ seam은 '바꿀 수 있게' 만들되 **'바꾸기 쉬워 보이게' 만들면 안 된다**.
   표의 `requires_reindex`가 그 사실을 코드에 남긴다.

🔴 그리고 `num_ctx`는 **선택이 아니다**. 안 실으면 전역 8192를 먹어 임베딩 모델 KV가
   4.5GB로 불고, 상주 14b(10.5GB)와 겹쳐 16GB에서 터진다(v2.86.12 실측 — RAG가 500).

이 파일이 고정하는 것:
  ① 임베딩 task는 전부 `requires_reindex`를 선언한다
  ② 어댑터는 **항상** `num_ctx`를 싣는다(빠뜨릴 수 없는 구조)
  ③ ctx 값이 위험 구간(전역 8192)으로 올라가지 않는다
  ④ 실패는 예외가 아니라 빈 리스트(호출자가 폴백을 돌린다)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import model_router as mr


class _Sess:
    def __init__(self, payload=None, boom=False):
        self.calls = []
        self._p = payload if payload is not None else {"embeddings": [[0.1, 0.2]]}
        self._boom = boom

    def post(self, url, json=None, timeout=None, stream=False):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self._boom:
            raise RuntimeError("engine down")

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return self._p
        return _R()


# ── ① 재색인 비용 선언 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("task", ["rag.embed", "coding.embed"])
def test_embed_tasks_declare_reindex_cost(task):
    """표가 '이거 바꾸면 전 코퍼스를 다시 만들어야 한다'를 말해야 한다."""
    assert mr.resolve(task)["requires_reindex"] is True


def test_embed_models_are_distinct_and_frozen():
    """🔴 rag와 coding은 **다른 모델**이고 각자 저장 벡터와 짝이다.
    같게 만들면 한쪽 색인이 통째로 무의미해진다."""
    assert mr.resolve("rag.embed")["model"] == "bge-m3"
    assert mr.resolve("coding.embed")["model"] == "qwen3-embedding:8b"
    assert mr.resolve("rag.embed")["model"] != mr.resolve("coding.embed")["model"]


def test_coding_embed_declares_fallback():
    """1순위 모델 미적재 시에만 쓰는 폴백 — 표에 있어야 추적된다."""
    assert mr.resolve("coding.embed")["fallback"] == "nomic-embed-text"


# ── ② num_ctx는 빠뜨릴 수 없다 ──────────────────────────────────────────────
def test_adapter_always_sends_num_ctx():
    """🔴 안 실으면 전역 8192를 먹어 KV 4.5GB → 상주 14b와 겹쳐 RAG가 500이 됐다."""
    s = _Sess()
    mr.OllamaAdapter(base="http://t", session=s).embed(model="m", texts="x")
    assert s.calls[0]["json"]["options"]["num_ctx"] > 0


def test_embed_entrypoint_uses_task_ctx():
    s = _Sess()
    mr.embed("rag.embed", ["a"], adapter=mr.OllamaAdapter(base="http://t", session=s))
    assert s.calls[0]["json"]["options"]["num_ctx"] == 2048
    assert s.calls[0]["json"]["model"] == "bge-m3"


def test_caller_ctx_override_still_bounded():
    """호출자가 ctx를 줘도 실린다 — 다만 '안 실리는' 경우는 없다."""
    s = _Sess()
    mr.embed("rag.embed", ["a"], ctx=1024,
             adapter=mr.OllamaAdapter(base="http://t", session=s))
    assert s.calls[0]["json"]["options"]["num_ctx"] == 1024


# ── ③ 위험 구간 감시 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("task", ["rag.embed", "coding.embed"])
def test_shipped_ctx_stays_below_global(task):
    """전역 컨텍스트(8192)에 근접하면 봉합의 의미가 사라진다."""
    ctx = mr.resolve(task).get("ctx")
    assert ctx and ctx <= 4096, "임베딩 ctx가 위험 구간으로 올라갔다"


# ── ④ 실패 처리 ─────────────────────────────────────────────────────────────
def test_embed_failure_returns_empty_not_raise():
    """호출자가 폴백 모델을 돌릴 수 있어야 한다 — 예외로 끊지 않는다."""
    s = _Sess(boom=True)
    out = mr.embed("rag.embed", ["a"],
                   adapter=mr.OllamaAdapter(base="http://t", session=s))
    assert out == []


def test_embed_http_error_returns_empty():
    class _ErrSess:
        def post(self, *a, **k):
            class _R:
                status_code = 500

                @staticmethod
                def json():
                    return {}
            return _R()
    out = mr.embed("rag.embed", ["a"],
                   adapter=mr.OllamaAdapter(base="http://t", session=_ErrSess()))
    assert out == []


# ── 라우트 배선 ─────────────────────────────────────────────────────────────
def test_rag_embed_text_goes_through_router(monkeypatch):
    """`client/rag.embed_text`가 라우터를 타는지 — 캐시를 피하려 고유 문장을 쓴다."""
    from client import rag
    seen = {}

    def fake_embed(task, texts, **kw):
        seen["task"] = task
        seen["ctx"] = kw.get("ctx")
        return [[0.5, 0.6]]

    monkeypatch.setattr(mr, "embed", fake_embed)
    out = rag.embed_text("라우터 경유 확인 %d" % id(seen))
    assert out == [0.5, 0.6]
    assert seen["task"] == "rag.embed" and seen["ctx"]


def test_coding_embed_uses_router_and_ctx(monkeypatch):
    """🔴 이 경로만 num_ctx가 없어 OOM 결함이 남아 있었다 — 이관이 곧 봉합이다."""
    from routes import coding
    s = _Sess()
    monkeypatch.setattr(mr, "OllamaAdapter",
                        lambda *a, **k: _Adapter(s))
    vec = coding._get_code_embedding("고유한 코드 조각 %d" % id(s))
    assert vec == [0.1, 0.2]
    assert s.calls[0]["json"]["options"]["num_ctx"] == 2048
    assert s.calls[0]["json"]["model"] == "qwen3-embedding:8b"


def test_coding_embed_falls_back_on_failure(monkeypatch):
    """1순위 모델이 죽으면 폴백을 시도한다 — 종전 동작 보존."""
    from routes import coding
    tried = []

    class _FailFirst(mr.OllamaAdapter):
        def __init__(self):
            super().__init__(base="http://t")

        def embed(self, *, model, texts, ctx=2048, timeout=120):
            tried.append(model)
            return [] if model == "qwen3-embedding:8b" else [[0.9]]

    monkeypatch.setattr(mr, "OllamaAdapter", lambda *a, **k: _FailFirst())
    vec = coding._get_code_embedding("폴백 확인 %d" % id(tried))
    assert vec == [0.9] and tried == ["qwen3-embedding:8b", "nomic-embed-text"]


class _Adapter(mr.OllamaAdapter):
    def __init__(self, sess):
        super().__init__(base="http://t", session=sess)
