"use client";

import { useState, useCallback } from "react";
import { gpuFetch } from "@/lib/api";

interface TriageResult {
  emergency?: boolean;
  departments?: { name: string; confidence: number }[];
  follow_up_questions?: string[];
  raw?: string;
}

export default function Page() {
  const [symptoms, setSymptoms] = useState("");
  const [result, setResult] = useState<TriageResult | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);

  const triage = useCallback(async () => {
    if (!symptoms.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await gpuFetch("/api/medical/triage", {
        method: "POST",
        body: { symptoms: symptoms.trim() },
      });
      if (data.error) throw new Error(data.error);
      setResult(data);
      if (data.conversation_id) setConversationId(data.conversation_id);
    } catch (e) {
      setError((e as Error).message);
    }
    setLoading(false);
  }, [symptoms]);

  const sendFollowUp = useCallback(async () => {
    if (!followUp.trim()) return;
    setLoading(true);
    try {
      const data = await gpuFetch("/api/medical/triage/follow-up", {
        method: "POST",
        body: {
          conversation_id: conversationId,
          symptoms: symptoms.trim(),
          additional_info: followUp.trim(),
        },
      });
      if (data.error) throw new Error(data.error);
      setResult(data);
      setFollowUp("");
    } catch (e) {
      setError((e as Error).message);
    }
    setLoading(false);
  }, [followUp, conversationId, symptoms]);

  const confBar = (pct: number) => {
    const w = Math.min(pct, 100);
    const color = pct >= 60 ? "bg-emerald-500" : pct >= 30 ? "bg-blue-500" : "bg-[var(--border)]";
    return (
      <div className="flex items-center gap-2 flex-1">
        <div className="w-full bg-[var(--bg)] rounded-full h-2">
          <div className={`h-2 rounded-full transition-all ${color}`} style={{ width: `${w}%` }} />
        </div>
        <span className="text-xs text-[var(--muted)] w-10 text-right">{pct}%</span>
      </div>
    );
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Symptom Triage</h1>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>
      )}

      {/* Input */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <label className="block text-xs text-[var(--muted)] mb-2">증상을 자유롭게 입력하세요</label>
        <textarea value={symptoms} onChange={(e) => setSymptoms(e.target.value)} rows={3}
          placeholder="두통이 3일째 계속되고 구토가 있어요. 빛에 민감하고 목이 뻣뻣합니다."
          className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] resize-y mb-3" />
        <button onClick={triage} disabled={loading}
          className="px-5 py-2.5 bg-[var(--accent)] text-white rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-50">
          {loading ? "분석 중..." : "🚑 추천 받기"}
        </button>
      </div>

      {/* Result */}
      {result && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
          {/* Emergency Flag */}
          {result.emergency != null && (
            <div className={`inline-block px-3 py-1 rounded-full text-sm font-semibold mb-4 ${
              result.emergency ? "bg-red-500/20 text-red-400" : "bg-emerald-500/20 text-emerald-400"
            }`}>
              {result.emergency ? "응급 의심" : "비응급"}
            </div>
          )}

          {/* Departments */}
          {result.departments && result.departments.length > 0 && (
            <div className="mb-6">
              <h3 className="text-sm font-semibold mb-3">추천 진료과</h3>
              <div className="space-y-3">
                {result.departments.map((d, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <span className="text-lg w-8 text-center">{i === 0 ? "1️⃣" : i === 1 ? "2️⃣" : "3️⃣"}</span>
                    <span className="font-semibold text-sm w-28">{d.name}</span>
                    {confBar(d.confidence)}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Follow-up Questions */}
          {result.follow_up_questions && result.follow_up_questions.length > 0 && (
            <div className="mb-4">
              <h3 className="text-sm font-semibold mb-2">추가 질문</h3>
              <ul className="list-disc list-inside text-sm text-[var(--muted)] space-y-1">
                {result.follow_up_questions.map((q, i) => <li key={i}>{q}</li>)}
              </ul>

              <div className="mt-3 flex gap-2">
                <input value={followUp} onChange={(e) => setFollowUp(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendFollowUp()}
                  placeholder="추가 정보 입력..."
                  className="flex-1 bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
                <button onClick={sendFollowUp} disabled={loading}
                  className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50">
                  재분석
                </button>
              </div>
            </div>
          )}

          {/* Raw fallback */}
          {result.raw && (
            <pre className="text-xs text-[var(--text)] bg-[var(--bg)] rounded-lg p-4 whitespace-pre-wrap max-h-[300px] overflow-y-auto">
              {result.raw}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
