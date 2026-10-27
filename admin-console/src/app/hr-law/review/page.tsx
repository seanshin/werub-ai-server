"use client";

import { useState } from "react";
import { gpuFetch, tenantHeaders } from "@/lib/api";

interface ReviewResult {
  violations: number;
  warnings: number;
  compliant: number;
  review_text: string;
  items?: { type: string; section: string; description: string }[];
}

export default function Page() {
  const [tenantKey, setTenantKey] = useState("");
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runReview = async () => {
    if (!tenantKey.trim()) { setError("Tenant Key를 입력하세요."); return; }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await gpuFetch("/api/hr-law/rules/review", {
        method: "POST",
        headers: tenantHeaders(tenantKey),
      });
      setResult(data);
    } catch {
      setError("법위반 검토 실패");
    }
    setLoading(false);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">법위반 검토</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <label className="block text-sm text-[var(--muted)] mb-2">Tenant API Key</label>
        <div className="flex gap-3">
          <input
            value={tenantKey}
            onChange={(e) => setTenantKey(e.target.value)}
            placeholder="X-Tenant-Key 입력"
            className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]"
          />
          <button onClick={runReview} disabled={loading || !tenantKey.trim()}
            className="px-6 py-3 bg-red-600 text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
            {loading ? "검토 중..." : "법위반 검토 실행"}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {result && (
        <>
          <div className="grid grid-cols-3 gap-4 mb-6">
            <div className="bg-[var(--card)] border border-red-500/30 rounded-xl p-6 text-center">
              <div className="text-3xl font-bold text-red-400">{result.violations}</div>
              <div className="text-sm text-[var(--muted)] mt-1">위반</div>
            </div>
            <div className="bg-[var(--card)] border border-yellow-500/30 rounded-xl p-6 text-center">
              <div className="text-3xl font-bold text-yellow-400">{result.warnings}</div>
              <div className="text-sm text-[var(--muted)] mt-1">경고</div>
            </div>
            <div className="bg-[var(--card)] border border-green-500/30 rounded-xl p-6 text-center">
              <div className="text-3xl font-bold text-green-400">{result.compliant}</div>
              <div className="text-sm text-[var(--muted)] mt-1">준수</div>
            </div>
          </div>

          {result.items && result.items.length > 0 && (
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
              <h2 className="text-lg font-semibold mb-4">상세 항목</h2>
              <div className="flex flex-col gap-3">
                {result.items.map((item, i) => (
                  <div key={i} className="flex items-start gap-3 bg-[var(--bg)] rounded-lg p-4">
                    <span className={`text-xs px-2 py-1 rounded-full shrink-0 mt-0.5 ${
                      item.type === "violation" ? "bg-red-500/20 text-red-400" :
                      item.type === "warning" ? "bg-yellow-500/20 text-yellow-400" :
                      "bg-green-500/20 text-green-400"
                    }`}>{item.type === "violation" ? "위반" : item.type === "warning" ? "경고" : "준수"}</span>
                    <div>
                      <div className="font-medium text-sm text-[var(--text)]">{item.section}</div>
                      <div className="text-sm text-[var(--muted)] mt-1">{item.description}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
            <h2 className="text-lg font-semibold mb-4">검토 결과</h2>
            <pre className="text-sm text-[var(--text)] whitespace-pre-wrap leading-relaxed bg-[var(--bg)] p-4 rounded-lg">
              {result.review_text}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
