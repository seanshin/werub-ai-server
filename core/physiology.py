"""werub-physio-v1 — 0D 다계통 생리 시뮬 + 계통 간 커플링 (R1, 환자 아바타).

트윈 0D(sim/organ.py: Windkessel·renal_pk·respiratory)를 **horizon 시계열**로 격상하고,
트윈 0D가 갖지 못한 **계통 간 커플링 2개**를 얹어 차별화한다.
  ① 신장 eGFR → 약동(1구획 PK) → 심혈관(MAP/HR)   (CKD→약물축적→혈역학)
  ② 혈당 동역학 (경구혈당강하제 → glucose_mgdl 궤적)

설계 원칙: 순수 numpy·CPU(GPU 불요·운영 LLM 무경합), 비식별 입력, 결과 미저장,
결정적(난수 없음 — 재현성), 공인 참조범위 내 클램프. 참고용 — 임상 판단 대체 아님.

계약(트윈 합의): 입력 {age,sex,conditions[],vitals,meds[],egfr,weight_kg?,height_cm?,horizon_sec,tick_ms}
→ 출력 series{hr,map,sbp,dbp,rr,spo2,glucose_mgdl,cardiac_output_lmin} + drug_response[] + coupling[].
"""
from __future__ import annotations

import math

import numpy as np

DISCLAIMER = "참고용 0D 생리 시뮬레이션 · 임상 판단을 대체하지 않습니다."

# 참조범위(클램프) — 공인 생리 구간
_RANGE = {
    "hr": (30.0, 200.0), "sbp": (60.0, 250.0), "dbp": (30.0, 150.0),
    "map": (40.0, 180.0), "rr": (5.0, 45.0), "spo2": (60.0, 100.0),
    "glucose_mgdl": (40.0, 500.0), "cardiac_output_lmin": (1.5, 12.0),
}

# 신배설 심혈관 약물 (fe=신배설분율, klass, 효과부호) — eGFR→PK→cardio 커플링 대상
#   map_delta_max: 만성 정상용량의 정상 eGFR 기준 MAP 변화량(mmHg). 신기능저하 시 축적→효과 증폭.
_CARDIO_DRUGS = {
    "lisinopril":  {"fe": 0.60, "klass": "acei",    "map_delta_max": -12.0, "hr_delta_max": 1.0},
    "enalapril":   {"fe": 0.60, "klass": "acei",    "map_delta_max": -11.0, "hr_delta_max": 1.0},
    "ramipril":    {"fe": 0.60, "klass": "acei",    "map_delta_max": -12.0, "hr_delta_max": 1.0},
    "losartan":    {"fe": 0.10, "klass": "arb",     "map_delta_max": -10.0, "hr_delta_max": 1.0},
    "atenolol":    {"fe": 0.90, "klass": "bb",      "map_delta_max": -8.0,  "hr_delta_max": -12.0},
    "bisoprolol":  {"fe": 0.50, "klass": "bb",      "map_delta_max": -8.0,  "hr_delta_max": -11.0},
    "metoprolol":  {"fe": 0.10, "klass": "bb",      "map_delta_max": -8.0,  "hr_delta_max": -10.0},
    "hydrochlorothiazide": {"fe": 0.95, "klass": "diuretic", "map_delta_max": -9.0, "hr_delta_max": 1.0},
    "furosemide":  {"fe": 0.65, "klass": "diuretic", "map_delta_max": -7.0, "hr_delta_max": 1.5},
    "digoxin":     {"fe": 0.70, "klass": "glycoside", "map_delta_max": 0.0, "hr_delta_max": -8.0},
    "amlodipine":  {"fe": 0.10, "klass": "ccb",     "map_delta_max": -10.0, "hr_delta_max": 2.0},
}
# 경구혈당강하제 — glucose 커플링 대상 (정상 eGFR 기준 정상상태 강하율, onset)
_GLUCOSE_DRUGS = {
    "metformin":    {"drop_frac": 0.12, "onset_min": 60, "fe": 0.90},
    "glimepiride":  {"drop_frac": 0.18, "onset_min": 30, "fe": 0.60},
    "gliclazide":   {"drop_frac": 0.17, "onset_min": 30, "fe": 0.20},
    "sitagliptin":  {"drop_frac": 0.10, "onset_min": 60, "fe": 0.80},
    "empagliflozin": {"drop_frac": 0.11, "onset_min": 60, "fe": 0.50},
    "insulin":      {"drop_frac": 0.25, "onset_min": 15, "fe": 0.0},
}


# 약물명 정규화 — 한글 일반명·국내 흔한 브랜드 → 영문 INN(커플링 테이블 키).
#   트윈 실데이터 플래그(2026-06-11): 한국 HIS 약물명이 한글/브랜드/용량표기라 영문 INN 정확매칭 실패
#   → 커플링 빈 배열. AI가 약물지식 소유하므로 AI측 정규화로 봉합(트윈은 원문 그대로 전달 가능).
_DRUG_SYNONYMS = {
    # 심혈관 (한글 일반명)
    "리시노프릴": "lisinopril", "에날라프릴": "enalapril", "라미프릴": "ramipril",
    "로사르탄": "losartan", "아테놀롤": "atenolol", "비소프롤롤": "bisoprolol",
    "메토프롤롤": "metoprolol", "히드로클로로티아지드": "hydrochlorothiazide",
    "하이드로클로로티아지드": "hydrochlorothiazide", "푸로세미드": "furosemide",
    "퓨로세미드": "furosemide", "디곡신": "digoxin", "암로디핀": "amlodipine",
    # 혈당 (한글 일반명)
    "메트포르민": "metformin", "메트포민": "metformin", "글리메피리드": "glimepiride",
    "글리클라지드": "gliclazide", "시타글립틴": "sitagliptin",
    "엠파글리플로진": "empagliflozin", "인슐린": "insulin",
    # 흔한 브랜드(영문) → INN
    "glucophage": "metformin", "diabex": "metformin", "amaryl": "glimepiride",
    "januvia": "sitagliptin", "jardiance": "empagliflozin", "lasix": "furosemide",
    "norvasc": "amlodipine", "zestril": "lisinopril", "cozaar": "losartan",
    "tenormin": "atenolol", "lanoxin": "digoxin",
}
# 용량·제형 토큰 제거용 (정규화 시 약물명만 추출)
_DOSE_TOKEN = __import__("re").compile(
    r"\b\d+(\.\d+)?\s*(mg|mcg|µg|g|ml|iu|단위|정|캡슐|tab|cap|q\d+h|bid|tid|qd|qhs|po|iv)\b", flags=__import__("re").I)


def _normalize_drug(raw):
    """약물명 → 커플링 테이블 키(영문 INN). 한글/브랜드/용량표기 흡수.
    매칭 실패 시 소문자 원문 반환(테이블 미스→커플링 미발화는 정직한 결과)."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    s = s.split("(")[0].strip()              # 괄호주석 제거 "metformin (glucophage)"
    s = _DOSE_TOKEN.sub(" ", s)              # 용량/제형 토큰 제거
    s = s.replace("-", " ").strip()
    # 1) 전체 문자열 동의어 직매칭
    if s in _DRUG_SYNONYMS:
        return _DRUG_SYNONYMS[s]
    # 2) 토큰별(한글/영문 혼재 "메트포르민 500mg" 등)
    for tok in s.split():
        tok = tok.strip(".,;")
        if tok in _DRUG_SYNONYMS:
            return _DRUG_SYNONYMS[tok]
        if tok in _CARDIO_DRUGS or tok in _GLUCOSE_DRUGS:
            return tok
    return s.split()[0] if s.split() else s   # 미매칭=첫 토큰(테이블 미스 시 커플링 미발화)


def _clamp(name, arr):
    lo, hi = _RANGE[name]
    return np.clip(arr, lo, hi)


def _est_weight_height(sex, age):
    """체중·키 HIS 미노출 → 성/연령 표준 추정 폴백 (트윈 reply3 §4 확정)."""
    male = str(sex or "").lower().startswith("m")
    w, h = (70.0, 170.0) if male else (58.0, 160.0)
    if age and float(age) >= 70:   # 고령 근감소 보정(경미)
        w -= 3.0
    return w, h


def _egfr_accum_factor(egfr, fe):
    """eGFR 보정 약물 축적비. 신기능저하(eGFR↓)·신배설분율↑일수록 정상상태 농도↑(축적).
    정상 eGFR≈90에서 1.0, 저하 시 >1.0. CKD→약물축적의 정량."""
    egfr = max(5.0, min(150.0, float(egfr)))
    cl_rel = (1.0 - fe) + fe * (egfr / 90.0)   # 상대 클리어런스 (renal_pk와 동일 식)
    return 1.0 / max(cl_rel, 0.15)             # 농도 ∝ 1/CL → 축적비


def simulate(payload: dict) -> dict:
    """werub-physio-v1 메인 — 비식별 입력 → 다계통 시계열 + 커플링."""
    age = payload.get("age")
    sex = payload.get("sex", "")
    conditions = [str(c).lower() for c in (payload.get("conditions") or [])]
    # 약물: (정규화 INN, 원문) — 한글/브랜드/용량표기 정규화(트윈 한국 HIS 대응)
    meds = [(_normalize_drug(m), str(m)) for m in (payload.get("meds") or [])]
    vitals = payload.get("vitals") or {}
    egfr = payload.get("egfr")
    egfr = float(egfr) if egfr not in (None, "") else 90.0

    horizon_sec = float(payload.get("horizon_sec", 60) or 60)
    horizon_sec = max(5.0, min(600.0, horizon_sec))
    tick_ms = float(payload.get("tick_ms", 200) or 200)
    tick_ms = max(50.0, min(1000.0, tick_ms))
    n = int(round(horizon_sec * 1000.0 / tick_ms))
    n = max(2, min(6000, n))
    t = np.arange(n) * (tick_ms / 1000.0)   # 초

    # 체중·키 (있으면 사용, 없으면 표준 추정)
    weight = payload.get("weight_kg")
    height = payload.get("height_cm")
    est_w, est_h = _est_weight_height(sex, age)
    weight = float(weight) if weight not in (None, "") else est_w
    height = float(height) if height not in (None, "") else est_h
    weight_estimated = payload.get("weight_kg") in (None, "")

    # 베이스라인 (실측 vital 우선)
    hr0 = float(vitals.get("hr", 72) or 72)
    sbp0 = float(vitals.get("sbp", 120) or 120)
    dbp0 = float(vitals.get("dbp", 78) or 78)
    rr0 = float(vitals.get("rr", 14) or 14)
    spo20 = float(vitals.get("spo2", 98) or 98)
    map0 = dbp0 + (sbp0 - dbp0) / 3.0
    sv = max(40.0, min(110.0, weight * 1.0))      # 1구획 근사 SV≈체중(mL)
    co0 = sv * hr0 / 1000.0                         # Windkessel CO(L/min)

    # 혈당 베이스라인 (실측 없으면 진단 기반)
    glucose0 = payload.get("glucose")
    if glucose0 in (None, ""):
        diabetic = any("diabet" in c or "dm" in c for c in conditions)
        glucose0 = 158.0 if diabetic else 95.0
    glucose0 = float(glucose0)

    # ── 결정적 생리 진동(난수 없음) ─────────────────────────────
    resp_hz = rr0 / 60.0
    rsa = 2.0 * np.sin(2.0 * np.pi * resp_hz * t)        # 호흡성 동성부정맥(HR ±2)
    mayer = 3.0 * np.sin(2.0 * np.pi * 0.1 * t)          # Mayer파(BP ±3, 0.1Hz)
    resp_osc = 0.6 * np.sin(2.0 * np.pi * resp_hz * t)

    # ── 커플링 ① eGFR→PK→심혈관 ────────────────────────────────
    cardio_map = np.zeros(n)
    cardio_hr = np.zeros(n)
    drug_response = []
    coupling_active = []
    for inn, orig in meds:
        d = _CARDIO_DRUGS.get(inn)
        if not d:
            continue
        acc = _egfr_accum_factor(egfr, d["fe"])          # 신기능 축적비(정상 eGFR≈1.0, CKD>1)
        amp = min(acc, 2.0)                                 # 효과 증폭(정상=라벨효과, CKD 최대 2배 캡)
        # 윈도우 내 미세 정착(느린 onset 램프) — 60s 내 ~정착
        settle = 1.0 - 0.15 * np.exp(-t / 30.0)
        cardio_map += d["map_delta_max"] * amp * settle
        cardio_hr += d["hr_delta_max"] * amp * settle
        eff_map = round(d["map_delta_max"] * amp, 1)
        eff_hr = round(d["hr_delta_max"] * amp, 1)
        entry = {
            "drug": inn, "klass": d["klass"],
            "effect": (f"map_{eff_map:+.0f}mmHg" if d["map_delta_max"] else f"hr_{eff_hr:+.0f}bpm"),
            "egfr_accumulation": round(acc, 2),
            "note": "eGFR 저하 시 축적→효과 증폭" if acc > 1.15 else "정상 신기능 기준",
        }
        if orig.lower() != inn:
            entry["matched_from"] = orig         # 정규화 출처(한글/브랜드 원문)
        drug_response.append(entry)
        if "egfr_pk_cardio" not in coupling_active:
            coupling_active.append("egfr_pk_cardio")

    # ── 커플링 ② 혈당 동역학 ───────────────────────────────────
    glucose = np.full(n, glucose0)
    for inn, orig in meds:
        g = _GLUCOSE_DRUGS.get(inn)
        if not g:
            continue
        # eGFR 축적이 큰 약(metformin 등)은 강하 효과 소폭 증폭
        acc = _egfr_accum_factor(egfr, g["fe"])
        target = glucose0 * (1.0 - g["drop_frac"] * min(1.4, acc))
        tau_s = g["onset_min"] * 60.0
        glucose = target + (glucose - target) * np.exp(-t / tau_s)
        entry = {
            "drug": inn, "klass": "antidiabetic",
            "effect": f"glucose_-{round(g['drop_frac']*100)}%",
            "onset_min": g["onset_min"],
        }
        if orig.lower() != inn:
            entry["matched_from"] = orig
        drug_response.append(entry)
        if "glucose" not in coupling_active:
            coupling_active.append("glucose")

    # ── 계통 합성 ──────────────────────────────────────────────
    hr = hr0 + rsa + cardio_hr
    map_ = map0 + mayer + cardio_map
    # sbp/dbp를 MAP 변화에 비례 추종(맥압 유지)
    pulse_press = sbp0 - dbp0
    dbp = map_ - pulse_press / 3.0
    sbp = dbp + pulse_press
    rr = rr0 + resp_osc
    spo2 = spo20 - 0.3 * (np.maximum(0.0, rr0 - rr))     # 환기↓ 시 미세 SpO2↓
    co = sv * hr / 1000.0                                  # CO는 HR 추종

    series = {
        "hr": _clamp("hr", hr),
        "map": _clamp("map", map_),
        "sbp": _clamp("sbp", sbp),
        "dbp": _clamp("dbp", dbp),
        "rr": _clamp("rr", rr),
        "spo2": _clamp("spo2", spo2),
        "glucose_mgdl": _clamp("glucose_mgdl", glucose),
        "cardiac_output_lmin": _clamp("cardiac_output_lmin", co),
    }
    series = {k: [round(float(x), 2) for x in v] for k, v in series.items()}

    return {
        "model": "werub-physio-v1",
        "horizon_sec": horizon_sec, "tick_ms": tick_ms, "n_points": n,
        "series": series,
        "drug_response": drug_response,
        "coupling": coupling_active,
        "inputs_resolved": {
            "egfr": egfr, "weight_kg": weight, "height_cm": height,
            "weight_estimated": weight_estimated, "sv_ml": round(sv, 1),
        },
        "disclaimer": DISCLAIMER,
    }
