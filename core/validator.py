"""Input Validation Module — Flask 독립, 순수 유효성 검사

Usage:
    from core.validator import validate

    errors = validate(data, {
        "prompt": {"required": True, "max_length": 50000},
        "tone": {"enum": ["professional", "friendly"], "default": "professional"},
        "chapter_num": {"required": True, "type": "int", "min": 1, "max": 999},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400
"""


def validate(data: dict, schema: dict) -> list:
    """데이터를 스키마에 따라 검증. 에러 리스트 반환 (빈 리스트 = 통과).

    스키마 옵션:
        required (bool): 필수 필드 여부
        type (str): "str", "int", "float", "list", "dict"
        max_length (int): 문자열 최대 길이
        min_length (int): 문자열 최소 길이
        enum (list): 허용 값 목록
        min (number): 최솟값 (int/float)
        max (number): 최댓값 (int/float)
        default: 값이 없을 때 data에 주입할 기본값
    """
    errors = []

    for field, rules in schema.items():
        value = data.get(field)

        # --- default 처리 ---
        if value is None and "default" in rules:
            data[field] = rules["default"]
            continue

        # --- required 체크 ---
        if rules.get("required"):
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append({"field": field, "msg": "required"})
                continue

        # 값이 없고 필수도 아니면 스킵
        if value is None:
            continue

        # --- type 체크 ---
        expected_type = rules.get("type")
        if expected_type:
            type_map = {
                "str": str, "string": str,
                "int": int, "integer": int,
                "float": (int, float),
                "list": list, "array": list,
                "dict": dict, "object": dict,
            }
            py_type = type_map.get(expected_type)
            if py_type and not isinstance(value, py_type):
                errors.append({"field": field, "msg": f"must be {expected_type}"})
                continue

        # --- str 길이 체크 ---
        if isinstance(value, str):
            v = value.strip()
            max_len = rules.get("max_length")
            if max_len and len(v) > max_len:
                errors.append({"field": field, "msg": f"max_length {max_len} exceeded ({len(v)})"})
            min_len = rules.get("min_length")
            if min_len and len(v) < min_len:
                errors.append({"field": field, "msg": f"min_length {min_len} (got {len(v)})"})

        # --- enum 체크 ---
        enum_vals = rules.get("enum")
        if enum_vals is not None:
            check_val = value.strip().lower() if isinstance(value, str) else value
            if check_val not in enum_vals:
                errors.append({"field": field, "msg": f"must be one of: {enum_vals}"})

        # --- 숫자 범위 체크 ---
        if isinstance(value, (int, float)):
            min_val = rules.get("min")
            if min_val is not None and value < min_val:
                errors.append({"field": field, "msg": f"min {min_val} (got {value})"})
            max_val = rules.get("max")
            if max_val is not None and value > max_val:
                errors.append({"field": field, "msg": f"max {max_val} (got {value})"})

    return errors
