"""응답 에러 위생 (보안 L1) — 내부 예외 메시지가 500 응답 바디로 새지 않는다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_internal_error_generic_body():
    import flask
    from core.errors import internal_error
    app = flask.Flask(__name__)
    with app.app_context():
        resp, status = internal_error(ValueError("/secret/path/leak.py line 42"))
    assert status == 500
    body = resp.get_json()
    assert body["error"] == "internal server error"
    assert "secret" not in str(body) and "leak" not in str(body)  # 내부 상세 미노출


def test_no_unredacted_str_e_in_500_responses():
    """레포 전체에서 무절단 str(e)를 500과 함께 반환하는 라우트가 없어야 한다.

    [:200] 절단은 의도적 방어라 허용, 무절단 노출만 금지(L1).
    """
    import glob
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for f in glob.glob(os.path.join(root, "routes", "*.py")) + [os.path.join(root, "app.py")]:
        for ln, line in enumerate(open(f).read().splitlines(), 1):
            if ("str(e)" in line and "), 500" in line
                    and "str(e)[:" not in line and "internal_error" not in line):
                offenders.append((os.path.basename(f), ln, line.strip()[:70]))
    assert not offenders, f"무절단 str(e) 500 노출: {offenders}"


def test_no_plaintext_api_key_storage():
    """API 키 평문 저장 재발 차단 가드 (v2.124.46).

    발급 응답이 원본을 보는 유일한 기회다. 코드가 다시 key_value에 쓰거나 읽으면
    DB·백업 유출 시 키가 그대로 재사용되므로 CI에서 막는다.
    """
    import glob
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = ([os.path.join(root, "app.py")]
               + glob.glob(os.path.join(root, "routes", "*.py"))
               + glob.glob(os.path.join(root, "core", "*.py")))
    offenders = []
    for path in targets:
        src = open(path, encoding="utf-8").read()
        for line in src.splitlines():
            if "key_value" not in line or line.lstrip().startswith("#"):
                continue
            if re.search(r"(INSERT|UPDATE|SELECT)[^\n]*key_value", line, re.I):
                offenders.append(f"{os.path.basename(path)}: {line.strip()[:90]}")
    assert not offenders, "api_keys.key_value 평문 사용 재발: " + " | ".join(offenders)
