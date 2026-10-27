"""없는 이미지는 404 — v2.125.70 (2026-10-07 점검).

실측: node 클라이언트가 09-09 Wasabi 폐지로 사라진 구 이미지를 계속 요청했다.
- `GET /api/image/view/ollama-admin_00025_.png` → **503**(로그 0줄) ×3 — S3(객체 0개)·디스크에 없고,
  on-demand라 꺼져 있는 ComfyUI로 프록시하다 연결 거부. 맞는 답은 404.
- `GET /api/image/s3/url/images/20260630_…_00292_.png` → **200 + 열면 NoSuchKey인 URL** ×9(반복 클래스 #1).
잠재: ComfyUI가 떠 있으면 상태 코드를 무시해 404를 200으로 · S3 탐색이 앞 200키 **부분 일치**
(파일 카운터 리셋 뒤 같은 이름이 재등장하면 엉뚱한 이미지로 리다이렉트).
"""
import datetime as dt
import logging

import pytest
import requests
from flask import Flask

from core import s3_storage as S


@pytest.fixture(autouse=True, scope="module")
def _app_imported_first():
    # ⚠️ 임시 Flask에 image_bp를 **먼저** 등록하면, 그 뒤 `import app`이 블루프린트에 before_request
    #    (탭 권한 가드)를 붙이다 Flask가 거부한다 — 이 파일이 다른 app 임포트 테스트보다 먼저 돌 때만
    #    나는 순서 의존 실패였다(v2.125.70 작성 중 실측: 이 파일 → path_traversal 순서에서 3건 실패).
    import app  # noqa: F401,PLC0415


class _Resp:
    def __init__(self, status, content=b"PNGDATA", ctype="image/png"):
        self.status_code, self.content, self.headers = status, content, {"Content-Type": ctype}


@pytest.fixture
def view(monkeypatch, tmp_path):
    from routes import image as I
    monkeypatch.setattr(I, "_COMFYUI_BASE", "http://comfy")
    monkeypatch.setattr(I, "_get_image_dir", lambda: str(tmp_path))
    monkeypatch.setattr(I, "_apply_marking", lambda b, model=None, visible_override=None: b)
    monkeypatch.setattr(S, "is_enabled", lambda: True)
    monkeypatch.setattr(S, "find_image_key", lambda fn, prefix=None, max_pages=10: (None, None), raising=False)
    app = Flask(__name__)
    app.register_blueprint(I.image_bp)
    app.config["TESTING"] = True

    class V:
        I_ = I
        dir = tmp_path

        def comfy(self, behaviour):
            def fake_get(url, **kw):
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour
            monkeypatch.setattr(I.http_requests, "get", fake_get)

        def get(self, path):
            with app.test_client() as c:
                return c.get(path, headers={"User-Agent": "node"})
    return V()


# ── /api/image/view ────────────────────────────────────────────────────────
def test_missing_everywhere_with_comfyui_down_is_404_and_logged(view, caplog):
    view.comfy(requests.exceptions.ConnectionError("refused"))
    with caplog.at_level(logging.INFO, logger="ollama-admin"):
        r = view.get("/api/image/view/ollama-admin_00025_.png")
    assert r.status_code == 404                                          # 구: 503
    assert r.get_json() == {"error": "image not found"}
    log = "\n".join(x.getMessage() for x in caplog.records)
    assert "ollama-admin_00025_.png" in log and "ua=node" in log and "key=" in log


def test_comfyui_down_but_file_on_disk_is_served(view):
    (view.dir / "ollama-admin_00007_.png").write_bytes(b"\x89PNGdisk")
    view.comfy(requests.exceptions.ConnectionError("refused"))
    r = view.get("/api/image/view/ollama-admin_00007_.png")
    assert r.status_code == 200 and r.data == b"\x89PNGdisk"            # 구: 503


@pytest.mark.parametrize("upstream,expected", [(404, 404), (500, 502), (200, 200)])
def test_comfyui_status_is_respected(view, upstream, expected):
    view.comfy(_Resp(upstream, b"PNGDATA" if upstream == 200 else b'{"error":"x"}'))
    r = view.get("/api/image/view/ollama-admin_00003_.png")
    assert r.status_code == expected                                     # 구: 셋 다 200
    if expected == 200:
        assert r.data == b"PNGDATA"


def test_s3_hit_redirects_to_exact_key(view, monkeypatch):
    monkeypatch.setattr(S, "find_image_key",
                        lambda fn, prefix=None, max_pages=10: ("images/20261001_101010_" + fn, None), raising=False)
    monkeypatch.setattr(S, "get_presigned_url", lambda key, ttl=None: "https://pub/" + key)
    view.comfy(AssertionError("S3에서 찾았으면 ComfyUI를 부르면 안 된다"))
    r = view.get("/api/image/view/ollama-admin_00001_.png")
    assert r.status_code == 302 and r.headers["Location"].endswith("20261001_101010_ollama-admin_00001_.png")


# ── find_image_key: 정확 일치·최신·페이지네이션 ─────────────────────────────
class _Client:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def list_objects_v2(self, **kw):
        self.calls.append(kw)
        i = 0 if "ContinuationToken" not in kw else int(kw["ContinuationToken"])
        last = i == len(self.pages) - 1
        out = {"Contents": [{"Key": k, "LastModified": t} for k, t in self.pages[i]], "IsTruncated": not last}
        if not last:
            out["NextContinuationToken"] = str(i + 1)
        return out

    def head_object(self, **kw):
        raise AssertionError


def _t(d):
    return dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=d)


@pytest.fixture
def s3(monkeypatch):
    def use(client):
        monkeypatch.setattr(S, "_client", client)
        monkeypatch.setattr(S, "_config", {"bucket": "b", "prefix": "images/"})
        return client
    return use


def test_find_exact_newest_across_pages(s3):
    c = s3(_Client([
        [("images/20260601_000000_ollama-admin_00001_.png", _t(150)),      # 카운터 리셋 전 같은 이름(구)
         ("images/20261001_000000_ollama-admin_000010_.png", _t(270))],    # 부분 일치 미끼
        [("images/20261002_000000_ollama-admin_00001_.png", _t(275))],     # 2쪽에 있는 최신 정답
    ]))
    key, err = S.find_image_key("ollama-admin_00001_.png")
    assert err is None and key == "images/20261002_000000_ollama-admin_00001_.png"
    assert len(c.calls) == 2 and c.calls[1]["ContinuationToken"] == "1"


def test_find_does_not_match_partial_name(s3):
    s3(_Client([[("images/20261001_000000_ollama-admin_000010_.png", _t(1))]]))
    assert S.find_image_key("ollama-admin_00001_.png") == (None, None)
    # 구 코드의 `filename in key`라면 "_00001_.png"가 아니라 "0_.png"류에 걸릴 수 있다 — 정확 일치만


def test_find_surfaces_error():
    class Boom:
        def list_objects_v2(self, **kw):
            raise RuntimeError("AccountProblem")
    S_client, S_conf = S._client, S._config
    try:
        S._client, S._config = Boom(), {"bucket": "b", "prefix": "images/"}
        key, err = S.find_image_key("x.png")
    finally:
        S._client, S._config = S_client, S_conf
    assert key is None and "AccountProblem" in err


# ── object_exists + /api/image/s3/url ───────────────────────────────────────
class _ClientErr(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.mark.parametrize("code,expected", [("404", False), ("NoSuchKey", False), ("NotFound", False), ("403", None)])
def test_object_exists_codes(s3, code, expected):
    class C:
        def head_object(self, **kw):
            raise _ClientErr(code)
    s3(C())
    assert S.object_exists("images/x.png") is expected


def test_object_exists_true(s3):
    class C:
        def head_object(self, **kw):
            return {"ContentLength": 3}
    s3(C())
    assert S.object_exists("images/x.png") is True


@pytest.mark.parametrize("exists,status", [(False, 404), (None, 502), (True, 200)])
def test_s3_url_checks_existence(monkeypatch, exists, status):
    from routes import image as I
    monkeypatch.setattr(S, "is_enabled", lambda: True)
    monkeypatch.setattr(S, "key_allowed", lambda k: True)
    monkeypatch.setattr(S, "object_exists", lambda k: exists, raising=False)
    monkeypatch.setattr(S, "get_presigned_url", lambda key, ttl=None: "https://pub/" + key)
    app = Flask(__name__)
    app.register_blueprint(I.image_bp)
    with app.test_client() as c:
        r = c.get("/api/image/s3/url/images/20260630_013417_ollama-admin_00292_.png")
    assert r.status_code == status                                       # 구: 셋 다 200
    if status == 200:
        assert r.get_json()["url"].startswith("https://pub/images/")
