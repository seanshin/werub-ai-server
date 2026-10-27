"use client";

import { useState } from "react";
import type { StepData } from "./ReasoningPipeline";

const STEP_NAMES = ["증상 정규화", "감별진단", "순위화", "검사 제안", "최종 평가"];

export default function StepAccordion({ steps }: { steps: StepData[] }) {
  const [open, setOpen] = useState<number | null>(null);

  const doneSteps = steps.filter((s) => s.status === "done" || s.status === "error");

  if (doneSteps.length === 0) return null;

  return (
    <div className="space-y-2">
      {doneSteps.map((step) => {
        const isOpen = open === step.step;
        const name = STEP_NAMES[step.step - 1] || `Step ${step.step}`;
        const resultText =
          typeof step.result === "string" ? step.result :
          step.result ? JSON.stringify(step.result, null, 2) : "결과 없음";

        return (
          <div key={step.step} className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
            <button
              onClick={() => setOpen(isOpen ? null : step.step)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-[rgba(99,102,241,0.05)] transition-colors"
            >
              <div className="flex items-center gap-3">
                <span className={`text-sm ${step.status === "done" ? "text-emerald-400" : "text-red-400"}`}>
                  {step.status === "done" ? "✅" : "❌"}
                </span>
                <span className="text-sm font-semibold">Step {step.step}: {name}</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-[var(--muted)]">
                {step.elapsed_ms != null && <span>{(step.elapsed_ms / 1000).toFixed(1)}s</span>}
                {step.tokens != null && <span>{step.tokens} tok</span>}
                <span className="text-[var(--muted)]">{isOpen ? "▲" : "▼"}</span>
              </div>
            </button>
            {isOpen && (
              <div className="px-4 pb-4 border-t border-[var(--border)]">
                <pre className="mt-3 text-xs text-[var(--text)] bg-[var(--bg)] rounded-lg p-4 overflow-x-auto whitespace-pre-wrap max-h-[400px] overflow-y-auto">
                  {resultText}
                </pre>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
