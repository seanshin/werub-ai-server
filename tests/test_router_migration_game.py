"""D1 router 이관 — routes/game.py 12곳 (v2.125.13). 이관은 변경이 아니다: 페이로드가 종전 직접 호출과 **동일**해야 한다.

종전(2026-08-19 커밋 ccad2e1 기준) 12곳 전부 `_ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={…})` +
`r.json().get("response", default)` — **상태 코드 미확인**(HTTP 오류가 default로 조용히 흐르는 무성 실패 클래스).
그 계약을 `_router_response()`가 그대로 잇는다: HTTP 오류=default(종전과 동일·별건) · 전송 예외=던진다(종전 except 분기).

검증 방식: `model_router.load_routing`을 가짜 표로 바꿔 **프로덕션 코드 경로**(엔드포인트→`_router_adapter()`→
호출자 세션)를 그대로 태우고, 세션 스텁이 잡은 본문을 종전 페이로드 dict와 **글자 그대로** 비교한다.
엔드포인트 테스트는 tmp game.db(테넌트 표만)로 돈다 — 네트워크 0.
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import json
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_router  # noqa: E402

GAME_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routes", "game.py")

# 표에 추가될 task(값=종전 코드 기본값). 코드는 `_load_config().get("default_model","qwen2.5:14b")`를
# `model=`로 넘기므로 표 값은 **폴백이 아니라 광고용**이다 — 아래 테스트가 그 우선순위를 확인한다.
FAKE_ROUTING = {
    "defaults": {"policy": "local_only", "provider": "ollama"},
    "tasks": {
        "game.npc_generate": {"model": "qwen2.5:14b"},
        "game.npc_dialogue": {"model": "qwen2.5:14b"},
        "game.levelup_skill": {"model": "qwen2.5:14b"},
        "game.scenario": {"model": "qwen2.5:14b"},
        "game.image_prompt": {"model": "qwen2.5:14b"},
        "game.npc_interact": {"model": "qwen2.5:14b"},
        "game.heroine_date": {"model": "qwen2.5:14b"},
        "game.heroine_gift": {"model": "qwen2.5:14b"},
        "game.heroine_confess": {"model": "qwen2.5:14b"},
        "game.heroine_generate": {"model": "qwen2.5:14b"},
    },
}

OLLAMA_BASE = "http://ollama-under-test:11434"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class _Sess:
    """`_ollama_session` 자리에 꽂는 세션 스텁 — 어댑터가 보낸 (url, payload, timeout)을 잡는다."""
    def __init__(self, body=None, status=200, raise_exc=None):
        self.body, self.status, self.raise_exc, self.sent = body or {}, status, raise_exc, []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json, timeout))
        if self.raise_exc is not None:
            raise self.raise_exc
        return _Resp(self.status, self.body)


def _site_tasks():
    """routes/game.py가 실제로 부르는 task 이름 집합."""
    with open(GAME_PY, encoding="utf-8") as f:
        src = f.read()
    return set(re.findall(r'model_router\.generate\("(game\.[a-z_]+)"', src)), src


# ── 라우팅 표·잔여 직접 호출 ────────────────────────────────────────────────
def test_all_sites_use_router_and_tasks_are_covered():
    tasks, src = _site_tasks()
    assert "_ollama_session.post(" not in src, "직접 Ollama 호출이 남아 있다"
    assert src.count('model_router.generate("game.') == 12, "이관 지점은 12곳"
    assert tasks == set(FAKE_ROUTING["tasks"]), (
        f"코드의 task와 표 초안이 어긋난다\n  코드={sorted(tasks)}\n  표={sorted(FAKE_ROUTING['tasks'])}")


def test_tasks_registered_in_real_table():
    """표에 등록되기 전엔 xfail — 등록되면 통과(미등록 task는 UnknownTask로 즉시 실패하는 설계)."""
    r = model_router.load_routing()
    missing = sorted(set(FAKE_ROUTING["tasks"]) - set(r.get("tasks") or {}))
    if missing:
        pytest.xfail(f"config/model_routing.json에 아직 없음: {missing}")
    for t in FAKE_ROUTING["tasks"]:
        assert model_router.resolve(t)["policy"] == "local_only"


# ── 응답 계약 (`_router_response`) ──────────────────────────────────────────
def test_router_response_contract():
    from routes import game as G
    R = model_router.Result
    # 200 → 종전 `.get("response", default)`
    assert G._router_response(R(text="답", ok=True, status=200, raw={"response": "답"})) == "답"
    assert G._router_response(R(ok=True, status=200, raw={}), "D") == "D"          # response 키 없음 → default
    # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 **같은 분기**로 던진다 —
    # 종전엔 default로 조용히 흘렀다(200인데 빈 결과 클래스). 호출자의 기존 except(500/폴백)가 받는다.
    with pytest.raises(RuntimeError):
        G._router_response(R(ok=False, status=500, error="HTTP 500"), "D")
    with pytest.raises(RuntimeError):
        G._router_response(R(ok=False, status=404, error="HTTP 404"))
    # 전송 예외 → 종전엔 requests 예외가 except 분기로 갔다 → 여기서도 던진다
    with pytest.raises(RuntimeError):
        G._router_response(R(ok=False, status=None, error="ConnectionError"))


# ── 엔드포인트 경유(프로덕션 경로) ──────────────────────────────────────────
@pytest.fixture
def game_env(tmp_path, monkeypatch):
    """tmp game.db(테넌트 표만) + 세션 스텁 주입 + 가짜 라우팅 표. `sess`는 테스트가 바꿔 끼운다."""
    from routes import game as G
    from flask import Flask

    monkeypatch.setattr(model_router, "load_routing", lambda path=None: FAKE_ROUTING)
    holder = {"sess": _Sess({"response": ""}), "cfg": {"default_model": "qwen2.5:14b"}}

    class _SessProxy:
        def post(self, *a, **k):
            return holder["sess"].post(*a, **k)

    G.init_game(load_config=lambda: dict(holder["cfg"]), ollama_session=_SessProxy(),
                llm_generate_korean=None, force_korean_token=None,
                ollama_base=OLLAMA_BASE, base_dir=str(tmp_path))
    G._game_db_local.conn = None                     # 스레드 로컬 커넥션이 다른 DB를 물고 있지 않게
    conn = sqlite3.connect(os.path.join(str(tmp_path), "game.db"))
    conn.execute("CREATE TABLE game_tenants (id TEXT, name TEXT, api_key TEXT, plan TEXT, "
                 "max_players INTEGER, max_npcs INTEGER, is_active INTEGER DEFAULT 1, created_at TEXT)")
    conn.execute("INSERT INTO game_tenants VALUES ('g1','t','KEY1','free',10,10,1,'now')")
    conn.commit()
    conn.close()

    app = Flask("game_router_test")
    app.register_blueprint(G.game_bp)
    yield G, app.test_client(), holder
    G._game_db_local.conn = None


def _post(client, path, body):
    return client.post(path, json=body, headers={"X-Game-Key": "KEY1"})


def test_illustrate_scene_payload_identical(game_env, monkeypatch):
    """L1100 `game.image_prompt` — options={num_predict} · system 없음 · 모델은 config 동적값이 우선."""
    G, client, holder = game_env
    holder["cfg"] = {"default_model": "qwen2.5:7b"}                # 표(14b)가 아니라 config(7b)가 이겨야 한다
    holder["sess"] = _Sess({"response": " a dark castle, concept art "})
    monkeypatch.setattr(G.requests, "post", lambda *a, **k: _Resp(200, {"image_url": "u"}))
    r = _post(client, "/api/game/illustrate/scene", {"description": "어두운 성", "mood": "dark"})
    assert r.status_code == 200 and r.get_json()["prompt_used"] == "a dark castle, concept art"
    url, payload, timeout = holder["sess"].sent[-1]
    assert url == OLLAMA_BASE + "/api/generate" and timeout == 60
    # 종전 직접 호출 본문 그대로(커밋 ccad2e1 L1100)
    assert payload == {"model": "qwen2.5:7b",
                       "prompt": "Translate to English image prompt: 어두운 성. Mood: dark. Output only prompt.",
                       "stream": False, "options": {"num_predict": 150, "repeat_penalty": 1.1}}


def test_illustrate_scene_error_contract_fallback(game_env, monkeypatch):
    """HTTP 오류·전송 예외 모두 호출자 except의 `eng = desc` **폴백**으로 200 — 의도된 강등이다.
    v2.125.15부터 HTTP 오류도 같은 except를 타므로(무성 실패 일괄) 두 경우의 결과가 같고,
    실패 자체는 어댑터 로그(`[model_router] HTTP …`)에 남는다(종전엔 로그 무흔적)."""
    G, client, holder = game_env
    monkeypatch.setattr(G.requests, "post", lambda *a, **k: _Resp(200, {"image_url": "u"}))
    holder["sess"] = _Sess({"error": "model not found"}, status=404)
    r = _post(client, "/api/game/illustrate/scene", {"description": "castle"})
    assert r.status_code == 200 and r.get_json()["prompt_used"] == "castle"
    holder["sess"] = _Sess(raise_exc=ConnectionError("down"))
    r = _post(client, "/api/game/illustrate/scene", {"description": "castle"})
    assert r.status_code == 200 and r.get_json()["prompt_used"] == "castle"


def test_heroine_generate_payload_identical(game_env):
    """L2262 `game.heroine_generate` — options={temperature,num_predict} · config에 default_model 없으면 코드 기본값 14b."""
    G, client, holder = game_env
    holder["cfg"] = {}
    holder["sess"] = _Sess({"response": '설명 {"name": "유리", "age": 18} 끝'})
    r = _post(client, "/api/game/heroine/generate", {"archetype": "츤데레", "genre": "학원"})
    assert r.status_code == 200 and r.get_json()["generated"] == {"name": "유리", "age": 18}
    url, payload, timeout = holder["sess"].sent[-1]
    assert url == OLLAMA_BASE + "/api/generate" and timeout == 60
    expected_prompt = (
        "연애 시뮬레이션 히로인 프로필 JSON 생성. 반드시 모든 필드를 한국어로만 작성하세요. 절대 중국어, 일본어, 영어를 사용하지 마세요.\n"
        "이름은 자유롭게 생성하세요.\n"
        "유형: 츤데레, 장르: 학원\n"
        "\n"
        "\n"
        '{"name": "이름", "age": 18, "personality": "성격 상세", "appearance": "외모 묘사",\n'
        '"background": "배경 스토리", "likes": "좋아하는 것 3개", "dislikes": "싫어하는 것 3개",\n'
        '"ideal_type": "이상형", "voice_style": "말투 스타일"}\n'
        "JSON만 출력.")
    assert payload == {"model": "qwen2.5:14b", "prompt": expected_prompt, "stream": False,
                       "options": {"temperature": 0.9, "num_predict": 800, "repeat_penalty": 1.1}}


def test_heroine_generate_transport_error_is_500(game_env):
    """종전: requests 예외 → except → internal_error(500). 라우터 Result(ok=False,status=None)도 같은 분기."""
    G, client, holder = game_env
    holder["sess"] = _Sess(raise_exc=ConnectionError("down"))
    r = _post(client, "/api/game/heroine/generate", {})
    assert r.status_code == 500


def test_npc_generate_payload_shape(game_env):
    """L414 `game.npc_generate` — options={temperature:0.8,num_predict:1000}·timeout 60. 프롬프트는 긴 템플릿이라
    본문 필드만 그대로 비교하고 prompt는 클래스 포함·동일 객체 전달만 확인."""
    G, client, holder = game_env
    holder["sess"] = _Sess({"response": '{"name": "아렌"}'})
    r = _post(client, "/api/game/npc/template/generate", {"class": "기사", "level": 3})
    assert r.status_code == 200 and r.get_json()["generated"]["class"] == "기사"
    url, payload, timeout = holder["sess"].sent[-1]
    assert timeout == 60
    assert "기사" in payload.pop("prompt")
    assert payload == {"model": "qwen2.5:14b", "stream": False,
                       "options": {"temperature": 0.8, "num_predict": 1000, "repeat_penalty": 1.1}}


# ── 엔드포인트가 DB 표를 많이 요구하는 지점 — 사이트의 라우터 호출 인자를 그대로 재현 ──────
def test_shared_interact_payload_with_system(monkeypatch):
    """L1340 `game.npc_interact` — 유일하게 `system`이 있는 지점: {"model","prompt","system","stream","options"}."""
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: FAKE_ROUTING)
    from routes import game as G
    sess = _Sess({"response": "안녕 *미소*"})
    monkeypatch.setattr(G, "_ollama_session", sess)
    monkeypatch.setattr(G, "_OLLAMA_BASE", OLLAMA_BASE)
    res = model_router.generate("game.npc_interact", "안녕?", model="qwen2.5:14b", system="SYS",
                                options={"temperature": 0.8}, timeout=60, adapter=G._router_adapter())
    assert G._router_response(res) == "안녕 *미소*"
    url, payload, timeout = sess.sent[-1]
    assert url == OLLAMA_BASE + "/api/generate" and timeout == 60
    assert payload == {"model": "qwen2.5:14b", "prompt": "안녕?", "system": "SYS", "stream": False,
                       "options": {"temperature": 0.8, "repeat_penalty": 1.1}}


def test_heroine_confess_payload_temperature_only(monkeypatch):
    """L2053 `game.heroine_confess` — options={temperature:0.85}만(num_predict 없음)·timeout 60.
    D2(v2.125.14): 호출자 options는 temperature만이나, 라우터 핀 repeat_penalty 1.1이 합류한다."""
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: FAKE_ROUTING)
    from routes import game as G
    sess = _Sess({"response": "…"})
    monkeypatch.setattr(G, "_ollama_session", sess)
    monkeypatch.setattr(G, "_OLLAMA_BASE", OLLAMA_BASE)
    model_router.generate("game.heroine_confess", "P", model="qwen2.5:14b",
                          options={"temperature": 0.85}, timeout=60, adapter=G._router_adapter())
    assert sess.sent[-1][1] == {"model": "qwen2.5:14b", "prompt": "P", "stream": False,
                                "options": {"temperature": 0.85, "repeat_penalty": 1.1}}


def test_unknown_task_fails_loudly(monkeypatch):
    """오타 task는 조용히 기본 모델을 타지 않는다(v2.124.62 클래스)."""
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: FAKE_ROUTING)
    with pytest.raises(model_router.UnknownTask):
        model_router.generate("game.typo", "P", adapter=model_router.OllamaAdapter(session=_Sess()))
