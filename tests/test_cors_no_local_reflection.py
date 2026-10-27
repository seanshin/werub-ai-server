"""⑦ CORS 로컬 오리진 반사 제거 고정 (v2.125.22 · v2.125.18 확진분).

종전 `add_cors_headers`는 `is_local_request()`면 **임의 Origin을 반사**했다 —
ssh -L 터널을 연 개발자의 브라우저에서 악성 사이트가 localhost:8585로 요청하면
반사된 CORS 헤더 + 로컬 인증 bypass의 결합으로 교차 출처 읽기가 가능했다.
처치 후 반사 조건은 화이트리스트(`allowed_origins`)와 /v1 표면(키 인증 보호)뿐.

app.py는 테스트에서 import 불가(전체 앱 기동)라 소스 스캔으로 고정한다
(v2.125.16 규율 — 패턴 재유입 차단).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _cors_fn_body():
    with open(_APP, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"def add_cors_headers\(.*?(?=\n@app\.|\ndef )", src, re.S)
    assert m, "add_cors_headers 함수를 찾지 못함 — 이 테스트의 추출 정규식을 갱신할 것"
    return m.group(0)


def test_local_request_clause_removed():
    # 주석 제거 후 검사 — 처치 주석 자체가 그 문자열을 언급한다
    body = "\n".join(ln.split("#", 1)[0] for ln in _cors_fn_body().splitlines())
    assert "is_local_request()" not in body, (
        "CORS 반사 조건에 is_local_request()가 다시 들어왔다 — "
        "로컬 bypass와 결합해 교차 출처 읽기가 되살아난다(v2.125.18 ⑦)")


def test_whitelist_and_v1_reflection_kept():
    body = _cors_fn_body()
    assert "origin in allowed" in body
    assert "is_v1_surface" in body
    assert 'Access-Control-Allow-Origin"] = origin' in body
