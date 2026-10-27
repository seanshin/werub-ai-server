"""게이트 베이스라인 — 신규 실패만 차단 (기획서 §8-3b 구현).

🔴 배경: 계약 스모크를 "전건 통과 아니면 배포 중단"으로 잡으면 못 쓴다.
08-06~08-10 **나흘간 63/64**였고, 그 기준이었다면 그 사이 나간 v2.124.92·93·94가
전부 막혔을 것이다. LLM 출력 게이트의 실패는 확률적이라(같은 질의 12회에 6~8회)
결정적 전건 통과는 원리적으로 성립하지 않는다.

이 파일은 **"알려진 실패는 막지 않고, 새 실패만 막는다"**는 불변식을 고정한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date

import pytest

from core import gate_baseline as gb


def _r(name, ok, detail=""):
    return (name, ok, detail)


# ── 로드 ──
def test_missing_file_is_empty_baseline(tmp_path):
    assert gb.load(str(tmp_path / "nope.json")) == {}


def test_load_object_form(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"known_failures": [
        {"name": "a", "note": "사유", "since": "2026-08-01"}]}), encoding="utf-8")
    b = gb.load(str(p))
    assert b["a"]["note"] == "사유"


def test_load_bare_list_and_strings(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(["a", {"name": "b"}]), encoding="utf-8")
    b = gb.load(str(p))
    assert set(b) == {"a", "b"}


def test_broken_file_raises_not_silently_empty(tmp_path):
    """조용히 빈 베이스라인으로 떨어지면 기존 실패가 전부 신규가 되어 배포를 막는다.
    그러면 사람이 게이트를 끈다 — 그래서 예외를 올린다."""
    p = tmp_path / "b.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(Exception):
        gb.load(str(p))


# ── 🔑 핵심 불변식 ──
def test_new_failure_blocks():
    v = gb.classify([_r("x", False, "터짐")], {})
    assert [f["name"] for f in v["new_failures"]] == ["x"]
    assert gb.should_block(v) is True


def test_known_failure_does_not_block():
    """08-06~10의 63/64 상황 — 배포가 막히면 안 된다."""
    base = {"control[penalty-unlicensed]: 과거 오류 재발 없음":
            {"name": "control[penalty-unlicensed]: 과거 오류 재발 없음",
             "note": "제90조 오귀속", "since": "2026-08-06"}}
    v = gb.classify([_r("control[penalty-unlicensed]: 과거 오류 재발 없음", False, "제90조"),
                     _r("dur: HTTP 200", True)], base, today=date(2026, 8, 8))
    assert v["new_failures"] == []
    assert gb.should_block(v) is False
    assert v["known_failures"][0]["age_days"] == 2


def test_known_and_new_together_blocks():
    base = {"old": {"name": "old", "note": "n"}}
    v = gb.classify([_r("old", False), _r("new", False)], base)
    assert gb.should_block(v) is True
    assert [f["name"] for f in v["new_failures"]] == ["new"]
    assert [f["name"] for f in v["known_failures"]] == ["old"]


def test_recovered_is_reported_not_blocking():
    """08-10에 제90조가 닫힌 상황 — 등록 해제 후보로 알려준다."""
    base = {"old": {"name": "old", "note": "고친 그것"}}
    v = gb.classify([_r("old", True)], base)
    assert [f["name"] for f in v["recovered"]] == ["old"]
    assert gb.should_block(v) is False


def test_all_pass_with_empty_baseline():
    v = gb.classify([_r("a", True), _r("b", True)], {})
    assert v["passed"] == 2 and gb.should_block(v) is False


# ── 방치 감지 ──
def test_stale_after_threshold():
    base = {"x": {"name": "x", "since": "2026-07-01", "note": "오래됨"}}
    v = gb.classify([_r("x", False)], base, stale_days=30, today=date(2026, 8, 10))
    assert v["stale"] and v["stale"][0]["age_days"] == 40
    assert gb.should_block(v) is False        # 방치 경고가 차단은 아니다


def test_not_stale_before_threshold():
    base = {"x": {"name": "x", "since": "2026-08-01"}}
    v = gb.classify([_r("x", False)], base, stale_days=30, today=date(2026, 8, 10))
    assert v["stale"] == []


def test_missing_or_bad_since_is_not_stale():
    for entry in ({"name": "x"}, {"name": "x", "since": "언제였더라"}):
        v = gb.classify([_r("x", False)], {"x": entry}, today=date(2026, 8, 10))
        assert v["stale"] == [] and v["known_failures"][0]["age_days"] is None


# ── 리포트 ──
def test_report_mentions_block_and_counts():
    v = gb.classify([_r("a", False), _r("b", True)], {})
    txt = gb.format_report(v)
    assert "신규 실패 1건" in txt and "차단" in txt and "1/2" in txt


def test_report_flags_recovered_and_stale():
    base = {"r": {"name": "r"}, "s": {"name": "s", "since": "2026-06-01"}}
    v = gb.classify([_r("r", True), _r("s", False)], base, today=date(2026, 8, 10))
    txt = gb.format_report(v)
    assert "회복" in txt and "방치" in txt


def test_baseline_size_is_tracked():
    """항목 수가 증가 추세면 게이트가 죽고 있다는 신호(§13 S7) — 값이 리포트에 나와야 한다."""
    base = {"a": {"name": "a"}, "b": {"name": "b"}}
    v = gb.classify([_r("a", False)], base)
    assert v["baseline_size"] == 2
    assert "베이스라인 2건" in gb.format_report(v)


# ── 실제 배포 중인 베이스라인 파일 ──
def test_shipped_baseline_is_loadable_and_documented():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "config", "gate_baseline.json")
    raw = json.load(open(p, encoding="utf-8"))
    assert "_doc" in raw, "규율을 파일 안에 적어 둘 것(등록하는 사람이 읽는 유일한 곳)"
    assert isinstance(raw["known_failures"], list)
    gb.load(p)


def test_shipped_baseline_entries_have_reasons():
    """등록은 사유와 함께 — 사유 없는 등록은 '조용히 지우기'와 구분되지 않는다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = json.load(open(os.path.join(root, "config", "gate_baseline.json"), encoding="utf-8"))
    for it in raw["known_failures"]:
        assert it.get("note"), "note(사유) 필수: %s" % it
        assert it.get("since"), "since(등록일) 필수: %s" % it


# ── 배선 검증: smoke_contract --gate 통합 지점 ──
# 순수 모듈이 맞아도 **배선이 틀리면 게이트가 안 걸린다**. 실제로 v2.124.47에서
# "시그니처만 검증되고 실제 라우팅은 무검증"인 사례가 있었다.
def _load_smoke():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "smoke_contract", os.path.join(root, "scripts", "smoke_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_gate(monkeypatch, results, baseline_json, tmp_path, gate=True):
    """스위트 실행은 막고 집계·게이트 분기만 태운다."""
    sm = _load_smoke()
    sm._results[:] = results
    monkeypatch.setattr(sm, "SUITES", {})           # 실제 호출 0
    monkeypatch.setattr(sm, "smoke_vision", lambda *a, **k: None)
    p = tmp_path / "b.json"
    p.write_text(json.dumps(baseline_json), encoding="utf-8")
    argv = ["smoke", "--only", "nope"]
    if gate:
        argv += ["--gate", "--baseline", str(p)]
    monkeypatch.setattr(sys, "argv", argv)
    return sm.main()


def test_gate_blocks_new_failure(monkeypatch, tmp_path):
    rc = _run_gate(monkeypatch, [("x", False, "터짐")], {"known_failures": []}, tmp_path)
    assert rc == 1


def test_gate_passes_known_failure(monkeypatch, tmp_path):
    """08-06~10의 63/64 — 배포가 막히면 안 된다."""
    rc = _run_gate(monkeypatch, [("x", False, "알려진 것"), ("y", True, "")],
                   {"known_failures": [{"name": "x", "note": "n", "since": "2026-08-06"}]},
                   tmp_path)
    assert rc == 0


def test_without_gate_flag_behaviour_is_unchanged(monkeypatch, tmp_path):
    """`--gate` 없이 실행하면 **종전 그대로** — 실패 하나면 비-0."""
    rc = _run_gate(monkeypatch, [("x", False, "터짐")], {}, tmp_path, gate=False)
    assert rc == 1


def test_gate_with_broken_baseline_blocks(monkeypatch, tmp_path):
    """깨진 베이스라인을 조용히 통과시키면 게이트가 무의미해진다 → 별도 코드(2)로 차단."""
    sm = _load_smoke()
    sm._results[:] = [("x", True, "")]
    monkeypatch.setattr(sm, "SUITES", {})
    monkeypatch.setattr(sm, "smoke_vision", lambda *a, **k: None)
    p = tmp_path / "b.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["smoke", "--only", "nope", "--gate", "--baseline", str(p)])
    assert sm.main() == 2


def test_smoke_script_can_import_core_standalone():
    """`scripts/`에서 단독 실행해도 `core.gate_baseline`을 임포트할 수 있어야 한다.

    🔴 2026-08-10 첫 실전: `deploy.sh`가 `scripts/smoke_contract.py --gate`를 돌렸는데
    프로젝트 루트가 sys.path에 없어 `No module named 'core'`로 게이트가 차단됐다.
    (스모크 자체는 64/64 통과였다.) fail-closed 설계가 자기 배선 결함을 잡은 사례이며,
    같은 일이 반복되지 않도록 **하위 프로세스로 실제 실행해** 확인한다.
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; sys.argv=['smoke','--help']; "
         "runpy.run_path('scripts/smoke_contract.py', run_name='__main__')"],
        cwd=root, capture_output=True, text=True, timeout=60)
    # --help는 SystemExit(0). 중요한 건 import 단계에서 죽지 않는 것.
    assert "No module named 'core'" not in (r.stderr or "")
    # 게이트 경로가 core를 실제로 찾을 수 있는지 직접 확인
    r2 = subprocess.run(
        [sys.executable, "-c",
         "import sys,os; sys.path.insert(0,os.getcwd()); "
         "from core import gate_baseline; print(gate_baseline.DEFAULT_STALE_DAYS)"],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0, r2.stderr
