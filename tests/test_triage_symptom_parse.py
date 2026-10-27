# -*- coding: utf-8 -*-
"""triage 증상 파싱 회귀 — 2026-08-26 실측 원자료를 그대로 입력으로 쓴다.

사전 등록: docs/01-plan/references/triage-json-retry-preregistration-2026-08-26.md
원자료:   docs/01-plan/references/triage-json-retry-rawdata-2026-08-26.json

측정에서 확인된 것:
  · 이 경로의 LLM은 10%(3/30)가 배열을 쓰다 말고 중국어 잡담으로 폭주한다.
  · P5-1이 지정한 재시도는 8회 시도 0회 전환 → 사전 등록 기준에 따라 기각(재시도 미구현).
  · 진짜 결함은 쉼표 분리 폴백이 그 쓰레기를 증상으로 내보낸 것 → 이 파일이 재유입을 막는다.
"""
import json
import os
import re

import pytest

from routes.triage import _parse_symptom_list

_RAW_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "01-plan",
                         "references", "triage-json-retry-rawdata-2026-08-26.json")
_CJK = re.compile(r"[一-鿿぀-ヿ]")


def _load_raws():
    with open(_RAW_PATH, encoding="utf-8") as f:
        d = json.load(f)
    rows = d["round1"]["rows"] + d["round2"]["rows"] + d["round2"]["repeats"]
    return [(r["text"], r["a1_raw"], r["a1_pass"]) for r in rows if "a1_raw" in r]


RAWS = _load_raws()


def test_rawdata_present():
    """원자료가 사라지면 이 회귀는 공허해진다(X9 교훈)."""
    assert len(RAWS) == 35, f"원자료 35건이어야 한다 (현재 {len(RAWS)})"
    assert sum(1 for _, _, p in RAWS if not p) == 8, "실패 표본 8건이 보존돼야 한다"


@pytest.mark.parametrize("text,raw,passed", RAWS,
                         ids=[f"{i}-{'ok' if p else 'derail'}" for i, (_, _, p) in enumerate(RAWS)])
def test_no_contamination_in_parsed_symptoms(text, raw, passed):
    """실측 응답 35건 어디에서도 한자·가나·대괄호 잔재가 증상으로 새 나가지 않는다."""
    out = _parse_symptom_list(raw)
    assert isinstance(out, list) and out, "계약: 항상 비지 않은 리스트"
    for s in out:
        assert isinstance(s, str)
        assert not _CJK.search(s), f"한자/가나 오염이 증상으로 나갔다: {s!r} (입력: {text})"
        assert not s.startswith(("[", '"')), f"대괄호/따옴표 잔재: {s!r}"
        assert len(s) < 20


def test_derailed_responses_recover_korean_symptoms():
    """폭주 응답에서도 한국어 증상을 건져낸다 — `['기타증상']`으로 뭉개지 않는다."""
    derailed = [(t, r) for t, r, p in RAWS if not p]
    recovered = [t for t, r in derailed if _parse_symptom_list(r) != ["기타증상"]]
    assert len(recovered) == len(derailed), (
        f"폭주 8건 전부 증상을 건져야 한다 (건진 것 {len(recovered)})")


def test_known_derail_case_exact():
    """대표 사례 고정 — 종전엔 `'[\"손발冰冷\"'`·`'\"手指变白\"]'`가 증상으로 나갔다."""
    raw = next(r for t, r, p in RAWS if not p and "손발이 늘 차고" in t)
    assert _parse_symptom_list(raw) == ["손발이 차다", "손끝이 하얗게 변함"]


def test_clean_json_array_unchanged():
    """정상 JSON 배열은 종전과 동일하게 통과한다(퇴행 0)."""
    assert _parse_symptom_list('["두통", "발열"]') == ["두통", "발열"]
    assert _parse_symptom_list('  ["기침"]  ') == ["기침"]


def test_english_items_preserved():
    """영어 항목은 손대지 않는다 — 오염과 달리 근거가 없다(효과 미확인 처치 금지)."""
    assert _parse_symptom_list('["olfaction loss"]') == ["olfaction loss"]


def test_empty_and_garbage_fall_back():
    assert _parse_symptom_list("") == ["기타증상"]
    assert _parse_symptom_list("我不知道") == ["기타증상"]


def test_dedup():
    assert _parse_symptom_list('["두통", "두통", "발열"]') == ["두통", "발열"]


# ── 표본 밖 엣지 케이스 ────────────────────────────────────────────────────
# 실측 35건에는 dict 응답이 없어 원자료 평가가 이 퇴행을 못 잡았다(배포 전 적발).
# 배열 조각 수집을 붙이면서 dict 응답이 쉼표 분리로 흘러내려 `{"증상": "두통"}`이
# 통째로 증상이 될 뻔했다 — 정화 후 JSON 구조 문자가 남으면 버린다.
@pytest.mark.parametrize("raw,expected", [
    ('{"증상": "두통"}', ["기타증상"]),      # dict 응답 — 종전 동작 유지
    ('[{"a": 1}]', ["기타증상"]),            # 객체 배열
    ('我不知道', ["기타증상"]),               # 한자만 — 종전엔 그대로 나갔다
    ('', ["기타증상"]),
    ('["두통","발열"]', ["두통", "발열"]),
])
def test_edge_cases_outside_sample(raw, expected):
    assert _parse_symptom_list(raw) == expected


def test_no_structural_chars_ever_escape():
    """어떤 입력에서도 JSON 구조 문자가 증상으로 나가지 않는다."""
    for _, raw, _ in RAWS:
        for s in _parse_symptom_list(raw):
            assert not any(ch in s for ch in '{}[]"'), f"구조 문자 잔재: {s!r}"
