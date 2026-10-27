"""아바타 R3 — 심장 전기생리(EP) 표면 대리모델(eikonal LAT + 현상학적 12-lead ECG).

**순수 numpy**(세그멘테이션/메시 IO 비의존) — iMac 메인 venv에서 단위테스트 가능.
무거운 openCARP 체적 솔브 대신 **심장 표면 메시 정점 위에서 직접** 활성화를 전파한다:

  LAT[v] = (init 부위로부터의 측지거리) / 전도속도(CV)        # eikonal 근사

→ 트윈이 요구한 **정점 정렬**이 구조적으로 보장된다(LAT가 표면 메시의 그 정점에서 바로
계산되므로 재매핑이 없다). 12-lead ECG는 활성화 시퀀스로부터 이동 쌍극자(moving dipole)
forward 합성 — **현상학적 근사**(환자별 흉곽 기하 아님)이며 진단용이 아니다(자문 보조).

좌표: LAT는 측지거리라 좌표 불변. ECG는 해부 방향이 필요 → 호출부가 body frame
(x=left, y=foot, z=anterior)으로 정렬한 정점 사본을 넘긴다(없으면 PCA 휴리스틱 폴백).
"""
from __future__ import annotations

import heapq
import math

import numpy as np

# ── EP 파라미터(생리 범위 근사) — v1.1: 다중개시 + 이방성 ────────────────────
# 유효 표면 전도속도(순수 심근 + Purkinje 빠른 심내막 전파 등가). 다중개시로
# 단일개시 과대평가가 해소돼 더 생리적인 종/횡 이방을 둔다. 자문 보조 근사.
DEFAULT_CV_LONG_MM_PER_MS = 1.2    # 섬유 종방향(빠름)
DEFAULT_CV_TRANS_MM_PER_MS = 0.7   # 섬유 횡방향(느림)
DEFAULT_ANISO_RATIO = DEFAULT_CV_LONG_MM_PER_MS / DEFAULT_CV_TRANS_MM_PER_MS
DEFAULT_CV_MM_PER_MS = (DEFAULT_CV_LONG_MM_PER_MS * DEFAULT_CV_TRANS_MM_PER_MS) ** 0.5  # 기하평균(하위호환)
DEFAULT_N_INIT_SITES = 5           # 심첨부 다중 breakthrough(His-Purkinje 등가)
DEFAULT_APICAL_FRAC = 0.28         # 개시부 후보=장축 하단 비율(심첨 1/4 근처)
DEFAULT_APD_MS = 280.0          # 기준 활동전위지속(repolarization 시점 결정)
APD_GRADIENT = 0.35             # APD = APD0 - g*(lat-mean): 늦게 활성=짧은 APD→T 동심
QRS_ONSET_MS = 220.0           # 한 주기 내 QRS 시작(앞에 P 여유)
P_CENTER_MS = 90.0             # P파 중심(주기 시작 기준), PR≈130ms
DEPOL_SIGMA_MS = 5.0           # 탈분극 쌍극자 시간 폭
REPOL_SIGMA_MS = 22.0          # 재분극(완만)
P_SIGMA_MS = 14.0

# 병태 프리셋 — 표면 대리 근사(BBB=벽 지연, 비대=CV 둔화). 진단 아님.
CONDITION_PRESETS = {
    "lbbb": {"wall_delay_ms": 55.0, "wall": "left"},
    "rbbb": {"wall_delay_ms": 50.0, "wall": "right"},
    "lvh": {"cv_scale": 0.85},
    "rvh": {"cv_scale": 0.88},
    "lqts": {"apd_scale": 1.4},     # QT 연장
}


# ── LAT (eikonal: 측지거리 / CV) ─────────────────────────────────────────────
def _principal_axis(vertices: np.ndarray):
    """PCA 최대분산 축(심장 장축 ≈ apex-base) 단위벡터 반환."""
    c = vertices.mean(axis=0)
    cov = np.cov((vertices - c).T)
    w, v = np.linalg.eigh(cov)
    return v[:, int(np.argmax(w))], c


def fiber_field(vertices: np.ndarray) -> np.ndarray:
    """규칙기반 섬유 방향장(원주방향 휴리스틱) — LDRB 없이 표면 이방성 근사.

    심실 중벽 섬유는 대체로 원주방향(circumferential). f = norm(장축 × 반경방향).
    축상(반경≈0) 정점은 장축으로 폴백. 단위벡터[N,3] 반환.
    """
    axis, c = _principal_axis(vertices)
    rel = vertices - c
    radial = rel - np.outer(rel @ axis, axis)          # 장축 성분 제거 → 반경방향
    rn = np.linalg.norm(radial, axis=1, keepdims=True)
    radial_u = np.divide(radial, rn, out=np.zeros_like(radial), where=rn > 1e-9)
    f = np.cross(np.broadcast_to(axis, vertices.shape), radial_u)   # 원주방향
    fn = np.linalg.norm(f, axis=1, keepdims=True)
    f = np.divide(f, fn, out=np.zeros_like(f), where=fn > 1e-9)
    f[fn[:, 0] < 1e-6] = axis                           # 축상 degenerate 폴백
    return f


def _aniso_adjacency(vertices, faces, fiber, cv_long, cv_trans):
    """삼각망 → 인접 리스트. 가중치=변길이/이방성속도(타원형 eikonal).

    방향 d, 섬유 f에 대해 1/v² = cos²/cv_long² + (1-cos²)/cv_trans² (cos=|d·f|).
    종방향(섬유 따라) 빠르고 횡방향 느림. f는 양 끝점 섬유의 평균.
    """
    n = len(vertices)
    adj = [[] for _ in range(n)]
    seen = set()
    cl2, ct2 = cv_long * cv_long, cv_trans * cv_trans
    for tri in faces:
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        for i, j in ((a, b), (b, c), (c, a)):
            key = (i, j) if i < j else (j, i)
            if key in seen:
                continue
            seen.add(key)
            seg = vertices[j] - vertices[i]
            L = float(np.linalg.norm(seg))
            if L <= 1e-9:
                continue
            d = seg / L
            fe = fiber[i] + fiber[j]
            fn = np.linalg.norm(fe)
            cos2 = float((d @ (fe / fn)) ** 2) if fn > 1e-9 else 0.0
            v = 1.0 / np.sqrt(cos2 / cl2 + (1.0 - cos2) / ct2)
            w = L / v
            adj[i].append((j, w))
            adj[j].append((i, w))
    return adj


def select_apex(vertices: np.ndarray) -> int:
    """장축 양 극단 중 단면이 더 좁은 쪽(뾰족한 apex)을 활성 개시 정점으로 선택."""
    axis, c = _principal_axis(vertices)
    proj = (vertices - c) @ axis
    lo_mask = proj <= np.percentile(proj, 6)
    hi_mask = proj >= np.percentile(proj, 94)

    def _spread(mask):
        pts = vertices[mask]
        if len(pts) < 2:
            return np.inf
        off = pts - pts.mean(axis=0)
        off = off - np.outer(off @ axis, axis)   # 축 제거 → 단면 퍼짐
        return float(np.linalg.norm(off, axis=1).mean())

    end_mask = lo_mask if _spread(lo_mask) <= _spread(hi_mask) else hi_mask
    idxs = np.where(end_mask)[0]
    # 그 극단의 무게중심에 가장 가까운 정점
    sub = vertices[idxs]
    apex_local = int(np.argmin(np.linalg.norm(sub - sub.mean(axis=0), axis=1)))
    return int(idxs[apex_local])


def select_init_sites(vertices: np.ndarray, n_sites: int = DEFAULT_N_INIT_SITES,
                      apical_frac: float = DEFAULT_APICAL_FRAC) -> list:
    """심첨부 다중 개시부(His-Purkinje breakthrough 등가) 선택.

    심첨(apex)을 첫 site로, 심첨 영역(장축 하단 apical_frac)에서 farthest-point
    sampling으로 단축을 가로질러 분산 배치 → 단일 점 bullseye 대신 RV/LV 심첨
    near-simultaneous breakthrough 근사. 정점 인덱스 리스트 반환.
    """
    n = len(vertices)
    apex = select_apex(vertices)
    if n_sites <= 1 or n < 4:
        return [apex]
    axis, c = _principal_axis(vertices)
    proj = (vertices - c) @ axis
    apex_proj = proj[apex]
    span = float(proj.max() - proj.min()) or 1.0
    # 심첨이 있는 극단 방향으로 apical_frac 이내 후보
    if apex_proj < proj.mean():
        region = np.where(proj <= proj.min() + apical_frac * span)[0]
    else:
        region = np.where(proj >= proj.max() - apical_frac * span)[0]
    if len(region) <= n_sites:
        return sorted({apex, *region.tolist()})
    # farthest-point sampling(apex 시드) — 후보들 사이 최대 분산
    pts = vertices[region]
    chosen = [int(np.where(region == apex)[0][0])] if apex in region else [0]
    dmin = np.linalg.norm(pts - pts[chosen[0]], axis=1)
    while len(chosen) < n_sites:
        nxt = int(np.argmax(dmin))
        chosen.append(nxt)
        dmin = np.minimum(dmin, np.linalg.norm(pts - pts[nxt], axis=1))
    sites = sorted({apex, *region[chosen].tolist()})
    return sites


def _dijkstra(adj, sources) -> np.ndarray:
    """다중소스 Dijkstra(heapq) → 각 정점까지 최단 측지거리(mm)."""
    n = len(adj)
    dist = np.full(n, np.inf)
    pq = []
    for s in sources:
        dist[s] = 0.0
        heapq.heappush(pq, (0.0, s))
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist


def compute_lat(vertices, faces, cv_mm_per_ms: float = None,
                cv_long: float = None, cv_trans: float = None,
                anisotropy_ratio: float = DEFAULT_ANISO_RATIO,
                n_init_sites: int = DEFAULT_N_INIT_SITES,
                conditions=None, init_sites=None) -> dict:
    """표면 정점별 국소활성시각(LAT, ms). 정점 순서 = 입력 vertices 순서(정렬 보장).

    v1.1: **다중 개시부**(심첨 breakthrough 등가) + **이방성 전도**(섬유 종>횡).
    속도 지정: cv_long/cv_trans 직접 | cv_mm_per_ms(기하평균)+anisotropy_ratio | 기본값.
    반환: {lat_ms float32[N], min_ms, max_ms, init_index, init_sites,
           cv_long, cv_trans, cv_mm_per_ms, n_init_sites, unreachable}
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces)
    n = len(vertices)
    conditions = [str(x).lower() for x in (conditions or [])]

    # 속도 결정(하위호환: cv_mm_per_ms=기하평균)
    if cv_long is None or cv_trans is None:
        if cv_mm_per_ms is not None:
            r = max(float(anisotropy_ratio), 1.0) ** 0.5
            cv_long, cv_trans = float(cv_mm_per_ms) * r, float(cv_mm_per_ms) / r
        else:
            cv_long, cv_trans = DEFAULT_CV_LONG_MM_PER_MS, DEFAULT_CV_TRANS_MM_PER_MS
    cv_scale = 1.0
    for cond in conditions:
        cv_scale *= CONDITION_PRESETS.get(cond, {}).get("cv_scale", 1.0)
    cv_long = max(cv_long * cv_scale, 0.05)
    cv_trans = max(cv_trans * cv_scale, 0.05)
    cv_mean = (cv_long * cv_trans) ** 0.5

    if n == 0 or len(faces) == 0:
        return {"lat_ms": np.zeros(0, np.float32), "min_ms": 0.0, "max_ms": 0.0,
                "init_index": -1, "init_sites": [], "cv_long": cv_long,
                "cv_trans": cv_trans, "cv_mm_per_ms": cv_mean,
                "n_init_sites": 0, "unreachable": 0}

    fiber = fiber_field(vertices)
    adj = _aniso_adjacency(vertices, faces, fiber, cv_long, cv_trans)
    if init_sites is None:
        init_sites = select_init_sites(vertices, n_sites=n_init_sites)
    init_sites = [int(s) for s in init_sites]
    lat = _dijkstra(adj, init_sites)   # 이방성 가중 → 시간(ms) 직접

    # 고립 정점(비연결 컴포넌트) → 최대 도달시간으로 클램프(무한 방지)
    reachable = np.isfinite(lat)
    unreachable = int((~reachable).sum())
    fill = lat[reachable].max() if reachable.any() else 0.0
    lat = np.where(reachable, lat, fill)

    # 병태: 벽 지연(BBB) — 장축에 직교하는 단축으로 좌/우 반 식별 후 지연 주입
    delay_cfg = next((CONDITION_PRESETS[c] for c in conditions
                      if "wall_delay_ms" in CONDITION_PRESETS.get(c, {})), None)
    if delay_cfg:
        axis, c0 = _principal_axis(vertices)
        rem = (vertices - c0) - np.outer((vertices - c0) @ axis, axis)
        cov = np.cov(rem.T)
        w_, v_ = np.linalg.eigh(cov)
        short = v_[:, int(np.argmax(w_))]
        side = (vertices - c0) @ short
        sel = side < 0 if delay_cfg["wall"] == "left" else side > 0
        lat = lat.copy()
        lat[sel] += float(delay_cfg["wall_delay_ms"])

    lat = lat - lat.min()
    return {"lat_ms": lat.astype(np.float32), "min_ms": float(lat.min()),
            "max_ms": float(lat.max()), "init_index": int(init_sites[0]),
            "init_sites": init_sites, "cv_long": round(cv_long, 4),
            "cv_trans": round(cv_trans, 4), "cv_mm_per_ms": round(cv_mean, 4),
            "n_init_sites": len(init_sites), "unreachable": unreachable}


# ── 12-lead ECG (이동 쌍극자 forward 합성 — 현상학적 근사) ─────────────────────
# 전두면 림프 리드 방향(x=left, y=foot, z=anterior)
_LIMB_DIRS = {
    "I": (1.0, 0.0, 0.0),
    "II": (0.5, math.sin(math.radians(60)), 0.0),
    "III": (-0.5, math.sin(math.radians(60)), 0.0),
    "aVR": (-math.cos(math.radians(30)), -0.5, 0.0),
    "aVL": (math.cos(math.radians(30)), -0.5, 0.0),
    "aVF": (0.0, 1.0, 0.0),
}
# 흉부 리드(횡단면 x=left, z=anterior 근사: V1 우전 → V6 좌측)
_PRECORDIAL_DEG = {"V1": 150, "V2": 120, "V3": 100, "V4": 75, "V5": 45, "V6": 15}
_PRECORDIAL_DIRS = {k: (math.cos(math.radians(a)), 0.0, math.sin(math.radians(a)))
                    for k, a in _PRECORDIAL_DEG.items()}
LEAD_ORDER = ["I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]


def _gauss_kernel(sigma_ms: float, dt_ms: float) -> np.ndarray:
    half = max(1, int(round(3 * sigma_ms / dt_ms)))
    x = np.arange(-half, half + 1) * dt_ms
    k = np.exp(-0.5 * (x / sigma_ms) ** 2)
    return k / k.sum()


def _accumulate(pos, weight, times_ms, T, dt_ms):
    """이벤트(정점 위치 pos[N,3], 시각 times_ms[N])를 시간 빈에 가중 누적 → (3,T)."""
    bins = np.round(times_ms / dt_ms).astype(int)
    ok = (bins >= 0) & (bins < T)
    bins, p, w = bins[ok], pos[ok], weight[ok]
    out = np.zeros((3, T))
    for ax in range(3):
        np.add.at(out[ax], bins, p[:, ax] * w)
    return out


def synthesize_ecg_12lead(vertices_anat, lat_ms, hr_bpm: float = 75.0,
                          fs_hz: int = 500, duration_ms: int = 2500,
                          conditions=None) -> dict:
    """활성화 시퀀스 → 12-lead ECG(현상학적 근사, mV·진단 아님).

    vertices_anat: body frame(x=left,y=foot,z=anterior)으로 정렬된 정점[N,3].
    반환: {leads:{I..V6: [float]}, fs_hz, duration_ms, hr_bpm, n_beats, approximate:true}
    """
    v = np.asarray(vertices_anat, dtype=np.float64)
    lat = np.asarray(lat_ms, dtype=np.float64)
    conditions = [str(x).lower() for x in (conditions or [])]
    fs = int(fs_hz)
    dt = 1000.0 / fs
    T = max(1, int(round(duration_ms / dt)))
    if len(v) == 0:
        zeros = [0.0] * T
        return {"leads": {k: list(zeros) for k in LEAD_ORDER}, "fs_hz": fs,
                "duration_ms": duration_ms, "hr_bpm": hr_bpm, "n_beats": 0,
                "approximate": True}

    centroid = v.mean(axis=0)
    pos = v - centroid
    radius = np.linalg.norm(pos, axis=1).max() or 1.0
    pos = pos / radius                       # 무차원화
    w = np.ones(len(v))

    apd0 = DEFAULT_APD_MS
    for c in conditions:
        apd0 *= CONDITION_PRESETS.get(c, {}).get("apd_scale", 1.0)
    apd = apd0 - APD_GRADIENT * (lat - lat.mean())
    apd = np.clip(apd, 120.0, 600.0)

    rr = 60000.0 / max(hr_bpm, 20.0)
    onsets = np.arange(0.0, duration_ms, rr)

    hd = np.zeros((3, T))   # 탈분극(depol)
    hr_ = np.zeros((3, T))  # 재분극(repol)
    hp = np.zeros((3, T))   # P파(심방, 고정 벡터 근사)
    p_vec = np.array([0.35, 0.55, -0.1])    # 정상 P축 ≈ 하좌
    for b in onsets:
        depol_t = b + QRS_ONSET_MS + lat
        hd += _accumulate(pos, w, depol_t, T, dt)
        repol_t = b + QRS_ONSET_MS + lat + apd
        hr_ += _accumulate(pos, w, repol_t, T, dt)
        pb = int(round((b + P_CENTER_MS) / dt))
        if 0 <= pb < T:
            hp[:, pb] += p_vec

    kd = _gauss_kernel(DEPOL_SIGMA_MS, dt)
    kr = _gauss_kernel(REPOL_SIGMA_MS, dt)
    kp = _gauss_kernel(P_SIGMA_MS, dt)
    M = np.zeros((3, T))
    for ax in range(3):
        M[ax] += np.convolve(hd[ax], kd, mode="same")
        M[ax] += -0.30 * np.convolve(hr_[ax], kr, mode="same")   # T파(동심·완만)
        M[ax] += 0.18 * np.convolve(hp[ax], kp, mode="same")     # P파

    # 정규화: 림프 리드 최대 |R| ≈ 1.0 mV가 되도록 스케일
    limb = np.array([np.array(d) @ M for d in _LIMB_DIRS.values()])
    scale = np.abs(limb).max() or 1.0
    M = M / scale

    leads = {}
    for name in LEAD_ORDER:
        d = _LIMB_DIRS.get(name) or _PRECORDIAL_DIRS[name]
        leads[name] = [round(float(x), 5) for x in (np.array(d) @ M)]
    return {"leads": leads, "fs_hz": fs, "duration_ms": duration_ms,
            "hr_bpm": float(hr_bpm), "n_beats": int(len(onsets)), "approximate": True}
