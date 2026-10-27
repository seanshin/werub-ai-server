"""테스트 공통 설정 — 어댑터 계약 라이브 대조 옵션 (기획 Phase 0-3).

기본은 **순수 테스트만** 돈다(모델 불요·빠름). 라이브 대조는 명시 opt-in:

    pytest tests/test_llm_adapter_contract.py --live
    pytest tests/test_llm_adapter_contract.py --live --adapter=litellm

⚠️ 라이브는 GPU를 쓴다. **평가 배치와 동시에 실행하지 말 것**
   (2026-08-10 실측: 배치를 돌리며 다른 GPU 작업을 겹쳐 배치가 38분간 멎었다).
"""
import os

# 공개판: 로컬 우회 기본값이 꺼졌다 — 테스트 클라이언트(127.0.0.1)를 로컬 관리자로 쓰는 기존 시험은 명시적으로 켠다.
# 우회가 꺼진 동작 자체는 test_public_edition_bypass.py 가 따로 시험한다.
os.environ.setdefault("AI_LOCAL_BYPASS", "1")

import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="라이브 엔진 대조 실행(GPU 사용). 미지정 시 순수 테스트만")
    parser.addoption("--adapter", default="ollama",
                     help="검증할 어댑터 — ollama(기준선) | litellm | …")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: 실제 엔진 호출(--live 필요)")
    config.addinivalue_line("markers", "contract: LLM 어댑터 계약(DEVELOPER_GUIDE §1)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="라이브 대조는 --live 필요(GPU 사용)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _engine_defaults_pin_deterministic(monkeypatch):
    """D2 핀의 `/api/show` 조회를 결정적으로 — 단위 테스트에서 네트워크 금지.

    스텁 = "Modelfile 자체 값 없음"(빈 parameters) ⇒ 라우터 페이로드에 핀이 **발동**한다.
    핀 로직 자체는 살아 있으므로 페이로드 동일성 회귀는 **핀 포함** 본문으로 검증된다
    (핀을 끄는 env로 갈랐다면 프로덕션과 테스트가 갈린다 — 기획서 §3-D2가 기각한 방향).
    `tests/test_engine_defaults.py`는 이 스텁 위에 자기 스텁을 다시 얹는다(후승자 우선).
    캐시는 테스트 간 오염을 막기 위해 양쪽에서 비운다.
    """
    from core import engine_defaults as ed
    monkeypatch.setattr(ed, "_fetch_modelfile_params", lambda model, base=None: "")
    ed._cache.clear()
    yield
    ed._cache.clear()


@pytest.fixture(autouse=True)
def _medlaw_state_isolated(monkeypatch, tmp_path):
    """medlaw state 파일(khidi_state.json)을 테스트마다 격리 — 실 파일 오염 금지.

    🔴 2026-09-03 실사고: dedup apply가 tombstone을 state에 기록하게 되자,
    state 격리 없던 `test_medlaw_delete_guard`의 apply=True가 **배포 중 회귀
    실행에서 라이브 khidi_state.json에 `100-1-foo.pdf`를 써 넣었다**. 픽스처
    하나만 고치면 다음 테스트가 또 쓴다 — 클래스 전체를 여기서 봉쇄한다.
    """
    import core.medlaw_processor as _mp
    monkeypatch.setattr(_mp, "_state_path",
                        lambda: str(tmp_path / "khidi_state_test.json"))
    yield


class _OllamaAdapter:
    """기준선 어댑터 — 현재 프로덕션 경로 그대로."""

    name = "ollama"

    def __init__(self):
        import os
        self.base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        self.model = os.environ.get("CONTRACT_MODEL", "qwen2.5:14b")

    def ask_json(self, system, prompt, schema, timeout=180):
        from core.faithfulness import _default_ask_json
        return _default_ask_json(system, prompt, schema, model=self.model, timeout=timeout)

    def _prompt_eval(self, prompt, n, timeout=600):
        import requests
        r = requests.post(f"{self.base}/api/generate", json={
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"num_ctx": n, "num_predict": 1, "temperature": 0},
            "keep_alive": "5m"}, timeout=timeout)
        r.raise_for_status()
        return r.json().get("prompt_eval_count")

    def num_ctx_scales(self, small=8192, large=16384):
        """num_ctx가 **실제로 적용되는지** — 절단 구간에서 소비 토큰이 비례하는가.

        🔴 "HTTP 200이면 통과"는 못 쓴다. 어댑터가 num_ctx를 **조용히 무시해도** 200이
        오고, 그게 정확히 §1-1의 무성 절단 사고다.
        `/api/ps`의 `context_length`도 못 쓴다 — 요청별 값을 반영하지 않는다(실측:
        16384·4096을 보내도 8192로 표시).

        결정적 관측점은 `prompt_eval_count`다. 컨텍스트를 넘는 프롬프트를 주면
        엔진이 앞을 버리고 남긴 만큼만 평가하므로, 그 수가 num_ctx에 따라 달라진다.
        2026-08-10 실측(qwen2.5:14b·NUM_PARALLEL=2): **4,098 ↔ 8,194**
        (v2.124.32가 로그에서 본 `truncating limit≈4098`과 일치).

        반환: (small_eval, large_eval, scaled?)
        """
        # 0.64 tok/자 실측(반복 filler는 BPE로 잘 압축된다) → 32,000자 ≈ 20K토큰
        prompt = ("의료법 시행규칙에 따른 진료기록부 보존에 관한 설명이 이어집니다. " * 900)[:32000]
        a = self._prompt_eval(prompt, small)
        b = self._prompt_eval(prompt, large)
        scaled = bool(a and b and b > a)
        return a, b, scaled


def _build_adapter(name):
    if name == "ollama":
        return _OllamaAdapter()
    raise pytest.skip.Exception(f"어댑터 '{name}' 미구현 — Phase 4에서 추가", _use_item_location=False)


@pytest.fixture(scope="session")
def live_adapter(request):
    return _build_adapter(request.config.getoption("--adapter"))
