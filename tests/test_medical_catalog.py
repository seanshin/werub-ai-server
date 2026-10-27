"""의료 데이터 카탈로그 — 내부 시스템 연동용 단일 발견 지점의 계약.

왜 만들었나(2026-08-31 실측): 의료 엔드포인트가 **26개 태그로 흩어져** 있었다
(`Medical - DUR`·`Medical - DrugInfo`·`Notice`·`Medical Law`… 1~3개짜리 태그가 다수).
전체 622경로 안에서 연동자가 "무슨 의료 데이터가 있고 어떤 API로 꺼내는가"를 알 방법이
문서 통독뿐이었다. Swagger 문서화 자체는 이미 105/108 라우트에 있었으므로 공백은
**문서가 아니라 발견 지점**이었다.

이 파일이 고정하는 것:
  ① 데이터셋별 **fail-open** — 저장소 하나가 없어도 나머지를 낸다(그 항목만 available:false+error)
  ② **결측은 null** — 최신성을 못 재면 0이 아니라 null(0으로 세면 '방금 갱신됨'으로 보인다)
  ③ 모든 데이터셋이 **서빙 엔드포인트를 최소 1개** 들고 있다(카탈로그의 존재 이유)
  ④ 카탈로그가 가리키는 엔드포인트가 **실제로 코드에 존재**한다(문서-코드 드리프트 차단)
  ⑤ 집계만 낸다 — 레코드 본문·PHI 없음
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import medical_catalog as mc


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _routes_src() -> str:
    out = []
    for fn in os.listdir(os.path.join(ROOT, "routes")):
        if fn.endswith(".py"):
            with open(os.path.join(ROOT, "routes", fn), encoding="utf-8") as f:
                out.append(f.read())
    return "\n".join(out)


# ── 정의 자체의 무결성(저장소 없이도 검증 가능) ─────────────────────────────
def test_every_dataset_declares_a_serving_endpoint():
    """서빙 경로가 없는 데이터셋은 카탈로그에 실을 이유가 없다."""
    for did, label, source, db, table, endpoints, _log in mc._DATASETS:
        assert endpoints, f"{did}: 서빙 엔드포인트 미선언"
        assert label and source and db and table, did


def test_dataset_ids_are_unique():
    ids = [d[0] for d in mc._DATASETS] + [r[0] for r in mc._RAG_COLLECTIONS]
    assert len(ids) == len(set(ids)), "중복 id는 소비자 쪽 매핑을 깨뜨린다"


@pytest.mark.parametrize("path", sorted({
    ep.split(" ", 1)[1] for _d in mc._DATASETS for ep in _d[5]
} | {
    ep.split(" ", 1)[1] for _r in mc._RAG_COLLECTIONS for ep in _r[3]
}))
def test_declared_endpoints_exist_in_code(path):
    """🔑 카탈로그가 없는 API를 가리키면 연동자를 잘못된 전제로 보낸다.

    `{code}` 같은 자리표시자는 Flask 규칙(`<code>`)으로 바꿔 대조한다.
    """
    flask_path = re.sub(r"\{(\w+)\}", r"<\1>", path)
    src = _routes_src()
    assert f'"{flask_path}"' in src, f"{path} 가 라우트에 없다"


def test_sync_endpoints_exist_in_code():
    src = _routes_src()
    ce = mc.build.__globals__  # noqa: F841  (문서 목적)
    for path in ("/api/medical/dur/sync", "/api/notice/hira-drug/sync",
                 "/api/medical/dur/sync-status", "/api/notice/hira-drug/sync-status",
                 "/api/notice/collect/logs"):
        assert f'"{path}"' in src, path


# ── fail-open / 결측 처리 ───────────────────────────────────────────────────
def test_missing_store_is_reported_not_swallowed(monkeypatch):
    """🔴 저장소가 없을 때 조용히 rows=0으로 내면 '데이터 없음'과 구분이 안 된다."""
    def _boom(name):
        raise FileNotFoundError(name)
    monkeypatch.setattr(mc, "_db", _boom)
    out = mc.build(include_rag=False)
    assert out["datasets"], "전멸해도 목록 자체는 나와야 한다"
    for d in out["datasets"]:
        assert d["available"] is False
        assert d["rows"] is None, "미측정을 0으로 세지 않는다"
        assert "error" in d and d["error"]
    assert out["summary"]["datasets_available"] == 0
    assert out["summary"]["datasets_unavailable"] == len(out["datasets"])


def test_one_broken_store_does_not_sink_the_rest(monkeypatch):
    """dur.db만 깨져도 notice.db 데이터셋은 나와야 한다(fail-open)."""
    real = mc._db

    def _partial(name):
        if name == "dur.db":
            raise sqlite_err()
        return real(name)

    def sqlite_err():
        import sqlite3
        return sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(mc, "_db", _partial)
    out = mc.build(include_rag=False)
    dur = [d for d in out["datasets"] if d["store"] == "dur.db"]
    assert dur and all(d["available"] is False for d in dur)


def test_age_days_is_none_when_unmeasured():
    assert mc._age_days(None) is None
    assert mc._age_days("not-a-timestamp") is None


def test_age_days_parses_repo_timestamp_shape():
    """레포의 `…+00:00Z` 이형 타임스탬프(X1에서 파싱을 깨뜨렸던 그 모양)."""
    assert mc._age_days("2026-08-31T00:00:00+00:00Z") is not None
    assert mc._age_days("2026-08-31T00:00:00Z") is not None


def test_stalest_age_ignores_unmeasured():
    """미측정(null)이 max에 섞여 들어가면 예외이거나 잘못된 최댓값이 된다."""
    out = mc.build(include_rag=False)
    s = out["summary"]["stalest_age_days"]
    assert s is None or isinstance(s, int)


# ── 노출 범위 ───────────────────────────────────────────────────────────────
def test_catalog_exposes_aggregates_only():
    """레코드 본문이 새어 나가면 PHI 경로가 된다 — 허용 키를 화이트리스트로 고정."""
    out = mc.build(include_rag=False)
    allowed = {"id", "label", "source", "store", "endpoints", "available", "rows",
               "last_sync_at", "last_sync_status", "age_days", "error"}
    for d in out["datasets"]:
        assert set(d) <= allowed, f"예상 밖 키: {set(d) - allowed}"


def test_route_is_registered_with_swagger_tag():
    with open(os.path.join(ROOT, "routes/medical_advanced.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"/api/medical/catalog"' in src
    assert "tags: [Medical - Catalog]" in src
    assert "@require_auth" in src[src.index('"/api/medical/catalog"'):
                                  src.index('"/api/medical/catalog"') + 200]


# ── Swagger 배치 ────────────────────────────────────────────────────────────
def _app_src() -> str:
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
        return f.read()


def test_declared_swagger_tags_cover_medical_surface():
    """미선언 태그는 /docs에서 설명 없이 알파벳순으로 흩어진다 — 연동자가 못 찾는다."""
    src = _app_src()
    block = src[src.index('swagger_template = {'):src.index('swagger = Swagger(app')]
    for tag in ("Medical - Catalog", "Medical - DUR", "Medical - DrugInfo", "Medical - Code",
                "Medical Law", "Medical - Knowledge", "Notice", "Medical - Clinical",
                "Medical - Triage", "Medical Imaging"):
        assert f'"name": "{tag}"' in block, f"{tag} 미선언"


def test_catalog_tag_is_declared_first():
    """카탈로그가 진입점이므로 목록 맨 위에 와야 한다(Swagger UI는 선언 순서를 따른다)."""
    src = _app_src()
    block = src[src.index('"tags": ['):src.index('swagger = Swagger(app')]
    names = re.findall(r'\{"name": "([^"]+)"', block)
    assert names and names[0] == "Medical - Catalog", names[:3]


def test_api_description_points_to_the_catalog():
    """제목만 바꾸고 진입점을 안 적으면 배치의 목적이 사라진다."""
    src = _app_src()
    head = src[src.index('swagger_template = {'):src.index('"host"')]
    assert "/api/medical/catalog" in head


def test_catalog_endpoints_have_prose_descriptions():
    """카탈로그가 가리키는 경로 = 내부 시스템의 실제 연동 표면.

    한 줄 summary만 있으면 연동자가 "무엇을 넣고 무엇을 받는지"를 코드로 확인해야 한다.
    flasgger는 docstring 첫 줄을 summary로, `---` 앞 본문을 description으로 싣는다
    (2026-08-31 라이브 확인 — 처음엔 'flasgger가 본문을 안 싣는다'고 잘못 판단했다).
    """
    import re as _re
    from core import medical_catalog as _mc
    want = {tuple(ep.split(" ", 1))
            for d in _mc._DATASETS for ep in d[5]}
    want |= {tuple(ep.split(" ", 1))
             for r in _mc._RAG_COLLECTIONS for ep in r[3]}
    srcs = _routes_src()
    missing = []
    for _m, path in sorted(want):
        flask_path = _re.sub(r"\{(\w+)\}", r"<\1>", path)
        i = srcs.find(f'"{flask_path}"')
        if i < 0:
            continue
        seg = srcs[i:i + 2500]
        j = seg.find('"""')
        if j < 0:
            continue
        body = seg[j + 3:seg.find("---", j)]
        # 첫 줄(summary) 다음에 산문이 있어야 한다
        prose = [ln for ln in body.split("\n")[1:] if ln.strip()]
        if not prose:
            missing.append(path)
    assert not missing, f"설명 없는 연동 경로 {len(missing)}: {missing}"


def test_catalog_endpoints_document_response_schema():
    """연동 표면은 **응답 필드**까지 알려야 한다 — 상태코드만으론 파싱 코드를 못 짠다.

    필드명은 코드의 실제 반환에서 뽑은 것만 적었다(지어낸 스키마 금지).
    """
    import ast as _ast
    from core import medical_catalog as _mc
    want = {tuple(ep.split(" ", 1)) for d in _mc._DATASETS for ep in d[5]}
    want |= {tuple(ep.split(" ", 1)) for r in _mc._RAG_COLLECTIONS for ep in r[3]}
    import re as _re
    srcs = _routes_src()
    missing = []
    for _m, path in sorted(want):
        flask_path = _re.sub(r"\{(\w+)\}", r"<\1>", path)
        i = srcs.find(f'"{flask_path}"')
        if i < 0:
            continue
        seg = srcs[i:i + 6000]
        j = seg.find('"""')
        k = seg.find('"""', j + 3)
        doc = seg[j:k]
        if "responses:" in doc and "schema:" not in doc:
            missing.append(path)
    assert not missing, f"응답 스키마 없는 연동 경로: {missing}"


def test_medical_law_schema_carries_the_disclaimer_field():
    """🔴 법률 자문이 아니라는 고지는 **응답 필드**로 나간다 — 스펙에 없으면
    연동 화면이 그 필드를 렌더하지 않는다."""
    src = _routes_src()
    for path in ("/api/medical-law/ask", "/api/medical-law/review"):
        i = src.find(f'"{path}"')
        seg = src[i:i + 6000]
        doc = seg[seg.find('"""'):seg.find('"""', seg.find('"""') + 3)]
        assert "disclaimer" in doc, path
