"use client";

import { useState, useCallback } from "react";
import { gpuFetch } from "@/lib/api";

interface Drug {
  code: string;
  name_ko: string;
  name_en?: string;
  category?: string;
  dosage?: string;
  route?: string;
  source_tier?: string;
  source_org?: string;
  source_url?: string;
  confidence?: number;
  side_effects?: string;
  contraindications?: string;
  renal_adjustment?: string;
  pregnancy_category?: string;
}

interface Interaction {
  drug_a: string;
  drug_b: string;
  severity: string;
  description: string;
}

export default function Page() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [interactions, setInteractions] = useState<Record<string, Interaction[]>>({});
  const [stats, setStats] = useState<{ drug_count?: number; interaction_count?: number; denial_count?: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const search = useCallback(async () => {
    if (!query.trim() && !category) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (category) params.set("category", category);
      const data = await gpuFetch(`/api/medical/clinical/drug?${params}`);
      setDrugs(data.drugs || data.results || []);
      if (!stats) {
        const s = await gpuFetch("/api/medical/clinical/drug/stats").catch(() => null);
        if (s) setStats(s);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [query, category, stats]);

  const loadInteractions = async (code: string) => {
    if (interactions[code]) { setExpanded(expanded === code ? null : code); return; }
    setExpanded(code);
    try {
      const data = await gpuFetch(`/api/medical/clinical/interaction?drug=${code}`);
      setInteractions((prev) => ({ ...prev, [code]: data.interactions || [] }));
    } catch { /* ignore */ }
  };

  const tierColor = (tier?: string) => {
    if (!tier) return "bg-gray-500/20 text-gray-400";
    if (tier.includes("T1")) return "bg-emerald-500/20 text-emerald-400";
    if (tier.includes("T2")) return "bg-blue-500/20 text-blue-400";
    if (tier.includes("T3")) return "bg-yellow-500/20 text-yellow-400";
    return "bg-red-500/20 text-red-400";
  };

  const severityColor = (s: string) => {
    const l = s.toLowerCase();
    if (l.includes("major") || l.includes("심각")) return "text-red-400";
    if (l.includes("moderate") || l.includes("중등")) return "text-yellow-400";
    return "text-blue-400";
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Drug Database</h1>

      {/* Search */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 mb-6">
        <div className="flex gap-3">
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
            placeholder="약물명 검색 (메로페넴, aspirin, ...)"
            className="flex-1 bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          <input value={category} onChange={(e) => setCategory(e.target.value)}
            placeholder="카테고리"
            className="w-40 bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          <button onClick={search} disabled={loading}
            className="px-5 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-50">
            {loading ? "..." : "검색"}
          </button>
        </div>
        {stats && (
          <div className="flex gap-4 mt-3 text-xs text-[var(--muted)]">
            <span>Drug: {stats.drug_count}건</span>
            <span>Interactions: {stats.interaction_count}건</span>
            <span>Denial: {stats.denial_count}건</span>
          </div>
        )}
      </div>

      {/* Results */}
      {drugs.length === 0 && !loading && (
        <p className="text-[var(--muted)] text-sm">검색어를 입력하세요.</p>
      )}

      <div className="space-y-3">
        {drugs.map((d) => (
          <div key={d.code} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5">
            <div className="flex items-start justify-between mb-2">
              <div>
                <span className="font-mono text-xs text-[var(--muted)] mr-2">{d.code}</span>
                <span className="font-semibold text-[var(--text)]">{d.name_ko}</span>
                {d.name_en && <span className="text-sm text-[var(--muted)] ml-2">({d.name_en})</span>}
              </div>
              <div className="flex gap-2">
                {d.source_tier && (
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${tierColor(d.source_tier)}`}>
                    {d.source_tier}
                  </span>
                )}
                {d.source_org && (
                  d.source_url ? (
                    <a href={d.source_url} target="_blank" rel="noreferrer" className="text-[10px] text-blue-400 hover:underline">{d.source_org}</a>
                  ) : (
                    <span className="text-[10px] text-[var(--muted)]">{d.source_org}</span>
                  )
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-[var(--muted)] mb-3">
              {d.category && <span>카테고리: {d.category}</span>}
              {d.dosage && <span>용법: {d.dosage}</span>}
              {d.route && <span>투여: {d.route}</span>}
              {d.pregnancy_category && <span>임신: {d.pregnancy_category}</span>}
            </div>

            {d.contraindications && <div className="text-xs mb-1"><span className="text-red-400">금기:</span> {d.contraindications}</div>}
            {d.side_effects && <div className="text-xs mb-1"><span className="text-yellow-400">부작용:</span> {d.side_effects}</div>}
            {d.renal_adjustment && <div className="text-xs mb-2"><span className="text-blue-400">신기능:</span> {d.renal_adjustment}</div>}

            <button onClick={() => loadInteractions(d.code)}
              className="text-xs text-[var(--accent)] hover:underline">
              {expanded === d.code ? "▲ 상호작용 닫기" : "▼ 상호작용 보기"}
            </button>

            {expanded === d.code && interactions[d.code] && (
              <div className="mt-2 space-y-1.5">
                {interactions[d.code].length === 0 ? (
                  <p className="text-xs text-[var(--muted)]">상호작용 정보 없음</p>
                ) : (
                  interactions[d.code].map((inter, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs bg-[var(--bg)] rounded-lg p-2">
                      <span className={`font-semibold ${severityColor(inter.severity)}`}>{inter.severity}</span>
                      <span className="text-[var(--text)]">{inter.drug_a} + {inter.drug_b}</span>
                      <span className="text-[var(--muted)]">— {inter.description}</span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
