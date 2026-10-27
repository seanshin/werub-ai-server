"""v2.125.49 — GPU 시간대 프로파일 자동 적용 기본 off.

라이브(2026-09-27~28): 워커 재기동마다 off_hours 프로파일이 14b를 내리고 7b를 올리면
6초 뒤 시작 예열(상주 정책)이 14b를 되올려 7b를 내쫓았다 — 프로파일은 실제로 적용된 적이
없고 적재 3회만 헛돌았다. 두 정책 중 라이브 진실은 상주 정책(llm_resident·14b)이다.
"""
from pathlib import Path

from core import gpu_scheduler as gs


def test_disabled_by_default_starts_no_thread(monkeypatch):
    started = []
    monkeypatch.setattr(gs.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(gs, "_scheduler_running", False)
    gs.start_scheduler_loop()
    assert started == [] and gs._scheduler_running is False


def test_enabled_starts_thread(monkeypatch):
    started = []
    monkeypatch.setattr(gs.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(gs, "_scheduler_running", False)
    gs.start_scheduler_loop(enabled=True)
    assert started == [1]


def test_app_wires_config_default_false():
    src = (Path(gs.__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert ('start_scheduler_loop(enabled=bool(load_config().get("gpu_schedule_profiles_enabled", False)))'
            in src)
