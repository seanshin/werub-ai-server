"""결정적 질의 확장 회귀 (id175 방사선 보존기간 검색 커버리지 갭 봉합, v2.124.7).
_query_expansions 매칭 규칙 + _rag_law_search 확장 병합(dedup·개수 상한)."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import _query_expansions, _rag_law_search


# ── _query_expansions 매칭 규칙 ──
def test_expansion_radiology_retention():
    # id175 실증 질의 — 보존+방사선 → 제15조 표제어 확장
    out = _query_expansions("방사선 사진 및 영상물의 보존기간은?")
    assert out == ["진료기록부 등의 보존 기간"]


def test_expansion_other_record_types():
    # 계열 문항(사진/영상/명부/소견)도 동일 확장 — 이미 정상 검색되는 문항이어도
    # dedup 병합이라 무해
    for q in ("환자 명부의 보존기간은?", "검사소견기록은 몇 년 보존해야 하나요?"):
        assert _query_expansions(q) == ["진료기록부 등의 보존 기간"]


def test_expansion_requires_all_terms():
    # '보존' 없으면 미확장 (all 조건)
    assert _query_expansions("방사선 발생장치 설치 절차는?") == []


def test_expansion_requires_any_term():
    # '보존'만 있고 기록물명 없으면 미확장 (any 조건)
    assert _query_expansions("문화재 보존 정책은?") == []


def test_expansion_skips_identical_query():
    # 확장 질의와 원 질의가 같으면 생략
    assert _query_expansions("진료기록부 등의 보존 기간") == []


def test_expansion_empty_and_none_safe():
    assert _query_expansions("") == []
    assert _query_expansions(None) == []


def test_expansion_config_override():
    # config `medlaw_query_expansions`가 기본 테이블을 대체
    rules = [{"all": ["삭감"], "query": "요양급여비용 심사기준"}]
    with patch("routes.medical_law.load_config", return_value={"medlaw_query_expansions": rules}):
        assert _query_expansions("이 청구 삭감 위험은?") == ["요양급여비용 심사기준"]
        assert _query_expansions("방사선 사진 보존기간은?") == []  # 기본 룰 대체됨


def test_expansion_broken_rule_isolated():
    # 깨진 룰(비리스트 all)은 건너뛰고 정상 룰만 적용
    rules = [{"all": 123, "query": "X"},
             {"all": ["보존"], "any": ["방사선"], "query": "진료기록부 등의 보존 기간"}]
    with patch("routes.medical_law.load_config", return_value={"medlaw_query_expansions": rules}):
        assert _query_expansions("방사선 사진 보존기간?") == ["진료기록부 등의 보존 기간"]


# ── _rag_law_search 확장 병합 ──
def _mk(id_, score, origin="law3dan", text="제15조(진료기록부 등의 보존) 방사선 사진 5년"):
    return {"id": id_, "score": score, "text": text,
            "metadata": {"origin": origin}}


def test_search_merges_expansion_hits():
    """원 질의가 엉뚱한 법령만 잡을 때 확장 질의 hit(제15조)가 결과에 합류."""
    wrong1 = _mk("auto29", 0.40, text="제29조(진료에 관한 기록 등의 보존) 교통사고환자")
    wrong2 = _mk("aid11", 0.38, text="제11조(서류의 보존) 의료급여기관")
    art15 = _mk("rule15", 0.58)

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_pub_knowledge":
            return []
        if "진료기록부 등의 보존" in query:  # 확장 질의
            return [art15]
        return [wrong1, wrong2]  # 원 질의: 용어 충돌로 정답 미포함

    with patch("client.rag.search", side_effect=fake_search):
        hits = _rag_law_search("방사선 사진 및 영상물의 보존기간은?", top_k=5)
    ids = [h["id"] for h in hits]
    assert "rule15" in ids            # 확장으로 정답 조문 합류
    assert ids[:2] == ["auto29", "aid11"]  # 기존 floor 동작 불변


def test_search_expansion_dedup():
    """확장 hit이 원 질의 결과와 중복이면 늘어나지 않음."""
    art15 = _mk("rule15", 0.58)

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_pub_knowledge":
            return []
        return [art15]

    with patch("client.rag.search", side_effect=fake_search):
        hits = _rag_law_search("방사선 사진 및 영상물의 보존기간은?", top_k=5)
    assert [h["id"] for h in hits] == ["rule15"]


def test_search_no_expansion_for_unrelated_query():
    """미매칭 질의는 확장 검색 자체가 없음(호출 질의 기록으로 검증)."""
    calls = []

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        calls.append(query)
        return [_mk("a", 0.5), _mk("b", 0.45)]

    with patch("client.rag.search", side_effect=fake_search):
        _rag_law_search("의료광고 사전심의 의무는?", top_k=5)
    assert all("진료기록부 등의 보존" not in q for q in calls)


def test_search_expansion_failure_degrades_gracefully():
    """확장 검색만 예외여도 원 질의 결과는 보존."""
    # 텍스트를 구분(v2.124.8 동일원문 dedup이 mock 기본 텍스트 공유에 걸리지 않도록)
    wrong1 = _mk("auto29", 0.40, text="제29조(진료에 관한 기록 등의 보존) 교통사고환자")
    wrong2 = _mk("aid11", 0.38, text="제11조(서류의 보존) 의료급여기관")

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_pub_knowledge":
            return []
        if "진료기록부 등의 보존" in query:
            raise RuntimeError("chroma down")
        return [wrong1, wrong2]

    with patch("client.rag.search", side_effect=fake_search):
        hits = _rag_law_search("방사선 사진 및 영상물의 보존기간은?", top_k=5)
    assert [h["id"] for h in hits] == ["auto29", "aid11"]


# ── eval 근거 수집 정합 (생성/판정 근거 비대칭 방지) ──
def test_eval_evidence_applies_expansion():
    """eval get_evidence_chunks도 /ask와 동일하게 확장 적용 — 생성만 확장하면
    judge가 정답 인용을 unsupported로 오판(id175 재측정 faith 0.000 실측)."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "eval_faithfulness", os.path.join(root, "scripts", "eval_faithfulness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    art15 = _mk("rule15", 0.58)
    calls = []

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        calls.append((collection_name, query))
        if collection_name == "medical_law_knowledge" and "진료기록부 등의 보존" in query:
            return [art15]
        return []

    with patch("client.rag.search", side_effect=fake_search):
        chunks = mod.get_evidence_chunks("방사선 사진 및 영상물의 보존기간은?")
    assert any("제15조" in c for c in chunks)          # 확장 hit이 judge 근거에 합류
    # 확장은 본 코퍼스에만 (발간물 컬렉션엔 확장 질의 없음)
    assert all(col != "medical_pub_knowledge" or "진료기록부 등의 보존" not in q
               for col, q in calls)
