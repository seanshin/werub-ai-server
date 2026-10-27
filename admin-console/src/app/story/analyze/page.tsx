"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

const ANALYSES = [
  { key: "pacing", label: "페이싱", icon: "📈", desc: "챕터별 긴장도 곡선" },
  { key: "character", label: "캐릭터", icon: "👤", desc: "등장 빈도, 감정 아크" },
  { key: "readability", label: "가독성", icon: "📖", desc: "문장 길이, 어휘 다양성" },
  { key: "dialogue", label: "대화", icon: "💬", desc: "말투 일관성, 서브텍스트" },
  { key: "worldbuilding", label: "세계관", icon: "🌍", desc: "설정 모순 탐지" },
  { key: "emotion_map", label: "감정 지도", icon: "❤️", desc: "감정 흐름 시각화" },
];

export default function Page() {
  const [projects, setProjects] = useState<Record<string, unknown>[]>([]);
  const [projectId, setProjectId] = useState("");
  const [running, setRunning] = useState("");
  const [results, setResults] = useState<Record<string, unknown>>({});
  const [scoreResult, setScoreResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    gpuFetch("/api/novel/projects").then((d) => setProjects(d.projects || []));
  }, []);

  const runAnalysis = async (key: string) => {
    if (!projectId) return;
    setRunning(key);
    const d = await gpuFetch(`/api/novel/analyze/${key}`, {
      method: "POST",
      body: { project_id: projectId },
    });
    setResults((prev) => ({ ...prev, [key]: d }));
    setRunning("");
  };

  const runScore = async () => {
    if (!projectId) return;
    setRunning("score");
    const d = await gpuFetch("/api/novel/score", {
      method: "POST",
      body: { project_id: projectId },
    });
    setScoreResult(d);
    setRunning("");
  };

  const scores = scoreResult?.scores as Record<string, Record<string, unknown>> | undefined;

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">소설 분석</h1>

      <div className="flex gap-3 mb-6">
        <select value={projectId} onChange={(e) => { setProjectId(e.target.value); setResults({}); setScoreResult(null); }}
          className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-2 text-sm flex-1">
          <option value="">프로젝트 선택</option>
          {projects.map((p) => (
            <option key={String(p.id)} value={String(p.id)}>{String(p.title)} ({String(p.genre)})</option>
          ))}
        </select>
        <button onClick={runScore} disabled={!projectId || running === "score"}
          className="px-4 py-2 bg-amber-600 text-white rounded-lg text-sm disabled:opacity-50">
          {running === "score" ? "평가 중..." : "종합 점수"}
        </button>
      </div>

      {/* Score Card */}
      {scoreResult && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
          <div className="flex items-center gap-4 mb-4">
            <div className="text-4xl font-bold text-[var(--accent)]">{String(scoreResult.total || "?")}</div>
            <div className="text-sm text-[var(--muted)]">/ 100</div>
          </div>
          {scores && (
            <div className="grid grid-cols-3 gap-3 mb-4">
              {Object.entries(scores).map(([k, v]) => (
                <div key={k} className="bg-[var(--surface)] rounded-lg p-3 text-sm">
                  <div className="flex justify-between mb-1">
                    <span className="capitalize">{k}</span>
                    <span className="font-bold">{String(v.score)}</span>
                  </div>
                  <div className="w-full bg-gray-700 rounded-full h-1.5">
                    <div className="bg-[var(--accent)] h-1.5 rounded-full" style={{ width: `${Number(v.score)}%` }} />
                  </div>
                  <div className="text-xs text-[var(--muted)] mt-1">{String(v.comment || "")}</div>
                </div>
              ))}
            </div>
          )}
          {scoreResult.overall_comment && (
            <div className="text-sm">{String(scoreResult.overall_comment)}</div>
          )}
        </div>
      )}

      {/* Analysis Grid */}
      <div className="grid grid-cols-3 gap-4">
        {ANALYSES.map((a) => (
          <div key={a.key} className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span>{a.icon}</span>
                <span className="font-semibold text-sm">{a.label}</span>
              </div>
              <button onClick={() => runAnalysis(a.key)}
                disabled={!projectId || running === a.key}
                className="px-2 py-1 bg-[var(--accent)] text-white rounded text-xs disabled:opacity-50">
                {running === a.key ? "..." : "실행"}
              </button>
            </div>
            <div className="text-xs text-[var(--muted)] mb-2">{a.desc}</div>
            {results[a.key] && (
              <div className="text-xs bg-[var(--surface)] rounded p-2 max-h-40 overflow-auto">
                <pre className="whitespace-pre-wrap">{JSON.stringify(results[a.key], null, 2).slice(0, 500)}</pre>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
