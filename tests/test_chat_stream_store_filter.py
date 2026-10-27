"""`/api/chat` 스트림 저장분 정화 — sanitize_stream_store (v2.125.21).

실측(N=8·references/chat-stream-cache-divergence-2026-08-26.md)이 확진한 결함의 처치 회귀:
종전 force_korean 저장은 코드·영어 줄을 통째로 지워(분기 4/8) 캐시 재생이 라이브와
갈렸고, 전량 삭제 시 full_text=""라 저장 자체가 스킵됐다(캐시 미스·세션 이력 소실 2/8).
회귀 입력은 그 측정의 원자료(라이브 응답 전문)를 그대로 쓴다.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.korean import sanitize_stream_store, force_korean  # noqa: E402

_RAW = os.path.join(_ROOT, "docs", "01-plan", "references",
                    "chat-divergence-raw-n8-20260826.json")


@pytest.fixture(scope="module")
def raw():
    with open(_RAW, encoding="utf-8") as f:
        return {r["i"]: r for r in json.load(f)}


def test_code_block_preserved(raw):
    """#2 파이썬 코드 — 종전엔 571→167자로 코드 전삭제. 이제 코드 줄이 살아야 한다."""
    live = raw[2]["live"]
    out = sanitize_stream_store(live)
    assert "def fibonacci_recursive" in out
    assert "return fibonacci_recursive(n-1) + fibonacci_recursive(n-2)" in out
    assert len(out) >= int(len(live) * 0.95)
    # 종전 동작이 실제로 파괴적이었다는 전제 고정(전제가 사라지면 이 처치도 재검토)
    assert "def fibonacci_recursive" not in force_korean(live)


def test_english_answer_stored_nonempty(raw):
    """#3·#4 영어 답변 — 종전엔 전량 삭제→저장 스킵(캐시 미스·이력 소실). 이제 보존."""
    for i in (3, 4):
        live = raw[i]["live"]
        out = sanitize_stream_store(live)
        assert out.strip() == live.strip()
        assert out.strip()          # 비어 있지 않음 = 캐시·세션 저장 조건 충족


def test_hanja_contamination_still_removed(raw):
    """#8 정화 효용 보존 — 모델이 혼입한 한자·중국어는 여전히 제거, 본문 줄은 생존."""
    live = raw[8]["live"]
    out = sanitize_stream_store(live)
    assert "脔" not in out and "细" not in out
    assert "돼지고기" in out and "김치" in out


def test_kana_strip_tradeoff_retained(raw):
    """#6 가나 제거는 force_korean_token과 같은 강도로 유지(알려진 트레이드오프) —
    일본어 혼입 방지가 이 레포의 실전 결함 클래스라 정화를 남긴다. 한글 발음은 생존."""
    live = raw[6]["live"]
    out = sanitize_stream_store(live)
    assert "こんにちは" not in out
    assert "곤니치와" in out


def test_empty_result_falls_back_to_raw():
    """정화가 전부 비우면 raw 반환 — 저장 스킵(무성 소실) 방지."""
    kana_only = "こんにちは、ありがとう"
    assert sanitize_stream_store(kana_only) == kana_only
    assert sanitize_stream_store("") == ""
    assert sanitize_stream_store(None) is None


def test_route_wiring_uses_sanitize():
    """소스 스캔(v2.125.16 규율) — 스트림 저장 지점이 force_korean으로 회귀하면 잡는다."""
    with open(os.path.join(_ROOT, "routes", "ollama.py"), encoding="utf-8") as f:
        src = f.read()
    assert "sanitize_stream_store as _fk_stream" in src
    assert "force_korean as _fk_stream" not in src
