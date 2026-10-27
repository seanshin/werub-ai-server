"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch } from "@/lib/api";

interface ModelInfo {
  name: string;
  size: number;
}

interface RunningModel {
  name: string;
  size_vram: number;
  context_length: number;
}

interface DrugBatchRun {
  batch_run_id: number;
  run_type: string;
  status: string;
  fetched: number;
  enriched: number;
  committed: number;
  skipped: number;
  failed: number;
  started_at: string;
  completed_at: string;
}

const POLL_INTERVAL = 10000;

export default function Page() {
  const [health, setHealth] = useState<{
    running: boolean;
    models: ModelInfo[];
    running_models: RunningModel[];
  } | null>(null);
  const [queueStatus, setQueueStatus] = useState<{
    pending: number;
    running: number;
    completed: number;
    failed: number;
    worker_alive: boolean;
    paused: boolean;
  } | null>(null);
  const [drugStats, setDrugStats] = useState<{
    drug_count: number;
    interaction_count: number;
    denial_count: number;
  } | null>(null);
  const [drugBatchStatus, setDrugBatchStatus] = useState<{
    running: boolean;
    night_mode: boolean;
    last_run?: DrugBatchRun;
  } | null>(null);
  const [drugBatchHistory, setDrugBatchHistory] = useState<DrugBatchRun[]>([]);
  const [activeModel, setActiveModel] = useState("");
  const [queueStats, setQueueStats] = useState<{
    avg_tok_per_sec: number;
    total_tokens: number;
    total: number;
  } | null>(null);
  const [recentActivity, setRecentActivity] = useState<
    { time: string; type: string; summary: string; status: string }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadAll = useCallback(async () => {
    try {
      const [healthData, queueStatusData, drugData, modelData, qStatsData, batchStatusData, batchHistoryData] =
        await Promise.all([
          gpuFetch("/api/status").catch(() => null),
          gpuFetch("/api/queue/status").catch(() => null),
          gpuFetch("/api/medical/clinical/drug/stats").catch(() => null),
          gpuFetch("/api/medical/training/active").catch(() => null),
          gpuFetch("/api/queue/stats").catch(() => null),
          gpuFetch("/api/drug-batch/status").catch(() => null),
          gpuFetch("/api/drug-batch/history").catch(() => null),
        ]);
      if (healthData) setHealth(healthData);
      if (queueStatusData) setQueueStatus(queueStatusData);
      if (drugData) setDrugStats(drugData);
      if (modelData?.model) setActiveModel(modelData.model);
      if (qStatsData) {
        setQueueStats(qStatsData);
        if (qStatsData.recent_jobs) {
          setRecentActivity(
            qStatsData.recent_jobs.slice(0, 10).map(
              (j: { completed_at?: string; model?: string; prompt?: string; status?: string }) => ({
                time: j.completed_at
                  ? new Date(j.completed_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
                  : "--:--",
                type: j.model || "unknown",
                summary: (j.prompt || "").slice(0, 60),
                status: j.status || "completed",
              })
            )
          );
        }
      }
      if (batchStatusData) setDrugBatchStatus(batchStatusData);
      if (batchHistoryData?.runs) setDrugBatchHistory(batchHistoryData.runs.slice(0, 5));
      setError("");
    } catch {
      setError("서버 연결 실패");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadAll();
    const timer = setInterval(loadAll, POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [loadAll]);

  const fmtSize = (bytes: number) => `${(bytes / 1e9).toFixed(1)}GB`;
  const fmtVram = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(0)}MB`;

  const medicalModels = health?.models?.filter(
    (m) => m.name.includes("med") || m.name.includes("werub")
  ) || [];
  const otherModels = health?.models?.filter(
    (m) => !m.name.includes("med") && !m.name.includes("werub")
  ) || [];
  const runningModel = health?.running_models?.[0];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Medical AI Dashboard</h1>
        <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
          <span className={`w-2 h-2 rounded-full ${health?.running ? "bg-emerald-400" : "bg-red-400"}`} />
          {health?.running ? "Online" : "Offline"}
          <span className="ml-2">auto-refresh 10s</span>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>
      )}

      {loading ? (
        <p className="text-[var(--muted)]">로딩 중...</p>
      ) : (
        <>
          {/* Row 1: Key Stats */}
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
            {[
              { label: "Active Model", value: activeModel || "N/A", color: "text-emerald-400", sub: runningModel ? fmtVram(runningModel.size_vram) + " VRAM" : "" },
              { label: "Drug DB", value: drugStats ? `${drugStats.drug_count}` : "--", color: "text-blue-400", sub: "건" },
              { label: "Interactions", value: drugStats ? `${drugStats.interaction_count}` : "--", color: "text-yellow-400", sub: "건" },
              { label: "Queue", value: queueStatus ? `${queueStatus.completed}` : "--", color: "text-purple-400", sub: `P:${queueStatus?.pending || 0} R:${queueStatus?.running || 0}` },
              { label: "Throughput", value: queueStats ? `${queueStats.avg_tok_per_sec.toFixed(1)}` : "--", color: "text-cyan-400", sub: "tok/s avg" },
            ].map((s) => (
              <div key={s.label} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 text-center">
                <div className={`text-2xl font-bold ${s.color} truncate`}>{s.value}</div>
                <div className="text-sm text-[var(--muted)] mt-1">{s.label}</div>
                {s.sub && <div className="text-xs text-[var(--muted)] mt-0.5">{s.sub}</div>}
              </div>
            ))}
          </div>

          {/* Row 2: GPU + Queue Status */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
            {/* GPU / Running Model */}
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">GPU Status</h2>
              {runningModel ? (
                <div className="space-y-3">
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-[var(--text)]">Loaded Model</span>
                    <span className="font-mono text-sm text-emerald-400">{runningModel.name}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-[var(--text)]">VRAM Usage</span>
                    <span className="font-mono text-sm text-blue-400">{fmtVram(runningModel.size_vram)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-[var(--text)]">Context Length</span>
                    <span className="font-mono text-sm text-[var(--muted)]">{runningModel.context_length.toLocaleString()}</span>
                  </div>
                  <div className="w-full bg-[var(--bg)] rounded-full h-2.5 mt-2">
                    <div className="h-2.5 rounded-full bg-blue-500 transition-all"
                      style={{ width: `${Math.min((runningModel.size_vram / (16303 * 1024 * 1024)) * 100, 100)}%` }} />
                  </div>
                  <div className="text-xs text-[var(--muted)] text-right">{fmtVram(runningModel.size_vram)} / 16,303MB</div>
                </div>
              ) : (
                <p className="text-[var(--muted)] text-sm">모델 로드 없음 (GPU 유휴)</p>
              )}
            </div>

            {/* Queue Status */}
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Queue Status</h2>
              {queueStatus ? (
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm">Worker</span>
                    <span className={`text-sm font-semibold ${queueStatus.worker_alive ? "text-emerald-400" : "text-red-400"}`}>
                      {queueStatus.worker_alive ? "Alive" : "Dead"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm">Paused</span>
                    <span className={`text-sm ${queueStatus.paused ? "text-yellow-400" : "text-[var(--muted)]"}`}>
                      {queueStatus.paused ? "Yes" : "No"}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    {[
                      { label: "Pending", value: queueStatus.pending, color: "text-yellow-400" },
                      { label: "Running", value: queueStatus.running, color: "text-blue-400" },
                      { label: "Completed", value: queueStatus.completed, color: "text-emerald-400" },
                      { label: "Failed", value: queueStatus.failed, color: "text-red-400" },
                    ].map((q) => (
                      <div key={q.label} className="bg-[var(--bg)] rounded-lg p-2 text-center">
                        <div className={`text-lg font-bold ${q.color}`}>{q.value}</div>
                        <div className="text-[10px] text-[var(--muted)]">{q.label}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-[var(--muted)] text-sm">큐 상태 불명</p>
              )}
            </div>

            {/* Drug Pipeline Status */}
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Drug Pipeline</h2>
              {drugBatchStatus ? (
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm">Pipeline</span>
                    <span className={`text-sm font-semibold ${drugBatchStatus.running ? "text-blue-400 animate-pulse" : "text-[var(--muted)]"}`}>
                      {drugBatchStatus.running ? "Running" : "Idle"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm">Night Mode</span>
                    <span className={`text-sm ${drugBatchStatus.night_mode ? "text-indigo-400" : "text-[var(--muted)]"}`}>
                      {drugBatchStatus.night_mode ? "Active" : "Off"}
                    </span>
                  </div>
                  {drugBatchStatus.last_run && (
                    <>
                      <div className="border-t border-[var(--border)] pt-2 mt-2">
                        <div className="text-xs text-[var(--muted)] mb-1">Last Run #{drugBatchStatus.last_run.batch_run_id}</div>
                        <div className="grid grid-cols-3 gap-1">
                          <div className="text-center">
                            <div className="text-sm font-bold text-emerald-400">{drugBatchStatus.last_run.committed}</div>
                            <div className="text-[10px] text-[var(--muted)]">Added</div>
                          </div>
                          <div className="text-center">
                            <div className="text-sm font-bold text-[var(--muted)]">{drugBatchStatus.last_run.skipped}</div>
                            <div className="text-[10px] text-[var(--muted)]">Skipped</div>
                          </div>
                          <div className="text-center">
                            <div className="text-sm font-bold text-red-400">{drugBatchStatus.last_run.failed}</div>
                            <div className="text-[10px] text-[var(--muted)]">Failed</div>
                          </div>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <p className="text-[var(--muted)] text-sm">파이프라인 상태 불명</p>
              )}
            </div>
          </div>

          {/* Row 3: Pipeline History + Models */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            {/* Drug Pipeline History */}
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Pipeline History (Recent 5)</h2>
              {drugBatchHistory.length === 0 ? (
                <p className="text-[var(--muted)] text-sm">실행 이력 없음</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-[var(--muted)] border-b border-[var(--border)]">
                        <th className="pb-2 text-left">#</th>
                        <th className="pb-2 text-left">Type</th>
                        <th className="pb-2 text-right">Fetch</th>
                        <th className="pb-2 text-right">Add</th>
                        <th className="pb-2 text-right">Skip</th>
                        <th className="pb-2 text-right">Fail</th>
                        <th className="pb-2 text-left">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {drugBatchHistory.map((r) => (
                        <tr key={r.batch_run_id} className="border-b border-[var(--border)]/30">
                          <td className="py-1.5 font-mono text-[var(--muted)]">{r.batch_run_id}</td>
                          <td className="py-1.5 text-xs">{r.run_type}</td>
                          <td className="py-1.5 text-right text-blue-400">{r.fetched}</td>
                          <td className="py-1.5 text-right text-emerald-400">{r.committed}</td>
                          <td className="py-1.5 text-right text-[var(--muted)]">{r.skipped}</td>
                          <td className="py-1.5 text-right text-red-400">{r.failed}</td>
                          <td className="py-1.5">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${
                              r.status === "completed" ? "bg-emerald-500/10 text-emerald-400" :
                              r.status === "running" ? "bg-blue-500/10 text-blue-400" :
                              "bg-red-500/10 text-red-400"
                            }`}>{r.status}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Medical Models */}
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Medical Models</h2>
              {medicalModels.length === 0 ? (
                <p className="text-[var(--muted)] text-sm">모델 정보 없음</p>
              ) : (
                <div className="space-y-2">
                  {medicalModels.map((m) => (
                    <div key={m.name} className="flex items-center justify-between bg-[var(--bg)] rounded-lg p-3">
                      <div className="flex items-center gap-3">
                        <span className={`w-2.5 h-2.5 rounded-full ${
                          runningModel?.name === m.name ? "bg-emerald-400 animate-pulse" :
                          activeModel === m.name ? "bg-blue-400" : "bg-[var(--muted)]"
                        }`} />
                        <div>
                          <span className="font-mono text-sm">{m.name}</span>
                          {activeModel === m.name && (
                            <span className="ml-2 text-[10px] px-1.5 py-0.5 bg-emerald-500/10 text-emerald-400 rounded">active</span>
                          )}
                          {runningModel?.name === m.name && (
                            <span className="ml-1 text-[10px] px-1.5 py-0.5 bg-blue-500/10 text-blue-400 rounded">loaded</span>
                          )}
                        </div>
                      </div>
                      <span className="text-xs text-[var(--muted)]">{fmtSize(m.size)}</span>
                    </div>
                  ))}
                  {otherModels.length > 0 && (
                    <details className="mt-3">
                      <summary className="text-xs text-[var(--muted)] cursor-pointer hover:text-[var(--text)]">
                        Other Models ({otherModels.length})
                      </summary>
                      <div className="space-y-1 mt-2">
                        {otherModels.map((m) => (
                          <div key={m.name} className="flex items-center justify-between bg-[var(--bg)] rounded-lg px-3 py-2">
                            <span className="font-mono text-xs text-[var(--muted)]">{m.name}</span>
                            <span className="text-xs text-[var(--muted)]">{fmtSize(m.size)}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Row 4: Recent Activity */}
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
            <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Recent Activity</h2>
            {recentActivity.length === 0 ? (
              <p className="text-[var(--muted)] text-sm">최근 활동 없음</p>
            ) : (
              <div className="space-y-1.5">
                {recentActivity.map((a, i) => (
                  <div key={i} className="flex items-center gap-3 text-sm bg-[var(--bg)] rounded-lg p-2.5">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      a.status === "completed" ? "bg-emerald-400" :
                      a.status === "failed" ? "bg-red-400" : "bg-yellow-400"
                    }`} />
                    <span className="text-xs text-[var(--muted)] font-mono w-12 shrink-0">{a.time}</span>
                    <span className="text-xs font-mono text-blue-400 w-28 shrink-0 truncate">{a.type}</span>
                    <span className="text-[var(--muted)] truncate text-xs">{a.summary || "(empty)"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Row 5: Quick Links */}
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
            <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Quick Access</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { href: "/admin-console/medical/reasoning", icon: "🧠", label: "추론 체인", desc: "5단계 진단 추론" },
                { href: "/admin-console/medical/predict", icon: "🔮", label: "예측 분석", desc: "재입원/합병증/부작용" },
                { href: "/admin-console/medical/drugs", icon: "💊", label: "약물 검색", desc: `${drugStats?.drug_count || 0}건 DB` },
                { href: "/admin-console/medical/triage", icon: "🚑", label: "증상 분류", desc: "진료과 추천" },
              ].map((item) => (
                <a key={item.href} href={item.href}
                  className="bg-[var(--bg)] border border-[var(--border)] rounded-lg p-4 text-center no-underline hover:border-[var(--accent)] transition-colors">
                  <div className="text-2xl mb-2">{item.icon}</div>
                  <div className="text-sm font-semibold text-[var(--text)]">{item.label}</div>
                  <div className="text-xs text-[var(--muted)] mt-1">{item.desc}</div>
                </a>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
