"""werub-physio-v1 회귀 — 0D 다계통 생리 시뮬 + 커플링 (R1, 환자 아바타).

순수 단위 — 외부 의존(GPU/네트워크/Ollama) 0. numpy만.
실행: venv/bin/python -m pytest tests/test_physiology.py -q
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import physiology  # noqa: E402

SAMPLE_A = {  # 당뇨·CKD 고위험 — 커플링 활성
    "age": 68, "sex": "male",
    "conditions": ["type2_diabetes", "ckd", "hypertension"],
    "vitals": {"hr": 88, "sbp": 152, "dbp": 94, "rr": 18, "spo2": 96, "temp": 37.1},
    "meds": ["metformin", "lisinopril"], "egfr": 55,
    "horizon_sec": 60, "tick_ms": 200,
}
SAMPLE_B = {  # 정상 성인 — 커플링 비활성
    "age": 40, "sex": "female", "conditions": [],
    "vitals": {"hr": 72, "sbp": 118, "dbp": 76, "rr": 14, "spo2": 98},
    "meds": [], "egfr": 95, "horizon_sec": 60, "tick_ms": 200,
}

CHANNELS = {"hr", "map", "sbp", "dbp", "rr", "spo2", "glucose_mgdl", "cardiac_output_lmin"}


def test_contract_shape():
    r = physiology.simulate(SAMPLE_A)
    assert r["model"] == "werub-physio-v1"
    assert r["n_points"] == 300                      # 60s / 200ms
    assert set(r["series"].keys()) == CHANNELS
    assert all(len(v) == 300 for v in r["series"].values())
    assert "disclaimer" in r


def test_couplings_active_on_A_inactive_on_B():
    a = physiology.simulate(SAMPLE_A)
    b = physiology.simulate(SAMPLE_B)
    assert set(a["coupling"]) == {"egfr_pk_cardio", "glucose"}
    assert b["coupling"] == []
    assert b["drug_response"] == []
    # A: 약물반응에 lisinopril(심혈관)·metformin(혈당) 포함
    drugs = {d["drug"] for d in a["drug_response"]}
    assert {"lisinopril", "metformin"} <= drugs


def test_egfr_pk_cardio_monotonic():
    """eGFR↓ → 신배설약물 축적↑ → MAP 강하 증폭 (커플링 단조성)."""
    def mean_map(egfr):
        r = physiology.simulate(dict(SAMPLE_A, egfr=egfr))
        return statistics.mean(r["series"]["map"])
    lo, mid, hi = mean_map(20), mean_map(55), mean_map(120)
    assert lo < mid < hi      # 신기능 나쁠수록 MAP 더 낮음(약물 축적 효과)


def test_glucose_dynamics_lowering():
    """metformin → glucose 하강(궤적이 베이스라인 이하로 정착)."""
    r = physiology.simulate(SAMPLE_A)
    g = r["series"]["glucose_mgdl"]
    assert g[-1] <= g[0]                       # 하강 또는 유지(60s 내 onset 60min이라 소폭)
    # 최대 horizon(600s)에선 단조 하강 — onset 60min이라 일부만 진행(~2%+)
    r2 = physiology.simulate(dict(SAMPLE_A, horizon_sec=600, tick_ms=1000))
    g2 = r2["series"]["glucose_mgdl"]
    assert g2[-1] < g2[0]                       # 명확히 하강
    assert (g2[0] - g2[-1]) / g2[0] >= 0.02    # 600s에 ≥2% 하강(생리적 onset 반영)


def test_clamp_within_physiologic_range():
    # 극단 입력에도 참조범위 내로 클램프
    extreme = dict(SAMPLE_A, vitals={"hr": 250, "sbp": 400, "dbp": 5, "rr": 90, "spo2": 50},
                   meds=["lisinopril", "atenolol", "hydrochlorothiazide"], egfr=8)
    r = physiology.simulate(extreme)
    s = r["series"]
    assert all(30 <= x <= 200 for x in s["hr"])
    assert all(60 <= x <= 250 for x in s["sbp"])
    assert all(40 <= x <= 180 for x in s["map"])
    assert all(60 <= x <= 100 for x in s["spo2"])


def test_deterministic():
    a1 = physiology.simulate(SAMPLE_A)
    a2 = physiology.simulate(SAMPLE_A)
    assert a1["series"] == a2["series"]        # 난수 없음 — 동일 입력 동일 출력


def test_weight_height_fallback():
    r = physiology.simulate(SAMPLE_B)          # 체중·키 미전달
    assert r["inputs_resolved"]["weight_estimated"] is True
    assert r["inputs_resolved"]["weight_kg"] == 58.0   # 여성 표준 추정
    r2 = physiology.simulate(dict(SAMPLE_B, weight_kg=80))
    assert r2["inputs_resolved"]["weight_estimated"] is False
    assert r2["inputs_resolved"]["weight_kg"] == 80.0


def test_drug_name_normalization():
    """한글/브랜드/용량표기 약물명 → INN 정규화 (트윈 한국 HIS 실데이터 대응)."""
    from core.physiology import _normalize_drug
    assert _normalize_drug("메트포르민") == "metformin"
    assert _normalize_drug("메트포르민 500mg") == "metformin"
    assert _normalize_drug("리시노프릴") == "lisinopril"
    assert _normalize_drug("Glucophage") == "metformin"
    assert _normalize_drug("metformin (glucophage)") == "metformin"
    assert _normalize_drug("Lasix 40mg") == "furosemide"
    assert _normalize_drug("암로디핀 5mg") == "amlodipine"
    assert _normalize_drug("덱사메타손") == "덱사메타손"   # 미매칭=원문(정직)


def test_korean_meds_trigger_coupling():
    """한글 약물명도 커플링 발화 + matched_from에 원문 보존."""
    a = physiology.simulate(dict(SAMPLE_A, meds=["메트포르민", "리시노프릴"]))
    assert set(a["coupling"]) == {"egfr_pk_cardio", "glucose"}
    inns = {d["drug"] for d in a["drug_response"]}
    assert {"metformin", "lisinopril"} <= inns
    froms = {d.get("matched_from") for d in a["drug_response"]}
    assert "메트포르민" in froms and "리시노프릴" in froms
    # 비커플링 약물(덱사메타손)은 빈 배열 — 정직
    b = physiology.simulate(dict(SAMPLE_A, meds=["덱사메타손"]))
    assert b["coupling"] == []


def test_baseline_map_formula():
    """커플링 없는 베이스(B): MAP ≈ DBP + (SBP-DBP)/3."""
    r = physiology.simulate(SAMPLE_B)
    expected = 76 + (118 - 76) / 3.0           # 90.0
    assert abs(r["series"]["map"][0] - expected) < 1.5
