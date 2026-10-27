"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

interface RagStats {
  name: string;
  count: number;
  metadata: Record<string, unknown>;
}

interface SearchResult {
  id: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

export default function KnowledgePage() {
  const [stats, setStats] = useState<RagStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // 색인
  const [indexForm, setIndexForm] = useState({ id: "", text: "", category: "", source: "" });
  const [indexing, setIndexing] = useState(false);

  // 검색
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);

  // 질의응답
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState("");

  const fetchStats = async () => {
    try {
      const data = await gpuFetch("/api/rag/stats");
      setStats(data);
    } catch { setError("통계 로딩 실패"); }
    setLoading(false);
  };

  useEffect(() => { fetchStats(); }, []);

  const handleIndex = async () => {
    if (!indexForm.text.trim()) { setError("텍스트를 입력하세요"); return; }
    setIndexing(true); setError(""); setSuccess("");
    const docId = indexForm.id.trim() || `doc-${Date.now()}`;
    try {
      await gpuFetch("/api/rag/index", {
        method: "POST",
        body: {
          id: docId,
          text: indexForm.text,
          metadata: {
            ...(indexForm.category ? { category: indexForm.category } : {}),
            ...(indexForm.source ? { source: indexForm.source } : {}),
          },
        },
      });
      setSuccess(`문서 "${docId}" 색인 완료`);
      setIndexForm({ id: "", text: "", category: "", source: "" });
      fetchStats();
    } catch { setError("색인 실패"); }
    setIndexing(false);
  };

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true); setResults([]);
    try {
      const data = await gpuFetch("/api/rag/search", {
        method: "POST",
        body: { query, top_k: 5 },
      });
      setResults(data.results || []);
    } catch { setError("검색 실패"); }
    setSearching(false);
  };

  const handleAsk = async () => {
    if (!question.trim()) return;
    setAsking(true); setAnswer("");
    try {
      const data = await gpuFetch("/api/rag/ask", {
        method: "POST",
        body: { question, n_context: 3 },
      });
      setAnswer(data.answer || "답변 없음");
    } catch { setError("질의 실패"); }
    setAsking(false);
  };

  const handleDelete = async (docId: string) => {
    if (!confirm(`문서 "${docId}"를 삭제합니다.`)) return;
    try {
      await gpuFetch(`/api/rag/document/${docId}`, { method: "DELETE" });
      setSuccess(`"${docId}" 삭제 완료`);
      setResults(results.filter(r => r.id !== docId));
      fetchStats();
    } catch { setError("삭제 실패"); }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Knowledge Base (RAG)</h1>

      {/* 통계 */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4">
          <div className="text-xs text-[var(--muted)]">색인 문서</div>
          <div className="text-2xl font-bold text-[var(--text)]">{loading ? "..." : stats?.count ?? 0}</div>
        </div>
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4">
          <div className="text-xs text-[var(--muted)]">임베딩 모델</div>
          <div className="text-sm font-bold text-[var(--text)]">bge-m3</div>
          <div className="text-[10px] text-[var(--muted)]">1024차원, 다국어</div>
        </div>
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4">
          <div className="text-xs text-[var(--muted)]">컬렉션</div>
          <div className="text-sm font-bold text-[var(--text)]">{stats?.name ?? "werub_docs"}</div>
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-4 text-red-400 text-sm">{error}</div>}
      {success && <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-4 mb-4 text-green-400 text-sm">{success}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 문서 색인 */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">문서 색인</h2>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <input value={indexForm.id} onChange={e => setIndexForm({ ...indexForm, id: e.target.value })}
                placeholder="문서 ID (자동생성)" className="px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
              <input value={indexForm.category} onChange={e => setIndexForm({ ...indexForm, category: e.target.value })}
                placeholder="카테고리 (선택)" className="px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
            </div>
            <input value={indexForm.source} onChange={e => setIndexForm({ ...indexForm, source: e.target.value })}
              placeholder="출처 (선택)" className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
            <textarea value={indexForm.text} onChange={e => setIndexForm({ ...indexForm, text: e.target.value })}
              placeholder="색인할 텍스트를 입력하세요..." rows={5}
              className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)] resize-none" />
            <button onClick={handleIndex} disabled={indexing || !indexForm.text.trim()}
              className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-40">
              {indexing ? "색인 중..." : "색인 추가"}
            </button>
          </div>
        </div>

        {/* 검색 */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">벡터 검색</h2>
          <div className="flex gap-2 mb-4">
            <input value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSearch()}
              placeholder="검색어 입력..." className="flex-1 px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
            <button onClick={handleSearch} disabled={searching}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-40">
              {searching ? "검색 중..." : "검색"}
            </button>
          </div>
          {results.length > 0 && (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {results.map(r => (
                <div key={r.id} className="bg-[var(--bg)] rounded-lg p-3 border border-[var(--border)]/50">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-mono text-[var(--muted)]">{r.id}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">{(r.score * 100).toFixed(0)}%</span>
                      <button onClick={() => handleDelete(r.id)}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30">삭제</button>
                    </div>
                  </div>
                  <p className="text-xs text-[var(--text)] line-clamp-3">{r.text.slice(0, 200)}</p>
                  {r.metadata && Object.keys(r.metadata).length > 0 && (
                    <div className="flex gap-1 mt-1">
                      {Object.entries(r.metadata).map(([k, v]) => (
                        <span key={k} className="text-[9px] px-1 py-0.5 rounded bg-[var(--surface)] text-[var(--muted)]">{k}: {String(v)}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* RAG 질의응답 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mt-6">
        <h2 className="text-lg font-semibold mb-4">RAG 질의응답</h2>
        <div className="flex gap-2 mb-4">
          <input value={question} onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAsk()}
            placeholder="질문을 입력하세요 (색인된 문서 기반으로 답변)..." className="flex-1 px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <button onClick={handleAsk} disabled={asking}
            className="px-4 py-2 bg-violet-600 text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-40">
            {asking ? "답변 중..." : "질문하기"}
          </button>
        </div>
        {answer && (
          <div className="bg-[var(--bg)] rounded-lg p-4 border border-[var(--border)] text-sm text-[var(--text)] whitespace-pre-wrap">
            {answer}
          </div>
        )}
      </div>
    </div>
  );
}
