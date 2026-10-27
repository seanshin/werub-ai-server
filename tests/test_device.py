"""core/device.py 단위테스트 — cross-arch device 선택 단일 진실 공급원.

순수 로직(가용성 mock) — 네트워크 0, torch 실제 가속기 불필요.
어떤 머신(GPU/Linux, iMac/Mac)에서도 동일하게 통과해야 한다.
"""
import pytest

from core import device


@pytest.fixture
def fake_caps(monkeypatch):
    """cuda/mps 가용성을 자유롭게 주입하는 헬퍼."""
    def _set(cuda: bool, mps: bool):
        monkeypatch.setattr(device, "_cuda_available", lambda: cuda)
        monkeypatch.setattr(device, "_mps_available", lambda: mps)
    return _set


# --- resolve(): 선호 + 가용성 조합 ---

def test_cpu_preference_always_cpu(fake_caps):
    fake_caps(cuda=True, mps=True)
    assert device.resolve("cpu") == "cpu"


def test_cuda_when_available(fake_caps):
    fake_caps(cuda=True, mps=False)
    assert device.resolve("cuda") == "cuda"


def test_cuda_falls_back_to_cpu_when_absent(fake_caps):
    """iMac 등 CUDA 없는 머신에서 'cuda' 요청 시 크래시 대신 cpu 폴백."""
    fake_caps(cuda=False, mps=True)
    assert device.resolve("cuda") == "cpu"


def test_mps_when_available(fake_caps):
    fake_caps(cuda=False, mps=True)
    assert device.resolve("mps") == "mps"


def test_mps_falls_back_to_cpu_when_absent(fake_caps):
    fake_caps(cuda=False, mps=False)
    assert device.resolve("mps") == "cpu"


def test_auto_prefers_cuda(fake_caps):
    fake_caps(cuda=True, mps=True)
    assert device.resolve("auto") == "cuda"


def test_auto_uses_mps_when_no_cuda(fake_caps):
    fake_caps(cuda=False, mps=True)
    assert device.resolve("auto") == "mps"


def test_auto_cpu_when_nothing(fake_caps):
    fake_caps(cuda=False, mps=False)
    assert device.resolve("auto") == "cpu"


@pytest.mark.parametrize("bad", ["", None, "gpu", "xpu", "  CUDA  "])
def test_unknown_preference_is_safe(fake_caps, bad):
    """알 수 없는 값도 예외 없이 처리(대소문자/공백 정규화 포함)."""
    fake_caps(cuda=False, mps=False)
    assert device.resolve(bad) == "cpu"


@pytest.mark.parametrize("typo", ["gpu", "xpu", "cudaa", "Metal"])
def test_typo_never_grabs_accelerator(fake_caps, typo):
    """오타성 값은 가속기가 가용해도 cpu로 방어 — 잘못된 config가 GPU를 잡지 않도록."""
    fake_caps(cuda=True, mps=True)
    assert device.resolve(typo) == "cpu"


def test_empty_means_auto(fake_caps):
    """None/빈 문자열은 미설정=auto로 간주 → 가용 가속기 선택."""
    fake_caps(cuda=True, mps=False)
    assert device.resolve(None) == "cuda"
    assert device.resolve("") == "cuda"


def test_case_insensitive(fake_caps):
    fake_caps(cuda=True, mps=False)
    assert device.resolve("  CUDA ") == "cuda"


# --- pipeline_arg(): transformers 인자 매핑 ---

def test_pipeline_arg_mapping():
    assert device.pipeline_arg("cuda") == 0
    assert device.pipeline_arg("mps") == "mps"
    assert device.pipeline_arg("cpu") == -1
    assert device.pipeline_arg("unknown") == -1
    assert device.pipeline_arg(None) == -1


# --- info(): 진단 스냅샷 ---

def test_info_shape(fake_caps):
    fake_caps(cuda=False, mps=True)
    snap = device.info()
    assert set(snap) >= {"os", "arch", "is_arm", "cuda_available", "mps_available", "auto_device"}
    assert snap["auto_device"] == "mps"
