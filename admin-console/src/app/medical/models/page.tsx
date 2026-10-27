"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

interface ModelInfo {
  id: string;
  name: string;
  size_gb: number;
  lang: string;
  tier: string;
  domains: string[];
  strength: string;
}

interface GpuStatus {
  name: string;
  memory_used_mb: number;
  memory_total_mb: number;
  memory_util: number;
  gpu_util: number;
  temperature: number;
}

export default function Page() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [gpu, setGpu] = useState<GpuStatus | null>(null);
  const [runningModels, setRunningModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      gpuFetch("/api/medical/models").catch(() => ({ models: [] })),
      gpuFetch("/api/gpu/status").catch(() => ({ gpu: {} })),
      gpuFetch("/api/status").catch(() => ({ running_models: [] })),
    ]).then(([med, gpuData, status]) => {
      setModels(med.models || []);
      setGpu(gpuData.gpu || null);
      setRunningModels((status.running_models || []).map((m: { name: string }) => m.name));
      setLoading(false);
    });
  }, []);

  const tierColors: Record<string, string> = {
    primary: "bg-emerald-500/20 text-emerald-400",
    specialist: "bg-blue-500/20 text-blue-400",
    heavy: "bg-purple-500/20 text-purple-400",
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">모델 관리</h1>

      {gpu && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
            <div className="text-xs text-[var(--muted)]">GPU</div>
            <div className="text-lg font-bold">{gpu.name || "RTX 5080"}</div>
          </div>
          <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
            <div className="text-xs text-[var(--muted)]">VRAM</div>
            <div className="text-lg font-bold">{(gpu.memory_used_mb / 1024).toFixed(1)}GB / {(gpu.memory_total_mb / 1024).toFixed(1)}GB</div>
            <div className="w-full bg-gray-700 rounded h-1.5 mt-1">
              <div className="bg-emerald-400 h-1.5 rounded" style={{ width: `${gpu.memory_util}%` }} />
            </div>
          </div>
          <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
            <div className="text-xs text-[var(--muted)]">GPU 사용률</div>
            <div className="text-lg font-bold">{gpu.gpu_util}%</div>
          </div>
          <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
            <div className="text-xs text-[var(--muted)]">적재 모델</div>
            <div className="text-lg font-bold">{runningModels.length}개</div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-[var(--muted)]">로딩 중...</div>
      ) : (
        <div className="space-y-3">
          {models.map((m) => (
            <div key={m.id} className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)] flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-bold">{m.name || m.id}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full ${tierColors[m.tier] || "bg-gray-500/20 text-gray-400"}`}>
                    {m.tier}
                  </span>
                  {runningModels.includes(m.id) && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">LOADED</span>
                  )}
                </div>
                <div className="text-xs text-[var(--muted)]">{m.strength}</div>
                <div className="text-xs text-[var(--muted)] mt-1">
                  {m.size_gb}GB | {m.lang} | {(m.domains || []).join(", ")}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
