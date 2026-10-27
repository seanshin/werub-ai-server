"""코딩 API — `code` 필드가 **문서 계약대로 순수 코드**인가 (2026-08-12 실결함).

🔴 증상: `/api/code/generate`가 응답 `code`에 LLM 원문을 그대로 실었다 —
   ```` ```python ````로 시작하는 **마크다운 펜스**와 코드 뒤의 **설명 산문**까지.

   그런데 문서(`docs/CODING_API_GUIDE.md`)의 응답 예시는
   `"code": "from fastapi import Depends, HTTPException..."` — **순수 코드**다.
   구현이 자기 문서를 위반하고 있었다.

🔴 더 나쁜 것: `syntax_valid`와 `security_scan`은 **펜스를 벗긴 `code_block`**을
   검사한 값이었다. 즉 **호출자가 받은 문자열과 서버가 검사한 문자열이 달랐다** —
   받은 걸 그대로 `ast.parse`하면 실패하는데 플래그는 `true`였다.
   (`code_block`은 계산은 되고 **반환되지 않았다**.)

이 파일이 고정하는 것:
  ① `code`/`refactored`/`converted`는 펜스·산문 없는 코드다
  ② 검사(`syntax_valid`)와 반환 값이 **같은 대상**이다
  ③ 원문은 `raw`로 보존된다(정보를 버리지 않는다)
  ④ 펜스가 없는 응답은 **그대로** 통과한다(폴백)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ast

import pytest

from core.quality import extract_code_block


FENCED = (
    "```python\n"
    "def gcd(a: int, b: int) -> int:\n"
    "    while b:\n"
    "        a, b = b, a % b\n"
    "    return a\n"
    "```\n"
    "\n"
    "This function uses the Euclidean algorithm to compute the GCD.\n"
)


# ── ① 추출 자체 ─────────────────────────────────────────────────────────────
def test_extracted_block_parses_but_raw_does_not():
    """실제로 관측된 형태 — 원문은 파싱 실패, 추출본은 성공."""
    with pytest.raises(SyntaxError):
        ast.parse(FENCED)
    ast.parse(extract_code_block(FENCED, "python"))


def test_extractor_drops_fence_and_trailing_prose():
    body = extract_code_block(FENCED, "python")
    assert "```" not in body
    assert "Euclidean algorithm" not in body, "코드 뒤 산문이 남으면 파일로 쓸 수 없다"
    assert body.strip().startswith("def gcd")


def test_extractor_passes_through_bare_code():
    """④ 펜스가 없으면 전체를 돌려준다 — 폴백이 내장돼 있어 빈 값이 되지 않는다."""
    bare = "def f(x):\n    return x * 2\n"
    assert extract_code_block(bare, "python").strip() == bare.strip()


def test_extractor_handles_empty():
    assert extract_code_block("", "python") == ""


# ── ②③ 라우트 배선 ─────────────────────────────────────────────────────────
def _src():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "routes/coding.py"), encoding="utf-8") as f:
        return f.read()


def test_generate_returns_extracted_not_raw():
    """🔴 `code`가 원문이면 문서 계약 위반이고, `syntax_valid`가 다른 문자열을 가리킨다."""
    s = _src()
    assert '"code": code_block or response' in s
    assert '"code": response,' not in s, "원문을 그대로 싣는 배선이 남아 있다"


@pytest.mark.parametrize("field,decl", [
    ("refactored", '"refactored": extract_code_block(response) or response'),
    ("converted", '"converted": extract_code_block(response, target) or response'),
])
def test_other_code_fields_extracted_too(field, decl):
    """한 곳만 고치면 같은 클래스가 나머지에 남는다(v2.124.65 교훈)."""
    assert decl in _src(), field


def test_raw_preserved_everywhere():
    """③ 설명이 필요한 호출자를 위해 원문을 남긴다 — 정보를 버리는 처치는 하지 않는다."""
    s = _src()
    assert s.count('"raw": response') >= 3


def test_syntax_check_targets_returned_value():
    """② 검사 대상과 반환 값이 같은 변수여야 한다 —
    다르면 '플래그는 true인데 파싱은 실패'가 다시 생긴다."""
    s = _src()
    i = s.index("code_block = extract_code_block(response, language")
    j = s.index('"code": code_block or response')
    k = s.index("ast.parse(code_block)")
    assert i < j < k, "추출 → 반환 → 검사가 같은 code_block을 써야 한다"
