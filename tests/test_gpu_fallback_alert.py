"""GPU CPU-폴백 health 경보 — 순수 판정 회귀 (v2.124.33).

2026-07-25 인시던트: 무인 드라이버 업그레이드→커널 모듈 미리로드→nvidia-smi mismatch→
ollama가 로드 모델을 CPU 폴백(size_vram=0)했으나 /api/health는 healthy로 오인. GPU 사용
여부를 stats_history(model_vram_ratio) + _perf_alerts로 명시 판정.
"""
from routes.system import _gpu_fallback_finding


def _min_vram_ratio(models):
    # app._min_vram_ratio 로직 미러(app 모듈은 requests 의존이라 로직만 검증).
    ratios = []
    for m in models or []:
        size = m.get("size") or 0
        if size > 0:
            ratios.append((m.get("size_vram") or 0) / size)
    return round(min(ratios), 3) if ratios else None


# ── _min_vram_ratio ──
def test_ratio_full_gpu():
    assert _min_vram_ratio([{"size": 100, "size_vram": 100}]) == 1.0


def test_ratio_cpu_fallback():
    assert _min_vram_ratio([{"size": 100, "size_vram": 0}]) == 0.0


def test_ratio_partial_and_min_across_models():
    # 여러 모델 중 최악(최소) 비율 채택.
    assert _min_vram_ratio([{"size": 100, "size_vram": 100},
                            {"size": 100, "size_vram": 40}]) == 0.4


def test_ratio_none_when_empty_or_no_size():
    assert _min_vram_ratio([]) is None
    assert _min_vram_ratio([{"size": 0, "size_vram": 0}]) is None


# ── _gpu_fallback_finding ──
def test_no_resident_no_finding():
    assert _gpu_fallback_finding(None, None, 10000) is None
    assert _gpu_fallback_finding("", 0.0, 10000) is None


def test_full_gpu_no_finding():
    assert _gpu_fallback_finding("qwen2.5:14b", 1.0, 10000) is None


def test_cpu_fallback_is_critical():
    f = _gpu_fallback_finding("qwen2.5:14b", 0.0, 0)
    assert f is not None and f[2] == "critical" and "CPU" in f[0]


def test_partial_offload_is_warning():
    f = _gpu_fallback_finding("qwen2.5:14b", 0.3, 12000, ratio_min=0.5)
    assert f is not None and f[2] == "warning" and "Partial" in f[0]


def test_partial_above_threshold_no_finding():
    assert _gpu_fallback_finding("qwen2.5:14b", 0.8, 12000, ratio_min=0.5) is None


def test_nvidia_smi_unavailable_but_resident_warns():
    # vram_used 없음(nvidia-smi 실패) + 비율 불명 + 모델 상주 → 조기 경보(드라이버 의심).
    f = _gpu_fallback_finding("qwen2.5:14b", None, None)
    assert f is not None and f[2] == "warning" and "Unavailable" in f[0]


def test_ratio_unknown_but_smi_ok_no_finding():
    # nvidia-smi는 정상(vram_used 있음)인데 비율만 불명 → 판정 보류(오탐 방지).
    assert _gpu_fallback_finding("qwen2.5:14b", None, 10000) is None
