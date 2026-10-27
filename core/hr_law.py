"""
HR Law (근로기준법) Quality Module
법령 검색, 질문 분류, 하이브리드 RAG 통합
"""

import sqlite3
import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# 법령 DB 경로 (원격 배포 시 상대 경로 사용)
HR_LAW_DB = "hr_law.db"

# 질문 난이도 키워드
SIMPLE_KEYWORDS = [
    "뭔가요", "뭐예요", "뭐지", "정의", "의미", "뜻",
    "무엇", "어떻게", "어디", "언제", "누가",
    "기본", "초급", "입문",
]

COMPLEX_KEYWORDS = [
    "판례", "사건", "소송", "중재", "중복", "충돌",
    "예외", "특수", "고급", "복합", "다단계",
    "위반", "처벌", "징계", "손해배상", "부당해고",
]


def get_db():
    """HR Law DB 연결"""
    try:
        conn = sqlite3.connect(HR_LAW_DB, timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")  # ingest 동시 진행 시 BUSY 즉시실패 방어
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"HR Law DB 연결 실패: {e}")
        return None


def search_by_sqlite(query: str, n_results: int = 5) -> List[Dict]:
    """
    SQLite LIKE 검색 (정확 검색)

    Args:
        query: 검색어
        n_results: 최대 결과 수

    Returns:
        법령 문서 리스트 [{"law_name": "...", "article": "...", "content": "...", "score": 0.9}]
    """
    conn = get_db()
    if not conn:
        return []

    try:
        # 키워드 추출: 한글 단어와 숫자만 추출
        import re
        keywords = re.findall(r'[\uac00-\ud7a3\uac00-\ud7a30-9]+', query)
        search_queries = keywords if keywords else [query[:20]]

        all_results = {}

        # 각 키워드로 검색
        for search_term in search_queries:
            if len(search_term) < 2:
                continue

            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, law_name, article, title, content, category, summary
                FROM law_documents
                WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
                ORDER BY
                    CASE
                        WHEN title LIKE ? THEN 1.0
                        WHEN content LIKE ? THEN 0.7
                        ELSE 0.5
                    END DESC
                LIMIT ?
                """,
                (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%",
                 f"%{search_term}%", f"%{search_term}%", n_results * 2),
            )

            for row in cursor.fetchall():
                if row["id"] not in all_results:
                    all_results[row["id"]] = row

        # 결과를 리스트로 변환
        cursor = conn.cursor()

        results = []
        for row_id, row in list(all_results.items())[:n_results]:
            # 스코어링: title 매칭이 높은 점수
            score = 0.8  # 기본값
            query_lower = query.lower()
            if any(kw.lower() in row["title"].lower() for kw in keywords):
                score = 1.0
            elif any(kw.lower() in (row["content"] or "").lower() for kw in keywords):
                score = 0.85

            results.append({
                "id": row["id"],
                "law_name": row["law_name"],
                "article": row["article"],
                "title": row["title"],
                "content": row["content"][:500],  # 처음 500자만
                "category": row["category"],
                "summary": row["summary"],
                "source": "sqlite",
                "score": score,
            })

        conn.close()
        return results

    except Exception as e:
        logger.error(f"SQLite 검색 실패: {e}")
        conn.close()
        return []


def search_by_rag(
    query: str, rag_search_func, n_results: int = 5, min_score: float = 0.5
) -> List[Dict]:
    """
    RAG 기반 검색 (의미론적 검색)

    Args:
        query: 검색어
        rag_search_func: RAG 검색 함수 (ChromaDB)
        n_results: 최대 결과 수
        min_score: 유사도 임계값

    Returns:
        법령 문서 리스트 [{"content": "...", "score": 0.8, "source": "rag"}]
    """
    try:
        # RAG 검색 (기본 컬렉션 사용)
        logger.info(f"[search_by_rag] Calling rag_search_func with query='{query}', n_results={n_results}")
        results = rag_search_func(query, n_results=n_results)
        logger.info(f"[search_by_rag] rag_search_func returned {len(results) if results else 0} results")

        # 임계값 필터링
        filtered = []
        for r in results:
            score = r.get("score", 0)
            if score >= min_score:
                # RAG 결과 형식 변환 (metadata에서 법령 정보 추출)
                metadata = r.get("metadata", {})
                converted = {
                    "id": r.get("id", ""),
                    "law_name": metadata.get("law", ""),
                    "article": metadata.get("article", ""),
                    "title": metadata.get("title", ""),
                    "content": r.get("text", ""),  # text -> content로 변환
                    "category": metadata.get("category", ""),
                    "summary": metadata.get("summary", ""),
                    "source": "rag",
                    "score": score,
                }
                filtered.append(converted)

        logger.info(f"[search_by_rag] Filtered {len(filtered)} results (score >= {min_score})")
        return filtered

    except Exception as e:
        logger.warning(f"RAG 검색 실패: {e}")
        return []


def hybrid_search(
    query: str, rag_search_func=None, n_results: int = 5, min_rag_score: float = 0.5
) -> List[Dict]:
    """
    하이브리드 검색 (SQLite + RAG 병합)

    Args:
        query: 검색어
        rag_search_func: RAG 검색 함수
        n_results: 최대 결과 수
        min_rag_score: RAG 유사도 임계값

    Returns:
        병합된 검색 결과 (점수 내림차순)

    구현:
    1. SQLite 검색 (정확 매칭)
    2. RAG 검색 (의미론적 매칭)
    3. 중복 제거 (id 기준)
    4. 점수 내림차순 정렬
    """
    logger.info(f"[hybrid_search] Starting: query='{query}', rag_func={type(rag_search_func).__name__}")
    # 1. SQLite 검색
    sqlite_results = search_by_sqlite(query, n_results=n_results)
    logger.info(f"[hybrid_search] SQLite results: {len(sqlite_results)}")

    # 2. RAG 검색 (존재 시)
    rag_results = []
    if rag_search_func:
        logger.info(f"[hybrid_search] Calling RAG search...")
        rag_results = search_by_rag(
            query, rag_search_func, n_results=n_results, min_score=min_rag_score
        )
        logger.info(f"[hybrid_search] RAG results: {len(rag_results)}")
    else:
        logger.info(f"[hybrid_search] RAG search skipped (rag_search_func is None)")

    # 3. 병합: SQLite 결과 + RAG 결과 (중복 제거)
    seen_ids = set()
    merged = []

    # SQLite 우선 (정확 검색은 더 신뢰도 높음)
    for result in sqlite_results:
        if result["id"] not in seen_ids:
            merged.append(result)
            seen_ids.add(result["id"])

    # RAG 추가 (SQLite에 없던 것만)
    for result in rag_results:
        result_id = result.get("id", result.get("text", ""))
        if result_id not in seen_ids:
            merged.append(result)
            seen_ids.add(result_id)

    # 4. 점수 내림차순 정렬
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 최대 n_results만 반환
    return merged[:n_results]


def classify_question(query: str) -> str:
    """
    질문 난이도 분류 (단순 vs 복합)

    Args:
        query: 질문

    Returns:
        "simple" 또는 "complex"

    로직:
    - SIMPLE_KEYWORDS 포함 → simple (빠른 모델)
    - COMPLEX_KEYWORDS 포함 → complex (정확한 모델)
    - 기본 → simple (짧은 질문) 또는 complex (긴 질문)
    """
    query_lower = query.lower()

    # 키워드 기반 분류
    simple_score = sum(1 for kw in SIMPLE_KEYWORDS if kw in query_lower)
    complex_score = sum(1 for kw in COMPLEX_KEYWORDS if kw in query_lower)

    if complex_score > simple_score:
        return "complex"

    if simple_score > complex_score:
        return "simple"

    # 기본: 길이 기반
    if len(query) < 30:
        return "simple"
    else:
        return "complex"


def select_model_by_difficulty(difficulty: str) -> str:
    """
    난이도에 따른 모델 선택

    Args:
        difficulty: "simple" 또는 "complex"

    Returns:
        모델명 ("qwen2.5:7b" 또는 "qwen2.5:14b")

    로직:
    - simple: qwen2.5:7b (빠름, 2-3초)
    - complex: qwen2.5:14b (정확, 4-6초)
    """
    if difficulty == "complex":
        return "qwen2.5:14b"
    return "qwen2.5:7b"


def format_law_reference(law_name: str, article: str, title: str) -> str:
    """
    법령 인용 포맷

    Args:
        law_name: 법률명 (근로기준법)
        article: 조항 (제23조)
        title: 조항명

    Returns:
        포맷된 참고: "[근로기준법 제23조] 조항명"

    Example:
        >>> format_law_reference("근로기준법", "제23조", "계약 기간")
        "[근로기준법 제23조] 계약 기간"
    """
    if article and title:
        return f"[{law_name} {article}] {title}"
    elif article:
        return f"[{law_name} {article}]"
    else:
        return f"[{law_name}]"


def prepare_law_context(
    results: List[Dict], max_chars: int = 1000
) -> Tuple[str, List[Dict]]:
    """
    검색 결과를 LLM 프롬프트용 컨텍스트로 포맷

    Args:
        results: 검색 결과 리스트
        max_chars: 최대 문자 수

    Returns:
        (컨텍스트 텍스트, 인용 메타데이터)

    포맷:
        [문서 1] (출처: SQLite, 점수: 0.95)
        법률명 조항: 내용...

        [문서 2] ...
    """
    logger.info(f"[prepare_law_context] Processing {len(results)} results")
    context_lines = []
    references = []

    for i, result in enumerate(results, 1):
        law_name = result.get("law_name", "")
        article = result.get("article", "")
        title = result.get("title", "")
        content = result.get("content", "")
        source = result.get("source", "unknown")
        score = result.get("score", 0)
        logger.info(f"[prepare_law_context] Result {i}: law={law_name}, article={article}, content_len={len(content)}")

        # 참고 포맷
        ref_title = format_law_reference(law_name, article, title)

        # 컨텍스트 추가
        source_label = "RAG" if source == "rag" else "근로기준법DB"
        context_chunk = content[:max_chars // len(results)]
        line = f"[문서 {i}] ({source_label}, 관련도: {score:.2f})\n{ref_title}\n{context_chunk}"
        context_lines.append(line)
        logger.info(f"[prepare_law_context] Context chunk {i} length: {len(context_chunk)}")

        # 메타데이터 저장 (답변에 포함할 용)
        references.append({
            "law": law_name,
            "article": article,
            "title": title,
            "source": source,
            "score": score,
        })

    context = "\n\n---\n\n".join(context_lines)
    logger.info(f"[prepare_law_context] Final context length: {len(context)}")
    return context, references
