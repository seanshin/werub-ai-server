"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

interface Pipeline { name: string; description?: string; type?: string; stages: Stage[] }
interface Stage { model: string; role: string; system?: string; on_fail?: string; fallback_model?: string }

export default function Page() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [selected, setSelected] = useState<Pipeline | null>(null);
  const [testInput, setTestInput] = useState("");
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [testMode, setTestMode] = useState("sync");
  const [running, setRunning] = useState(false);
  const [sseEvents, setSseEvents] = useState<string[]>([]);

  useEffect(() => {
    gpuFetch("/api/orchestration/pipelines").then((d) => setPipelines(d.pipelines || []));
  }, []);

  const runPipeline = async () => {
    if (!selected || !testInput.trim()) return;
    setRunning(true);
    setTestResult(null);
    setSseEvents([]);

    if (testMode === "stream") {
      // SSE streaming
      const basePath = typeof window !== "undefined"
        ? window.location.pathname.replace(/\/admin-console.*$/, "").replace(/\/+$/, "") : "";
      const res = await fetch(`${basePath}/api/orchestration/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pipeline: selected.name, input: testInput, mode: "stream", debug: true }),
      });
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const text = decoder.decode(value);
          for (const line of text.split("\n")) {
            if (line.startsWith("data: ")) {
              setSseEvents((prev) => [...prev, line.slice(6)]);
            }
          }
        }
      }
      setRunning(false);
    } else {
      const data = await gpuFetch("/api/orchestration/run", {
        method: "POST",
        body: { pipeline: selected.name, input: testInput, mode: testMode, debug: true },
      });
      setTestResult(data);
      setRunning(false);
    }
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">파이프라인 에디터</h1>

      <div className="grid grid-cols-3 gap-6">
        {/* Pipeline List */}
        <div className="space-y-2">
          <h3 className="text-sm font-bold text-[var(--muted)] mb-2">파이프라인 ({pipelines.length})</h3>
          {pipelines.map((p) => (
            <button key={p.name} onClick={() => setSelected(p)}
              className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                selected?.name === p.name ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--accent)]"
              }`}>
              <div className="font-medium">{p.name}</div>
              <div className="text-xs text-[var(--muted)]">{p.stages?.length || 0}단계 | {p.type || "custom"}</div>
            </button>
          ))}
        </div>

        {/* Pipeline Detail + Test */}
        <div className="col-span-2">
          {selected ? (
            <div className="space-y-4">
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h3 className="font-bold mb-3">{selected.name}</h3>
                <p className="text-sm text-[var(--muted)] mb-4">{selected.description}</p>

                <div className="space-y-2">
                  {selected.stages.map((s, i) => (
                    <div key={i} className="flex items-center gap-3 bg-[var(--surface)] rounded-lg p-3">
                      <span className="w-7 h-7 rounded-full bg-[var(--accent)]/20 text-[var(--accent)] flex items-center justify-center text-xs font-bold">{i + 1}</span>
                      <div className="flex-1">
                        <div className="text-sm font-medium">{s.role}</div>
                        <div className="text-xs text-[var(--muted)]">{s.model} {s.on_fail ? `| on_fail: ${s.on_fail}` : ""} {s.fallback_model ? `| fallback: ${s.fallback_model}` : ""}</div>
                      </div>
                      {i < selected.stages.length - 1 && <span className="text-[var(--muted)]">→</span>}
                    </div>
                  ))}
                </div>
              </div>

              {/* Test */}
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h4 className="text-sm font-bold mb-3">테스트 실행</h4>
                <div className="flex gap-2 mb-3">
                  {["sync", "stream", "async"].map((m) => (
                    <button key={m} onClick={() => setTestMode(m)}
                      className={`px-3 py-1 rounded text-xs ${testMode === m ? "bg-[var(--accent)] text-white" : "bg-[var(--surface)] text-[var(--muted)]"}`}>
                      {m}
                    </button>
                  ))}
                </div>
                <textarea value={testInput} onChange={(e) => setTestInput(e.target.value)}
                  placeholder="입력 텍스트..." rows={3}
                  className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm mb-3" />
                <button onClick={runPipeline} disabled={running}
                  className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
                  {running ? "실행 중..." : "실행"}
                </button>
              </div>

              {/* SSE Events */}
              {sseEvents.length > 0 && (
                <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                  <h4 className="text-sm font-bold mb-3">SSE 이벤트</h4>
                  <div className="space-y-1 max-h-60 overflow-y-auto">
                    {sseEvents.map((e, i) => {
                      try {
                        const d = JSON.parse(e);
                        return (
                          <div key={i} className="text-xs font-mono bg-[var(--surface)] rounded p-2">
                            <span className="text-[var(--accent)]">{d.type}</span> {d.stage && `stage:${d.stage}`} {d.tokens && `${d.tokens}tok`} {d.duration_ms && `${d.duration_ms}ms`}
                          </div>
                        );
                      } catch { return <div key={i} className="text-xs font-mono text-[var(--muted)]">{e}</div>; }
                    })}
                  </div>
                </div>
              )}

              {/* Sync Result */}
              {testResult && (
                <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                  <h4 className="text-sm font-bold mb-2">결과</h4>
                  <div className="text-sm whitespace-pre-wrap mb-3">{String(testResult.result || "").slice(0, 500)}</div>
                  <div className="text-xs text-[var(--muted)]">
                    status: {String(testResult.status)} | {String(testResult.total_tokens)}tok | {String(testResult.total_duration_ms)}ms
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-[var(--muted)] text-center mt-20">왼쪽에서 파이프라인을 선택하세요</div>
          )}
        </div>
      </div>
    </div>
  );
}
