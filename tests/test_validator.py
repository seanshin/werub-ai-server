"""core.validator.validate 단위 테스트 (입력 스키마 검증, Flask 독립)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.validator import validate  # noqa: E402


class TestRequired:
    def test_missing_required(self):
        errors = validate({}, {"name": {"required": True}})
        assert errors == [{"field": "name", "msg": "required"}]

    def test_empty_string_required(self):
        errors = validate({"name": "   "}, {"name": {"required": True}})
        assert errors and errors[0]["msg"] == "required"

    def test_present_required(self):
        assert validate({"name": "홍길동"}, {"name": {"required": True}}) == []

    def test_optional_absent_ok(self):
        assert validate({}, {"name": {"max_length": 10}}) == []


class TestType:
    def test_type_mismatch(self):
        errors = validate({"age": "x"}, {"age": {"type": "int"}})
        assert errors and "must be int" in errors[0]["msg"]

    def test_type_ok(self):
        assert validate({"age": 5}, {"age": {"type": "int"}}) == []

    def test_float_accepts_int(self):
        assert validate({"x": 3}, {"x": {"type": "float"}}) == []

    def test_list_type(self):
        assert validate({"items": [1, 2]}, {"items": {"type": "list"}}) == []
        assert validate({"items": "x"}, {"items": {"type": "list"}}) != []


class TestLength:
    def test_max_length_exceeded(self):
        errors = validate({"s": "abcdef"}, {"s": {"max_length": 3}})
        assert errors and "max_length" in errors[0]["msg"]

    def test_min_length(self):
        errors = validate({"s": "ab"}, {"s": {"min_length": 5}})
        assert errors and "min_length" in errors[0]["msg"]

    def test_within_bounds(self):
        assert validate({"s": "abc"}, {"s": {"min_length": 1, "max_length": 5}}) == []


class TestEnum:
    def test_valid_enum_lowercased(self):
        # 문자열은 strip+lower 후 비교
        assert validate({"tone": "Friendly"}, {"tone": {"enum": ["friendly", "formal"]}}) == []

    def test_invalid_enum(self):
        errors = validate({"tone": "angry"}, {"tone": {"enum": ["friendly"]}})
        assert errors and "must be one of" in errors[0]["msg"]


class TestNumericRange:
    def test_min(self):
        errors = validate({"n": 0}, {"n": {"min": 1}})
        assert errors and "min 1" in errors[0]["msg"]

    def test_max(self):
        errors = validate({"n": 1000}, {"n": {"max": 999}})
        assert errors and "max 999" in errors[0]["msg"]

    def test_in_range(self):
        assert validate({"n": 5}, {"n": {"min": 1, "max": 10}}) == []


class TestDefault:
    def test_default_injected(self):
        data = {}
        validate(data, {"tone": {"default": "professional"}})
        assert data["tone"] == "professional"

    def test_default_skips_other_checks(self):
        # default 주입 시 enum 등 후속 검사 스킵
        data = {}
        errors = validate(data, {"tone": {"default": "x", "enum": ["a"]}})
        assert errors == []
        assert data["tone"] == "x"


class TestCombined:
    def test_multiple_errors(self):
        errors = validate(
            {"name": "", "age": "x"},
            {"name": {"required": True}, "age": {"type": "int"}},
        )
        assert len(errors) == 2

    def test_full_valid(self):
        errors = validate(
            {"prompt": "안녕", "tone": "friendly", "num": 5},
            {
                "prompt": {"required": True, "max_length": 100},
                "tone": {"enum": ["friendly", "formal"]},
                "num": {"type": "int", "min": 1, "max": 10},
            },
        )
        assert errors == []
