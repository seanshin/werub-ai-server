"""core.hira_fee_parser 순수 단위테스트 (PDF 불요 — 합성 데이터).

표 레이아웃·행 클러스터·컬럼 분배·서브청킹·산출물 변환을 PDF 없이 검증한다.
GPU/임베딩 불요 → iMac에서도 실행 가능.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hira_fee_parser import (  # noqa: E402
    _CATNO_RE, _CODE_RE, _SCORE_RE, _TOC_RE, _cells, _row_cluster, _split_body,
    FeeItem, RagChunk, ParsedValuepoint, fee_rows, rag_documents,
)


def _w(text, x0, top):
    return {"text": text, "x0": x0, "x1": x0 + 10 * len(text), "top": top}


# ── 정규식 ──
def test_code_regex():
    assert _CODE_RE.match("F6100") and _CODE_RE.match("AU931") and _CODE_RE.match("SB271")
    assert not _CODE_RE.match("나-610") and not _CODE_RE.match("-SB277")


def test_catno_regex():
    assert _CATNO_RE.match("가-1") and _CATNO_RE.match("나-610-1") and _CATNO_RE.match("자-17-2")
    assert not _CATNO_RE.match("F6100") and not _CATNO_RE.match("가나다")


def test_score_regex():
    assert _SCORE_RE.match("146.75") and _SCORE_RE.match("1,220.21")
    assert not _SCORE_RE.match("146") and not _SCORE_RE.match("12.3")


def test_toc_regex():
    assert _TOC_RE.search("제1부 질병군 급여 일반원칙 ················ 803")
    assert not _TOC_RE.search("F6100 가. 단순검사 146.75")


# ── 행 클러스터 ──
def test_row_cluster_groups_by_y():
    words = [_w("F6100", 100, 50), _w("단순검사", 130, 51), _w("146.75", 360, 50),
             _w("F6101", 100, 70), _w("일반검사", 130, 70)]
    rows = _row_cluster(words)
    assert len(rows) == 2
    assert [w["text"] for w in rows[0]] == ["F6100", "단순검사", "146.75"]


# ── 컬럼 분배(4컬럼: 코드95 분류128 점수354) ──
def test_cells_four_columns():
    bounds = [95, 128, 354]
    row = [_w("나-610", 62, 0), _w("신경학적", 131, 0), _w("검사", 170, 0)]
    catno, code, name, score = _cells(row, bounds)
    assert catno == "나-610" and code == "" and "신경학적" in name and score == ""

    row2 = [_w("F6100", 99, 0), _w("가.", 131, 0), _w("단순검사", 150, 0), _w("146.75", 362, 0)]
    _, code2, name2, score2 = _cells(row2, bounds)
    assert code2 == "F6100" and score2 == "146.75" and "단순검사" in name2


def test_cells_three_columns_eval():
    # 별첨3은 점수 컬럼 없음 → bounds 2개, 점수는 항상 ""
    bounds = [98, 134]
    row = [_w("누-659", 60, 0), _w("핵산교잡", 211, 0)]
    catno, code, name, score = _cells(row, bounds)
    assert catno == "누-659" and score == "" and "핵산교잡" in name


# ── 서브청킹 ──
def test_split_body_short_passthrough():
    assert _split_body("짧은 본문입니다 여기") == ["짧은 본문입니다 여기"]


def test_split_body_splits_long():
    text = "\n".join(f"라인 {i} 내용이 길게 들어갑니다" for i in range(100))
    parts = _split_body(text, max_chars=200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)


def test_split_body_hard_slice_single_long_line():
    parts = _split_body("가" * 2000, max_chars=500)
    assert len(parts) >= 4 and all(len(p) <= 500 for p in parts)


# ── 산출물 변환 ──
def _sample_doc():
    d = ParsedValuepoint(source_file="valuepoint.pdf", edition="2021년 2월판")
    d.fee_items = [
        FeeItem(code="F6100", score="146.75", name="신경학적 검사 > 가. 단순검사",
                category_no="나-610", page=200),
        FeeItem(code="F6101", score="", name="신경학적 검사 > 나. 일반검사",
                category_no="나-610", page=200),
    ]
    d.chunks = [
        RagChunk(text="[나-610] 신경학적 검사\nF6100 가. 단순검사 146.75",
                 category_no="나-610", title="신경학적 검사", section="valuepoint", page=200),
        RagChunk(text="[누-659] 핵산교잡\n∘목적：HPV 검출\n∘대상：자궁경부\n∘방법：PCR",
                 category_no="누-659", title="핵산교잡", section="eval", page=1200),
    ]
    return d


def test_fee_rows_category_and_score_in_name():
    rows = fee_rows(_sample_doc())
    assert len(rows) == 2
    assert all(r["category"] == "TARIFF" for r in rows)
    assert "146.75점" in rows[0]["name"]          # 점수 본문에 포함
    assert rows[1]["name"].endswith("일반검사")    # 점수 없으면 미부착


def test_rag_documents_metadata_and_origin():
    docs = rag_documents(_sample_doc())
    assert len(docs) == 2
    ids = [d[0] for d in docs]
    assert len(set(ids)) == 2 and all(i.startswith("hv-") for i in ids)
    metas = {m["section"]: m for _, _, m in docs}
    assert metas["valuepoint"]["category"] == "급여기준"
    assert metas["eval"]["category"] == "신의료기술"
    assert all(m["origin"] == "hira_valuepoint" for _, _, m in docs)
    assert all(m["source_file"] == "valuepoint.pdf" for _, _, m in docs)
    assert "2021년 2월판" in metas["valuepoint"]["law_name"]


def test_rag_documents_subsplits_oversized():
    d = ParsedValuepoint(source_file="v.pdf")
    big = "\n".join(f"항목 {i} 상세 설명이 충분히 길게" for i in range(120))
    d.chunks = [RagChunk(text=big, category_no="노-580", title="유전성검사",
                         section="eval", page=1330)]
    docs = rag_documents(d, max_chars=300)
    assert len(docs) > 1                          # 1 청크 → 다수 서브문서
    assert all(len(t) <= 300 for _, t, _ in docs)
    assert all(m["chunk_seq"] == 0 for _, _, m in docs)   # 동일 원청크
    assert len({m["sub_seq"] for _, _, m in docs}) == len(docs)
