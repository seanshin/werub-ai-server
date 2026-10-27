"use client";

import { useState, useRef, useCallback } from "react";
import ReasoningPipeline, { type StepData } from "@/components/ReasoningPipeline";
import StepAccordion from "@/components/StepAccordion";

const GPU_BASE = typeof window !== "undefined"
  ? window.location.pathname.replace(/\/admin-console.*$/, "").replace(/\/+$/, "")
  : "";

const SPECIALTIES = ["내과", "외과", "정형외과", "신경과", "심장내과", "소아과", "산부인과", "비뇨기과", "정신건강의학과", "이비인후과", "안과", "피부과", "응급의학과"];

const MODELS = [
  { id: "", label: "기본 (서버 설정)" },
  { id: "werubmedv2:latest", label: "werubmedv2 (파인튜닝 v2)" },
  { id: "werub-medical:7b", label: "werub-medical v1" },
  { id: "qwen2.5:7b", label: "qwen2.5:7b (범용)" },
  { id: "qwen2.5:14b", label: "qwen2.5:14b (범용 대형)" },
  { id: "medgemma:4b", label: "MedGemma 4B" },
];

interface FinalResult {
  urgency?: string;
  primary_diagnosis?: string;
  differential_diagnoses?: string[];
  icd_code?: string;
  required_tests?: string[];
  total_tokens?: number;
  total_elapsed_ms?: number;
  model?: string;
  summary?: string;
}

interface HistoryEntry {
  id: number;
  symptoms: string;
  model: string;
  result: FinalResult;
  steps: StepData[];
  timestamp: string;
}

export default function Page() {
  const [age, setAge] = useState("55");
  const [sex, setSex] = useState("M");
  const [specialty, setSpecialty] = useState("내과");
  const [conditions, setConditions] = useState("");
  const [medications, setMedications] = useState("");
  const [symptoms, setSymptoms] = useState("");
  const [selectedModel, setSelectedModel] = useState("");

  const [steps, setSteps] = useState<StepData[]>([]);
  const [finalResult, setFinalResult] = useState<FinalResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  // History + Compare
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [compareA, setCompareA] = useState<HistoryEntry | null>(null);
  const [compareB, setCompareB] = useState<HistoryEntry | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const historyIdRef = useRef(0);

  const addToHistory = (sym: string, model: string, result: FinalResult, stps: StepData[]) => {
    historyIdRef.current += 1;
    const entry: HistoryEntry = {
      id: historyIdRef.current,
      symptoms: sym,
      model: model || result.model || "default",
      result,
      steps: stps,
      timestamp: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    };
    setHistory((prev) => [entry, ...prev].slice(0, 20));
    return entry;
  };

  const runChain = useCallback(async () => {
    if (!symptoms.trim()) { setError("증상을 입력하세요"); return; }
    setError("");
    setSteps([]);
    setFinalResult(null);
    setRunning(true);

    abortRef.current = new AbortController();
    const collectedSteps: StepData[] = [];
    let collectedResult: FinalResult | null = null;

    try {
      const body: Record<string, unknown> = {
        patient: {
          age: parseInt(age) || 55,
          sex,
          specialty,
          conditions: conditions.split(",").map((s) => s.trim()).filter(Boolean),
          medications: medications.split(",").map((s) => s.trim()).filter(Boolean),
        },
        symptoms: symptoms.trim(),
      };
      if (selectedModel) body.model = selectedModel;

      const res = await fetch(`${GPU_BASE}/api/medical/reasoning/chain/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: abortRef.current.signal,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No readable stream");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") continue;

          try {
            const data = JSON.parse(raw);

            if (data.type === "step_start") {
              const step: StepData = { step: data.step, name: data.name || "", status: "running" };
              collectedSteps.push(step);
              setSteps((prev) => {
                const filtered = prev.filter((s) => s.step !== data.step);
                return [...filtered, step];
              });
            } else if (data.type === "step_result") {
              const idx = collectedSteps.findIndex((s) => s.step === data.step);
              if (idx >= 0) Object.assign(collectedSteps[idx], { status: "done", elapsed_ms: data.elapsed_ms, tokens: data.tokens, result: data.result });
              setSteps((prev) =>
                prev.map((s) =>
                  s.step === data.step
                    ? { ...s, status: "done", elapsed_ms: data.elapsed_ms, tokens: data.tokens, result: data.result }
                    : s
                )
              );
            } else if (data.type === "step_error") {
              const idx = collectedSteps.findIndex((s) => s.step === data.step);
              if (idx >= 0) Object.assign(collectedSteps[idx], { status: "error", result: data.error });
              setSteps((prev) =>
                prev.map((s) =>
                  s.step === data.step ? { ...s, status: "error", result: data.error } : s
                )
              );
            } else if (data.type === "final") {
              collectedResult = data;
              setFinalResult(data);
            }
          } catch { /* skip malformed JSON */ }
        }
      }

      if (collectedResult) {
        addToHistory(symptoms.trim(), selectedModel, collectedResult, collectedSteps);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError((e as Error).message);
      }
    }
    setRunning(false);
  }, [age, sex, specialty, conditions, medications, symptoms, selectedModel]);

  const runSync = useCallback(async () => {
    if (!symptoms.trim()) { setError("증상을 입력하세요"); return; }
    setError("");
    setSteps([]);
    setFinalResult(null);
    setRunning(true);

    try {
      const body: Record<string, unknown> = {
        patient: {
          age: parseInt(age) || 55,
          sex,
          specialty,
          conditions: conditions.split(",").map((s) => s.trim()).filter(Boolean),
          medications: medications.split(",").map((s) => s.trim()).filter(Boolean),
        },
        symptoms: symptoms.trim(),
      };
      if (selectedModel) body.model = selectedModel;

      const res = await fetch(`${GPU_BASE}/api/medical/reasoning/chain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

      const stps: StepData[] = data.steps
        ? data.steps.map((s: { step: number; name?: string; elapsed_ms?: number; tokens?: number; result?: unknown }) => ({
            step: s.step, name: s.name || "", status: "done" as const,
            elapsed_ms: s.elapsed_ms, tokens: s.tokens, result: s.result,
          }))
        : [];
      setSteps(stps);
      setFinalResult(data);
      addToHistory(symptoms.trim(), selectedModel, data, stps);
    } catch (e) {
      setError((e as Error).message);
    }
    setRunning(false);
  }, [age, sex, specialty, conditions, medications, symptoms, selectedModel]);

  const cancel = () => {
    abortRef.current?.abort();
    setRunning(false);
  };

  const urgencyColor = (u?: string) => {
    if (!u) return "text-[var(--muted)]";
    const l = u.toLowerCase();
    if (l.includes("high") || l.includes("긴급")) return "text-red-400";
    if (l.includes("medium") || l.includes("중간")) return "text-yellow-400";
    return "text-emerald-400";
  };

  const loadEntry = (entry: HistoryEntry) => {
    setSteps(entry.steps);
    setFinalResult(entry.result);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Reasoning Chain</h1>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>
      )}

      {/* Input Form */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-sm font-semibold text-[var(--muted)] uppercase tracking-wider mb-4">환자 정보</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
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
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">전문과</label>
            <select value={specialty} onChange={(e) => setSpecialty(e.target.value)}
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]">
              {SPECIALTIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">모델 선택</label>
            <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]">
              {MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">기저질환 (쉼표 구분)</label>
            <input value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder="고혈압, 당뇨"
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">복용약물 (쉼표 구분)</label>
            <input value={medications} onChange={(e) => setMedications(e.target.value)} placeholder="암로디핀, 메트포르민"
              className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)]" />
          </div>
        </div>
        <div className="mb-4">
          <label className="block text-xs text-[var(--muted)] mb-1">증상 (자유 텍스트)</label>
          <textarea value={symptoms} onChange={(e) => setSymptoms(e.target.value)} rows={3}
            placeholder="3일 전부터 흉통 + 좌측 팔 방사통. 운동 시 악화. 발한 동반. BP 150/95."
            className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] resize-y" />
        </div>
        <div className="flex gap-3">
          <button onClick={runChain} disabled={running}
            className="px-5 py-2.5 bg-[var(--accent)] text-white rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity">
            {running ? "추론 중..." : "추론 시작 (SSE)"}
          </button>
          <button onClick={runSync} disabled={running}
            className="px-5 py-2.5 bg-[var(--surface)] border border-[var(--border)] text-[var(--text)] rounded-lg text-sm hover:border-[var(--accent)] disabled:opacity-50 transition-colors">
            동기 실행
          </button>
          {running && (
            <button onClick={cancel}
              className="px-5 py-2.5 bg-red-500/20 border border-red-500/50 text-red-400 rounded-lg text-sm hover:bg-red-500/30 transition-colors">
              취소
            </button>
          )}
        </div>
      </div>

      {/* Pipeline Visualization */}
      {steps.length > 0 && <ReasoningPipeline steps={steps} />}

      {/* Step Details */}
      {steps.length > 0 && <StepAccordion steps={steps} />}

      {/* Final Summary */}
      {finalResult && (
        <div className="mt-6 bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">Summary</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            {finalResult.urgency && (
              <div className="text-center">
                <div className={`text-xl font-bold ${urgencyColor(finalResult.urgency)}`}>{finalResult.urgency}</div>
                <div className="text-xs text-[var(--muted)]">긴급도</div>
              </div>
            )}
            {finalResult.primary_diagnosis && (
              <div className="text-center">
                <div className="text-sm font-bold text-[var(--text)]">{finalResult.primary_diagnosis}</div>
                <div className="text-xs text-[var(--muted)]">주진단 {finalResult.icd_code && `(${finalResult.icd_code})`}</div>
              </div>
            )}
            {finalResult.total_elapsed_ms != null && (
              <div className="text-center">
                <div className="text-xl font-bold text-blue-400">{(finalResult.total_elapsed_ms / 1000).toFixed(1)}s</div>
                <div className="text-xs text-[var(--muted)]">총 소요</div>
              </div>
            )}
            {finalResult.total_tokens != null && (
              <div className="text-center">
                <div className="text-xl font-bold text-purple-400">{finalResult.total_tokens}</div>
                <div className="text-xs text-[var(--muted)]">총 토큰</div>
              </div>
            )}
          </div>
          {finalResult.differential_diagnoses && finalResult.differential_diagnoses.length > 0 && (
            <div className="mt-3">
              <span className="text-xs text-[var(--muted)]">감별진단: </span>
              {finalResult.differential_diagnoses.map((d, i) => (
                <span key={i} className="inline-block bg-purple-500/10 text-purple-400 text-xs px-2 py-0.5 rounded mr-1.5 mb-1">{d}</span>
              ))}
            </div>
          )}
          {finalResult.required_tests && finalResult.required_tests.length > 0 && (
            <div className="mt-3">
              <span className="text-xs text-[var(--muted)]">필수 검사: </span>
              {finalResult.required_tests.map((t, i) => (
                <span key={i} className="inline-block bg-blue-500/10 text-blue-400 text-xs px-2 py-0.5 rounded mr-1.5 mb-1">{t}</span>
              ))}
            </div>
          )}
          {finalResult.model && (
            <div className="mt-2 text-xs text-[var(--muted)]">모델: {finalResult.model}</div>
          )}
          {finalResult.summary && (
            <pre className="mt-3 text-xs text-[var(--text)] bg-[var(--bg)] rounded-lg p-4 whitespace-pre-wrap">{finalResult.summary}</pre>
          )}
        </div>
      )}

      {/* Session History + Compare */}
      {history.length > 0 && (
        <div className="mt-6 bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Session History ({history.length})</h2>
            {history.length >= 2 && (
              <button onClick={() => setShowCompare(!showCompare)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                  showCompare ? "bg-[var(--accent)] text-white border-[var(--accent)]" : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)]"
                }`}>
                {showCompare ? "비교 닫기" : "모델 비교"}
              </button>
            )}
          </div>
          <div className="space-y-2">
            {history.map((entry) => (
              <div key={entry.id} className="flex items-center gap-3 bg-[var(--bg)] rounded-lg p-3">
                <span className="text-xs font-mono text-[var(--muted)] w-16 shrink-0">{entry.timestamp}</span>
                <span className="text-xs font-mono text-blue-400 w-32 shrink-0 truncate">{entry.model}</span>
                <span className="text-xs text-[var(--text)] truncate flex-1">{entry.symptoms.slice(0, 60)}</span>
                <div className="flex gap-1 shrink-0">
                  {entry.result.total_elapsed_ms != null && (
                    <span className="text-[10px] text-[var(--muted)]">{(entry.result.total_elapsed_ms / 1000).toFixed(1)}s</span>
                  )}
                  <button onClick={() => loadEntry(entry)} className="text-xs text-[var(--accent)] hover:underline ml-2">보기</button>
                  {showCompare && (
                    <>
                      <button onClick={() => setCompareA(entry)}
                        className={`text-[10px] ml-1 px-1.5 py-0.5 rounded ${compareA?.id === entry.id ? "bg-emerald-500/20 text-emerald-400" : "text-[var(--muted)] hover:text-emerald-400"}`}>A</button>
                      <button onClick={() => setCompareB(entry)}
                        className={`text-[10px] px-1.5 py-0.5 rounded ${compareB?.id === entry.id ? "bg-blue-500/20 text-blue-400" : "text-[var(--muted)] hover:text-blue-400"}`}>B</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Compare View */}
          {showCompare && compareA && compareB && (
            <div className="mt-4 border-t border-[var(--border)] pt-4">
              <h3 className="text-sm font-semibold mb-3">Model Comparison</h3>
              <div className="grid grid-cols-2 gap-4">
                {[compareA, compareB].map((entry, idx) => (
                  <div key={idx} className={`rounded-lg border p-4 ${idx === 0 ? "border-emerald-500/30" : "border-blue-500/30"}`}>
                    <div className={`text-xs font-semibold mb-2 ${idx === 0 ? "text-emerald-400" : "text-blue-400"}`}>
                      {idx === 0 ? "A" : "B"}: {entry.model}
                    </div>
                    <div className="space-y-2 text-xs">
                      <div className="flex justify-between">
                        <span className="text-[var(--muted)]">주진단</span>
                        <span className="text-[var(--text)] font-semibold">{entry.result.primary_diagnosis || "-"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-[var(--muted)]">긴급도</span>
                        <span className={urgencyColor(entry.result.urgency)}>{entry.result.urgency || "-"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-[var(--muted)]">ICD</span>
                        <span className="text-[var(--text)]">{entry.result.icd_code || "-"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-[var(--muted)]">소요</span>
                        <span className="text-blue-400">{entry.result.total_elapsed_ms ? `${(entry.result.total_elapsed_ms / 1000).toFixed(1)}s` : "-"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-[var(--muted)]">토큰</span>
                        <span className="text-purple-400">{entry.result.total_tokens || "-"}</span>
                      </div>
                      {entry.result.required_tests && (
                        <div>
                          <span className="text-[var(--muted)]">검사: </span>
                          <span className="text-[var(--text)]">{entry.result.required_tests.join(", ")}</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
