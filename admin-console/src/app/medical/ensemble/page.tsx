"use client";

import { useState } from "react";
import { gpuFetch } from "@/lib/api";

export default function Page() {
  const [symptoms, setSymptoms] = useState("");
  const [age, setAge] = useState("55");
  const [sex, setSex] = useState("M");
  const [chainMode, setChainMode] = useState("multi");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (!symptoms.trim()) return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const data = await gpuFetch("/api/medical/reasoning/ensemble", {
        method: "POST",
        body: {
          patient_text: symptoms,
          chain_mode: chainMode,
          patient_info: { age: parseInt(age), sex },
        },
      });
      if (data.error) setError(data.error);
      else setResult(data);
    } catch (e) {
      setError(String(e));
    }
    setRunning(false);
  };

  const consensus = result?.consensus as Record<string, unknown> | undefined;
  const steps = (result?.steps || []) as Array<Record<string, unknown>>;
  const finalAssessment = result?.final_assessment as Record<string, unknown> | undefined;

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">Multi-Model Ensemble</h1>

      <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
        <div className="grid grid-cols-4 gap-4 mb-4">
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">나이</label>
            <input value={age} onChange={(e) => setAge(e.target.value)} className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">성별</label>
            <select value={sex} onChange={(e) => setSex(e.target.value)} className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
              <option value="M">남성</option><option value="F">여성</option>
            </select>
          </div>
          <div className="col-span-2">
            <label className="text-xs text-[var(--muted)] block mb-1">체인 모드</label>
            <div className="flex gap-2">
              {[
                { id: "multi", label: "Multi (30~40초)", desc: "단계별 전문 모델" },
                { id: "consensus", label: "Consensus (50~70초)", desc: "3모델 투표" },
              ].map((m) => (
                <button key={m.id} onClick={() => setChainMode(m.id)}
                  className={`flex-1 px-3 py-1.5 rounded text-sm border transition-colors ${
                    chainMode === m.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.15)] text-[var(--accent)]"
                    : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)]"
                  }`}>
                  {m.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <textarea value={symptoms} onChange={(e) => setSymptoms(e.target.value)} placeholder="환자 증상을 입력하세요..."
          className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm min-h-[80px] mb-3" />

        <button onClick={run} disabled={running || !symptoms.trim()}
          className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
          {running ? "분석 중..." : "앙상블 실행"}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400 text-sm mb-4">{error}</div>}

      {result && (
        <div className="space-y-4">
          {/* Steps */}
          <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
            <h3 className="font-bold mb-3">실행 단계 ({steps.length})</h3>
            <div className="space-y-2">
              {steps.map((s, i) => (
                <div key={i} className="flex items-center gap-3 text-sm">
                  <span className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-xs font-bold">{i + 1}</span>
                  <span className="font-medium w-40">{String(s.step)}</span>
                  <span className="text-[var(--muted)]">{String(s.tokens)}tok / {String(s.duration_ms)}ms</span>
                </div>
              ))}
            </div>
          </div>

          {/* Consensus */}
          {consensus && (
            <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
              <h3 className="font-bold mb-3">Consensus 투표 결과</h3>
              <div className="text-xs text-[var(--muted)] mb-2">
                모델: {String((consensus.models_used as string[])?.join(", "))} | 후보: {String(consensus.total_candidates)} | 유니크: {String(consensus.unique_diagnoses)}
              </div>
              <div className="space-y-2">
                {((consensus.merged_diagnoses || []) as Array<Record<string, unknown>>).map((dx, i) => (
                  <div key={i} className="flex items-center gap-3 text-sm bg-[var(--surface)] rounded-lg p-3">
                    <span className="font-bold text-lg w-6">{i + 1}</span>
                    <div className="flex-1">
                      <div className="font-medium">{String(dx.diagnosis_ko || dx.diagnosis)}</div>
                      <div className="text-xs text-[var(--muted)]">ICD: {String(dx.icd10)} | 투표: {String(dx.vote_count)} | 신뢰도: {String(dx.confidence)}</div>
                    </div>
                    {Boolean(dx.is_emergency) && <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/20 text-red-400">응급</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Final Assessment */}
          {finalAssessment && (
            <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
              <h3 className="font-bold mb-3">최종 평가</h3>
              <pre className="text-sm text-[var(--muted)] whitespace-pre-wrap">{JSON.stringify(finalAssessment, null, 2)}</pre>
            </div>
          )}

          <div className="text-xs text-[var(--muted)]">
            총 {String(result.total_tokens)}tok / {String(result.total_duration_ms)}ms | 모드: {String(result.chain_mode)}
          </div>
        </div>
      )}
    </div>
  );
}
