"use client";

import { useState, useCallback, useRef } from "react";
import { gpuFetch } from "@/lib/api";

type TabKey = "readmission" | "complication" | "adverse-drug";

const TABS: { key: TabKey; label: string; icon: string; api: string }[] = [
  { key: "readmission", label: "재입원", icon: "🏥", api: "/api/medical/predict/readmission" },
  { key: "complication", label: "합병증", icon: "⚠️", api: "/api/medical/predict/complication" },
  { key: "adverse-drug", label: "약물부작용", icon: "💊", api: "/api/medical/predict/adverse-drug" },
];

interface PredictionResult {
  risk_score?: number;
  risk_level?: string;
  factors?: { name: string; contribution: number }[];
  recommendations?: string[];
  raw?: string;
  model?: string;
  elapsed_ms?: number;
  tokens?: number;
}

interface HistoryEntry {
  id: number;
  tab: TabKey;
  diagnosis: string;
  result: PredictionResult;
  timestamp: string;
}

export default function Page() {
  const [tab, setTab] = useState<TabKey>("readmission");
  const [age, setAge] = useState("65");
  const [sex, setSex] = useState("M");
  const [diagnosis, setDiagnosis] = useState("");
  const [medications, setMedications] = useState("");
  const [history, setHistory] = useState("");
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [predHistory, setPredHistory] = useState<HistoryEntry[]>([]);
  const historyIdRef = useRef(0);

  const predict = useCallback(async () => {
    const tabConfig = TABS.find((t) => t.key === tab)!;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await gpuFetch(tabConfig.api, {
        method: "POST",
        body: {
          age: parseInt(age) || 65,
          sex,
          diagnosis: diagnosis.trim(),
          medications: medications.split(",").map((s) => s.trim()).filter(Boolean),
          history: history.trim(),
        },
      });
      if (data.error) throw new Error(data.error);
      setResult(data);

      historyIdRef.current += 1;
      setPredHistory((prev) => [{
        id: historyIdRef.current,
        tab,
        diagnosis: diagnosis.trim() || "N/A",
        result: data,
        timestamp: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }),
      }, ...prev].slice(0, 15));
    } catch (e) {
      setError((e as Error).message);
    }
    setLoading(false);
  }, [tab, age, sex, diagnosis, medications, history]);

  const riskColor = (score?: number) => {
    if (score == null) return "text-[var(--muted)]";
    if (score >= 70) return "text-red-400";
    if (score >= 40) return "text-yellow-400";
    return "text-emerald-400";
  };

  const riskBg = (score?: number) => {
    if (score == null) return "bg-[var(--border)]";
    if (score >= 70) return "bg-red-500";
    if (score >= 40) return "bg-yellow-500";
    return "bg-emerald-500";
  };

  const factorBarColor = (contrib: number) => {
    if (contrib >= 30) return "bg-red-500";
    if (contrib >= 15) return "bg-yellow-500";
    return "bg-blue-500";
  };

  const maxContribution = result?.factors?.reduce((max, f) => Math.max(max, f.contribution), 0) || 100;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Prediction Dashboard</h1>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-[var(--surface)] rounded-xl p-1 w-fit">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => { setTab(t.key); setResult(null); }}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
              tab === t.key ? "bg-[var(--accent)] text-white" : "text-[var(--muted)] hover:text-[var(--text)]"
            }`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Input */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">환자 정보</h2>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">나이</label>
              <input type="number" value={age} onChange={(e) => setAge(e.target.value)}
                className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
            </div>
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">성별</label>
              <select value={sex} onChange={(e) => setSex(e.target.value)}
                className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]">
                <option value="M">남</option>
                <option value="F">여</option>
              </select>
            </div>
          </div>
          <div className="mb-4">
            <label className="block text-xs text-[var(--muted)] mb-1">진단명</label>
            <input value={diagnosis} onChange={(e) => setDiagnosis(e.target.value)} placeholder="급성심근경색, 폐렴 ..."
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          </div>
          <div className="mb-4">
            <label className="block text-xs text-[var(--muted)] mb-1">복용약물 (쉼표 구분)</label>
            <input value={medications} onChange={(e) => setMedications(e.target.value)} placeholder="암로디핀, 메트포르민"
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          </div>
          <div className="mb-4">
            <label className="block text-xs text-[var(--muted)] mb-1">병력/추가 정보</label>
            <textarea value={history} onChange={(e) => setHistory(e.target.value)} rows={3}
              placeholder="이전 입원 2회/년, COPD, 독거노인 ..."
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] resize-y" />
          </div>
          <button onClick={predict} disabled={loading}
            className="px-5 py-2.5 bg-[var(--accent)] text-white rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-50 w-full">
            {loading ? "예측 중..." : `${TABS.find((t) => t.key === tab)?.icon} 예측 실행`}
          </button>
        </div>

        {/* Result */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">예측 결과</h2>
          {!result ? (
            <div className="flex items-center justify-center h-48 text-[var(--muted)] text-sm">예측을 실행하세요.</div>
          ) : (
            <>
              {/* Risk Gauge */}
              {result.risk_score != null && (
                <div className="text-center mb-6">
                  <div className="relative inline-flex items-center justify-center">
                    <svg className="w-32 h-32 -rotate-90" viewBox="0 0 120 120">
                      <circle cx="60" cy="60" r="54" fill="none" stroke="var(--border)" strokeWidth="8" />
                      <circle cx="60" cy="60" r="54" fill="none"
                        stroke={result.risk_score >= 70 ? "#ef4444" : result.risk_score >= 40 ? "#eab308" : "#10b981"}
                        strokeWidth="8" strokeLinecap="round"
                        strokeDasharray={`${(result.risk_score / 100) * 339.3} 339.3`} />
                    </svg>
                    <div className="absolute">
                      <div className={`text-3xl font-bold ${riskColor(result.risk_score)}`}>{result.risk_score}%</div>
                    </div>
                  </div>
                  {result.risk_level && (
                    <div className={`text-sm font-semibold mt-2 ${riskColor(result.risk_score)}`}>{result.risk_level}</div>
                  )}
                </div>
              )}

              {/* Factor Bar Chart */}
              {result.factors && result.factors.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-xs font-semibold text-[var(--muted)] mb-3">Risk Factors</h3>
                  <div className="space-y-2.5">
                    {result.factors.sort((a, b) => b.contribution - a.contribution).map((f, i) => (
                      <div key={i}>
                        <div className="flex items-center justify-between text-xs mb-1">
                          <span className="text-[var(--text)]">{f.name}</span>
                          <span className="text-[var(--muted)] font-mono">{f.contribution}%</span>
                        </div>
                        <div className="w-full bg-[var(--bg)] rounded-full h-2">
                          <div className={`h-2 rounded-full transition-all ${factorBarColor(f.contribution)}`}
                            style={{ width: `${(f.contribution / maxContribution) * 100}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recommendations */}
              {result.recommendations && result.recommendations.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-xs font-semibold text-[var(--muted)] mb-2">권고사항</h3>
                  <ol className="list-decimal list-inside space-y-1.5 text-sm text-[var(--text)]">
                    {result.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                  </ol>
                </div>
              )}

              {/* Meta */}
              <div className="flex gap-4 text-xs text-[var(--muted)] mt-4 pt-3 border-t border-[var(--border)]">
                {result.model && <span>모델: {result.model}</span>}
                {result.elapsed_ms != null && <span>{(result.elapsed_ms / 1000).toFixed(1)}s</span>}
                {result.tokens != null && <span>{result.tokens} tok</span>}
              </div>

              {/* Raw fallback */}
              {result.raw && (
                <pre className="mt-4 text-xs text-[var(--text)] bg-[var(--bg)] rounded-lg p-4 whitespace-pre-wrap max-h-[300px] overflow-y-auto">
                  {result.raw}
                </pre>
              )}
            </>
          )}
        </div>
      </div>

      {/* Prediction History */}
      {predHistory.length > 0 && (
        <div className="mt-6 bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">Prediction History</h2>
          <div className="space-y-1.5">
            {predHistory.map((entry) => {
              const tabInfo = TABS.find((t) => t.key === entry.tab);
              return (
                <div key={entry.id}
                  className="flex items-center gap-3 bg-[var(--bg)] rounded-lg p-2.5 cursor-pointer hover:border-[var(--accent)] border border-transparent transition-colors"
                  onClick={() => { setTab(entry.tab); setResult(entry.result); }}>
                  <span className="text-xs font-mono text-[var(--muted)] w-12 shrink-0">{entry.timestamp}</span>
                  <span className="text-sm shrink-0">{tabInfo?.icon}</span>
                  <span className="text-xs text-[var(--text)] truncate flex-1">{entry.diagnosis}</span>
                  {entry.result.risk_score != null && (
                    <span className={`text-sm font-bold shrink-0 ${riskColor(entry.result.risk_score)}`}>
                      {entry.result.risk_score}%
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
