"""WeRU.B RAG Module — 문서 색인 + 벡터 검색 + LLM 답변 + 스마트 청킹"""

import hashlib
import os
import re
import logging
import time
from collections import OrderedDict

import chromadb
import requests

logger = logging.getLogger("ollama-admin")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_PATH = os.path.join(BASE_DIR, "vector_db")
OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
# 임베딩 전용 context — 글로벌 OLLAMA_CONTEXT_LENGTH(예 8192)×NUM_PARALLEL이 임베딩 모델 KV를
# 수 GB로 부풀려 VRAM OOM(임베딩 500)을 유발하므로, 임베딩 요청은 작은 num_ctx를 명시한다.
# 2048이면 RAG 청크(기본 500자)·쿼리를 충분히 커버하고 bge-m3 풋프린트는 ~1.2GB로 LLM과 공존.
EMBED_NUM_CTX = int(os.environ.get("RAG_EMBED_NUM_CTX", "2048"))

# 임베딩 캐시 — 동일 쿼리 반복 시 Ollama 호출 생략 (~500ms 절감)
_embed_cache = OrderedDict()
_EMBED_CACHE_MAX = 1000   # 500→1000 (P1-2 캐시 확장)
_EMBED_CACHE_TTL = 7200   # 1시간→2시간 (P1-2)


def get_chroma():
    """ChromaDB 클라이언트 (영속 저장소)"""
    return chromadb.PersistentClient(path=CHROMA_PATH)


def get_collection(name="werub_docs"):
    """컬렉션 가져오기/생성"""
    client = get_chroma()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def embed_text(text, prefix=""):
    """Ollama로 텍스트 임베딩 (bge-m3, 캐시 지원)"""
    cache_key = hashlib.md5(f"{EMBED_MODEL}:{text[:500]}".encode()).hexdigest()

    # 캐시 조회
    cached = _embed_cache.get(cache_key)
    if cached and time.time() - cached[1] < _EMBED_CACHE_TTL:
        _embed_cache.move_to_end(cache_key)
        return cached[0]

    # v2.124.99 task 라우터 경유 — 🔴 **모델 값은 동결이다.** 표의 `requires_reindex`가
    # 그 사실을 말한다: 바꾸면 medical_law 13,002 + medical_pub 15,285 + werub_docs +
    # edu_docs 전량을 재색인해야 하고, 차원이 달라지면 저장 임베딩 재사용이 불가능하다.
    # seam은 '바꿀 수 있게' 만들되 '바꾸기 쉬워 보이게' 만들면 안 된다.
    from core import model_router
    vecs = model_router.embed("rag.embed", text, ctx=EMBED_NUM_CTX, timeout=30)
    if not vecs:
        raise RuntimeError("embedding failed")
    embedding = vecs[0]

    # 캐시 저장
    if len(_embed_cache) >= _EMBED_CACHE_MAX:
        _embed_cache.popitem(last=False)
    _embed_cache[cache_key] = (embedding, time.time())

    return embedding


def embed_batch(texts, prefix=""):
    """여러 텍스트 일괄 임베딩 (bge-m3)"""
    from core import model_router
    vecs = model_router.embed("rag.embed", texts, ctx=EMBED_NUM_CTX, timeout=120)
    if not vecs:
        raise RuntimeError("embedding failed")
    return vecs


def index_document(doc_id, text, metadata=None, collection_name="werub_docs"):
    """단일 문서 색인"""
    col = get_collection(collection_name)
    embedding = embed_text(text, prefix="search_document")
    col.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata or {}],
    )
    return {"ok": True, "doc_id": doc_id}


def index_batch(documents, collection_name="werub_docs"):
    """배치 문서 색인

    Args:
        documents: [{"id": "...", "text": "...", "metadata": {...}}, ...]
    """
    col = get_collection(collection_name)
    ids = [d["id"] for d in documents]
    texts = [d["text"] for d in documents]
    metadatas = [d.get("metadata", {}) for d in documents]

    # 50개씩 배치 임베딩
    all_embeddings = []
    for i in range(0, len(texts), 50):
        batch = texts[i:i+50]
        embs = embed_batch(batch)
        all_embeddings.extend(embs)

    col.upsert(ids=ids, embeddings=all_embeddings, documents=texts, metadatas=metadatas)
    return {"ok": True, "count": len(ids)}


def search(query, n_results=5, collection_name="werub_docs", where=None, min_score=0.3):
    """유사 문서 검색 (min_score 필터 적용)"""
    col = get_collection(collection_name)
    if col.count() == 0:
        return []

    query_embedding = embed_text(query)
    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, col.count()),
    }
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    docs = []
    for i in range(len(results["ids"][0])):
        distance = results["distances"][0][i] if results["distances"] else 0
        score = round(1 - distance, 4)
        if score < min_score:
            continue
        docs.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            "distance": distance,
            "score": score,
        })
    return docs


# ---------------------------------------------------------------------------
# Smart Chunking (v2.37.0)
# ---------------------------------------------------------------------------

def chunk_text(text, max_chars=500, overlap=50, strategy="paragraph"):
    """텍스트를 의미 단위로 분할

    Args:
        text: 원본 텍스트
        max_chars: 청크 최대 문자 수
        overlap: 청크 간 오버랩 문자 수
        strategy: paragraph | sentence | fixed | code | law

    Returns:
        [{"text": str, "index": int, "start": int, "end": int}, ...]
    """
    if not text or len(text) <= max_chars:
        return [{"text": text, "index": 0, "start": 0, "end": len(text)}]

    if strategy == "paragraph":
        segments = re.split(r'\n\s*\n', text)
    elif strategy == "sentence":
        segments = re.split(r'(?<=[.!?。])\s+', text)
    elif strategy == "code":
        segments = re.split(r'\n(?=(?:def |class |function |async function ))', text)
    elif strategy == "law":
        segments = re.split(r'\n(?=제\d+조)', text)
    else:  # fixed
        segments = None

    # Fixed-length chunking with overlap
    if segments is None or strategy == "fixed":
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            chunks.append({"text": text[start:end], "index": len(chunks), "start": start, "end": end})
            start = end - overlap if end < len(text) else end
        return chunks

    # Merge small segments into chunks up to max_chars
    chunks = []
    current = ""
    current_start = 0
    pos = 0

    for seg in segments:
        seg = seg.strip()
        if not seg:
            pos += 1
            continue

        if current and len(current) + len(seg) + 1 > max_chars:
            chunks.append({
                "text": current,
                "index": len(chunks),
                "start": current_start,
                "end": current_start + len(current),
            })
            # Overlap: keep tail of current chunk
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:] + " " + seg
            else:
                current = seg
            current_start = pos
        else:
            if current:
                current += "\n\n" + seg if strategy == "paragraph" else " " + seg
            else:
                current = seg
                current_start = pos
        pos += len(seg) + 1

    if current.strip():
        chunks.append({
            "text": current,
            "index": len(chunks),
            "start": current_start,
            "end": current_start + len(current),
        })

    return chunks if chunks else [{"text": text, "index": 0, "start": 0, "end": len(text)}]


def index_document_chunked(doc_id, text, metadata=None, collection_name="werub_docs",
                           chunk_strategy="paragraph", max_chars=500, overlap=50):
    """문서를 청킹 후 각 청크를 개별 벡터로 색인

    Args:
        doc_id: 원본 문서 ID
        text: 원본 텍스트
        metadata: 기본 메타데이터
        collection_name: 컬렉션명
        chunk_strategy: 청킹 전략
        max_chars: 청크 최대 문자 수
        overlap: 오버랩 문자 수

    Returns:
        {"ok": True, "doc_id": str, "chunks": int}
    """
    chunks = chunk_text(text, max_chars=max_chars, overlap=overlap, strategy=chunk_strategy)

    col = get_collection(collection_name)
    base_meta = metadata or {}

    ids = []
    texts = []
    metadatas = []

    for chunk in chunks:
        chunk_id = f"{doc_id}__chunk_{chunk['index']}"
        chunk_meta = {
            **base_meta,
            "source_doc_id": doc_id,
            "chunk_index": chunk["index"],
            "total_chunks": len(chunks),
            "chunk_start": chunk["start"],
            "chunk_end": chunk["end"],
        }
        ids.append(chunk_id)
        texts.append(chunk["text"])
        metadatas.append(chunk_meta)

    # Batch embed + upsert (50개씩)
    all_embeddings = []
    for i in range(0, len(texts), 50):
        batch = texts[i:i + 50]
        embs = embed_batch(batch)
        all_embeddings.extend(embs)

    col.upsert(ids=ids, embeddings=all_embeddings, documents=texts, metadatas=metadatas)

    logger.info(f"Indexed {doc_id} as {len(chunks)} chunks into {collection_name}")
    return {"ok": True, "doc_id": doc_id, "chunks": len(chunks)}


def search_with_context(query, n_results=5, collection_name="werub_docs",
                        where=None, merge_chunks=True):
    """검색 후 같은 문서의 인접 청크를 병합하여 컨텍스트 확대

    Args:
        query: 검색 쿼리
        n_results: 반환 결과 수
        collection_name: 컬렉션명
        where: 메타데이터 필터
        merge_chunks: True면 같은 doc_id의 인접 청크 병합

    Returns:
        list[dict] — search()와 동일 형식
    """
    # Fetch more results to allow merging
    raw_results = search(query, n_results=n_results * 2, collection_name=collection_name, where=where)

    if not merge_chunks or not raw_results:
        return raw_results[:n_results]

    # Group by source_doc_id
    groups = {}
    for doc in raw_results:
        source_id = doc.get("metadata", {}).get("source_doc_id", doc["id"])
        if source_id not in groups:
            groups[source_id] = []
        groups[source_id].append(doc)

    # Merge adjacent chunks per group
    merged = []
    for source_id, docs in groups.items():
        docs.sort(key=lambda d: d.get("metadata", {}).get("chunk_index", 0))

        combined_text = "\n\n".join(d["text"] for d in docs)
        best_score = max(d.get("score", 0) for d in docs)
        best_distance = min(d.get("distance", 1) for d in docs)

        merged.append({
            "id": source_id,
            "text": combined_text,
            "metadata": {**docs[0].get("metadata", {}), "merged_chunks": len(docs)},
            "distance": best_distance,
            "score": best_score,
        })

    merged.sort(key=lambda d: d["score"], reverse=True)
    return merged[:n_results]


CHUNK_SEP = "__chunk_"        # 청크 색인 id 규칙: {doc_id}__chunk_N


def delete_document(doc_id, collection_name="werub_docs"):
    """문서 삭제 (청크 색인분 동반 정리) → {"ok", "deleted", "chunks"}

    **삭제 건수를 돌려준다**(2026-07-30 Cerno 제보): 종전엔 무엇을 지웠든 `ok:true`만
    반환해 "지웠다"와 "지울 게 없었다"가 구분되지 않았다. 실제로 부모 id 삭제가 무효인데도
    ok:true가 나가 문서가 조용히 잔존했다.

    청크 정리는 두 경로로 시도한다 — ①`source_doc_id` 메타 매칭 ②**id 접두 매칭**.
    ②가 필요한 이유: 배치 색인 등 호출자가 그 메타를 안 남기면 ①이 통째로 무효가 된다
    (제보의 실제 원인). 접두는 `__chunk_` 구분자까지 포함해 비교하므로
    `note` 로 `note-A` 를 지우는 사고는 나지 않는다.
    """
    col = get_collection(collection_name)
    before = col.count()

    col.delete(ids=[doc_id])
    try:
        col.delete(where={"source_doc_id": doc_id})
    except Exception as e:
        logger.warning("chunk cleanup(meta) failed for %s/%s: %s", collection_name, doc_id, e)
    try:
        got = col.get(ids=None, include=[])          # id 목록만
        chunk_ids = [i for i in (got.get("ids") or []) if i.startswith(doc_id + CHUNK_SEP)]
        if chunk_ids:
            col.delete(ids=chunk_ids)
    except Exception as e:
        logger.warning("chunk cleanup(prefix) failed for %s/%s: %s", collection_name, doc_id, e)

    deleted = max(0, before - col.count())
    return {"ok": True, "deleted": deleted}


def collection_stats(collection_name="werub_docs"):
    """컬렉션 통계"""
    col = get_collection(collection_name)
    return {
        "name": collection_name,
        "count": col.count(),
        "metadata": col.metadata,
    }


# ---------------------------------------------------------------------------
# P2-3: Hybrid Search — 벡터 + 키워드 + Reranking (v2.44.0)
# ---------------------------------------------------------------------------

import sqlite3

_FTS_DB_PATH = os.path.join(BASE_DIR, "rag_fts.db")


def _get_fts_db():
    """FTS5 키워드 검색용 SQLite DB"""
    conn = sqlite3.connect(_FTS_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
        doc_id, text, title, category, collection, tenant,
        tokenize='unicode61'
    )""")
    # v2.88.3 마이그레이션: 구버전(tenant 컬럼 없음) 테이블이면 재구축.
    # 기존 행은 전부 'shared' — 도입 시점에 비공개(tenant) FTS 행이 0건임을 확인.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(docs_fts)")}
    if "tenant" not in cols:
        conn.execute("ALTER TABLE docs_fts RENAME TO docs_fts_old")
        conn.execute("""CREATE VIRTUAL TABLE docs_fts USING fts5(
            doc_id, text, title, category, collection, tenant,
            tokenize='unicode61')""")
        conn.execute("""INSERT INTO docs_fts (doc_id, text, title, category, collection, tenant)
            SELECT doc_id, text, title, category, collection, 'shared' FROM docs_fts_old""")
        conn.execute("DROP TABLE docs_fts_old")
        conn.commit()
        logger.info("[rag] docs_fts tenant 컬럼 마이그레이션 완료 (기존 행='shared')")
    return conn


def fts_index(doc_id, text, title="", category="", collection="werub_docs", tenant="shared"):
    """FTS5에 문서 색인 (벡터 색인과 병행). tenant: 비공개 문서면 테넌트 id, 공유면 'shared'."""
    conn = _get_fts_db()
    try:
        conn.execute("DELETE FROM docs_fts WHERE doc_id=?", (doc_id,))
        conn.execute(
            "INSERT INTO docs_fts (doc_id, text, title, category, collection, tenant) VALUES (?,?,?,?,?,?)",
            (doc_id, text[:5000], title, category, collection, tenant or "shared"))
        conn.commit()
    finally:
        conn.close()


def fts_index_batch(documents, collection="werub_docs"):
    """FTS5 배치 색인 (metadata.tenant_id → tenant, 없으면 'shared')"""
    conn = _get_fts_db()
    try:
        for doc in documents:
            doc_id = doc.get("id", "")
            text = doc.get("text", "")
            meta = doc.get("metadata", {})
            conn.execute("DELETE FROM docs_fts WHERE doc_id=?", (doc_id,))
            conn.execute(
                "INSERT INTO docs_fts (doc_id, text, title, category, collection, tenant) VALUES (?,?,?,?,?,?)",
                (doc_id, text[:5000], meta.get("title", ""),
                 meta.get("category", ""), collection,
                 meta.get("tenant_id") or "shared"))
        conn.commit()
    finally:
        conn.close()


def fts_delete(doc_id, collection="werub_docs"):
    """FTS5 색인 삭제 (벡터 delete_document와 병행)"""
    conn = _get_fts_db()
    try:
        conn.execute("DELETE FROM docs_fts WHERE doc_id=? AND collection=?", (doc_id, collection))
        conn.commit()
    finally:
        conn.close()


def fts_search(query, n_results=10, collection="werub_docs", category=None, tenant=None):
    """SQLite FTS5 키워드 검색.
    tenant 지정 시 공유('shared'/레거시 '')와 자기 테넌트 행만 반환(타 테넌트 비공개 격리, v2.88.3).
    미지정(None)은 필터 없음 — 테넌트 개념 없는 컬렉션(werub_docs 등) 호환.

    Returns:
        [{"doc_id": str, "text": str, "title": str, "category": str, "tenant": str, "rank": float}, ...]
    """
    conn = _get_fts_db()
    try:
        # FTS5 MATCH 쿼리 — 특수문자 이스케이프
        safe_query = re.sub(r'[^\w\s가-힣]', ' ', query).strip()
        if not safe_query:
            return []

        terms = safe_query.split()
        fts_query = " OR ".join(f'"{t}"' for t in terms if len(t) > 1)
        if not fts_query:
            return []

        sql = "SELECT doc_id, text, title, category, tenant, rank FROM docs_fts WHERE docs_fts MATCH ? AND collection=?"
        params = [fts_query, collection]
        if category:
            sql += " AND category=?"
            params.append(category)
        if tenant:
            sql += " AND tenant IN ('shared', '', ?)"
            params.append(tenant)
        sql += " ORDER BY rank LIMIT ?"
        params.append(n_results)

        rows = conn.execute(sql, params).fetchall()
        return [{
            "doc_id": r["doc_id"],
            "text": r["text"],
            "title": r["title"],
            "category": r["category"],
            "tenant": r["tenant"],
            "rank": abs(r["rank"]),
        } for r in rows]
    except Exception as e:
        logger.debug("FTS search error: %s", e)
        return []
    finally:
        conn.close()


def hybrid_search(query, n_results=5, collection_name="werub_docs",
                   where=None, category=None,
                   vector_weight=0.6, keyword_weight=0.4,
                   min_score=0.2, rerank=False, rerank_model=None,
                   tenant=None):
    """P2-3: 하이브리드 검색 — 벡터 + 키워드 + 선택적 Reranking

    Args:
        query: 검색 쿼리
        n_results: 최종 반환 수
        collection_name: ChromaDB 컬렉션
        where: ChromaDB 메타데이터 필터
        category: 카테고리 필터
        vector_weight: 벡터 점수 가중치 (0-1)
        keyword_weight: 키워드 점수 가중치 (0-1)
        min_score: 최소 점수 (이하 필터)
        rerank: LLM reranking 활성화
        rerank_model: reranking용 모델

    Returns:
        [{"id": str, "text": str, "metadata": dict, "score": float,
          "vector_score": float, "keyword_score": float, "source_idx": int}, ...]
    """
    # 1. 벡터 검색 (ChromaDB)
    vector_results = search(
        query, n_results=n_results * 3,
        collection_name=collection_name,
        where=where, min_score=0.1)

    # 2. 키워드 검색 (FTS5) — tenant 지정 시 타 테넌트 비공개 행 SQL 단계 격리(v2.88.3)
    keyword_results = fts_search(
        query, n_results=n_results * 3,
        collection=collection_name, category=category, tenant=tenant)

    # 3. 결과 병합 (Reciprocal Rank Fusion)
    score_map = {}  # doc_id → {vector_score, keyword_score, text, metadata}

    # 벡터 결과
    for i, doc in enumerate(vector_results):
        doc_id = doc["id"]
        rrf_score = 1.0 / (60 + i)  # RRF constant k=60
        score_map[doc_id] = {
            "id": doc_id,
            "text": doc["text"],
            "metadata": doc.get("metadata", {}),
            "vector_score": doc.get("score", 0),
            "keyword_score": 0.0,
            "rrf_vector": rrf_score,
            "rrf_keyword": 0.0,
        }

    # 키워드 결과 병합
    for i, doc in enumerate(keyword_results):
        doc_id = doc["doc_id"]
        rrf_score = 1.0 / (60 + i)
        if doc_id in score_map:
            score_map[doc_id]["keyword_score"] = min(doc["rank"] / 10.0, 1.0)
            score_map[doc_id]["rrf_keyword"] = rrf_score
        else:
            score_map[doc_id] = {
                "id": doc_id,
                "text": doc["text"],
                # tenant_id 동봉 — 후처리 격리(_tenant_kb_filter 등)가 FTS-only 항목도 판정 가능
                "metadata": {"title": doc.get("title", ""), "category": doc.get("category", ""),
                             "tenant_id": doc.get("tenant") or "shared"},
                "vector_score": 0.0,
                "keyword_score": min(doc["rank"] / 10.0, 1.0),
                "rrf_vector": 0.0,
                "rrf_keyword": rrf_score,
            }

    # 3-b. 🔴 메타데이터 필터를 **병합 후 다시 강제**한다 (2026-07-30 Cerno 보안 제보).
    # 벡터 경로는 Chroma가 where를 적용하지만 **FTS 경로는 메타데이터 자체가 없어** 필터를
    # 적용할 수 없다 → FTS-only 히트가 필터를 우회해 타 소유자 문서가 반환됐다(100% 재현).
    # 여기서 누락 메타를 Chroma에서 채운 뒤 판정하고, **채우지 못하면 버린다**(fail-closed).
    if where:
        from core.meta_filter import matches
        missing_ids = [d["id"] for d in score_map.values() if d["rrf_vector"] == 0.0]
        if missing_ids:
            try:
                col = get_collection(collection_name)
                got = col.get(ids=missing_ids, include=["metadatas"])
                for gid, gmeta in zip(got.get("ids") or [], got.get("metadatas") or []):
                    if gid in score_map and isinstance(gmeta, dict):
                        merged = dict(gmeta)
                        merged.setdefault("tenant_id",
                                          score_map[gid]["metadata"].get("tenant_id", "shared"))
                        score_map[gid]["metadata"] = merged
                    elif gid in score_map:
                        score_map.pop(gid, None)
            except Exception as e:               # noqa: BLE001 — 조회 실패 시 FTS-only 전량 배제
                logger.warning("hybrid 메타 보강 실패 — FTS-only 결과 배제(격리 우선): %s", e)
                for mid in missing_ids:
                    score_map.pop(mid, None)
        for doc_id in [d for d, v in score_map.items() if not matches(v.get("metadata"), where)]:
            score_map.pop(doc_id, None)

    # 4. 최종 점수 계산 (가중 RRF)
    for doc_id, doc in score_map.items():
        doc["score"] = round(
            vector_weight * doc["rrf_vector"] + keyword_weight * doc["rrf_keyword"],
            6)
        # score는 RRF **순위** 값이라 관련성 임계로 쓸 수 없다(1순위면 내용 무관 항상 ~0.0167).
        # 임계 게이트가 가능하도록 코사인 유사도를 별도 필드로 노출한다
        # (v2.124.18에서 /api/medical/reference에 같은 이유로 넣은 것과 동일 클래스).
        # FTS-only 히트는 벡터 점수가 없어 None — 호출자는 None을 "판정 불가"로 다뤄야 한다.
        doc["confidence"] = doc["vector_score"] if doc["rrf_vector"] else None

    # 정렬 + 필터
    results = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
    results = [r for r in results if r["score"] >= min_score / 100]  # RRF 스케일 보정
    results = results[:n_results * 2 if rerank else n_results]

    # 5. LLM Reranking (선택)
    if rerank and results and len(results) > 1:
        results = _rerank_results(query, results, n_results, rerank_model)
    else:
        results = results[:n_results]

    # 소스 인덱스 부여
    for i, r in enumerate(results):
        r["source_idx"] = i + 1

    return results


def _rerank_results(query, candidates, n_results, model=None):
    """LLM 기반 relevance reranking

    각 후보 문서의 쿼리 관련성을 0-10 점수로 평가하여 재정렬.
    """
    if not model:
        model = "qwen2.5:7b"  # 경량 모델 사용

    candidate_texts = []
    for i, c in enumerate(candidates[:10]):  # 최대 10개만 rerank
        text_preview = c["text"][:300]
        candidate_texts.append(f"[{i+1}] {text_preview}")

    prompt = f"""다음 질문에 대해 각 문서의 관련성을 0-10 점수로 평가하세요.
반드시 JSON 배열로만 출력하세요.

질문: {query}

문서:
{chr(10).join(candidate_texts)}

출력 형식: [{{"idx": 1, "relevance": 8}}, {{"idx": 2, "relevance": 3}}, ...]"""

    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json={
            "model": model,
            "prompt": prompt,
            "system": "당신은 문서 관련성 평가 전문가입니다. 반드시 JSON 배열만 출력하세요.",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 300},
        }, timeout=30)
        resp = r.json().get("response", "")

        # JSON 배열 파싱
        import json as _json
        match = re.search(r'\[[\s\S]*\]', resp)
        if match:
            scores = _json.loads(match.group(0))
            # 점수 매핑
            score_map = {}
            for s in scores:
                if isinstance(s, dict) and "idx" in s and "relevance" in s:
                    score_map[s["idx"]] = s["relevance"]

            # 재정렬
            for i, c in enumerate(candidates[:10]):
                rerank_score = score_map.get(i + 1, 5) / 10.0
                c["rerank_score"] = rerank_score
                c["score"] = round(c["score"] * 0.4 + rerank_score * 0.6, 4)

            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    except Exception as e:
        logger.debug("Rerank failed (falling back to original order): %s", e)

    return candidates[:n_results]


def build_context_with_citations(results, max_chars=5000):
    """검색 결과로 소스 인용이 포함된 컨텍스트 구성

    Args:
        results: hybrid_search 결과
        max_chars: 최대 컨텍스트 길이

    Returns:
        {"context": str, "citations": [{"idx": int, "id": str, "title": str, "score": float}]}
    """
    context_parts = []
    citations = []
    total_chars = 0

    for r in results:
        title = r.get("metadata", {}).get("title", r["id"])
        idx = r.get("source_idx", len(citations) + 1)
        text = r["text"]

        # 적응적 컨텍스트: 남은 공간에 맞춰 자르기
        remaining = max_chars - total_chars
        if remaining <= 100:
            break
        text = text[:remaining]

        context_parts.append(f"[{idx}] {title}\n{text}")
        meta = r.get("metadata", {}) or {}
        citations.append({
            "idx": idx,
            "id": r["id"],
            "title": title,
            "category": meta.get("category", ""),
            # 출처 식별자 — 색인 메타에 이미 있는 값(발행기관·표준 등). 인용의 실재성
            # 입증에 쓰이므로 빈 문자열로 흘리지 않는다.
            "source": meta.get("source", "") or "",
            "version": meta.get("version", "") or "",
            "score": r.get("score", 0),
            "vector_score": r.get("vector_score", 0),
            "keyword_score": r.get("keyword_score", 0),
        })
        total_chars += len(text) + len(title) + 10

    return {
        "context": "\n\n---\n\n".join(context_parts),
        "citations": citations,
        "total_chars": total_chars,
    }
