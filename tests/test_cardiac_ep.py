"""아바타 R3 — 심장 EP 표면 대리모델(eikonal LAT + 12-lead ECG) 단위테스트.

순수 numpy(세그/메시 IO 비의존) — iMac 메인 venv에서 실행 가능.
"""
import math

import numpy as np
import pytest

from core import cardiac_ep_model as ep


def _ellipsoid(nlat=18, nlon=24, a=30.0, b=30.0, c=45.0):
    """파라메트릭 타원체(닫힌 표면) 메시 — 심장 장축(c) 근사. (vertices, faces)."""
    verts, faces = [], []
    for i in range(nlat + 1):
        w = math.pi * i / nlat            # 0..pi (극)
        for j in range(nlon):
            u = 2 * math.pi * j / nlon
            verts.append((a * math.sin(w) * math.cos(u),
                          b * math.sin(w) * math.sin(u),
                          c * math.cos(w)))
    V = np.array(verts, dtype=float)

    def idx(i, j):
        return i * nlon + (j % nlon)

    for i in range(nlat):
        for j in range(nlon):
            v00, v01, v10, v11 = idx(i, j), idx(i, j + 1), idx(i + 1, j), idx(i + 1, j + 1)
            faces.append((v00, v10, v11))
            faces.append((v00, v11, v01))
    return V, np.array(faces, dtype=int)


def test_lat_basic_shape_and_order():
    V, F = _ellipsoid()
    r = ep.compute_lat(V, F)
    assert r["lat_ms"].shape[0] == len(V)        # 정점 정렬 보장(순서·개수 동일)
    assert np.all(np.isfinite(r["lat_ms"]))
    assert r["min_ms"] == pytest.approx(0.0, abs=1e-6)
    assert r["max_ms"] > 0
    assert r["unreachable"] == 0                  # 닫힌 표면=단일 컴포넌트
    # 개시 정점의 LAT ≈ 0
    assert r["lat_ms"][r["init_index"]] == pytest.approx(0.0, abs=1e-6)


def test_lat_cv_scaling_inverse():
    V, F = _ellipsoid()
    fast = ep.compute_lat(V, F, cv_mm_per_ms=1.0)
    slow = ep.compute_lat(V, F, cv_mm_per_ms=0.5)
    assert slow["max_ms"] > fast["max_ms"] * 1.5   # CV 절반 → LAT 약 2배


def test_condition_lvh_slows_conduction():
    V, F = _ellipsoid()
    base = ep.compute_lat(V, F)
    lvh = ep.compute_lat(V, F, conditions=["lvh"])
    assert lvh["max_ms"] > base["max_ms"]          # CV 둔화 → 더 넓은 활성
    assert lvh["cv_mm_per_ms"] < base["cv_mm_per_ms"]


def test_condition_bbb_adds_wall_delay():
    V, F = _ellipsoid()
    base = ep.compute_lat(V, F)
    lbbb = ep.compute_lat(V, F, conditions=["lbbb"])
    assert lbbb["max_ms"] > base["max_ms"] + 40    # 벽 지연 주입(≈55ms)


def test_apex_selection_in_range():
    V, F = _ellipsoid()
    idx = ep.select_apex(V)
    assert 0 <= idx < len(V)
    # 타원체 장축은 z(c=45) → apex는 극(|z| 최대) 근처
    assert abs(V[idx][2]) > 35


def test_multisite_initiation():
    """v1.1: 다중 개시부 — LAT==0 정점 수 = 개시부 수(>1, 단일 bullseye 아님)."""
    V, F = _ellipsoid()
    r = ep.compute_lat(V, F, n_init_sites=5)
    assert r["n_init_sites"] > 1
    assert len(r["init_sites"]) == r["n_init_sites"]
    assert int((r["lat_ms"] == 0).sum()) == r["n_init_sites"]   # 시드만 t=0
    # 단일개시 강제 시 1점
    r1 = ep.compute_lat(V, F, n_init_sites=1)
    assert r1["n_init_sites"] == 1


def test_anisotropy_changes_field():
    """v1.1: 이방성 — ratio=1(등방) vs ratio=3 LAT 분포 상이, 종방향 빠름."""
    V, F = _ellipsoid()
    iso = ep.compute_lat(V, F, cv_mm_per_ms=1.0, anisotropy_ratio=1.0, n_init_sites=1)
    aniso = ep.compute_lat(V, F, cv_mm_per_ms=1.0, anisotropy_ratio=3.0, n_init_sites=1)
    assert iso["cv_long"] == pytest.approx(iso["cv_trans"], rel=1e-6)   # 등방
    assert aniso["cv_long"] > aniso["cv_trans"] * 2                     # 이방
    assert not np.allclose(iso["lat_ms"], aniso["lat_ms"])             # 분포 변화


def test_fiber_field_unit_and_circumferential():
    """v1.1: 섬유장 — 단위벡터 + 반경방향에 대체로 직교(원주방향)."""
    V, F = _ellipsoid()
    f = ep.fiber_field(V)
    norms = np.linalg.norm(f, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-6)
    axis, c = ep._principal_axis(V)
    rel = V - c
    radial = rel - np.outer(rel @ axis, axis)
    rn = np.linalg.norm(radial, axis=1, keepdims=True)
    radial_u = np.divide(radial, rn, out=np.zeros_like(radial), where=rn > 1e-6)
    dot = np.abs(np.sum(f * radial_u, axis=1))
    assert dot[rn[:, 0] > 1e-3].mean() < 0.2          # 반경방향과 대체로 직교


def test_ecg_shape_and_leads():
    V, F = _ellipsoid()
    lat = ep.compute_lat(V, F)["lat_ms"]
    ecg = ep.synthesize_ecg_12lead(V, lat, hr_bpm=75, fs_hz=500, duration_ms=2500)
    assert set(ecg["leads"]) == set(ep.LEAD_ORDER)
    T = int(round(2500 / (1000.0 / 500)))
    for name, sig in ecg["leads"].items():
        assert len(sig) == T
        assert all(math.isfinite(x) for x in sig)
    assert ecg["n_beats"] == pytest.approx(2500 / (60000 / 75), abs=1)
    assert ecg["approximate"] is True


def test_ecg_einthoven_law():
    """II = I + III (Einthoven) — 리드 방향 설계상 정확히 성립."""
    V, F = _ellipsoid()
    lat = ep.compute_lat(V, F)["lat_ms"]
    ecg = ep.synthesize_ecg_12lead(V, lat)
    I = np.array(ecg["leads"]["I"])
    II = np.array(ecg["leads"]["II"])
    III = np.array(ecg["leads"]["III"])
    assert np.allclose(II, I + III, atol=1e-3)


def test_ecg_hr_changes_beat_count():
    V, F = _ellipsoid()
    lat = ep.compute_lat(V, F)["lat_ms"]
    slow = ep.synthesize_ecg_12lead(V, lat, hr_bpm=50, duration_ms=3000)
    fast = ep.synthesize_ecg_12lead(V, lat, hr_bpm=120, duration_ms=3000)
    assert fast["n_beats"] > slow["n_beats"]


def test_empty_inputs_graceful():
    r = ep.compute_lat(np.zeros((0, 3)), np.zeros((0, 3), int))
    assert r["lat_ms"].shape[0] == 0
    ecg = ep.synthesize_ecg_12lead(np.zeros((0, 3)), np.zeros(0))
    assert ecg["n_beats"] == 0
    assert all(len(v) > 0 for v in ecg["leads"].values())
