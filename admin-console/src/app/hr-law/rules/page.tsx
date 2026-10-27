"use client";

import { useState, useEffect } from "react";
import { gpuFetch, tenantHeaders } from "@/lib/api";

interface Article {
  id: string;
  title: string;
  required: boolean;
  hint?: string;
  legal?: string;
}

interface Section {
  id: string;
  title: string;
  required: boolean;
  legal_basis?: string;
  articles: Article[];
}

export default function Page() {
  const [sections, setSections] = useState<Section[]>([]);
  const [tenantKey, setTenantKey] = useState("");
  const [completeness, setCompleteness] = useState<{completeness_pct:number; total_required:number; total_completed:number; missing_required:{section:string;articles:string[]}[]} | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState<string | null>(null);
  const [draftResult, setDraftResult] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    gpuFetch("/api/hr-law/rules/template")
      .then((d) => setSections(d.sections || []))
      .catch(() => setError("템플릿 로딩 실패"));
    // 자동 테넌트 키 로딩
    gpuFetch("/api/hr-law/tenants")
      .then((d) => { if (d.tenants?.length) setTenantKey(d.tenants[0].api_key); })
      .catch(() => {});
  }, []);

  const fetchCompleteness = async () => {
    if (!tenantKey.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await gpuFetch("/api/hr-law/rules/completeness", { headers: tenantHeaders(tenantKey) });
      if (data.error) { setError(data.error); } else { setCompleteness(data); }
    } catch { setError("완성도 조회 실패"); }
    setLoading(false);
  };

  const generateDraft = async (sectionId: string) => {
    if (!tenantKey.trim()) { setError("회사 관리에서 먼저 회사를 등록하세요."); return; }
    setGenerating(sectionId);
    setDraftResult("");
    try {
      const data = await gpuFetch("/api/hr-law/rules/generate", {
        method: "POST",
        body: { section_id: sectionId },
        headers: tenantHeaders(tenantKey),
      });
      setDraftResult(data.draft || data.error || JSON.stringify(data, null, 2));
    } catch { setError("초안 생성 실패"); }
    setGenerating(null);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">📋 취업규칙 관리</h1>

      {/* 테넌트 키 + 완성도 조회 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <label className="block text-sm text-[var(--muted)] mb-2">Tenant API Key</label>
        <div className="flex gap-3">
          <input value={tenantKey} onChange={(e) => setTenantKey(e.target.value)} placeholder="X-Tenant-Key (회사 관리에서 발급)"
            className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <button onClick={fetchCompleteness} disabled={loading || !tenantKey.trim()}
            className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {loading ? "⏳" : "완성도 조회"}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {/* 완성도 */}
      {completeness && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
          <h2 className="text-lg font-semibold mb-4">완성도</h2>
          <div className="flex items-center gap-4 mb-3">
            <div className="flex-1 bg-[var(--bg)] rounded-full h-4 overflow-hidden">
              <div className="h-full bg-[var(--accent)] rounded-full transition-all" style={{ width: `${completeness.completeness_pct}%` }} />
            </div>
            <span className="text-2xl font-bold text-[var(--accent)]">{completeness.completeness_pct}%</span>
          </div>
          <p className="text-sm text-[var(--muted)]">{completeness.total_completed} / {completeness.total_required} 필수항목 등록</p>
          {completeness.missing_required?.length > 0 && (
            <div className="mt-4">
              <p className="text-sm font-medium text-red-400 mb-2">미등록 필수항목:</p>
              {completeness.missing_required.map((m, i) => (
                <div key={i} className="text-sm text-[var(--muted)] mb-1">
                  <span className="inline-block px-2 py-0.5 rounded bg-red-500/20 text-red-400 text-xs mr-2">{m.section}</span>
                  {m.articles.join(", ")}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 템플릿 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">표준 취업규칙 템플릿 ({sections.length}개 섹션)</h2>
        <div className="flex flex-col gap-3">
          {sections.map((s) => (
            <div key={s.id} className="bg-[var(--bg)] border border-[var(--border)] rounded-lg p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-[var(--text)]">{s.title}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${s.required ? "bg-red-500/20 text-red-400" : "bg-[var(--surface)] text-[var(--muted)]"}`}>
                    {s.required ? "필수" : "선택"}
                  </span>
                  {s.legal_basis && <span className="text-xs text-[var(--muted)]">{s.legal_basis}</span>}
                </div>
                <button onClick={() => generateDraft(s.id)} disabled={generating === s.id}
                  className="text-xs px-3 py-1.5 bg-[var(--accent)] text-white rounded-lg disabled:opacity-50">
                  {generating === s.id ? "⏳ 생성 중..." : "🤖 초안 생성"}
                </button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {s.articles.map((a) => (
                  <div key={a.id} className="text-sm flex items-start gap-2 py-1">
                    <span className={`inline-block w-1.5 h-1.5 rounded-full mt-1.5 ${a.required ? "bg-red-400" : "bg-[var(--muted)]"}`} />
                    <div>
                      <span className="text-[var(--text)]">{a.title}</span>
                      {a.hint && <span className="text-xs text-[var(--muted)] ml-1">— {a.hint}</span>}
                      {a.legal && <span className="text-xs text-[var(--accent)] ml-1">[{a.legal}]</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 생성 결과 */}
      {draftResult && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">🤖 생성된 초안</h2>
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 mb-4 text-amber-400 text-xs">
            ⚠️ AI가 생성한 초안입니다. 반드시 노무사 검토 후 사용하세요.
          </div>
          <pre className="text-sm text-[var(--text)] whitespace-pre-wrap leading-relaxed bg-[var(--bg)] p-4 rounded-lg">{draftResult}</pre>
        </div>
      )}
    </div>
  );
}
