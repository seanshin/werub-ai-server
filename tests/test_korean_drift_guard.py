"""v2.125.52 — CJK 표류 가드(story·novel) + story 뼈대 200 차단.

실측(2026-09-28 story-korean-only 동일 입력 N=20): qwen2.5:14b가 30%(6/20) 중국어 표류 —
'늦' 다음 '夏'(늦여름)에서 전환해 중국어로 계속 쓰고 메타 발언까지 했다. force_korean이
걷어내 붕괴 3(526→31자)·절반 소실 3(717→330자)이 200으로 나갔다.
원자료 raw(앞 400자)를 그대로 회귀 입력으로 쓴다: tests/fixtures/story_cjk_drift_samples_20260928.json
"""
import json
from pathlib import Path

import pytest

from core.korean import cjk_share, generate_with_drift_guard, force_korean, CJK_DRIFT_THRESHOLD

SAMPLES = json.loads((Path(__file__).parent / "fixtures" /
                      "story_cjk_drift_samples_20260928.json").read_text(encoding="utf-8"))
DRIFTED = [s for s in SAMPLES if s["i"] in (3, 4, 7, 10, 17, 18)]
CLEAN = [s for s in SAMPLES if s["i"] in (0, 1, 2)]


@pytest.mark.parametrize("s", DRIFTED, ids=lambda s: f"drift-{s['i']}")
def test_detector_flags_every_measured_drift(s):
    """탐지기부터: 실측 표류 6건 전부 임계 초과(과소계상 0)."""
    assert cjk_share(s["raw"]) > CJK_DRIFT_THRESHOLD


@pytest.mark.parametrize("s", CLEAN, ids=lambda s: f"clean-{s['i']}")
def test_detector_passes_measured_clean(s):
    """음성 통제: 실측 정상 3건은 표류 0(위양성 0)."""
    assert cjk_share(s["raw"]) == 0.0


def test_share_edge_cases():
    assert cjk_share("") == 0.0 and cjk_share("   \n") == 0.0
    assert cjk_share("한국어만 있는 문장") == 0.0
    assert cjk_share("한국어 中文") == pytest.approx(0.4)   # 공백 제외 5자 중 2자


class _Model:
    """시도마다 정해진 raw를 돌려주는 가짜 모델 — 받은 system을 기록."""
    def __init__(self, outputs):
        self.outputs, self.systems = list(outputs), []

    def __call__(self, system):
        self.systems.append(system)
        return self.outputs.pop(0), 10, 100


def test_clean_first_try_single_call():
    m = _Model([CLEAN[0]["raw"]])
    text, tok, ms = generate_with_drift_guard(m, "SYS")
    assert text == CLEAN[0]["raw"] and len(m.systems) == 1 and m.systems[0] == "SYS"
    assert (tok, ms) == (10, 100)


def test_drift_then_clean_regenerates_with_reinforced_prompt():
    m = _Model([DRIFTED[0]["raw"], CLEAN[1]["raw"]])
    text, tok, ms = generate_with_drift_guard(m, "SYS")
    assert text == CLEAN[1]["raw"]
    assert len(m.systems) == 2 and m.systems[1].startswith("SYS") and "한국어(한글)" in m.systems[1]
    assert (tok, ms) == (20, 200)          # 비용 누적 — 숨기지 않는다


def test_all_drift_returns_least_drifted_after_retries(caplog):
    worst, mid, least = DRIFTED[5]["raw"], DRIFTED[1]["raw"], DRIFTED[3]["raw"]
    shares = sorted([(cjk_share(x), x) for x in (worst, mid, least)])
    m = _Model([worst, mid, least])
    with caplog.at_level("WARNING"):
        text, tok, _ = generate_with_drift_guard(m, "SYS", retries=2)
    assert len(m.systems) == 3 and text == shares[0][1] and tok == 30
    assert any("최저 표류본 반환" in r.getMessage() for r in caplog.records)   # 무성 실패 금지


def test_error_result_is_not_retried():
    """전송·서킷 실패는 재시도하지 않는다(표류만 대상 — 장애 증폭 금지)."""
    m = _Model(["Error: timeout", CLEAN[0]["raw"]])
    text, _, _ = generate_with_drift_guard(m, "SYS")
    assert text == "Error: timeout" and len(m.systems) == 1


def test_measured_collapse_would_have_been_caught():
    """종전 결과 재현: 표류 raw를 그대로 필터하면 뼈대만 남는다 → 가드가 그 raw를 채택하지 않는다."""
    s = next(x for x in DRIFTED if x["i"] == 3)
    assert len(force_korean(s["raw"]).strip()) < 50
    m = _Model([s["raw"], CLEAN[0]["raw"]])
    assert generate_with_drift_guard(m, "SYS")[0] == CLEAN[0]["raw"]


@pytest.mark.parametrize("mod", ["routes/story.py", "routes/novel.py"])
def test_story_and_novel_generate_wired(mod):
    src = (Path(__file__).parent.parent / mod).read_text(encoding="utf-8")
    body = src[src.index("\ndef _generate(model"):src.index("\ndef _generate_once(model")]
    assert "generate_with_drift_guard(" in body and "_generate_once(" in body


def test_story_generate_returns_502_not_skeleton(monkeypatch):
    """가드 후에도 필터가 뼈대만 남기면 502 — 200+DB 저장 금지."""
    import app as A
    from routes import story
    raw = next(x for x in DRIFTED if x["i"] == 18)["raw"]
    monkeypatch.setattr(story, "_generate", lambda *a, **k: (raw, 10, 100))
    saved = []
    monkeypatch.setattr(story, "_save_story", lambda *a, **k: saved.append(1))
    r = A.app.test_client().post("/api/story/generate",
                                 json={"prompt": "비 오는 골목의 짧은 장면을 써 줘.", "length": "short"})
    assert r.status_code == 502 and r.get_json()["error"] == "korean_generation_failed"
    assert saved == []


def test_story_generate_normal_still_200(monkeypatch):
    import app as A
    from routes import story
    raw = CLEAN[0]["raw"]
    monkeypatch.setattr(story, "_generate", lambda *a, **k: (raw, 10, 100))
    monkeypatch.setattr(story, "_save_story", lambda *a, **k: None)
    monkeypatch.setattr(story, "_generate_branch_suggestions", lambda *a, **k: [])
    r = A.app.test_client().post("/api/story/generate",
                                 json={"prompt": "비 오는 골목의 짧은 장면을 써 줘.", "length": "short"})
    assert r.status_code == 200 and r.get_json()["story"]


# ── v2.125.53: force_korean 입력 표류 관측 로그 ──────────────────────────────────

def _caller_under_test(text):
    return force_korean(text)


def test_filter_logs_drift_with_caller(caplog):
    raw = next(x for x in DRIFTED if x["i"] == 17)["raw"]
    with caplog.at_level("WARNING"):
        _caller_under_test(raw)
    msgs = [r.getMessage() for r in caplog.records if "[korean-filter]" in r.getMessage()]
    assert len(msgs) == 1
    assert "test_korean_drift_guard:" in msgs[0]          # 호출 위치(모듈:줄)가 실린다


def test_filter_output_unchanged_by_observation(caplog, monkeypatch):
    """관측 로그가 반환값을 바꾸지 않는다 — 로그 함수를 꺼도 결과 동일."""
    import core.korean as K
    raw = next(x for x in DRIFTED if x["i"] == 4)["raw"]
    with_log = K.force_korean(raw)
    monkeypatch.setattr(K, "_log_drift_input", lambda t: None)
    assert K.force_korean(raw) == with_log


def test_filter_silent_on_clean(caplog):
    with caplog.at_level("WARNING"):
        force_korean(CLEAN[0]["raw"])
    assert not [r for r in caplog.records if "[korean-filter]" in r.getMessage()]


def test_filter_observation_never_raises(monkeypatch):
    import core.korean as K
    monkeypatch.setattr(K, "cjk_share", lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    assert K.force_korean("한국어 문장") == "한국어 문장"


# ── v2.125.65: 판정 전용 필터는 관측 로그를 남기지 않는다 ─────────────────────────

def test_observe_false_same_output_no_log(caplog):
    """observe=False는 로그만 끈다 — 반환값은 기본 호출과 같다."""
    raw = next(x for x in DRIFTED if x["i"] == 17)["raw"]
    with caplog.at_level("WARNING"):
        out = force_korean(raw, observe=False)
    assert not [r for r in caplog.records if "[korean-filter]" in r.getMessage()]
    assert out == force_korean(raw)


def test_writing_cacheable_does_not_log_translation_kana(caplog):
    """ja 번역(가나가 정답)이 캐시 판정에서 표류 경고로 찍히던 위양성 — 게이트마다 writing:121 share 0.929."""
    from routes import writing as W
    with caplog.at_level("WARNING"):
        assert W._cacheable("お名前を教えていただけますか？") is False     # 판정 자체는 불변(한글 없음 → 캐시 안 함)
    assert not [r for r in caplog.records if "[korean-filter]" in r.getMessage()]
