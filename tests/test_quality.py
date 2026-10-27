"""core.quality 순수 함수 단위 테스트.

force_json 계열(LLM 응답 JSON 추출), 필드/SOAP 검증, 코드 추출·문법·보안 스캔.
DB 의존(validate_icd_codes/check_drug_interactions)은 제외.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import quality as q  # noqa: E402


class TestForceJson:
    def test_direct(self):
        assert q.force_json('{"a": 1}') == {"a": 1}

    def test_json_fenced_block(self):
        assert q.force_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_embedded_object(self):
        assert q.force_json('설명: {"a": 1} 끝') == {"a": 1}

    def test_embedded_array(self):
        assert q.force_json('목록 [1, 2, 3]') == [1, 2, 3]

    def test_fallback_plain_text(self):
        assert q.force_json("그냥 텍스트") == {"response": "그냥 텍스트"}

    def test_custom_fallback_key(self):
        assert q.force_json("x", fallback_key="raw") == {"raw": "x"}

    def test_none(self):
        assert q.force_json(None) == {}

    def test_empty_string(self):
        assert q.force_json("") == {}


class TestForceJsonArray:
    def test_array(self):
        assert q.force_json_array("[1, 2]") == [1, 2]

    def test_object_wrapped(self):
        assert q.force_json_array('{"a": 1}') == [{"a": 1}]

    def test_plain_text_empty(self):
        assert q.force_json_array("json 아님") == []


class TestValidateFields:
    def test_all_present(self):
        passed, missing = q.validate_fields({"a": 1, "b": 2}, ["a", "b"])
        assert passed is True and missing == []

    def test_missing(self):
        passed, missing = q.validate_fields({"a": 1}, ["a", "b"])
        assert passed is False and missing == ["b"]

    def test_none_value_counts_missing(self):
        passed, missing = q.validate_fields({"a": None}, ["a"])
        assert passed is False and missing == ["a"]

    def test_non_dict(self):
        passed, missing = q.validate_fields("not a dict", ["a"])
        assert passed is False and missing == ["a"]


class TestSoapFields:
    def test_complete(self):
        soap = '{"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}'
        passed, _ = q.validate_soap_fields(soap)
        assert passed is True

    def test_nested_summary(self):
        soap = '{"summary": {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}}'
        assert q.validate_soap_fields(soap)[0] is True

    def test_missing(self):
        passed, msg = q.validate_soap_fields('{"subjective": "s"}')
        assert passed is False
        assert "plan" in msg


class TestValidateJsonResponse:
    def test_valid(self):
        assert q.validate_json_response('{"a": 1}')[0] is True

    def test_extractable(self):
        assert q.validate_json_response('답: {"a": 1}')[0] is True

    def test_invalid(self):
        assert q.validate_json_response("그냥 텍스트")[0] is False


class TestValidateJsonArrayResponse:
    def test_array(self):
        assert q.validate_json_array_response("[1, 2]")[0] is True

    def test_not_array(self):
        assert q.validate_json_array_response("텍스트")[0] is False


class TestExtractCodeBlock:
    def test_language_block(self):
        resp = "설명\n```python\nprint(1)\n```\n끝"
        assert q.extract_code_block(resp, "python") == "print(1)"

    def test_plain_block(self):
        assert q.extract_code_block("```\nx = 1\n```") == "x = 1"

    def test_no_block_returns_whole(self):
        assert q.extract_code_block("  코드 없음  ") == "코드 없음"

    def test_empty(self):
        assert q.extract_code_block("") == ""


class TestPythonSyntax:
    def test_valid(self):
        passed, _ = q.validate_python_syntax("x = 1\ny = x + 2")
        assert passed is True

    def test_invalid(self):
        passed, msg = q.validate_python_syntax("def (:")
        assert passed is False
        assert "SyntaxError" in msg


class TestValidateCodeSyntax:
    def test_valid_python_block(self):
        assert q.validate_code_syntax("```python\nprint(1)\n```", "python")[0] is True

    def test_invalid_python_block(self):
        assert q.validate_code_syntax("```python\ndef (:\n```", "python")[0] is False

    def test_empty_skips(self):
        assert q.validate_code_syntax("", "python")[0] is True

    def test_unsupported_language_passes(self):
        assert q.validate_code_syntax("```rust\nfn main(){}\n```", "rust")[0] is True

    def test_make_code_validator(self):
        validator = q.make_code_validator("python")
        assert validator("```python\nx=1\n```")[0] is True


class TestSecurityScan:
    def test_eval_critical(self):
        r = q.scan_code_security("result = eval(user_input)", "python")
        assert r["has_critical"] is True
        assert any(i["rule"] == "eval-usage" for i in r["issues"])

    def test_os_system_high(self):
        r = q.scan_code_security("os.system('ls')", "python")
        assert r["issue_count"] >= 1
        assert any(i["severity"] == "high" for i in r["issues"])

    def test_shell_injection_critical(self):
        r = q.scan_code_security("subprocess.run(cmd, shell=True)", "python")
        assert r["has_critical"] is True

    def test_safe_code_no_issues(self):
        r = q.scan_code_security("def add(a, b):\n    return a + b", "python")
        assert r["issue_count"] == 0
        assert r["has_critical"] is False

    def test_empty(self):
        r = q.scan_code_security("", "python")
        assert r == {"issues": [], "issue_count": 0, "has_critical": False}

    def test_line_number(self):
        r = q.scan_code_security("x = 1\ny = 2\neval(z)", "python")
        evals = [i for i in r["issues"] if i["rule"] == "eval-usage"]
        assert evals and evals[0]["line"] == 3
