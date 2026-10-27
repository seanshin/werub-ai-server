"use client";

import { useState } from "react";
import { gpuFetch } from "@/lib/api";

const CATEGORIES = [
  { id: "", label: "전체" },
  { id: "general", label: "일반" },
  { id: "record", label: "의무기록" },
  { id: "consent", label: "동의/설명" },
  { id: "emergency", label: "응급" },
  { id: "infection", label: "감염병" },
  { id: "pharmacy", label: "약사법" },
  { id: "privacy", label: "개인정보" },
];

export default function Page() {
  const [tab, setTab] = useState<"ask" | "check" | "guide">("ask");
  const [question, setQuestion] = useState("");
  const [category, setCategory] = useState("");
  const [answer, setAnswer] = useState<Record<string, unknown> | null>(null);
  const [running, setRunning] = useState(false);

  // Check tab state
  const [record, setRecord] = useState("");
  const [checkResult, setCheckResult] = useState<Record<string, unknown> | null>(null);

  // Guide tab state
  const [procedure, setProcedure] = useState("");
  const [guideResult, setGuideResult] = useState<Record<string, unknown> | null>(null);

  const ask = async () => {
    if (!question.trim()) return;
    setRunning(true);
    setAnswer(null);
    const data = await gpuFetch("/api/medical-law/ask", {
      method: "POST", body: { question, category: category || undefined },
    });
    setAnswer(data);
    setRunning(false);
  };

  const checkRecord = async () => {
    if (!record.trim()) return;
    setRunning(true);
    setCheckResult(null);
    const data = await gpuFetch("/api/medical-law/check/record", {
      method: "POST", body: { record },
    });
    setCheckResult(data);
    setRunning(false);
  };

  const getConsent = async () => {
    if (!procedure.trim()) return;
    setRunning(true);
    setGuideResult(null);
    const data = await gpuFetch("/api/medical-law/guide/informed-consent", {
      method: "POST", body: { procedure },
    });
    setGuideResult(data);
    setRunning(false);
  };

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">의료법 AI</h1>

      <div className="flex gap-2 mb-6">
        {([["ask", "법률 상담"], ["check", "기록 검사"], ["guide", "인폼드 컨센트"]] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === id ? "bg-[var(--accent)] text-white" : "bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--text)]"
            }`}>{label}</button>
        ))}
      </div>

      {tab === "ask" && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
          <div className="flex gap-3 mb-4">
            <select value={category} onChange={(e) => setCategory(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
              {CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder="의료법 관련 질문을 입력하세요..." rows={3}
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm mb-3" />
          <button onClick={ask} disabled={running}
            className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {running ? "분석 중..." : "질문하기"}
          </button>

          {answer && (
            <div className="mt-4 space-y-3">
              <div className="bg-[var(--surface)] rounded-lg p-4 text-sm whitespace-pre-wrap">{String(answer.answer)}</div>
              {(answer.legal_basis as Array<Record<string, string>>)?.length > 0 && (
                <div>
                  <h4 className="text-xs font-bold text-[var(--muted)] mb-2">법적 근거</h4>
                  {(answer.legal_basis as Array<Record<string, string>>).map((b, i) => (
                    <div key={i} className="text-xs bg-[var(--surface)] rounded p-2 mb-1">
                      <span className="font-medium">{b.law} {b.article}</span> — {b.title}
                      {b.penalty && <span className="text-red-400 ml-2">벌칙: {b.penalty}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === "check" && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
          <textarea value={record} onChange={(e) => setRecord(e.target.value)}
            placeholder="의무기록 (SOAP 등)을 붙여넣으세요..." rows={5}
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm mb-3" />
          <button onClick={checkRecord} disabled={running}
            className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {running ? "검사 중..." : "기재사항 검사"}
          </button>

          {checkResult && (
            <div className="mt-4">
              <div className={`text-sm font-bold mb-2 ${checkResult.compliant ? "text-emerald-400" : "text-red-400"}`}>
                {checkResult.compliant ? "적합" : "부적합"} — {String(checkResult.law_reference)}
              </div>
              {((checkResult.issues as Array<Record<string, string>>) || []).map((issue, i) => (
                <div key={i} className="text-xs bg-red-500/10 border border-red-500/20 rounded p-2 mb-1">
                  [{issue.severity}] {issue.issue} ({issue.law})
                </div>
              ))}
              <div className="grid grid-cols-3 gap-2 mt-3">
                {Object.entries((checkResult.checklist || {}) as Record<string, boolean>).map(([k, v]) => (
                  <div key={k} className="text-xs bg-[var(--surface)] rounded p-2">
                    {v ? "✅" : "❌"} {k}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "guide" && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
          <input value={procedure} onChange={(e) => setProcedure(e.target.value)}
            placeholder="시술/수술명 (예: 복강경 담낭절제술)"
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm mb-3" />
          <button onClick={getConsent} disabled={running}
            className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {running ? "생성 중..." : "인폼드 컨센트 가이드"}
          </button>

          {guideResult && (
            <div className="mt-4 text-sm space-y-3">
              <div className="bg-[var(--surface)] rounded-lg p-4">
                <h4 className="font-bold mb-2">{String(guideResult.legal_requirement)}</h4>
                {((guideResult.typical_risks as string[]) || []).length > 0 && (
                  <div className="mb-2">
                    <span className="text-xs text-[var(--muted)]">전형적 위험:</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {(guideResult.typical_risks as string[]).map((r, i) => (
                        <span key={i} className="text-xs bg-red-500/10 text-red-400 rounded px-2 py-0.5">{r}</span>
                      ))}
                    </div>
                  </div>
                )}
                {((guideResult.alternatives as string[]) || []).length > 0 && (
                  <div>
                    <span className="text-xs text-[var(--muted)]">대안 치료:</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {(guideResult.alternatives as string[]).map((a, i) => (
                        <span key={i} className="text-xs bg-blue-500/10 text-blue-400 rounded px-2 py-0.5">{a}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
