"""Wasabi → MinIO 전환 회귀 (v2.125.41) — 2026-09-09 라이브 결함 2건을 고정한다.

라이브에서 관측된 것(처치 전):
    GET /api/image/s3/status → {"accessible": false,
                                "error": "(AccountProblem) ... Cannot find user for the access key"}
    GET /api/image/s3/list   → **200 {"count": 0, "images": []}**   ← 조회 실패가 "0건"으로 보인다

🔑 두 번째가 이 파일의 본론이다. 상태 엔드포인트는 정직했는데 목록 엔드포인트는 같은 예외를
빈 리스트로 삼켰다(CLAUDE.md 결함 클래스 1 — "200인데 빈 결과"). 저장소를 바꾸는 것과
**실패가 보이게 만드는 것**은 별개의 처치라 둘 다 고정한다.

세 번째 축은 presigned URL의 서명 호스트다. MinIO는 127.0.0.1:9000 바인딩이라 그 주소로
서명하면 서버 밖에서 못 연다. SigV4는 host 헤더를 서명에 포함하므로 **외부가 접속할 호스트로
서명**해야 하고, 버킷을 서브도메인으로 붙이는 가상호스트 스타일은 DNS가 없어 쓸 수 없다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("boto3")

BASE_CFG = {
    "s3_enabled": True,
    "s3_endpoint": "http://127.0.0.1:9000",
    "s3_access_key": "testkey",
    "s3_secret_key": "testsecret",
    "s3_bucket": "werub-ai-images",
    "s3_region": "ap-northeast-1",
    "s3_prefix": "images/",
}


@pytest.fixture(autouse=True)
def _reset_s3():
    """모듈 전역 클라이언트가 테스트 간에 새지 않도록."""
    from core import s3_storage
    yield
    s3_storage._client = None
    s3_storage._signer = None
    s3_storage._config = {}


# ── ① presigned URL은 공개 엔드포인트로 서명된다 ─────────────────────────────
def test_presigned_url_signed_with_public_endpoint():
    from core import s3_storage

    cfg = dict(BASE_CFG, s3_public_endpoint="https://example.org")
    assert s3_storage.init_s3(cfg) is True

    url = s3_storage.get_presigned_url("images/x.png")
    assert url.startswith("https://example.org/"), url
    assert "127.0.0.1" not in url          # 내부 주소가 외부로 새지 않는다
    assert "X-Amz-Signature=" in url


def test_presigned_url_falls_back_to_internal_endpoint_when_public_absent():
    """공개 엔드포인트 미설정 = 종전 동작(내부 전용). 조용히 바뀌면 안 된다."""
    from core import s3_storage

    assert s3_storage.init_s3(dict(BASE_CFG)) is True
    url = s3_storage.get_presigned_url("images/x.png")
    assert url.startswith("http://127.0.0.1:9000/"), url


# ── ② 주소 스타일: 버킷은 경로에 온다(가상호스트면 MinIO에 안 닿는다) ────────
def test_path_addressing_keeps_bucket_out_of_hostname():
    from core import s3_storage

    cfg = dict(BASE_CFG, s3_public_endpoint="https://example.org")
    s3_storage.init_s3(cfg)
    url = s3_storage.get_presigned_url("images/x.png")

    assert url.startswith("https://example.org/werub-ai-images/images/x.png"), url
    assert not url.startswith("https://werub-ai-images."), "가상호스트 스타일이면 DNS가 없어 못 연다"


# ── ③ 목록 조회 실패는 빈 결과로 삼켜지지 않는다 ─────────────────────────────
def test_list_objects_ex_surfaces_error():
    from core import s3_storage

    s3_storage.init_s3(dict(BASE_CFG))

    class _Boom:
        def list_objects_v2(self, **kw):
            raise RuntimeError("AccountProblem: Cannot find user for the access key")

    s3_storage._client = _Boom()
    res = s3_storage.list_objects_ex()
    assert res["items"] == []
    assert "AccountProblem" in res["error"]      # 실패가 호출자에게 도달한다


def test_list_objects_ex_ok_path_has_no_error():
    from core import s3_storage
    import datetime as _dt

    s3_storage.init_s3(dict(BASE_CFG))

    class _Ok:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": "images/a.png", "Size": 3,
                                  "LastModified": _dt.datetime(2026, 9, 9)}]}

    s3_storage._client = _Ok()
    res = s3_storage.list_objects_ex()
    assert res["error"] is None
    assert [i["key"] for i in res["items"]] == ["images/a.png"]


def test_list_objects_legacy_still_returns_list_for_fallback_callers():
    """`/api/image/view`는 S3 실패 시 디스크로 폴백해야 하므로 예외를 올리면 안 된다."""
    from core import s3_storage

    s3_storage.init_s3(dict(BASE_CFG))

    class _Boom:
        def list_objects_v2(self, **kw):
            raise RuntimeError("nope")

    s3_storage._client = _Boom()
    assert s3_storage.list_objects() == []


# ── ④ 라우트: 조회 실패는 502로 나간다(200+빈 목록 금지) ─────────────────────
def test_s3_list_route_returns_502_on_backend_error(monkeypatch):
    import app as A
    from core import s3_storage

    s3_storage.init_s3(dict(BASE_CFG))

    class _Boom:
        def list_objects_v2(self, **kw):
            raise RuntimeError("AccountProblem")

    s3_storage._client = _Boom()

    r = A.app.test_client().get("/api/image/s3/list?limit=5")
    assert r.status_code == 502, r.data[:200]
    body = r.get_json()
    assert body["count"] == 0 and "AccountProblem" in body["error"]


# ── ⑤ 소스 규율: 사용자 표면은 삼키는 변형을 쓰지 않는다 ─────────────────────
def test_s3_list_route_does_not_use_swallowing_variant():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "routes", "image.py")).read()
    i = src.index("def api_image_s3_list")
    seg = src[i:i + 1600]
    assert "list_objects_ex" in seg
    assert "items = list_objects(" not in seg
