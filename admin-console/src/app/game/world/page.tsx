"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface WorldEntry {
  id?: string;
  title: string;
  category: string;
  content: string;
}

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [form, setForm] = useState({ title: "", category: "geography", content: "" });
  const [searchQuery, setSearchQuery] = useState("");
  const [results, setResults] = useState<WorldEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const categories = ["geography", "history", "faction", "race", "magic", "item"];

  const fetchKey = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      const list = data.tenants || data || [];
      if (list.length > 0) setGameKey(list[0].api_key);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchKey(); }, [fetchKey]);

  const submit = async () => {
    if (!form.title.trim() || !form.content.trim()) { setError("제목과 내용은 필수입니다."); return; }
    if (!gameKey) { setError("Game Key가 없습니다."); return; }
    setSubmitting(true);
    setError("");
    setSuccess("");
    try {
      await gpuFetch("/api/game/world/setting", {
        method: "POST",
        body: form,
        headers: gameHeaders(gameKey),
      });
      setSuccess("세계관 설정이 등록되었습니다.");
      setForm({ title: "", category: "geography", content: "" });
    } catch {
      setError("등록 실패");
    }
    setSubmitting(false);
  };

  const search = async () => {
    if (!searchQuery.trim()) return;
    if (!gameKey) { setError("Game Key가 없습니다."); return; }
    setSearching(true);
    setError("");
    try {
      const data = await gpuFetch("/api/game/world/search", {
        method: "POST",
        body: { query: searchQuery.trim() },
        headers: gameHeaders(gameKey),
      });
      setResults(data.results || data || []);
    } catch {
      setError("검색 실패");
    }
    setSearching(false);
  };

  const categoryLabel: Record<string, string> = {
    geography: "지리", history: "역사", faction: "세력",
    race: "종족", magic: "마법", item: "아이템",
  };

  const categoryColor: Record<string, string> = {
    geography: "bg-green-500/20 text-green-400",
    history: "bg-yellow-500/20 text-yellow-400",
    faction: "bg-red-500/20 text-red-400",
    race: "bg-blue-500/20 text-blue-400",
    magic: "bg-purple-500/20 text-purple-400",
    item: "bg-orange-500/20 text-orange-400",
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">세계관 설정</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">새 설정 등록</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="제목"
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
            {categories.map((c) => <option key={c} value={c}>{categoryLabel[c] || c}</option>)}
          </select>
        </div>
        <textarea value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })}
          placeholder="세계관 설정 내용을 입력하세요..." rows={5}
          className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm resize-none mb-3 focus:outline-none focus:border-[var(--accent)]" />
        <button onClick={submit} disabled={submitting}
          className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
          {submitting ? "등록 중..." : "등록"}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}
      {success && <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-4 mb-6 text-green-400 text-sm">{success}</div>}

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">세계관 검색</h2>
        <div className="flex gap-3">
          <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()} placeholder="검색어 입력"
            className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <button onClick={search} disabled={searching || !searchQuery.trim()}
            className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
            {searching ? "검색 중..." : "검색"}
          </button>
        </div>
      </div>

      {results.length > 0 && (
        <div className="flex flex-col gap-4">
          {results.map((r, i) => (
            <div key={i} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5">
              <div className="flex items-center gap-2 mb-3">
                <span className={`text-xs px-2 py-1 rounded-full ${categoryColor[r.category] || "bg-[var(--surface)] text-[var(--muted)]"}`}>
                  {categoryLabel[r.category] || r.category}
                </span>
                <h3 className="font-semibold text-[var(--text)]">{r.title}</h3>
              </div>
              <p className="text-sm text-[var(--muted)] leading-relaxed whitespace-pre-wrap">{r.content}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
