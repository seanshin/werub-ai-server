"""X6 — `/api/generate` 스트리밍 raw 중계 계약 고정 (2026-08-25).

배경 정정: "한국어 필터 소실(v2.125.15 chat.py 삭제)"은 인과가 틀렸다 —
삭제된 routes/chat.py는 v2.28.0 모듈 분리 이후 app.py에 등록된 적이 없고
(`git log -S"chat_bp" -- app.py` 0건·import도 불가), 실경로 routes/ollama.py는
**처음부터 raw 중계**였다. raw가 의도된 계약이다: `/api/generate`는 범용 프록시라
번역·영문 프롬프트가 정상 사용례(CLAUDE.md 예시)이고, force_korean(한글 없는 줄
삭제)을 걸면 그 사용례가 깨진다. 한국어 강제는 도메인 경로(story/writing/novel/
hr_law/counseling — force_korean_token 배선 확인됨)의 소관.

이 테스트는 그 계약을 고정한다: upstream NDJSON 바이트가 필터 없이 그대로
클라이언트에 나간다. 누군가 여기에 한국어 필터를 넣으면(또는 실수로 지우면)
여기서 걸린다 — "재발이 무성으로 지나간다"(remaining-work §9 X6)의 처치.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")
pytest.importorskip("requests")

from flask import Flask  # noqa: E402


# 혼합 언어 upstream — force_korean이면 영문 줄 삭제, force_korean_token이면 가나 제거.
# 어느 쪽이 끼어도 바이트 동일성이 깨진다.
_UPSTREAM_CHUNKS = [
    {"model": "m", "response": "Hello, ", "done": False},
    {"model": "m", "response": "世界 カタカナ ", "done": False},
    {"model": "m", "response": "안녕하세요", "done": False},
    {"model": "m", "response": "", "done": True, "done_reason": "stop"},
]


class _FakeStreamResp:
    def __init__(self, chunks):
        self._lines = [json.dumps(c, ensure_ascii=False).encode("utf-8") for c in chunks]

    def iter_lines(self):
        yield from self._lines


@pytest.fixture()
def client(monkeypatch):
    import routes.ollama as om
    captured = {}

    def _fake_post(url, json=None, stream=None, timeout=None, **kw):
        captured["url"] = url
        captured["payload"] = json
        return _FakeStreamResp(_UPSTREAM_CHUNKS)

    monkeypatch.setattr(om.http_requests, "post", _fake_post)
    # app.py init_ollama 주입분 중 이 경로가 쓰는 최소만 채운다(단독 BP 등록이라 미주입)
    monkeypatch.setattr(om, "_load_config",
                        lambda: {"default_system_prompt_enabled": False})
    monkeypatch.setattr(om, "_OLLAMA_BASE", "http://ollama.test")
    app = Flask(__name__)
    app.register_blueprint(om.ollama_bp)
    c = app.test_client()
    c._captured = captured
    return c


def test_generate_stream_is_raw_passthrough(client):
    r = client.post("/api/generate", json={"model": "m", "prompt": "translate this"})
    assert r.status_code == 200
    expected = "".join(
        json.dumps(c, ensure_ascii=False) + "\n" for c in _UPSTREAM_CHUNKS)
    assert r.get_data(as_text=True) == expected   # 바이트 동일 = 필터 0
    assert client._captured["url"].endswith("/api/generate")


def test_generate_requires_prompt(client):
    # model 생략은 config default_model 폴백이라 여기서 안 잰다(환경 의존).
    assert client.post("/api/generate", json={"model": "m"}).status_code == 400
