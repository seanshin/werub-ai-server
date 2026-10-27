"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

interface RunEntry {
  id: string;
  pipeline_name: string;
  pipeline_type: string;
  status: string;
  input_text: string;
  result_text: string;
  total_tokens: number;
  total_duration_ms: number;
  stages_json: string;
  created_at: string;
}

export default function Page() {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [selected, setSelected] = useState<RunEntry | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    gpuFetch("/api/orchestration/runs?limit=50").then((d) => {
      setRuns(d.runs || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const statusColor = (s: string) => {
    if (s === "completed") return "text-emerald-400";
    if (s === "partial" || s === "completed_with_errors") return "text-amber-400";
    return "text-red-400";
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">실행 이력</h1>

      {loading ? <div className="text-[var(--muted)]">로딩 중...</div> : (
        <div className="grid grid-cols-3 gap-6">
          <div className="space-y-2 max-h-[70vh] overflow-y-auto">
            {runs.map((r) => (
              <button key={r.id} onClick={() => setSelected(r)}
                className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                  selected?.id === r.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                  : "border-[var(--border)] bg-[var(--card)]"
                }`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium">{r.pipeline_name}</span>
                  <span className={`text-xs ${statusColor(r.status)}`}>{r.status}</span>
                </div>
                <div className="text-xs text-[var(--muted)]">
                  {r.total_tokens}tok / {(r.total_duration_ms / 1000).toFixed(1)}s | {r.created_at?.slice(0, 16)}
                </div>
              </button>
            ))}
            {runs.length === 0 && <div className="text-sm text-[var(--muted)]">실행 이력 없음</div>}
          </div>

          <div className="col-span-2">
            {selected ? (
              <div className="space-y-4">
                <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-bold">{selected.pipeline_name}</h3>
                    <span className={`text-sm font-medium ${statusColor(selected.status)}`}>{selected.status}</span>
                  </div>
                  <div className="text-xs text-[var(--muted)] mb-3">
                    ID: {selected.id} | {selected.total_tokens}tok | {(selected.total_duration_ms / 1000).toFixed(1)}s | {selected.created_at}
                  </div>

                  <h4 className="text-xs font-bold text-[var(--muted)] mt-3 mb-2">입력</h4>
                  <div className="text-sm bg-[var(--surface)] rounded p-3 mb-3 max-h-20 overflow-y-auto">{selected.input_text}</div>

                  {selected.stages_json && (() => {
                    try {
                      const stages = JSON.parse(selected.stages_json);
                      return (
                        <>
                          <h4 className="text-xs font-bold text-[var(--muted)] mb-2">단계별 결과</h4>
                          <div className="space-y-1">
                            {stages.map((s: Record<string, unknown>, i: number) => (
                              <div key={i} className="flex items-center gap-2 text-xs bg-[var(--surface)] rounded p-2">
                                <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold">{String(s.stage)}</span>
                                <span className="font-medium w-28">{String(s.role)}</span>
                                <span className="text-[var(--muted)]">{String(s.model)}</span>
                                <span className="ml-auto text-[var(--muted)]">{String(s.tokens)}tok / {String(s.duration_ms)}ms</span>
                                <span className={statusColor(String(s.status))}>{String(s.status)}</span>
                              </div>
                            ))}
                          </div>
                        </>
                      );
                    } catch { return null; }
                  })()}

                  <h4 className="text-xs font-bold text-[var(--muted)] mt-3 mb-2">결과</h4>
                  <div className="text-sm bg-[var(--surface)] rounded p-3 max-h-40 overflow-y-auto whitespace-pre-wrap">{selected.result_text}</div>
                </div>
              </div>
            ) : (
              <div className="text-[var(--muted)] text-center mt-20">왼쪽에서 실행 이력을 선택하세요</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
