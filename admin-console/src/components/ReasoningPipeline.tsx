"use client";

export interface StepData {
  step: number;
  name: string;
  status: "pending" | "running" | "done" | "error";
  elapsed_ms?: number;
  tokens?: number;
  result?: unknown;
}

const STEP_NAMES = ["증상 정규화", "감별진단", "순위화", "검사 제안", "최종 평가"];
const STEP_ICONS = ["📋", "🔬", "📊", "🧪", "✅"];

export default function ReasoningPipeline({ steps }: { steps: StepData[] }) {
  return (
    <div className="flex items-center gap-1 mb-6 overflow-x-auto pb-2">
      {STEP_NAMES.map((name, i) => {
        const step = steps.find((s) => s.step === i + 1);
        const status = step?.status || "pending";
        const elapsed = step?.elapsed_ms;

        const bg =
          status === "done" ? "bg-emerald-500/20 border-emerald-500/50" :
          status === "running" ? "bg-blue-500/20 border-blue-500/50 animate-pulse" :
          status === "error" ? "bg-red-500/20 border-red-500/50" :
          "bg-[var(--bg)] border-[var(--border)]";

        const textColor =
          status === "done" ? "text-emerald-400" :
          status === "running" ? "text-blue-400" :
          status === "error" ? "text-red-400" :
          "text-[var(--muted)]";

        return (
          <div key={i} className="flex items-center gap-1">
            <div className={`border rounded-lg px-3 py-2 min-w-[100px] text-center ${bg}`}>
              <div className="text-lg">{STEP_ICONS[i]}</div>
              <div className={`text-xs font-semibold mt-1 ${textColor}`}>
                {i + 1}. {name}
              </div>
              <div className="text-[10px] text-[var(--muted)] mt-0.5">
                {status === "done" && elapsed != null ? `${(elapsed / 1000).toFixed(1)}s` :
                 status === "running" ? "..." :
                 status === "error" ? "실패" : "대기"}
              </div>
            </div>
            {i < STEP_NAMES.length - 1 && (
              <span className={`text-lg ${status === "done" ? "text-emerald-400" : "text-[var(--border)]"}`}>▸</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
