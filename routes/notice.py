"""ERP 공시 수명주기 API — erp-notice-rag Day 2.

설계: docs/02-design/features/erp-notice-rag.design.md §4
- 수명주기 5종: 등록/일괄/개정/폐지/삭제 (+색인 잡 생성 — 잡 소비는 Day 3 워커)
- 멱등성: 같은 (erp_ref_id, revision_no) 재등록 시 해시 동일→200, 상이→409
- content_hash 대조: ERP 송신 해시 ≠ 당측 재계산 → 422 (정규화 7항)
- 인증: app.py 등록 시 _blueprint_auth(Bearer) + limiter 600/min (ERP batch 여유)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

from core.config import OLLAMA_BASE, load_config
from core.fee_codes import fee_stats, import_fee_codes, resolve_notice_types
from core.drug_substitution import lookup as sub_lookup, stats as sub_stats
from core.drug_master import resolve as dm_resolve, stats as dm_stats

from core.notice_db import (
    NOTICE_TYPES, STATUS_ACTIVE, STATUS_REVOKED, STATUS_SUPERSEDED, get_notice_db,
)
from core.notice_indexer import content_hash, normalize_body, verify_hash
from core.notice_processor import (
    NOTICE_COLLECTION, process_immediate_async, process_pending_jobs,
)

from core.cache import TTLCache

logger = logging.getLogger("ollama-admin")

notice_bp = Blueprint("notice", __name__)

MAX_BODY_BYTES = 1024 * 1024   # 1MB (정규화 후)
MAX_BATCH = 100

# 약물 데이터 스택 집계(4-DB·drug_master 305K 스캔 ~50ms) 캐시 — ingest/sync 시점만 변동
_DRUG_DATA_STATUS_CACHE = TTLCache(maxsize=1, ttl=120)

_db_path_override = None  # 테스트 주입용


def init_notice(db_path: str | None = None):
    global _db_path_override
    _db_path_override = db_path


def _db():
    return get_notice_db(_db_path_override)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _new_id() -> str:
    return "ntc-" + uuid.uuid4().hex[:12]


def _resolve_notice_id(ident):
    """식별자(ntc-id 또는 ERP erp_ref_id) → 내부 ntc-id. 없으면 None.
    erp_ref_id는 최신 revision(revision_no 최대) 우선. ERP가 자기 erp_ref_id로
    revoke/조회 호출 시 404 방지."""
    if not ident:
        return None
    conn = _db()
    row = conn.execute("SELECT id FROM notices WHERE id=?", (ident,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT id FROM notices WHERE erp_ref_id=? ORDER BY revision_no DESC LIMIT 1",
        (ident,)).fetchone()
    return row["id"] if row else None


def _validate(p: dict) -> tuple[dict | None, str | None]:
    """페이로드 검증 → (정제본, 오류메시지). 오류 시 정제본 None."""
    required = ("erp_ref_id", "notice_type", "title", "body", "effective_from")
    missing = [k for k in required if not p.get(k)]
    if missing:
        return None, f"required: {', '.join(missing)}"
    if p["notice_type"] not in NOTICE_TYPES:
        return None, f"invalid notice_type (allowed: {', '.join(NOTICE_TYPES)})"
    body_bytes = normalize_body(p["body"])
    if len(body_bytes) > MAX_BODY_BYTES:
        return None, f"body exceeds {MAX_BODY_BYTES} bytes after normalization"
    if not body_bytes:
        return None, "body empty after normalization"
    claimed = p.get("content_hash")
    if claimed and not verify_hash(p["body"], claimed):
        return None, "content_hash mismatch (정규화 7항 스펙 확인 — tests/data/notice_hash_vectors.json)"
    mode = p.get("mode", "nightly")
    if mode not in ("nightly", "immediate"):
        return None, "mode must be nightly|immediate"
    try:
        revision_no = int(p.get("revision_no", 1))
    except (TypeError, ValueError):
        return None, "revision_no must be int"
    return {
        "erp_ref_id": str(p["erp_ref_id"]),
        "revision_no": revision_no,
        "notice_type": p["notice_type"],
        "title": str(p["title"])[:500],
        "body": body_bytes.decode("utf-8"),
        "doc_no": p.get("doc_no"),
        "source": p.get("source"),
        "source_url": p.get("source_url"),
        "effective_from": p["effective_from"],
        "effective_to": p.get("effective_to"),
        "content_hash": content_hash(p["body"]),
        "mode": mode,
        "client_request_id": p.get("client_request_id"),
    }, None


def _create_job(conn, notice_id: str, action: str, mode: str, client_request_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO notice_index_jobs (notice_id, client_request_id, action, mode, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (notice_id, client_request_id, action, mode, _now()))
    return cur.lastrowid


def _insert_notice(v: dict) -> tuple[dict, int]:
    """등록 코어 — 멱등 처리 포함. (응답 dict, status code)"""
    conn = _db()
    existing = conn.execute(
        "SELECT id, content_hash, status FROM notices WHERE erp_ref_id=? AND revision_no=?",
        (v["erp_ref_id"], v["revision_no"])).fetchone()
    if existing:
        if existing["content_hash"] == v["content_hash"]:
            job = conn.execute(
                "SELECT id, mode, status FROM notice_index_jobs WHERE notice_id=? "
                "ORDER BY id DESC LIMIT 1", (existing["id"],)).fetchone()
            return {"id": existing["id"], "idempotent": True,
                    "index_job": dict(job) if job else None}, 200
        return {"error": "duplicate (erp_ref_id, revision_no) with different content — 개정은 PUT 사용",
                "id": existing["id"]}, 409

    nid = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO notices (id, erp_ref_id, revision_no, notice_type, title, body, doc_no, "
        "source, source_url, effective_from, effective_to, status, content_hash, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nid, v["erp_ref_id"], v["revision_no"], v["notice_type"], v["title"], v["body"],
         v["doc_no"], v["source"], v["source_url"], v["effective_from"], v["effective_to"],
         STATUS_ACTIVE, v["content_hash"], now, now))
    job_id = _create_job(conn, nid, "index", v["mode"], v["client_request_id"])
    conn.commit()
    if v["mode"] == "immediate":
        process_immediate_async()
    return {"id": nid, "index_job": {"id": job_id, "mode": v["mode"], "status": "pending"}}, 202


@notice_bp.route("/api/notice", methods=["POST"])
def notice_create():
    """공시 등록 (단건) — ERP push. 멱등: 동일 (erp_ref_id,revision_no)+동일 해시 → 200.
    ---
    tags: [Notice]
    responses:
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "json body required"}}
    """
    p = request.get_json(silent=True)
    if not p:
        return jsonify({"error": "json body required"}), 400
    v, err = _validate(p)
    if err:
        code = 422 if "hash" in err else 413 if "exceeds" in err else 400
        return jsonify({"error": err}), code
    body, code = _insert_notice(v)
    return jsonify(body), code


@notice_bp.route("/api/notice/batch", methods=["POST"])
def notice_batch():
    """일괄 등록 (초기 적재용, ≤100건) — 부분 성공 응답.
    ---
    tags: [Notice]
    responses:
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "notices array required"}}
    """
    p = request.get_json(silent=True) or {}
    items = p.get("notices", [])
    if not isinstance(items, list) or not items:
        return jsonify({"error": "notices array required"}), 400
    if len(items) > MAX_BATCH:
        return jsonify({"error": f"max {MAX_BATCH} per request"}), 400
    accepted, rejected = [], []
    for i, item in enumerate(items):
        v, err = _validate(item or {})
        if err:
            rejected.append({"index": i, "erp_ref_id": (item or {}).get("erp_ref_id"), "reason": err})
            continue
        body, code = _insert_notice(v)
        if code in (200, 202):
            accepted.append({"index": i, "erp_ref_id": v["erp_ref_id"], "id": body["id"],
                             "idempotent": body.get("idempotent", False)})
        else:
            rejected.append({"index": i, "erp_ref_id": v["erp_ref_id"], "reason": body.get("error")})
    return jsonify({"total": len(items), "accepted": accepted, "rejected": rejected}), 207 if rejected else 202


@notice_bp.route("/api/notice/<nid>", methods=["PUT"])
def notice_revise(nid):
    """개정 — 같은 erp_ref_id로 새 revision 행 생성(새 ntc-id), 구 revision superseded.
    ---
    tags: [Notice]
    responses:
      202:
        description: "접수됨 — 백그라운드 처리(결과는 상태 조회 경로로 확인)"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "erp_ref_id is immutable"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "notice not found"}}
      409:
        description: "충돌 — 이미 실행 중이거나 중복 요청"
    """
    p = request.get_json(silent=True)
    if not p:
        return jsonify({"error": "json body required"}), 400
    conn = _db()
    cur_row = conn.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
    if not cur_row:
        return jsonify({"error": "notice not found"}), 404
    # erp_ref_id는 불변 — 페이로드 누락 시 기존 값 승계, 불일치 시 거부
    p.setdefault("erp_ref_id", cur_row["erp_ref_id"])
    if p["erp_ref_id"] != cur_row["erp_ref_id"]:
        return jsonify({"error": "erp_ref_id is immutable"}), 400
    v, err = _validate(p)
    if err:
        code = 422 if "hash" in err else 413 if "exceeds" in err else 400
        return jsonify({"error": err}), code
    if v["revision_no"] <= cur_row["revision_no"]:
        return jsonify({"error": f"revision_no must be > {cur_row['revision_no']}"}), 409

    new_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO notices (id, erp_ref_id, revision_no, notice_type, title, body, doc_no, "
        "source, source_url, effective_from, effective_to, status, supersedes_id, content_hash, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id, v["erp_ref_id"], v["revision_no"], v["notice_type"], v["title"], v["body"],
         v["doc_no"], v["source"], v["source_url"], v["effective_from"], v["effective_to"],
         STATUS_ACTIVE, nid, v["content_hash"], now, now))
    conn.execute("UPDATE notices SET status=?, updated_at=? WHERE id=?",
                 (STATUS_SUPERSEDED, now, nid))
    rm_job = _create_job(conn, nid, "remove", v["mode"], v["client_request_id"])
    ix_job = _create_job(conn, new_id, "index", v["mode"], v["client_request_id"])
    conn.commit()
    if v["mode"] == "immediate":
        process_immediate_async()
    return jsonify({"id": new_id, "supersedes": nid, "revision_no": v["revision_no"],
                    "index_job": {"id": ix_job, "mode": v["mode"], "status": "pending"},
                    "remove_job": {"id": rm_job}}), 202


@notice_bp.route("/api/notice/<nid>/revoke", methods=["POST"])
def notice_revoke(nid):
    """폐지 — status=revoked + 색인 제거 잡. 메타필터가 즉시 검색 제외하므로 잡은 nightly 허용.
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "notice not found"}}
    """
    p = request.get_json(silent=True) or {}
    conn = _db()
    resolved = _resolve_notice_id(nid)  # ntc-id 또는 ERP erp_ref_id 허용
    if not resolved:
        return jsonify({"error": "notice not found", "ident": nid}), 404
    nid = resolved
    row = conn.execute("SELECT id, status FROM notices WHERE id=?", (nid,)).fetchone()
    if not row:
        return jsonify({"error": "notice not found"}), 404
    if row["status"] == STATUS_REVOKED:
        return jsonify({"id": nid, "idempotent": True, "status": STATUS_REVOKED}), 200
    conn.execute("UPDATE notices SET status=?, updated_at=? WHERE id=?",
                 (STATUS_REVOKED, _now(), nid))
    job_id = _create_job(conn, nid, "remove", p.get("mode", "nightly"), p.get("client_request_id"))
    conn.commit()
    return jsonify({"id": nid, "status": STATUS_REVOKED, "remove_job": {"id": job_id}}), 200


@notice_bp.route("/api/notice/<nid>", methods=["DELETE"])
def notice_delete(nid):
    """삭제 (오등록 정정 전용) — 행 삭제 + 색인 제거 잡.
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "notice not found"}}
    """
    conn = _db()
    resolved = _resolve_notice_id(nid)  # ntc-id 또는 ERP erp_ref_id 허용
    if not resolved:
        return jsonify({"error": "notice not found", "ident": nid}), 404
    nid = resolved
    job_id = _create_job(conn, nid, "remove", "immediate", None)
    conn.execute("DELETE FROM notices WHERE id=?", (nid,))
    conn.commit()
    process_immediate_async()
    return jsonify({"id": nid, "deleted": True, "remove_job": {"id": job_id}}), 200


_JOB_SELECT = ("SELECT id, notice_id, action, mode, status, chunks, error, created_at, "
               "started_at, finished_at FROM notice_index_jobs ")


def _index_status_payload(notice_id=None, client_request_id=None):
    conn = _db()
    if client_request_id:
        rows = conn.execute(_JOB_SELECT + "WHERE client_request_id=? ORDER BY id DESC",
                            (client_request_id,)).fetchall()
        key = {"client_request_id": client_request_id}
    else:
        rows = conn.execute(_JOB_SELECT + "WHERE notice_id=? ORDER BY id DESC",
                            (notice_id,)).fetchall()
        key = {"notice_id": notice_id}
    if not rows:
        return {"error": "no index jobs found", **key}, 404
    jobs = [dict(r) for r in rows]
    return {**key, "latest": jobs[0]["status"], "jobs": jobs}, 200


@notice_bp.route("/api/notice/index-status", methods=["GET"])
def notice_index_status_collection():
    """색인 상태 폴링(컬렉션) — `?client_request_id=` 또는 `?erp_ref_id=`/`?notice_id=`.
    경로 id 없이 ERP 발신 키로 추적(이전 405 해소).
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "client_request_id, notice_id, or erp_ref_id required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "notice not found"}}
    """
    crid = request.args.get("client_request_id")
    if crid:
        body, code = _index_status_payload(client_request_id=crid)
        return _index_status_response(body, code)
    ident = request.args.get("notice_id") or request.args.get("erp_ref_id")
    if not ident:
        return jsonify({"error": "client_request_id, notice_id, or erp_ref_id required"}), 400
    nid = _resolve_notice_id(ident)
    if not nid:
        return jsonify({"error": "notice not found", "ident": ident}), 404
    body, code = _index_status_payload(notice_id=nid)
    return _index_status_response(body, code)


def _index_status_response(body, code):
    """종결 상태(completed/failed)는 불변 → Cache-Control로 재폴링 억제 힌트.
    (ERP 폴러가 완료 잡을 상시 재폴링하던 관측에 대한 서버측 위생 — 근본은 클라 종료조건)"""
    resp = jsonify(body)
    latest = (body or {}).get("latest")
    if code == 200 and latest in ("completed", "failed"):
        resp.headers["Cache-Control"] = "max-age=86400"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp, code


@notice_bp.route("/api/notice/<nid>/index-status", methods=["GET"])
def notice_index_status(nid):
    """색인 상태 폴링(경로) — `nid`는 ntc-id 또는 ERP erp_ref_id. `?client_request_id` 우선.
    ---
    tags: [Notice]
    responses:
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "notice not found"}}
    """
    crid = request.args.get("client_request_id")
    if crid:
        body, code = _index_status_payload(client_request_id=crid)
        return jsonify(body), code
    resolved = _resolve_notice_id(nid)
    if not resolved:
        return jsonify({"error": "notice not found", "ident": nid}), 404
    body, code = _index_status_payload(notice_id=resolved)
    return jsonify(body), code


@notice_bp.route("/api/notice/process-jobs", methods=["POST"])
def notice_process_jobs():
    """pending 색인 잡 수동 처리(데모·복구·즉시색인용). 인증: Bearer(localhost 우회).

    body(옵션): {"modes": ["immediate","nightly"], "limit": 500}
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "limit must be int"}}
    """
    p = request.get_json(silent=True) or {}
    modes = p.get("modes")
    if modes is not None and (not isinstance(modes, list)
                              or any(m not in ("immediate", "nightly") for m in modes)):
        return jsonify({"error": "modes must be subset of [immediate, nightly]"}), 400
    try:
        limit = int(p.get("limit", 500))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be int"}), 400
    result = process_pending_jobs(_db(), modes=modes, limit=limit)
    return jsonify(result), 200


def _effective_on_ok(meta: dict, on: str) -> bool:
    """진료일(on) 시점에 시행 중인 공시인지. effective_from<=on<=effective_to(없으면 무기한)."""
    ef = meta.get("effective_from")
    et = meta.get("effective_to")  # None이면 메타에서 생략됨 → 무기한 시행
    if ef and ef > on:
        return False
    if et and et < on:
        return False
    return True


@notice_bp.route("/api/notice/search", methods=["POST"])
def notice_search():
    """공시 검색(P0 Retrieval) — 청구 키워드/코드(notice_type)/진료일 → 관련 고시.

    body: {
      "query": "상복부 초음파 본인부담",      # 필수
      "notice_type": ["REVIEW_CRITERIA","TARIFF"],  # 선택(ERP 코드→type 매핑 결과)
      "effective_on": "2026-06-25",            # 선택(진료일 시행여부 필터)
      "active_only": true,                      # 기본 true(폐지/개정 제외)
      "n_results": 5, "min_score": 0.2
    }
    검색 컬렉션: notice_knowledge. status는 색인 시점 메타이므로 active_only는
    1차 필터, 진료일은 후처리 필터(effective_to 무기한 케이스 정확 처리).
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "n_results/min_score type error"}}
    """
    from client.rag import search as _rag_search

    p = request.get_json(silent=True) or {}
    query = (p.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    notice_type = p.get("notice_type")
    if notice_type is not None:
        if isinstance(notice_type, str):
            notice_type = [notice_type]
        bad = [t for t in notice_type if t not in NOTICE_TYPES]
        if bad:
            return jsonify({"error": f"invalid notice_type: {bad}"}), 400
    # 코드 기반 자동 해석(방식 ②): notice_type 미지정 + code → fee_codes 매핑
    if notice_type is None and p.get("code"):
        notice_type = resolve_notice_types(p["code"]) or None
    active_only = p.get("active_only", True)
    effective_on = p.get("effective_on")
    try:
        n_results = min(int(p.get("n_results", 5)), 20)
        min_score = float(p.get("min_score", 0.2))
    except (TypeError, ValueError):
        return jsonify({"error": "n_results/min_score type error"}), 400

    # ChromaDB where (status·notice_type) — 진료일은 후처리
    conds = []
    if active_only:
        conds.append({"status": STATUS_ACTIVE})
    if notice_type:
        conds.append({"notice_type": {"$in": notice_type}})
    where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})

    # 진료일 후처리로 일부 탈락 → 여유분 검색
    raw = _rag_search(query, n_results=n_results * 3 if effective_on else n_results,
                      collection_name=NOTICE_COLLECTION,
                      where=where, min_score=min_score)
    results = []
    for r in raw:
        m = r.get("metadata", {})
        if effective_on and not _effective_on_ok(m, effective_on):
            continue
        results.append({
            "notice_id": m.get("notice_id"),
            "title": m.get("title"),
            "notice_type": m.get("notice_type"),
            "status": m.get("status"),
            "effective_from": m.get("effective_from"),
            "effective_to": m.get("effective_to"),
            "score": r.get("score"),
            "text": r.get("text"),
            "chunk_seq": m.get("chunk_seq"),
        })
        if len(results) >= n_results:
            break
    return jsonify({"query": query, "count": len(results), "collection": "notice_knowledge",
                    "filters": {"notice_type": notice_type, "effective_on": effective_on,
                                "active_only": bool(active_only)},
                    "results": results}), 200


# --- P0.5 삭감위험 평가 (RAG의 G) ---------------------------------------------

DIAGNOSE_DISCLAIMER = ("본 평가는 AI가 심사기준 고시를 근거로 생성한 참고 정보이며, "
                       "최종 청구 판단은 담당자의 검토(휴먼인더루프)에 따릅니다.")


def _retrieve_active_notices(query, notice_type=None, effective_on=None,
                             active_only=True, n=5, min_score=0.2):
    """notice_knowledge 검색 + 필터(공시 검색/평가 공용)."""
    from client.rag import search as _rag_search
    conds = []
    if active_only:
        conds.append({"status": STATUS_ACTIVE})
    if notice_type:
        conds.append({"notice_type": {"$in": notice_type}})
    where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})
    raw = _rag_search(query, n_results=n * 3 if effective_on else n,
                      collection_name=NOTICE_COLLECTION, where=where, min_score=min_score)
    out = []
    for r in raw:
        m = r.get("metadata", {})
        if effective_on and not _effective_on_ok(m, effective_on):
            continue
        out.append({"notice_id": m.get("notice_id"), "title": m.get("title"),
                    "notice_type": m.get("notice_type"), "status": m.get("status"),
                    "effective_from": m.get("effective_from"), "effective_to": m.get("effective_to"),
                    "score": r.get("score"), "text": r.get("text")})
        if len(out) >= n:
            break
    return out


_DEFAULT_RISK_HIGH = 0.70
_DEFAULT_RISK_MEDIUM = 0.40


def _risk_thresholds():
    """위험밴드 임계 (config `notice_diagnose_thresholds` {high, medium}, 기본 0.70/0.40).

    ERP P0.5 섀도 캘리브레이션(목표 precision≥0.8)을 **무재배포**로 조정하기 위한 config화.
    유효성(0≤medium<high≤1) 위반·파싱오류 시 기본값 폴백."""
    try:
        cfg = load_config().get("notice_diagnose_thresholds") or {}
        high = float(cfg.get("high", _DEFAULT_RISK_HIGH))
        medium = float(cfg.get("medium", _DEFAULT_RISK_MEDIUM))
        if not (0.0 <= medium < high <= 1.0):
            return _DEFAULT_RISK_HIGH, _DEFAULT_RISK_MEDIUM
        return high, medium
    except Exception:
        return _DEFAULT_RISK_HIGH, _DEFAULT_RISK_MEDIUM


def _risk_level(score: float) -> str:
    """위험밴드 — config 임계(기본 HIGH≥0.70 / MEDIUM≥0.40 / LOW). ERP precision 캘리브레이션."""
    high, medium = _risk_thresholds()
    if score >= high:
        return "HIGH"
    if score >= medium:
        return "MEDIUM"
    return "LOW"


def _llm_json(prompt: str, system: str, model: str, timeout: int = 120) -> dict:
    """Ollama 구조화(JSON) 생성 — v2.125.12 D1 슬라이스 2 task 라우터 경유(페이로드 동일: format json 포함)."""
    from core import model_router
    res = model_router.generate("notice.diagnose_json", prompt, model=model, system=system,
                                schema="json", options={"temperature": 0.2, "num_predict": 900},
                                timeout=timeout,
                                adapter=model_router.OllamaAdapter(base=OLLAMA_BASE))
    if not res.ok:
        raise RuntimeError(f"[notice.diagnose_json] 생성 실패: {res.error}")   # 종전 raise_for_status와 같은 층
    return json.loads(res.text or "{}")


@notice_bp.route("/api/notice/diagnose", methods=["POST"])
def notice_diagnose():
    """삭감위험 평가(P0.5 Generation) — 청구 라인(비식별) → 위험도·근거 고시 인용·권고.

    body: {
      "claim_line": {"code":"EB411","qty":1,"service_date":"2026-05-12",
                     "description":"상복부 초음파","context":"동일일 중복 산정"},
      "notice_type": ["REVIEW_CRITERIA","TARIFF"],   # 선택(코드→type 매핑)
      "n_context": 5
    }
    비식별 원칙: 환자 식별자 미수신(코드·수가·진료일·비식별 문구만).
    위험밴드 HIGH≥0.70/MED 0.40~0.69/LOW<0.40. 근거 인용 없으면 has_evidence=false
    (ERP는 'AI 판단 보류' 처리). 시행 중(진료일 기준) 심사기준만 근거로 사용.
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "claim_line.{code|description|context} 중 1개 이상 필요"}}
      503:
        description: "의존 서비스 사용 불가"
        schema:
          type: object
          properties: {error: {type: string, example: "evaluation engine unavailable"}}
    """
    p = request.get_json(silent=True) or {}
    claim = p.get("claim_line") or {}
    if not isinstance(claim, dict) or not any(claim.get(k) for k in ("code", "description", "context")):
        return jsonify({"error": "claim_line.{code|description|context} 중 1개 이상 필요"}), 400

    notice_type = p.get("notice_type")
    if notice_type is not None:
        if isinstance(notice_type, str):
            notice_type = [notice_type]
        bad = [t for t in notice_type if t not in NOTICE_TYPES]
        if bad:
            return jsonify({"error": f"invalid notice_type: {bad}"}), 400
    # 코드 기반 자동 해석(방식 ②): notice_type 미지정 + 청구코드 → fee_codes 매핑
    auto_type = None
    if notice_type is None and claim.get("code"):
        auto_type = resolve_notice_types(claim["code"]) or None
        notice_type = auto_type
    # 저가약 대체조제 동반(DRUG 청구면 삭감위험 + 저가 대체 기회 통합 뷰 — ERP 보조)
    substitution = None
    if claim.get("code"):
        try:
            sl = sub_lookup(claim["code"], conn=_db())
            if sl.get("found"):
                substitution = {"eligible": sl.get("eligible"),
                                "alternatives": sl.get("alternatives", [])[:5],
                                "alternatives_count": sl.get("alternatives_count", 0),
                                "cheaper_alternatives_count": sl.get("cheaper_alternatives_count", 0)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[notice] diagnose substitution lookup: {e}")
    service_date = claim.get("service_date")
    try:
        n_context = min(int(p.get("n_context", 5)), 10)
    except (TypeError, ValueError):
        return jsonify({"error": "n_context must be int"}), 400

    query = " ".join(str(claim[k]) for k in ("code", "description", "context") if claim.get(k))
    notices = _retrieve_active_notices(query, notice_type=notice_type,
                                       effective_on=service_date, n=n_context)
    model = p.get("model") or load_config().get("default_model", "qwen2.5:14b")

    if not notices:
        return jsonify({
            "risk_level": "LOW", "risk_score": 0.0, "has_evidence": False,
            "evidence_citations": [],
            "recommendation": "진료일 기준 시행 중인 관련 심사기준 고시를 찾지 못함 — AI 판단 보류 권고",
            "reasoning": "근거 고시 미발견(미적재 또는 비해당). 색인 커버리지 확인 필요.",
            "substitution": substitution,
            "model": model, "disclaimer": DIAGNOSE_DISCLAIMER,
        }), 200

    ctx = ""
    for i, n in enumerate(notices, 1):
        ctx += f"[{i}] ({n['notice_type']}) {n['title']} — 시행 {n['effective_from']}\n{n['text']}\n\n"

    system = (
        "당신은 건강보험 청구 적정성 심사 보조 AI입니다. 제공된 [심사기준 고시]만을 근거로 "
        "청구 라인의 삭감 위험을 평가하세요. 고시에 직접 근거가 없으면 위험도를 낮게 평가하고 "
        "cited를 비워 두세요. 추측 금지. 반드시 아래 JSON으로만 한국어로 답하세요.\n"
        '{"risk_score": 0.0~1.0 (삭감 가능성), "reasoning": "근거 고시 기반 판단 이유", '
        '"recommendation": "청구 권고(재검토/제외/적정 등)", "cited": [근거로 쓴 고시 번호 배열]}'
    )
    cl = ", ".join(f"{k}={claim[k]}" for k in ("code", "qty", "service_date", "description", "context") if claim.get(k))
    prompt = f"[청구 라인]\n{cl}\n\n[심사기준 고시]\n{ctx}\n위 청구의 삭감 위험을 평가해 JSON으로만 답하세요."

    try:
        out = _llm_json(prompt, system, model)
    except Exception as e:
        logger.warning(f"[notice] diagnose LLM error: {e}")
        return jsonify({"error": "evaluation engine unavailable", "detail": str(e)[:200]}), 503

    try:
        score = max(0.0, min(1.0, float(out.get("risk_score", 0))))
    except (TypeError, ValueError):
        score = 0.0
    cited = out.get("cited") or []
    if not isinstance(cited, list):
        cited = []
    evidence = []
    for idx in cited:
        try:
            j = int(idx)
        except (TypeError, ValueError):
            continue
        if 1 <= j <= len(notices):
            ev = notices[j - 1]
            evidence.append({"idx": j, "notice_id": ev["notice_id"], "title": ev["title"],
                             "notice_type": ev["notice_type"], "effective_from": ev["effective_from"],
                             "effective_to": ev["effective_to"], "score": ev["score"]})

    return jsonify({
        "risk_level": _risk_level(score),
        "risk_score": round(score, 3),
        "has_evidence": bool(evidence),
        "evidence_citations": evidence,
        "recommendation": str(out.get("recommendation", ""))[:500],
        "reasoning": str(out.get("reasoning", ""))[:2000],
        "retrieved": len(notices),
        "applied_notice_type": notice_type,
        "auto_resolved_type": bool(auto_type),
        "substitution": substitution,
        "risk_thresholds": dict(zip(("high", "medium"), _risk_thresholds())),
        "model": model,
        "disclaimer": DIAGNOSE_DISCLAIMER,
    }), 200


# --- 청구코드 → notice_type 매핑(방식 ②: 코드마스터 적재) -----------------------

@notice_bp.route("/api/notice/fee-codes/import", methods=["POST"])
def notice_fee_codes_import():
    """청구 코드마스터 적재 — code→category(→notice_type) 자동해석용. 인증: Bearer(localhost 우회).

    body 택1:
      {"rows": [{"code":"AA157","category":"TARIFF","name":"외래 재진 진찰료"}, ...]}
      {"csv": "code,category,name\\nAA157,TARIFF,...", "columns": {"code":"코드","category":"분류","name":"명칭"}}
    columns: CSV 헤더명 매핑(생략 시 code/category/name 헤더 가정). 대량은 점진/배치 권장.
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "rows[] or csv required"}}
      413:
        description: "요청 본문이 허용 크기를 초과"
        schema:
          type: object
          properties: {error: {type: string, example: "max 100000 rows per request — 배치 분할 요망"}}
    """
    p = request.get_json(silent=True) or {}
    rows = p.get("rows")
    if rows is None and p.get("csv"):
        cols = p.get("columns") or {}
        c_code = cols.get("code", "code")
        c_cat = cols.get("category", "category")
        c_name = cols.get("name", "name")
        try:
            reader = csv.DictReader(io.StringIO(p["csv"]))
            rows = [{"code": r.get(c_code), "category": r.get(c_cat), "name": r.get(c_name)}
                    for r in reader]
        except Exception as e:
            return jsonify({"error": f"csv parse failed: {str(e)[:200]}"}), 400
    if not isinstance(rows, list) or not rows:
        return jsonify({"error": "rows[] or csv required"}), 400
    if len(rows) > 100000:
        return jsonify({"error": "max 100000 rows per request — 배치 분할 요망"}), 413
    n = import_fee_codes(rows, conn=_db())
    return jsonify({"imported": n, "stats": fee_stats(conn=_db())}), 200


@notice_bp.route("/api/notice/fee-codes/stats", methods=["GET"])
def notice_fee_codes_stats():
    """코드마스터 적재 현황(총건수·분류별).

    수가·치료재료·약가를 아우르는 코드 마스터의 적재 현황(총건수·분류별).
    개별 코드 해석은 `GET /api/notice/fee-codes/{code}`.
    ---
    tags: [Notice]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            total: {type: integer}
            by_category: {type: object, description: "분류별 건수"}
    """
    return jsonify(fee_stats(conn=_db())), 200


@notice_bp.route("/api/notice/fee-codes/<code>", methods=["GET"])
def notice_fee_code_resolve(code):
    """청구/약가 코드 → notice_type(고시유형) 해석.

    code(청구코드·약가 제품코드·표준코드/바코드) → fee_codes category → notice_type 리스트.
    제품코드가 fee_codes에 없으면 drug_master 크로스워크로 표준코드/품목기준코드를 제품코드로
    자동 해석 후 재조회. 미매칭이면 notice_type=[]·resolved=false.
    ---
    tags: [Notice]
    parameters:
      - in: path
        name: code
        required: true
        type: string
        description: "청구/약가 코드 (예: AB100=수가, 645302132=약가 제품코드, 8806587001001=표준코드/바코드)"
    responses:
      200:
        description: "코드 → notice_type 해석 결과"
        schema:
          type: object
          properties:
            code: {type: string, example: "645302132"}
            notice_type:
              type: array
              items: {type: string}
              description: "고시 유형(TARIFF→[REVIEW_CRITERIA,TARIFF], DRUG/MATERIAL→[DRUG_MATERIAL,REVIEW_CRITERIA])"
              example: ["DRUG_MATERIAL", "REVIEW_CRITERIA"]
            resolved: {type: boolean, description: "해석 성공 여부(미적재 코드=false)"}
    """
    types = resolve_notice_types(code, conn=_db())
    return jsonify({"code": code, "notice_type": types, "resolved": bool(types)}), 200


@notice_bp.route("/api/notice/drug-substitution/stats", methods=["GET"])
def notice_drug_substitution_stats():
    """저가약 대체조제 장려금 지급대상 적재 현황.

    저가약 대체조제 장려금 지급대상 적재 현황.
    개별 조회는 `GET /api/notice/drug-substitution/{code}`, 다건은 POST.
    ---
    tags: [Notice]
    responses:
      200:
        description: "적재 현황"
        schema:
          type: object
          properties:
            total: {type: integer, description: "총 품목 수", example: 10979}
            eligible: {type: integer, description: "장려금 지급대상(단일품목 제외∩급여정지아님)", example: 10412}
            suspended: {type: integer, description: "급여정지 품목", example: 17}
            distinct_ingredient_codes: {type: integer, description: "고유 주성분코드 수", example: 1237}
    """
    return jsonify(sub_stats(conn=_db())), 200


@notice_bp.route("/api/notice/drug-substitution/<code>", methods=["GET"])
def notice_drug_substitution_lookup(code):
    """제품코드 → 저가약 대체조제/장려금 정보 + 대체 후보(동일 주성분코드·다른 대표코드·저가순).

    대체 후보는 임상안전을 위해 **정확한 주성분코드 동일**(성분+함량+제형 동일)이고
    대표코드가 다른 장려금 대상만 제시(최종 판단은 ERP/약사). 미적재 코드는 found=false.
    code가 제품코드가 아니어도(표준코드/품목기준코드) drug_master 크로스워크로 자동 해석(resolved_via).
    ---
    tags: [Notice]
    parameters:
      - in: path
        name: code
        required: true
        type: string
        description: "제품코드(보험 9자리) 또는 표준코드(바코드)·품목기준코드"
    responses:
      200:
        description: "대체조제 정보 + 대체 후보"
        schema:
          type: object
          properties:
            found: {type: boolean, description: "적재 여부"}
            code: {type: string, example: "651900330"}
            product: {type: string, example: "루나팜정(플루니트라제팜)_(1mg/1정)"}
            ingr_code: {type: string, description: "주성분코드(성분+함량+제형)", example: "160601ATB"}
            rep_code: {type: string, description: "대표코드"}
            ingredient: {type: string, description: "주성분명(영문)"}
            maker: {type: string}
            price: {type: integer, description: "상한금액(원, 산정불가=null)", example: 59}
            rx: {type: string, description: "전문/일반", example: "전문"}
            bioeq: {type: string, description: "생동인정여부(공고번호)"}
            suspended: {type: string, description: "급여정지(비어있으면 정상)"}
            eligible: {type: boolean, description: "장려금 지급대상 여부"}
            alternatives:
              type: array
              description: "대체 후보(동일 주성분코드·대표코드 상이·eligible·상한금액 오름차순)"
              items:
                type: object
                properties:
                  code: {type: string}
                  product: {type: string}
                  maker: {type: string}
                  price: {type: integer}
                  cheaper: {type: boolean, description: "질의약보다 저렴=장려금 대상"}
            alternatives_count: {type: integer}
            cheaper_alternatives_count: {type: integer, description: "질의약보다 저렴한 후보 수(장려금 가능)"}
            resolved_via: {type: object, description: "크로스워크 경유 시(바코드→제품코드)만 존재"}
    """
    return jsonify(sub_lookup(code, conn=_db())), 200


@notice_bp.route("/api/notice/drug-substitution", methods=["POST"])
def notice_drug_substitution_post():
    """제품코드 → 저가약 대체조제 lookup (POST body). GET /<code>와 응답 동일.

    오케스트레이터 도구(ai: POST 디스패치)용 동등 엔드포인트. 응답 스키마는 GET /<code> 참조.
    ---
    tags: [Notice]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string, description: "제품코드/표준코드/품목기준코드", example: "651900330"}
    responses:
      200:
        description: "대체조제 정보(GET /<code>와 동일 스키마)"
        schema:
          type: object
          properties:
            found: {type: boolean, description: "false면 code만 함께 반환된다"}
            code: {type: string}
            resolved_via: {type: object, description: "drug_master 크로스워크로 해석된 경우에만"}
            alternatives: {type: array, description: "동일 주성분·장려금 대상 대체 후보(가격 오름차순)"}
            alternatives_count: {type: integer}
            cheaper_alternatives_count: {type: integer, description: "양쪽 약가가 모두 있을 때만 판정"}
      400: {description: "code 누락"}
    """
    p = request.get_json(silent=True) or {}
    code = p.get("code")
    if not code:
        return jsonify({"error": "code required"}), 400
    return jsonify(sub_lookup(code, conn=_db())), 200


@notice_bp.route("/api/notice/drug-master/stats", methods=["GET"])
def notice_drug_master_stats():
    """약가마스터 의약품표준코드 크로스워크 적재 현황.

    약가 마스터의 의약품 표준코드 크로스워크 적재 현황.
    제품코드↔표준코드/품목기준코드 해석은 `POST /api/notice/drug-master/resolve`.
    ---
    tags: [Notice]
    responses:
      200:
        description: "적재 현황"
        schema:
          type: object
          properties:
            total_codes: {type: integer, description: "총 코드(표준코드 PK) 수", example: 305522}
            rows_with_product_code: {type: integer, description: "보험 제품코드 보유 행", example: 65283}
            distinct_product_codes: {type: integer, description: "고유 제품코드 수", example: 22306}
            cancelled: {type: integer, description: "취소(등재취소) 행", example: 74680}
    """
    return jsonify(dm_stats(conn=_db())), 200


@notice_bp.route("/api/notice/drug-data/status", methods=["GET"])
def notice_drug_data_status():
    """약물 데이터 스택 통합 현황 — 약가(fee_codes)·대체조제·코드 크로스워크·DUR.
    Admin 가시성용 집계(각 stats 합본).
    ---
    tags: [Notice]
    responses:
      200:
        description: "스택 집계"
        schema:
          type: object
          properties:
            fee_codes: {type: object, description: "약가/수가 코드 현황(total, by_category)"}
            drug_substitution: {type: object, description: "대체조제 현황"}
            drug_master: {type: object, description: "코드 크로스워크 현황"}
            dur: {type: object, description: "DUR 현황(interactions, single_warnings 등)"}
    """
    # 4-DB 집계(drug_master 305K 등)라 ~50ms — 데이터는 ingest/sync 시점만 변동 → 120s TTL 캐시.
    cached = _DRUG_DATA_STATUS_CACHE.get("v")
    if cached is not None:
        return jsonify(cached), 200
    out = {"fee_codes": fee_stats(conn=_db()),
           "drug_substitution": sub_stats(conn=_db()),
           "drug_master": dm_stats(conn=_db())}
    try:
        from core.dur import stats as dur_stats
        out["dur"] = dur_stats()
    except Exception as e:  # noqa: BLE001
        out["dur"] = {"error": str(e)[:120]}
    _DRUG_DATA_STATUS_CACHE.set("v", out)
    return jsonify(out), 200


@notice_bp.route("/api/notice/hira-drug/sync-status", methods=["GET"])
def notice_hira_drug_sync_status():
    """HIRA 게시판 자동 동기화 상태 — 약제급여목록(약가)+저가약 대체조제, 소스별 메타.
    ---
    tags: [Notice]
    responses:
      200:
        description: "소스별 동기화 메타"
        schema:
          type: object
          properties:
            sources:
              type: array
              items:
                type: object
                properties:
                  source: {type: string, example: "약제급여목록"}
                  pgmid: {type: string, example: "HIRAA030014050000"}
                  meta: {type: object, description: "status(ok/skip/error)·edition·count·last_check_at·last_sync_at·last_error"}
            running: {type: boolean}
    """
    from core.hira_drug_sync import sync_status as hira_sync_status
    return jsonify(hira_sync_status()), 200


@notice_bp.route("/api/notice/hira-drug/sync", methods=["POST"])
def notice_hira_drug_sync():
    """HIRA 게시판 수동 동기화 — 최신 게시글 다운로드→검증→교체 적재.

    소스: 약제급여목록(fee_codes DRUG)·저가약대체조제(drug_substitution).
    같은 게시글이면 skip(기존 보존). `{"force": true}` 재적재, `{"source": "..."}` 단일 소스.
    백그라운드 실행(202) — 결과는 sync-status로 확인.
    ---
    tags: [Notice]
    parameters:
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            force: {type: boolean, description: "동일 게시글이라도 재적재", example: false}
            source: {type: string, description: "단일 소스만(약제급여목록|저가약대체조제). 생략=전체", example: "저가약대체조제"}
    responses:
      202:
        description: "동기화 시작"
        schema:
          type: object
          properties:
            accepted: {type: boolean}
            force: {}
            source: {}
            status_url: {type: string}
      409:
        description: "이미 실행 중"
    """
    from core import hira_drug_sync as hds
    from core import hira_mcat_sync as hms
    if hds._running:
        return jsonify({"error": "already running"}), 409
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    source = (body.get("source") or "").strip() or None
    known = [s[0] for s in hds._SOURCES] + [hms.SOURCE]
    if source and source not in known:
        return jsonify({"error": f"unknown source: {source}", "sources": known}), 400
    if source == hms.SOURCE:
        target, kwargs = hms.sync, {"force": force}          # 치료재료=data.go.kr API
    else:
        target, kwargs = hds.sync, {"force": force, "source": source}
    threading.Thread(target=target, kwargs=kwargs,
                     name="hira-drug-sync-manual", daemon=True).start()
    return jsonify({"accepted": True, "force": force, "source": source or "all",
                    "status_url": "/api/notice/hira-drug/sync-status"}), 202


@notice_bp.route("/api/notice/collect/logs", methods=["GET"])
def notice_collect_logs():
    """수집 이력 로그 조회 — HIRA 게시판·data.go.kr API 전 소스의 동기화 시도 이력.

    쿼리: source(소스명)·status(ok/skip/error/denied)·since_days·limit(기본100, 최대1000).
    ---
    tags: [Notice]
    parameters:
      - {in: query, name: source, type: string, required: false, description: "소스 필터(약제급여목록 등)"}
      - {in: query, name: status, type: string, required: false, description: "ok|skip|error|denied"}
      - {in: query, name: since_days, type: number, required: false, description: "최근 N일"}
      - {in: query, name: limit, type: integer, required: false, description: "최대 행수(기본 100)"}
    responses:
      200:
        description: "수집 로그(최신순)"
        schema:
          type: object
          properties:
            logs: {}
            count: {type: integer}
    """
    from core.collect_log import query_logs
    logs = query_logs(source=request.args.get("source"),
                      status=request.args.get("status"),
                      since_days=request.args.get("since_days", type=float),
                      limit=request.args.get("limit", type=int) or 100)
    return jsonify({"logs": logs, "count": len(logs)}), 200


@notice_bp.route("/api/notice/collect/report", methods=["GET", "POST"])
def notice_collect_report():
    """수집보고서 — GET=최신 보고서 조회(없으면 404), POST=즉시 생성·저장.

    POST body(optional): `{"period_days": 7}`. 보고서=기간 내 소스별 시도/적재/오류
    집계+신규판 내역+현재 정본 규모 스냅샷+요약문. 주기 생성은 스케줄러(7일)가 수행.
    ---
    tags: [Notice]
    parameters:
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            period_days: {type: integer, description: "집계 기간(일, 기본 7)", example: 7}
    responses:
      200:
        description: "보고서(생성 시 신규, GET 시 최신 저장본)"
      404:
        description: "저장된 보고서 없음(GET)"
    """
    from core.collect_log import generate_report, latest_report
    if request.method == "POST":
        days = int((request.get_json(silent=True) or {}).get("period_days") or 7)
        return jsonify(generate_report(period_days=max(1, min(days, 90)))), 200
    rep = latest_report()
    if not rep:
        return jsonify({"error": "no report yet — POST로 생성"}), 404
    return jsonify(rep), 200


@notice_bp.route("/api/notice/collect/reports", methods=["GET"])
def notice_collect_reports():
    """수집보고서 목록(최신순 요약). limit 기본 20.
    ---
    tags: [Notice]
    responses:
      200:
        description: "보고서 목록(id·ts·period_days·summary)"
        schema:
          type: object
          properties:
            reports: {}
    """
    from core.collect_log import list_reports
    return jsonify({"reports": list_reports(request.args.get("limit", type=int) or 20)}), 200


@notice_bp.route("/api/notice/collect/purge", methods=["POST"])
def notice_collect_purge():
    """수집 로그 retention 정리 — `{"retention_days": 365}`(기본 config, 보고서는 4배 보존).
    ---
    tags: [Notice]
    parameters:
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            retention_days: {type: integer, example: 365}
    responses:
      200:
        description: "삭제 건수"
        schema:
          type: object
          properties:
            purged: {}
            retention_days: {type: integer}
    """
    from core.collect_log import purge_logs
    days = (request.get_json(silent=True) or {}).get("retention_days")
    if days is None:
        days = int(load_config().get("collect_log_retention_days", 365))
    purged = purge_logs(int(days))
    return jsonify({"purged": purged, "retention_days": int(days)}), 200


@notice_bp.route("/api/notice/drug-master/resolve", methods=["POST"])
@notice_bp.route("/api/notice/drug-master/<code>", methods=["GET"])
def notice_drug_master_resolve(code=None):
    """의약품 코드 크로스워크 해석 — 표준코드(바코드)/제품코드(보험)/품목기준코드/대표코드/
    성분명코드 중 무엇을 줘도 의약품 정체성(전 코드+성분코드+ATC+상품명) 해석.

    POST `{"code": ...}` 또는 GET /<code>. 같은 제품의 포장단위(표준코드)가 여럿이라
    제품(품목기준+제품+성분코드) 단위로 그룹화. 제품코드=fee_codes.code,
    성분명코드(예 487202ATB)=drug_substitution.ingr_code 연계.
    ---
    tags: [Notice]
    parameters:
      - in: path
        name: code
        required: false
        type: string
        description: "GET 경로변수 — 임의 코드(표준/제품/품목기준/대표/성분명코드)"
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            code: {type: string, description: "POST 시 코드", example: "658700100"}
    responses:
      200:
        description: "코드 → 의약품 정체성(제품 그룹화)"
        schema:
          type: object
          properties:
            found: {type: boolean}
            code: {type: string, example: "658700100"}
            match_count: {type: integer, description: "매칭된 제품 수"}
            matches:
              type: array
              items:
                type: object
                properties:
                  name: {type: string, description: "한글상품명", example: "바라크루드정0.5밀리그램(엔테카비르)"}
                  product_code: {type: string, description: "보험 제품코드(=fee_codes.code)", example: "658700100"}
                  item_code: {type: string, description: "품목기준코드"}
                  rep_code: {type: string, description: "대표코드"}
                  ingr_code: {type: string, description: "성분명코드(=drug_substitution.ingr_code)", example: "487202ATB"}
                  atc: {type: string, description: "국제표준 ATC 코드", example: "J05AF10"}
                  maker: {type: string}
                  rx: {type: string}
                  spec: {type: string, description: "약품규격"}
                  cancelled: {type: string, description: "취소일자(있으면 등재취소)"}
                  std_codes: {type: array, items: {type: string}, description: "포장단위 표준코드(바코드) 목록"}
                  std_code_count: {type: integer}
    """
    if code is None:
        code = (request.get_json(silent=True) or {}).get("code")
    if not code:
        return jsonify({"error": "code required"}), 400
    return jsonify(dm_resolve(code, conn=_db())), 200


# ── ERP 동기화 보조 채널 — 공문 확약분 (X1, 2026-08-25) ─────────────────────
# 회신 정본: docs/01-plan/references/notice-integration-reply-to-erp.md ①·⑥
# 설계 스키마: docs/02-design/features/erp-notice-rag.design.md §API sync/verify

CHANGES_DEFAULT_LIMIT = 500
CHANGES_MAX_LIMIT = 2000
SYNC_VERIFY_MAX = 20000


def _parse_ts(s):
    """ISO 타임스탬프 관대 파싱 → aware datetime(UTC). 실패 시 None.

    저장 포맷이 `_now()`의 `...+00:00Z`(isoformat이 이미 오프셋을 붙인 뒤 Z 추가)라
    문자열 부등호 비교는 클라이언트 포맷과 어긋난다 → 파싱 후 datetime으로 비교."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1]
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@notice_bp.route("/api/notice/changes", methods=["GET"])
def notice_changes():
    """변경분 일괄 조회 — ERP 누락 복구·정기 대사 보조 채널 (회신 ① 확약분).

    updated_at > since 인 공시 행(등록·개정·폐지가 전부 updated_at을 갱신).
    물리 삭제(DELETE)는 행이 사라져 여기 실리지 않는다 — 전체 정합은
    POST /api/notice/sync/verify 대사로 잡는다.
    ---
    tags: [Notice]
    parameters:
      - {in: query, name: since, type: string, required: true, description: "ISO-8601 타임스탬프 (이 시각 이후 변경분)"}
      - {in: query, name: limit, type: integer, required: false, description: "최대 행수(기본 500, 최대 2000)"}
    responses:
      200:
        description: "변경분(updated_at 오름차순). truncated=true면 마지막 updated_at으로 재호출"
        schema:
          type: object
          properties:
            since: {}
            count: {}
            truncated: {}
            changes: {type: array}
      400:
        description: "since 누락/파싱 불가"
    """
    since_raw = request.args.get("since")
    since = _parse_ts(since_raw)
    if since is None:
        return jsonify({"error": "since required (ISO-8601 timestamp)"}), 400
    limit = request.args.get("limit", type=int) or CHANGES_DEFAULT_LIMIT
    limit = max(1, min(limit, CHANGES_MAX_LIMIT))
    rows = _db().execute(
        "SELECT id, erp_ref_id, revision_no, notice_type, title, status, "
        "content_hash, effective_from, effective_to, created_at, updated_at "
        "FROM notices").fetchall()
    changes = []
    for r in rows:
        ts = _parse_ts(r["updated_at"]) or _parse_ts(r["created_at"])
        if ts is None or ts <= since:
            continue
        changes.append((ts, dict(r)))
    changes.sort(key=lambda t: t[0])
    truncated = len(changes) > limit
    return jsonify({"since": since_raw, "count": min(len(changes), limit),
                    "truncated": truncated,
                    "changes": [d for _, d in changes[:limit]]}), 200


@notice_bp.route("/api/notice/sync/verify", methods=["POST"])
def notice_sync_verify():
    """정합성 대사 — ERP가 전체 스냅샷을 push, 당측이 대사 후 불일치 목록 응답 (회신 ⑥).

    Request: {"snapshot": [{"erp_ref_id", "revision_no", "content_hash"}]}
             또는 {"items": [...]}(같은 항목 스키마 — ERP 구현 키)
    Response: {"total", "matched", "mismatched": [...], "mismatches": [...]}
              — 두 키는 **같은 목록**(설계서=`mismatched`, ERP 구현=`mismatches`, 양쪽 수용)
              각 항목: {erp_ref_id, revision_no, reason}
    reason — hash_diff: 해시 상이 / missing: ERP에 있고 당측에 없음(→ERP 재발신)
           / extra: 당측 active에만 있음(→revoke 후보). extra는 active 행만 센다
           (superseded·revoked는 제외 — ERP 스냅샷이 최신 revision만 실어도 위양성 없게).
    ---
    tags: [Notice]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          description: "snapshot 또는 items 중 하나(둘 다 보내면 400)"
          properties:
            snapshot:
              type: array
              items:
                type: object
                properties:
                  erp_ref_id: {type: string}
                  revision_no: {type: integer}
                  content_hash: {type: string, description: "sha256: 프리픽스 유무 무관"}
            items:
              type: array
              description: "snapshot과 동일 — ERP 구현 키"
              items:
                type: object
                properties:
                  erp_ref_id: {type: string}
                  revision_no: {type: integer}
                  content_hash: {type: string}
    responses:
      200:
        description: "대사 결과 (total=스냅샷 건수, matched+hash_diff+missing=total, extra는 추가분)"
        schema:
          type: object
          properties:
            total: {type: integer}
            matched: {}
            mismatched: {}
            mismatches: {description: "mismatched와 같은 목록(ERP 구현 키)"}
      400:
        description: "snapshot/items 형식 오류 또는 둘 다 보냄"
    """
    p = request.get_json(silent=True) or {}
    # (2026-09-28): 설계서 키 `snapshot`과 ERP 구현 키 `items`를 둘 다 받는다(항목 스키마 동일).
    if "snapshot" in p and "items" in p:
        return jsonify({"error": "provide one of snapshot|items, not both"}), 400
    key = "items" if "items" in p else "snapshot"
    snapshot = p.get(key)
    if not isinstance(snapshot, list):
        return jsonify({"error": "snapshot (or items) array required"}), 400
    if len(snapshot) > SYNC_VERIFY_MAX:
        return jsonify({"error": f"max {SYNC_VERIFY_MAX} per request"}), 400
    items = []
    for i, it in enumerate(snapshot):
        if not isinstance(it, dict) or not it.get("erp_ref_id"):
            return jsonify({"error": f"{key}[{i}]: erp_ref_id required"}), 400
        try:
            rev = int(it.get("revision_no", 1))
        except (TypeError, ValueError):
            return jsonify({"error": f"{key}[{i}]: revision_no must be int"}), 400
        ch = it.get("content_hash")
        if not ch or not isinstance(ch, str):
            return jsonify({"error": f"{key}[{i}]: content_hash required"}), 400
        if not ch.startswith("sha256:"):
            ch = "sha256:" + ch          # verify_hash와 같은 관대 프리픽스 규칙
        items.append((str(it["erp_ref_id"]), rev, ch))

    rows = _db().execute(
        "SELECT erp_ref_id, revision_no, content_hash, status FROM notices").fetchall()
    ours = {(r["erp_ref_id"], r["revision_no"]): r for r in rows}
    matched = 0
    mismatched = []
    seen = set()
    for ref, rev, ch in items:
        seen.add((ref, rev))
        row = ours.get((ref, rev))
        if row is None:
            mismatched.append({"erp_ref_id": ref, "revision_no": rev, "reason": "missing"})
        elif row["content_hash"] != ch:
            mismatched.append({"erp_ref_id": ref, "revision_no": rev, "reason": "hash_diff"})
        else:
            matched += 1
    for (ref, rev), row in sorted(ours.items()):
        if row["status"] == STATUS_ACTIVE and (ref, rev) not in seen:
            mismatched.append({"erp_ref_id": ref, "revision_no": rev, "reason": "extra"})
    return jsonify({"total": len(items), "matched": matched,
                    "mismatched": mismatched, "mismatches": mismatched}), 200
