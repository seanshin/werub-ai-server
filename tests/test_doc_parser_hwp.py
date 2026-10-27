"""core.doc_parser._hwp_para_text 순수 단위테스트 (HWP 컨트롤 폭 처리).

구형 HWP PARA_TEXT의 UTF-16LE wchar 스트림에서 8-wchar 확장/인라인 컨트롤을
정확히 건너뛰고(데이터 오염 방지) 본문만 추출하는지 검증. olefile/파일 불요.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.doc_parser import _hwp_para_text  # noqa: E402


def _wc(*codes) -> bytes:
    return b"".join(struct.pack("<H", c) for c in codes)


def test_plain_text():
    assert _hwp_para_text(_wc(ord("가"), ord("나"), ord("다"))) == "가나다"


def test_para_break_is_newline():
    # 10, 13 → 개행
    assert _hwp_para_text(_wc(ord("A"), 13, ord("B"))) == "A\nB"
    assert _hwp_para_text(_wc(ord("A"), 10, ord("B"))) == "A\nB"


def test_extended_control_8wchar_skipped():
    # 확장 컨트롤(코드 2)은 자신+데이터 7 wchar = 8 wchar(16바이트) 점유 → 통째 스킵
    body = _wc(ord("X")) + _wc(2, 9, 9, 9, 9, 9, 9, 9) + _wc(ord("Y"))
    assert _hwp_para_text(body) == "XY"


def test_inline_control_codes():
    # 코드 4,9,19 등도 8-wchar 점유
    for c in (4, 9, 19, 23):
        body = _wc(ord("A")) + _wc(c, 0, 0, 0, 0, 0, 0, 0) + _wc(ord("B"))
        assert _hwp_para_text(body) == "AB", f"control {c}"


def test_char_control_1wchar_skipped():
    # 1-wchar char 컨트롤(예 24)은 1 wchar만 스킵
    assert _hwp_para_text(_wc(ord("A"), 24, ord("B"))) == "AB"


def test_odd_trailing_byte_ignored():
    # 홀수 잔여 바이트는 무시(인덱스 오류 없이)
    assert _hwp_para_text(_wc(ord("A")) + b"\x99") == "A"


def test_empty():
    assert _hwp_para_text(b"") == ""
