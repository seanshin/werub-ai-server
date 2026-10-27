"""발간물 라벨 오귀속 방지 회귀 (eval id200 법령명 오귀속 폐루프, v2.124.8).

법령집 등 발간물 청크의 law_name은 문서 제목(예: 국민건강보험법령집)인데 기존
라벨 포맷이 법령과 동일해, 수록된 타 법령 조문(마약류관리법 제32조)을 모델이
"국민건강보험법 제32조"로 축약 오귀속 — 라벨에 '문서 제목이며 법령명 아님' 명시
+ MEDICAL_LAW_SYSTEM 규칙 7."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import MEDICAL_LAW_SYSTEM, _ask_assemble  # noqa: E402


def _fake_db():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    return conn


def _assemble_with(hits):
    with patch("routes.medical_law._get_db", return_value=_fake_db()), \
         patch("routes.medical_law._rag_law_search", return_value=hits):
        return _ask_assemble({"question": "처방전에 기재해야 하는 사항은 무엇인가?"})


def test_pub_chunk_label_marks_doc_title():
    """발간물 청크 라벨 = 발간물(종류) 「문서 제목」 + 법령명 아님 명시."""
    pub = {"id": "pub1", "score": 0.6,
           "text": "제32조(처방전의 기재) ① 마약류취급의료업자는 …",
           "metadata": {"origin": "hira_pub", "doc_type": "법령집",
                        "law_name": "국민건강보험법령집", "law_level": "발간물"}}
    ctx = _assemble_with([pub])["context"]
    assert "「국민건강보험법령집」" in ctx
    assert "문서 제목이며 법령명 아님" in ctx
    # 법령식 라벨(문서 제목이 법령명 위치에 노출)로는 렌더링되지 않음
    assert "[국민건강보험법령집 발간물" not in ctx


def test_law_chunk_label_unchanged():
    """법령(law3dan) 청크 라벨은 기존 포맷 불변."""
    law = {"id": "l12", "score": 0.7,
           "text": "제12조(처방전의 기재 사항 등) …",
           "metadata": {"origin": "law3dan", "law_name": "의료법",
                        "law_level": "시행규칙", "article_number": "제12조",
                        "article_title": "처방전의 기재 사항 등"}}
    ctx = _assemble_with([law])["context"]
    assert "[의료법 시행규칙 제12조(처방전의 기재 사항 등)]" in ctx
    assert "문서 제목이며 법령명 아님" not in ctx


def test_system_rule_forbids_doc_title_as_law_name():
    """규칙 7 상주 — 발간물 문서 제목의 법령명 오용 금지."""
    assert "문서 제목은 법령명이 아닙니다" in MEDICAL_LAW_SYSTEM
    assert "(발간물 수록 조문)" in MEDICAL_LAW_SYSTEM
