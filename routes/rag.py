"""RAG API Blueprint — 문서 색인/검색/질의응답"""
import logging
from flask import Blueprint, request, jsonify
from core.rate_limit import heavy_limit

from client.rag import (
    index_document, index_batch, search as rag_search,
    delete_document, collection_stats, fts_delete,
    index_document_chunked, search_with_context,
    hybrid_search, build_context_with_citations,
    fts_index_batch,
)
from core.config import OLLAMA_BASE
from core.security import require_auth

logger = logging.getLogger("ollama-admin")

rag_bp = Blueprint("rag", __name__)

# _ollama_session은 app.py에서 주입
_ollama_session = None

def init_rag(ollama_session, ollama_base=None):
    global _ollama_session
    global _OLLAMA_BASE
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base


_MAX_FILTER_KEYS = 10

# 🔴 v2.125.18: 이 Blueprint는 **파트너(Cerno·edu) 범용 스토어**라 collection을 클라이언트가
#    지정하는 것이 설계다. 그러나 의료·공시 전용 컬렉션은 자체 라우트가 `_require_tenant`·
#    `_tenant_kb_filter`로 테넌트 격리를 거는데, rag 경로는 그 격리가 **없다** → 임의 유효 키가
#    `collection:"medical_law_knowledge"`로 격리를 우회해 조회/삭제할 수 있었다(2026-08-25 실측).
#    범용 스토어에서 이 컬렉션들을 **차단 목록**으로 막는다(파트너 워크플로는 영향 0).
_PROTECTED_COLLECTIONS = frozenset({
    "medical_knowledge", "medical_law_knowledge", "medical_pub_knowledge", "notice_knowledge",
})


def _collection_blocked(collection):
    """보호 컬렉션이면 403 응답 튜플, 아니면 None."""
    if collection in _PROTECTED_COLLECTIONS:
        return jsonify({"error": "이 컬렉션은 전용 라우트로만 접근할 수 있습니다",
                        "collection": collection}), 403
    return None


def _build_where(filter_obj):
    """클라이언트 filter(dict) → ChromaDB where 변환 (메타데이터 격리: owner/ns 등).

    - {"owner": "u1"}                  → {"owner": "u1"}                (동등)
    - {"owner": "u1", "ns": "cardio"}  → {"$and": [{"owner":"u1"}, {"ns":"cardio"}]}
    - 값에 연산자 dict 통과: {"owner": {"$in": ["u1","u2"]}}
    - 최상위가 이미 $and/$or 등 ChromaDB 연산자면 그대로 사용
    개인별 지식 격리: 색인 시 metadata={"owner":..,"ns":..}로 저장 후 검색 filter로 격리.
    """
    if not filter_obj:
        return None
    if not isinstance(filter_obj, dict):
        raise ValueError("filter must be an object")
    if any(isinstance(k, str) and k.startswith("$") for k in filter_obj):
        return filter_obj  # 이미 ChromaDB where 형식
    items = list(filter_obj.items())
    if len(items) > _MAX_FILTER_KEYS:
        raise ValueError(f"filter supports up to {_MAX_FILTER_KEYS} keys")
    conds = [{k: v} for k, v in items]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


@rag_bp.route("/api/rag/index", methods=["POST"])
@require_auth
def rag_index():
    """Index documents for RAG search
    ---
    tags: [RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            id: {type: string, description: 단건 색인용 문서 ID}
            text: {type: string, description: 단건 색인용 본문}
            metadata: {type: object, description: 검색 filter 매칭 대상 메타(owner/ns/courseId 등)}
            documents: {type: array, items: {type: object}, description: '배치 색인(요청당 최대 500·초과 413) — 각 항목 {id, text, metadata}. 동일 id는 upsert(재색인 멱등)'}
            collection: {type: string, default: werub_docs, description: 대상 컬렉션(테넌트 격리)}
            chunk_strategy: {type: string, enum: [sentence, paragraph], description: 지정 시 청크 색인}
            max_chunk_chars: {type: integer, default: 500}
            chunk_overlap: {type: integer, default: 50}
    responses:
      200:
        schema: {type: object}
    """
    data = request.json or {}
    docs = data.get("documents", [])
    chunk_strategy = data.get("chunk_strategy")

    if not docs:
        doc_id = data.get("id")
        text = data.get("text", "")
        if not doc_id or not text:
            return jsonify({"error": "id and text required"}), 400
        collection = data.get("collection", "werub_docs")
        _blk = _collection_blocked(collection)
        if _blk:
            return _blk

        # Smart chunking (v2.37.0) — chunk_strategy 지정 시 청크 기반 색인
        if chunk_strategy:
            result = index_document_chunked(
                doc_id, text, data.get("metadata"),
                collection_name=collection,
                chunk_strategy=chunk_strategy,
                max_chars=data.get("max_chunk_chars", 500),
                overlap=data.get("chunk_overlap", 50),
            )
        else:
            # collection 전달 — 청크 경로만 반영되고 단건/배치는 기본 컬렉션에 고정되던
            # 비대칭 봉합(edu 연동서 발견: index에 collection 줘도 werub_docs에 들어감)
            result = index_document(doc_id, text, data.get("metadata"),
                                    collection_name=collection)
        # 배치 경로만 FTS에 쓰던 비대칭 봉합 — 단건/청크 색인도 키워드(FTS) 검색
        # 대상에 포함(청크는 부모 doc_id로 원문 기록=delete 동반 정리와 대칭)
        try:
            fts_index_batch([{"id": doc_id, "text": text,
                              "metadata": data.get("metadata") or {}}],
                            collection=collection)
        except Exception as e:
            logger.debug("FTS index failed (non-critical): %s", e)
        return jsonify(result)

    if len(docs) > 500:
        return jsonify({"error": "max 500 documents per batch"}), 413
    _blk = _collection_blocked(data.get("collection", "werub_docs"))
    if _blk:
        return _blk
    result = index_batch(docs, collection_name=data.get("collection", "werub_docs"))
    # P2-3: FTS5에도 동시 색인
    try:
        fts_index_batch(docs, collection=data.get("collection", "werub_docs"))
    except Exception as e:
        logger.debug("FTS batch index failed (non-critical): %s", e)
    logger.info(f"RAG indexed {result['count']} documents")
    return jsonify(result)


@rag_bp.route("/api/rag/search", methods=["POST"])
@heavy_limit("30/minute")
def rag_search_api():
    """Search similar documents
    ---
    tags: [RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string}
            n_results: {type: integer, default: 5, maximum: 20}
            collection: {type: string, default: werub_docs}
            mode: {type: string, enum: [vector, hybrid], default: vector, description: "vector=코사인(score가 유사도) · hybrid=벡터+FTS RRF(유사도는 confidence 필드). ~~keyword~~는 실제로 벡터로 동작해 v2.124.67에서 제거"}
            filter: {type: object, description: 메타데이터 동등/연산자 필터(owner/ns 등)}
            merge_chunks: {type: boolean, default: false}
            rerank: {type: boolean, default: false}
    responses:
      200:
        schema: {type: object}
    """
    data = request.json or {}
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "query required"}), 400
    n = min(data.get("n_results", 5), 20)
    merge = data.get("merge_chunks", False)
    collection = data.get("collection", "werub_docs")
    _blk = _collection_blocked(collection)
    if _blk:
        return _blk
    mode = data.get("mode", "vector")  # vector | hybrid (keyword는 벡터로 떨어져 제거·v2.124.67)
    rerank = data.get("rerank", False)
    try:
        where = _build_where(data.get("filter") or data.get("where"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        if mode == "hybrid":
            results = hybrid_search(query, n_results=n, collection_name=collection,
                                     where=where, rerank=rerank)
            return jsonify({"results": results, "count": len(results), "mode": "hybrid"})
        elif merge:
            results = search_with_context(query, n_results=n, collection_name=collection,
                                          where=where, merge_chunks=True)
        else:
            results = rag_search(query, n_results=n, collection_name=collection, where=where)
    except Exception as e:
        logger.error("rag_search embedding/search failed: %s", e)
        return jsonify({"error": "RAG 검색 일시 불가 (임베딩 모델 응답 실패)", "detail": str(e)[:200]}), 503
    return jsonify({"results": results, "count": len(results), "mode": mode})


@rag_bp.route("/api/rag/ask", methods=["POST"])
@heavy_limit("30/minute")
def rag_ask():
    """RAG: Search + generate answer
    ---
    tags: [RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string}
            model: {type: string, default: "qwen2.5:7b", description: 품질 우선은 qwen2.5:14b 권장}
            n_context: {type: integer, default: 3, maximum: 10}
            collection: {type: string, default: werub_docs, description: 대상 컬렉션(테넌트 격리, v2.124.37)}
            filter: {type: object, description: 컬렉션 내 메타데이터 필터(색인 metadata 키와 매칭)}
    responses:
      200:
        description: '{answer, sources:[{id,score,text}], model}'
        schema: {type: object}
    """
    data = request.json or {}
    question = data.get("question", "")
    if not question:
        return jsonify({"error": "question required"}), 400
    model = data.get("model", "qwen2.5:7b")
    n_ctx = min(data.get("n_context", 3), 10)
    # collection 지정 지원 — search와 달리 ask만 기본 컬렉션 고정이던 비대칭 봉합
    # (edu 연동 계약 {question, collection} → {answer, sources})
    collection = data.get("collection", "werub_docs")
    _blk = _collection_blocked(collection)
    if _blk:
        return _blk
    try:
        where = _build_where(data.get("filter") or data.get("where"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        sources = rag_search(question, n_results=n_ctx, collection_name=collection,
                             where=where)
    except Exception as e:
        logger.error("rag_ask embedding/search failed: %s", e)
        return jsonify({"error": "RAG 검색 일시 불가 (임베딩 모델 응답 실패)", "detail": str(e)[:200]}), 503
    if not sources:
        return jsonify({"answer": "\uad00\ub828 \ubb38\uc11c\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", "sources": [], "model": model})

    context = "\n\n---\n\n".join([
        f"[\ubb38\uc11c {i+1}] (\uc720\uc0ac\ub3c4: {s['score']})\n{s['text'][:500]}"
        for i, s in enumerate(sources)
    ])

    system = """\ub2f9\uc2e0\uc740 \ud55c\uad6d\uc5b4 Q&A \uc804\ubb38\uac00\uc785\ub2c8\ub2e4. \ubc18\ub4dc\uc2dc \ud55c\uad6d\uc5b4\ub85c\ub9cc \ub2f5\ubcc0\ud558\uc138\uc694.
\uc8fc\uc5b4\uc9c4 \ucc38\uace0 \ubb38\uc11c\ub97c \uae30\ubc18\uc73c\ub85c \uc815\ud655\ud558\uac8c \ub2f5\ubcc0\ud569\ub2c8\ub2e4.
\ubb38\uc11c\uc5d0 \uc5c6\ub294 \ub0b4\uc6a9\uc740 '\uad00\ub828 \ubb38\uc11c\uc5d0\uc11c \ud574\ub2f9 \uc815\ubcf4\ub97c \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4'\ub77c\uace0 \ub2f5\ud558\uc138\uc694."""

    prompt = f"\ucc38\uace0 \ubb38\uc11c:\n{context}\n\n\uc9c8\ubb38: {question}\n\n\ub2f5\ubcc0:"

    try:
        # v2.125.12 D1 슬라이스 2 — task 라우터 경유(페이로드 동일·호출자 세션 전달). 종전엔 HTTP 오류가
        # `response` 없는 JSON → **빈 answer로 200**이 나갔다(무성 실패 클래스) → 예외 분기와 같은 문구로 드러낸다.
        from core import model_router
        res = model_router.generate(
            "rag.answer", prompt, model=model, system=system,
            options={"temperature": 0.3, "num_predict": 300}, timeout=120,
            adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
        answer = res.text if res.ok else f"LLM \uc751\ub2f5 \uc2e4\ud328: {res.error}"
    except Exception as e:
        answer = f"LLM \uc751\ub2f5 \uc2e4\ud328: {str(e)}"

    return jsonify({
        "answer": answer,
        "sources": [{"id": s["id"], "score": s["score"], "text": s["text"][:200]} for s in sources],
        "model": model,
    })


@rag_bp.route("/api/rag/document/<doc_id>", methods=["DELETE"])
@require_auth
def rag_delete(doc_id):
    """Delete a document (멱등 — 없는 id도 ok:true. 청크 색인분·FTS 동반 정리)
    ---
    tags: [RAG]
    security: [{Bearer: []}]
    parameters:
      - {name: doc_id, in: path, type: string, required: true}
      - {name: collection, in: query, type: string, required: false, description: 기본 werub_docs — 대상 컬렉션 반드시 지정}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            deleted: {type: integer, description: "실제 삭제된 벡터 수(0이면 대상 없음 — 멱등이라 ok:true는 유지)"}
            fts_deleted: {type: boolean, description: "FTS 인덱스 동반 정리 성공 여부"}
    """
    # collection 쿼리 전달 — index/ask와 동일 비대칭 봉합(지정 컬렉션 문서 삭제 가능)
    collection = request.args.get("collection", "werub_docs")
    _blk = _collection_blocked(collection)
    if _blk:
        return _blk
    result = delete_document(doc_id, collection_name=collection)
    # 배치 색인이 FTS에도 쓰므로(fts_index_batch) 삭제도 동반 정리 —
    # 미정리 시 삭제 문서가 hybrid 검색 FTS 경로로 재부상
    try:
        fts_delete(doc_id, collection=collection)
        result["fts_deleted"] = True
    except Exception as e:
        logger.debug("FTS delete failed (non-critical): %s", e)
        result["fts_deleted"] = False
    return jsonify(result)


@rag_bp.route("/api/rag/stats")
def rag_stats():
    """RAG index statistics (컬렉션 단위 문서 수 — 메타 필터별 카운트는 미지원)
    ---
    tags: [RAG]
    security: [{Bearer: []}]
    parameters:
      - {name: collection, in: query, type: string, required: false, description: 기본 werub_docs}
    responses:
      200:
        schema:
          type: object
          properties:
            name: {type: string}
            count: {type: integer}
            metadata: {type: object}
    """
    return jsonify(collection_stats(
        collection_name=request.args.get("collection", "werub_docs")))
