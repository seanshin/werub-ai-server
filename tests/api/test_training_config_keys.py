"""학습 설정 저장 — 모르는 키는 400(아무것도 쓰지 않음) · 화면이 저장 실패를 '저장됨'으로 보이지 않게.

🔴 2026-04-21 '학습 방향' 카드가 preset·system_prompt·category_weights를 `/api/training/config`로 보냈는데
   서버 허용 목록에 없어 **한 번도 저장되지 않았다** — 응답은 `{"ok": true}`, 화면은 '✅ 저장됨'(5개월).
   운영자 결정(2026-09-30): 정직하게 — 서버는 거절, 화면은 저장 버튼을 끄고 '미구현' 안내.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _db(monkeypatch, tmp_path):
    from routes import training
    monkeypatch.setattr(training, "TRAINING_DB", str(tmp_path / "training.db"))
    return training


def test_unknown_keys_rejected_and_nothing_written(client, monkeypatch, tmp_path):
    training = _db(monkeypatch, tmp_path)
    r = client.post("/api/training/config",
                    json={"gpu_max_pct": "55", "preset": "news", "system_prompt": "x", "category_weights": "{}"})
    assert r.status_code == 400
    d = r.get_json()
    assert d["error"] == "unknown_keys" and d["keys"] == ["category_weights", "preset", "system_prompt"]
    # 허용 키가 섞여 있어도 **일부 저장 없음** — 400이면 상태가 그대로여야 호출자가 판단할 수 있다
    assert training._training_config("gpu_max_pct") is None


def test_allowed_keys_still_saved(client, monkeypatch, tmp_path):
    training = _db(monkeypatch, tmp_path)
    r = client.post("/api/training/config", json={"allowed_hours": "01-05", "gpu_max_pct": "70"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    g = client.get("/api/training/config").get_json()
    assert g["allowed_hours"] == "01-05" and g["gpu_max_pct"] == 70


def test_ui_save_paths_report_failures():
    """저장 4곳이 응답을 본다(saveJSON) — 예전엔 alert('Saved')·'✅ 저장됨'·try 없는 r.json()."""
    js = (ROOT / "static/js/main.js").read_text()

    def body(name):
        i = js.index(name)
        j = js.find("\nasync function ", i + 1)
        return js[i:j]
    for fn, url in (("async function saveTrainingConfig(", "/api/training/config"),
                    ("async function saveInitConfig(", "/api/models/init-config"),
                    ("async function saveImageSettings(", "/api/image/settings"),
                    ("async function saveDirection(", "/api/training/config")):
        b = body(fn)
        assert f"saveJSON('{url}'" in b, fn
        assert "await fetch(" not in b, fn
    # 코드 줄만 본다 — 주석이 예전 코드를 인용하고 있다(처음엔 그 주석에 걸려 실패했다)
    assert not re.search(r"^\s*alert\('Saved'\)", js, re.M)


def test_direction_card_is_honest():
    html = (ROOT / "templates/admin.html").read_text()
    assert 'id="wDirNotice"' in html and "학습에 반영되지 않" in html
    assert 'onclick="saveDirection()"' not in html          # 저장 버튼 비활성(호출 경로 없음)
    assert "방향 설정 저장 (미구현)" in html
