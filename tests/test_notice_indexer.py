"""erp-notice-rag Day 1 테스트 — 정규화 7항 / content_hash / 청크 분할 / notice.db 스키마.

전 플랫폼 (순수 core 단위 — 기존 157건 컨벤션). 외부 의존 0.
"""
import json
import os
import sqlite3

import pytest

from core.notice_indexer import (
    Chunk, chunk_notice, content_hash, normalize_body, verify_hash,
)

DATA = os.path.join(os.path.dirname(__file__), "data", "notice_hash_vectors.json")


# ── 정규화 7항 — 항목별 단위 검증 ──────────────────────────

def test_norm_1_bom_removed():
    assert normalize_body("﻿본문") == "본문\n".encode()


def test_norm_2_nfc():
    nfd = "가" + "가"  # NFC '가' + NFD '가'
    out = normalize_body(nfd).decode()
    assert out == "가가\n" and "ᄀ" not in out


def test_norm_3_newlines_unified():
    assert normalize_body("a\r\nb\rc\nd") == b"a\nb\nc\nd\n"


def test_norm_4_trailing_ws_stripped_5_final_newline():
    assert normalize_body("줄1   \n줄2\t\n\n\n") == "줄1\n줄2\n".encode()


def test_norm_6_inner_spaces_preserved():
    """표 서식 보호 — 행 내부 연속 공백은 그대로."""
    out = normalize_body("코드      항목명").decode()
    assert "코드      항목명" in out


def test_norm_empty_and_none():
    assert normalize_body("") == b""
    assert normalize_body("\n\n\n") == b""
    assert normalize_body(None) == b""


def test_hash_idempotent_after_normalization():
    """정규화된 본문을 다시 해시해도 동일 (멱등) — ERP 재발신 안전."""
    raw = "﻿제1조  \r\n내용\r\n\r\n"
    normalized = normalize_body(raw).decode()
    assert content_hash(raw) == content_hash(normalized)


# ── 테스트 벡터 (ERP 교환본) ───────────────────────────────

def test_exchange_vectors():
    with open(DATA, encoding="utf-8") as f:
        spec = json.load(f)
    assert len(spec["vectors"]) == 3
    for v in spec["vectors"]:
        assert content_hash(v["body_raw"]) == v["expected_hash"], v["id"]


def test_verify_hash_prefix_tolerant():
    h = content_hash("본문")
    assert verify_hash("본문", h)
    assert verify_hash("본문", h.replace("sha256:", ""))   # 프리픽스 없이도 허용
    assert verify_hash("본문", h.upper().replace("SHA256:", "sha256:"))
    assert not verify_hash("본문", "sha256:" + "0" * 64)
    assert not verify_hash("본문", "")


# ── 청크 분할 ──────────────────────────────────────────────

LAW_TEXT = """보건복지부 고시 제2026-141호

제1조(목적) 이 고시는 요양급여의 기준을 정함을 목적으로 한다.

제2조(적용범위) 이 고시는 의료기관에 적용한다.

제12조의2(특례) 다음 각 호의 경우 특례를 적용한다.
1. 응급 상황
2. 야간 진료

부칙
이 고시는 2026년 7월 1일부터 시행한다.

[별표 1] 수가 산정 기준표
AA100 초음파(복부) 150.5점
"""


def test_chunk_article_split():
    chunks = chunk_notice(LAW_TEXT)
    kinds = {c.kind for c in chunks}
    assert kinds == {"article"}
    texts = [c.text for c in chunks]
    assert any(t.startswith("제1조") for t in texts)
    assert any(t.startswith("제12조의2") for t in texts)
    assert any(t.startswith("부칙") for t in texts)
    assert any(t.startswith("[별표 1]") for t in texts)
    assert [c.seq for c in chunks] == list(range(len(chunks)))  # seq 연속


def test_chunk_sliding_fallback():
    """조문 패턴 없는 비정형 문서 → 512자 슬라이딩."""
    text = "안내문입니다. " * 200  # ~1400자, 조문 없음
    chunks = chunk_notice(text)
    assert all(c.kind == "sliding" for c in chunks)
    assert len(chunks) >= 2
    assert all(len(c.text) <= 512 for c in chunks)


def test_chunk_oversized_article_resplit():
    """1500자 초과 조문은 내부 재분할."""
    big = "제1조(목적) " + "내용 " * 600 + "\n제2조(기타) 짧은 조문."
    chunks = chunk_notice(big)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 1500 for c in chunks)


def test_chunk_empty():
    assert chunk_notice("") == []
    assert chunk_notice("\n\n") == []


# ── notice.db 스키마 ───────────────────────────────────────

def test_notice_db_schema(tmp_path):
    from core import notice_db
    conn = notice_db.get_notice_db(str(tmp_path / "notice.db"))
    # UNIQUE(erp_ref_id, revision_no)
    conn.execute("INSERT INTO notices (id, erp_ref_id, revision_no, notice_type, title, body, effective_from) "
                 "VALUES ('ntc-1', 'ERP-1', 1, 'REVIEW_CRITERIA', 't', 'b', '2026-07-01')")
    conn.execute("INSERT INTO notices (id, erp_ref_id, revision_no, notice_type, title, body, effective_from) "
                 "VALUES ('ntc-2', 'ERP-1', 2, 'REVIEW_CRITERIA', 't', 'b', '2026-07-01')")  # 개정 OK
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO notices (id, erp_ref_id, revision_no, notice_type, title, body, effective_from) "
                     "VALUES ('ntc-3', 'ERP-1', 1, 'REVIEW_CRITERIA', 't', 'b', '2026-07-01')")  # 중복 차단
    # 색인 잡 + client_request_id 조회
    conn.execute("INSERT INTO notice_index_jobs (notice_id, client_request_id, action) "
                 "VALUES ('ntc-1', 'erp-req-1', 'index')")
    row = conn.execute("SELECT * FROM notice_index_jobs WHERE client_request_id='erp-req-1'").fetchone()
    assert row["status"] == "pending" and row["mode"] == "nightly"
    # WAL 모드 확인
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # thread-local 캐시 분리 (다른 경로 요청 시 새 연결)
    conn2 = notice_db.get_notice_db(str(tmp_path / "other.db"))
    assert conn2 is not conn
