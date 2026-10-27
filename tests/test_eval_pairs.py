"""외부 답변 충실도 채점 입력 파싱 — (질문·답변·근거) 쌍.

Cerno 등 자기 파이프라인으로 답을 만드는 호출자는 "질문만 주면 서버가 답을 생성"하는
기존 배치 경로를 쓸 수 없다. 하루치 표본을 NDJSON으로 받아 채점하는 입력이라,
**한 줄이 깨져도 그날 측정 전체가 사라지면 안 된다**.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.eval_pairs import MAX_RECORDS, parse_pairs                    # noqa: E402


def _line(**kw):
    base = {"id": "c-1", "question": "심부전 환자 병용 주의는?",
            "answer": "베타차단제와 병용 시 서맥에 주의합니다.",
            "evidence": ["베타차단제 병용 시 서맥이 보고된다."]}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def test_basic_record():
    recs, errs = parse_pairs([_line()])
    assert errs == [] and len(recs) == 1
    r = recs[0]
    assert r["id"] == "c-1" and r["question"].startswith("심부전")
    assert r["evidence"] == "베타차단제 병용 시 서맥이 보고된다."


def test_evidence_list_is_joined():
    recs, _ = parse_pairs([_line(evidence=["근거1", "근거2"])])
    assert recs[0]["evidence"] == "근거1\n근거2"


def test_evidence_string_accepted():
    recs, _ = parse_pairs([_line(evidence="단일 근거")])
    assert recs[0]["evidence"] == "단일 근거"


def test_missing_evidence_rejected():
    """근거가 없으면 '무엇에 대한 지지'인지가 없어 충실도를 정의할 수 없다."""
    recs, errs = parse_pairs([_line(evidence=[])])
    assert recs == [] and "evidence" in errs[0]


def test_missing_required_fields():
    recs, errs = parse_pairs([_line(question=""), _line(answer="  ")])
    assert recs == [] and len(errs) == 2
    assert "question" in errs[0] and "answer" in errs[1]


def test_broken_line_does_not_kill_batch():
    """한 줄 오타로 하루치가 통째로 날아가면 안 된다 — 나머지는 살린다."""
    recs, errs = parse_pairs([_line(id="ok-1"), "{깨진 json", _line(id="ok-2")])
    assert [r["id"] for r in recs] == ["ok-1", "ok-2"]
    assert len(errs) == 1 and "2행" in errs[0]


def test_blank_and_comment_lines_ignored():
    recs, errs = parse_pairs(["", "   ", "# 주석", _line()])
    assert len(recs) == 1 and errs == []


def test_id_defaults_to_line_number():
    recs, _ = parse_pairs([_line(id="")])
    assert recs[0]["id"] == "line-1"


def test_meta_preserved_only_if_dict():
    assert parse_pairs([_line(meta={"visibility": "org"})])[0][0]["meta"] == {"visibility": "org"}
    assert parse_pairs([_line(meta="org")])[0][0]["meta"] is None


def test_record_cap_is_reported():
    """야간 배치 창을 밀어내지 않도록 상한을 두되, 잘랐다는 사실을 알린다."""
    lines = [_line(id="c-%d" % i) for i in range(MAX_RECORDS + 5)]
    recs, errs = parse_pairs(lines)
    assert len(recs) == MAX_RECORDS
    assert errs and "생략" in errs[0]


def test_non_object_line():
    recs, errs = parse_pairs(['["배열은 안 됨"]'])
    assert recs == [] and "객체가 아님" in errs[0]
