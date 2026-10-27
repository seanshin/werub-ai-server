"use client";

import { useState } from "react";
import { gpuFetch } from "@/lib/api";

interface SearchResult {
  law_name: string;
  article: string;
  title: string;
  content: string;
  category: string;
}

export default function Page() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);

  const categories = ["전체", "근로기준법", "최저임금법", "산업안전보건법", "남녀고용평등법", "근로자퇴직급여보장법"];

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await gpuFetch("/api/hr-law/search", {
        method: "POST",
        body: { query: query.trim(), category: category || undefined },
      });
      setResults(data.results || data || []);
      setSearched(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "검색 중 오류가 발생했습니다.");
    }
    setLoading(false);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">법률 검색</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
            placeholder="검색어를 입력하세요 (예: 연차휴가, 퇴직금)"
            className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]"
          >
            <option value="">카테고리 선택</option>
            {categories.map((c) => (
              <option key={c} value={c === "전체" ? "" : c}>{c}</option>
            ))}
          </select>
          <button
            onClick={search}
            disabled={loading || !query.trim()}
            className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50"
          >
            {loading ? "검색 중..." : "검색"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>
      )}

      {searched && results.length === 0 && !loading && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)]">
          검색 결과가 없습니다.
        </div>
      )}

      <div className="flex flex-col gap-4">
        {results.map((r, i) => (
          <div key={i} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
            <div className="flex items-start justify-between mb-3">
              <div>
                <span className="text-xs px-2 py-1 rounded-full bg-[var(--accent)]/20 text-[var(--accent)] mr-2">
                  {r.category}
                </span>
                <span className="font-semibold text-[var(--text)]">{r.law_name}</span>
              </div>
              <span className="text-sm text-[var(--muted)]">{r.article}</span>
            </div>
            <h3 className="font-medium text-[var(--text)] mb-2">{r.title}</h3>
            <p className="text-sm text-[var(--muted)] leading-relaxed whitespace-pre-wrap">{r.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
