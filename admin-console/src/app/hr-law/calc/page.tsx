"use client";

import { useState } from "react";
import { gpuFetch } from "@/lib/api";
import DateInput from "@/components/DateInput";

export default function HrLawCalc() {
  const [tab, setTab] = useState<"leave" | "overtime" | "severance">("leave");
  const [result, setResult] = useState<string>("");
  const [leaveDate, setLeaveDate] = useState("");
  const [sevStart, setSevStart] = useState("");
  const [sevEnd, setSevEnd] = useState("");

  const calcLeave = async () => {
    if (!leaveDate) return;
    const d = await gpuFetch("/api/hr-law/calculate/annual-leave", { method: "POST", body: { start_date: leaveDate } });
    setResult(`<div class="text-4xl font-bold text-[var(--accent)]">${d.total_leave}일</div><div class="mt-2 text-[var(--muted)]">${d.note || ""}</div><div class="text-sm text-[var(--muted)]">📎 ${d.legal_basis || ""}</div>`);
  };

  const calcOvertime = async () => {
    const v = (id: string) => +(document.getElementById(id) as HTMLInputElement)?.value || 0;
    const d = await gpuFetch("/api/hr-law/calculate/overtime", {
      method: "POST",
      body: { hourly_wage: v("otWage"), extended_hours: v("otExt"), night_hours: v("otNight"), holiday_hours: v("otHol"), holiday_over8: v("otHol8") },
    });
    const b = d.breakdown || {};
    setResult(`<div class="text-4xl font-bold text-amber-400">${(d.total_overtime_pay || 0).toLocaleString()}원</div>
      <table class="w-full mt-4 text-sm"><tr class="text-[var(--muted)]"><th class="text-left py-1">구분</th><th>시간</th><th>배율</th><th class="text-right">금액</th></tr>
      ${Object.entries(b).map(([k, v]) => { const item = v as {hours:number;rate:string;amount:number}; return `<tr><td class="py-1">${k}</td><td class="text-center">${item.hours}h</td><td class="text-center">${item.rate}</td><td class="text-right">${(item.amount||0).toLocaleString()}원</td></tr>`; }).join("")}
      </table><div class="mt-2 text-sm text-[var(--muted)]">📎 ${d.legal_basis || ""}</div>`);
  };

  const calcSeverance = async () => {
    const wage = +(document.getElementById("sevWage") as HTMLInputElement)?.value || 0;
    const d = await gpuFetch("/api/hr-law/calculate/severance", { method: "POST", body: { start_date: sevStart, end_date: sevEnd, avg_daily_wage: wage } });
    if (d.error) { setResult(`<div class="text-red-400">${d.error}</div>${d.legal_basis ? `<div class="text-sm text-[var(--muted)] mt-2">📎 ${d.legal_basis}</div>` : ""}`); return; }
    setResult(`<div class="text-4xl font-bold text-green-400">${(d.severance_pay || 0).toLocaleString()}원</div>
      <div class="mt-2 text-[var(--muted)]">근속 ${d.years}년 (${d.working_days}일)</div>
      <div class="text-sm text-[var(--muted)]">📎 ${d.legal_basis} | ${d.formula}</div>`);
  };

  const inputClass = "px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm w-48";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">🧮 계산기</h1>
      <div className="flex gap-2 mb-6">
        {(["leave", "overtime", "severance"] as const).map((t) => (
          <button key={t} onClick={() => { setTab(t); setResult(""); }}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${tab === t ? "bg-[var(--accent)] text-white" : "bg-[var(--surface)] text-[var(--muted)] border border-[var(--border)] hover:border-[var(--accent)]"}`}>
            {t === "leave" ? "📅 연차" : t === "overtime" ? "⏰ 초과근무" : "💰 퇴직금"}
          </button>
        ))}
      </div>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
        {tab === "leave" && (
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <DateInput label="입사일" value={leaveDate} onChange={setLeaveDate} />
            </div>
            <button onClick={calcLeave} className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:bg-[var(--accent)]/80">계산</button>
          </div>
        )}
        {tab === "overtime" && (
          <div className="space-y-3">
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">시급 (원)</label><input id="otWage" type="number" defaultValue={15000} className={inputClass} /></div>
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">연장 (시간)</label><input id="otExt" type="number" defaultValue={0} className={inputClass} /></div>
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">야간 (시간)</label><input id="otNight" type="number" defaultValue={0} className={inputClass} /></div>
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">휴일 (시간)</label><input id="otHol" type="number" defaultValue={0} className={inputClass} /></div>
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">휴일 8h초과</label><input id="otHol8" type="number" defaultValue={0} className={inputClass} /></div>
            <button onClick={calcOvertime} className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium">계산</button>
          </div>
        )}
        {tab === "severance" && (
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <DateInput label="입사일" value={sevStart} onChange={setSevStart} />
            </div>
            <div className="flex items-center gap-4">
              <DateInput label="퇴직일" value={sevEnd} onChange={setSevEnd} />
            </div>
            <div className="flex items-center gap-4"><label className="text-sm text-[var(--muted)] w-32">평균임금 (일급)</label><input id="sevWage" type="number" defaultValue={150000} className={inputClass} /></div>
            <button onClick={calcSeverance} className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium">계산</button>
          </div>
        )}
        {result && <div className="mt-6 p-6 bg-[var(--bg)] rounded-lg border border-[var(--border)]" dangerouslySetInnerHTML={{ __html: result }} />}
      </div>
    </div>
  );
}
