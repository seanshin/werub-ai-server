"""v2.125.46 — 무인증 공개 `GET /api/tags` (PACS 가용성 감시·HIS 모델 레지스트리).

종전: 라우트가 없어 OPTIONS catch-all에 걸려 **405**(라이브 30분 주기·7일 335건 →
PACS가 상시 down 판정). 외부(엣지 경유)에선 /api/health까지 전부 401이라 무인증으로
200을 줄 경로가 없었다.
"""
import pytest

EDGE = {"headers": {"X-Forwarded-For": "203.0.113.9"},
        "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

RAW = {"models": [
    {"name": "qwen2.5:14b", "model": "qwen2.5:14b", "size": 9_000_000_000,
     "digest": "abc123", "modified_at": "2026-09-01T00:00:00Z",
     "details": {"family": "qwen2", "parameter_size": "14.8B",
                 "quantization_level": "Q4_K_M", "format": "gguf"}},
    {"model": "only-model-key:1b", "digest": "d2", "details": {}},
    {"digest": "nameless"},
]}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body

    def json(self):
        return self._body


class _Session:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, 0

    def get(self, url, timeout=None):
        self.calls += 1
        assert url.endswith("/api/tags")
        if self.exc:
            raise self.exc
        return self.resp


@pytest.fixture
def A(monkeypatch):
    import app as A
    A._public_tags_cache.update(at=0.0, body=None, status=0)
    try:
        A.limiter.reset()
    except Exception:
        pass
    yield A
    A._public_tags_cache.update(at=0.0, body=None, status=0)


def _get(A, **kw):
    return A.app.test_client().get("/api/tags", **(kw or EDGE))


def test_external_no_auth_returns_200_ollama_shape(A, monkeypatch):
    """핵심: 엣지 모양 요청(루프백+XFF)·인증 헤더 없음 → 200(종전 405)."""
    monkeypatch.setattr(A, "_ollama_session", _Session(_Resp(200, RAW)))
    r = _get(A, **EDGE)
    assert r.status_code == 200, r.data[:200]
    models = r.get_json()["models"]
    assert [m["name"] for m in models] == ["qwen2.5:14b", "only-model-key:1b"]
    assert models[0] == {"name": "qwen2.5:14b", "model": "qwen2.5:14b",
                         "digest": "abc123", "details": {"family": "qwen2"}}


def test_only_whitelisted_fields_exposed(A, monkeypatch):
    """공개 범위 고정 — 크기·양자화·파라미터 수·수정 시각은 싣지 않는다."""
    monkeypatch.setattr(A, "_ollama_session", _Session(_Resp(200, RAW)))
    for m in _get(A, **EDGE).get_json()["models"]:
        assert set(m) == {"name", "model", "digest", "details"}
        assert set(m["details"]) == {"family"}


@pytest.mark.parametrize("sess", [
    _Session(exc=ConnectionError("refused")),
    _Session(_Resp(500, None)),
    _Session(exc=TimeoutError("slow")),
])
def test_engine_down_is_503_not_200(A, monkeypatch, sess, caplog):
    """엔진 미응답은 503 — 감시가 실제 상태를 본다(200+빈 목록이면 무성 실패)."""
    monkeypatch.setattr(A, "_ollama_session", sess)
    with caplog.at_level("WARNING"):
        r = _get(A, **EDGE)
    assert r.status_code == 503
    assert r.get_json() == {"models": [], "error": "engine_unavailable"}
    assert any("[api/tags]" in rec.getMessage() for rec in caplog.records)


def test_cached_within_ttl(A, monkeypatch):
    """외부 반복 호출이 Ollama로 새지 않는다(10s 캐시)."""
    sess = _Session(_Resp(200, RAW))
    monkeypatch.setattr(A, "_ollama_session", sess)
    for _ in range(3):
        assert _get(A, **EDGE).status_code == 200
    assert sess.calls == 1


def test_other_surfaces_still_401_through_edge(A):
    """음성 통제: 이 경로만 열렸다 — 대응 인증 경로는 여전히 401."""
    for path in ("/api/models", "/api/status", "/api/keys"):
        r = A.app.test_client().get(path, **EDGE)
        assert r.status_code == 401, (path, r.status_code)


def test_rate_limited_per_client(A, monkeypatch):
    monkeypatch.setattr(A, "_ollama_session", _Session(_Resp(200, RAW)))
    codes = [_get(A, **EDGE).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30
    assert codes[30] == 429
