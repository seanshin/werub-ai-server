"""미검증 보안 7건 재현·판정 후 처치 회귀 (v2.125.18).

2026-08-25 재현 판정 결과: 확진 4(①②④⑦) · 부분정정 2(③⑤) · 위험낮음 1(⑥).
여기서 고정하는 것은 확진 3건의 처치다(⑦ CORS·⑥ 문자열은 별건·경미).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── ① medical 테넌트 플레이스홀더 우회 봉합 ────────────────────────────────
def test_medical_placeholder_key_rejected():
    """`hashed:{id}` 리터럴로는 인증되지 않는다(hr_law.py:696과 같은 제외 절)."""
    import inspect
    from routes import medical as M
    src = inspect.getsource(M._resolve_medical_tenant)
    # 플레이스홀더를 명시적으로 거른다
    assert 'startswith("hashed:")' in src
    assert "api_key NOT LIKE 'hashed:%'" in src or "api_key<>''" in src


def test_medical_legacy_query_excludes_placeholder_rows():
    """SQL이 placeholder 행을 레거시 평문 매칭에서 뺀다 — 컬럼값=입력이어도 안 걸린다."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE medical_tenants(id TEXT, api_key TEXT, is_active INT)")
    conn.execute("INSERT INTO medical_tenants VALUES('t1','hashed:t1',1)")
    # 처치된 쿼리 형태
    row = conn.execute(
        "SELECT * FROM medical_tenants "
        "WHERE api_key=? AND api_key<>'' AND api_key NOT LIKE 'hashed:%' AND is_active=1",
        ("hashed:t1",)).fetchone()
    assert row is None, "플레이스홀더가 레거시 평문 매칭을 통과하면 안 된다"


# ── ④ /api/delete·/api/pull admin 게이트 ───────────────────────────────────
def test_destructive_ollama_routes_require_admin():
    import inspect
    from routes import ollama as O
    for fn in (O.api_delete, O.api_pull):
        src = inspect.getsource(fn)
        assert "_is_admin_request()" in src, "%s에 admin 게이트가 없다" % fn.__name__
        assert "403" in src


# ── ② RAG 보호 컬렉션 차단 ─────────────────────────────────────────────────
def test_rag_blocks_protected_collections():
    from routes import rag as R
    from flask import Flask
    with Flask(__name__).app_context():          # jsonify는 앱 컨텍스트가 필요
        for c in ("medical_knowledge", "medical_law_knowledge",
                  "medical_pub_knowledge", "notice_knowledge"):
            blk = R._collection_blocked(c)
            assert blk is not None and blk[1] == 403, c
    assert R._PROTECTED_COLLECTIONS >= {"medical_knowledge", "medical_law_knowledge",
                                        "medical_pub_knowledge", "notice_knowledge"}


def test_rag_allows_partner_collections():
    from routes import rag as R
    for c in ("werub_docs", "cerno_org", "edu_docs", "cerno_priv"):
        assert R._collection_blocked(c) is None, c


def test_rag_all_entry_points_wired():
    """5개 진입점(index 단건·배치·search·ask·delete)이 모두 차단을 호출한다."""
    import inspect
    from routes import rag as R
    for fn in (R.rag_index, R.rag_search_api, R.rag_ask, R.rag_delete):
        assert "_collection_blocked" in inspect.getsource(fn), fn.__name__
