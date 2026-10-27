"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

export default function Page() {
  const [projects, setProjects] = useState<Record<string, unknown>[]>([]);
  const [projectId, setProjectId] = useState("");
  const [running, setRunning] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  // Brainstorm
  const [keywords, setKeywords] = useState("");
  const [bsGenre, setBsGenre] = useState("fantasy");

  // Naming
  const [nameType, setNameType] = useState("character");
  const [nameStyle, setNameStyle] = useState("korean");

  // Unblock
  const [chapterNum, setChapterNum] = useState("1");
  const [stuckPoint, setStuckPoint] = useState("");

  useEffect(() => {
    gpuFetch("/api/novel/projects").then((d) => setProjects(d.projects || []));
  }, []);

  const brainstorm = async () => {
    setRunning("brainstorm");
    const d = await gpuFetch("/api/novel/brainstorm", {
      method: "POST",
      body: { keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean), genre: bsGenre, count: 5 },
    });
    setResult(d);
    setRunning("");
  };

  const naming = async () => {
    if (!projectId) return;
    setRunning("naming");
    const d = await gpuFetch("/api/novel/naming", {
      method: "POST",
      body: { project_id: projectId, type: nameType, style: nameStyle, count: 10 },
    });
    setResult(d);
    setRunning("");
  };

  const unblock = async () => {
    if (!projectId) return;
    setRunning("unblock");
    const d = await gpuFetch("/api/novel/prompt/unblock", {
      method: "POST",
      body: { project_id: projectId, chapter_num: parseInt(chapterNum), stuck_point: stuckPoint },
    });
    setResult(d);
    setRunning("");
  };

  const summary = async () => {
    if (!projectId) return;
    setRunning("summary");
    const d = await gpuFetch("/api/novel/chapter/summary", {
      method: "POST",
      body: { project_id: projectId, chapter_num: parseInt(chapterNum) },
    });
    setResult(d);
    setRunning("");
  };

  const timeline = async () => {
    if (!projectId) return;
    setRunning("timeline");
    const d = await gpuFetch("/api/novel/timeline", {
      method: "POST",
      body: { project_id: projectId },
    });
    setResult(d);
    setRunning("");
  };

  const GENRES = ["fantasy", "sf", "romance", "mystery", "horror", "literary", "web_novel"];
  const NAME_TYPES = ["character", "place", "magic", "tech", "org"];
  const NAME_STYLES = ["korean", "western", "fantasy", "japanese", "chinese"];

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">집필 보조</h1>

      <div className="mb-6">
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}
          className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-2 text-sm w-full">
          <option value="">프로젝트 선택 (일부 기능에 필요)</option>
          {projects.map((p) => (
            <option key={String(p.id)} value={String(p.id)}>{String(p.title)}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-4 mb-6">
        {/* Brainstorm */}
        <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
          <h3 className="font-semibold mb-3 flex items-center gap-2"><span>💡</span> 브레인스토밍</h3>
          <input value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="키워드 (쉼표 구분)"
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm mb-2" />
          <div className="flex gap-2">
            <select value={bsGenre} onChange={(e) => setBsGenre(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {GENRES.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
            <button onClick={brainstorm} disabled={running === "brainstorm"}
              className="px-3 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">
              {running === "brainstorm" ? "생성 중..." : "아이디어 생성"}
            </button>
          </div>
        </div>

        {/* Naming */}
        <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
          <h3 className="font-semibold mb-3 flex items-center gap-2"><span>🏷️</span> 이름 생성기</h3>
          <div className="flex gap-2 mb-2">
            <select value={nameType} onChange={(e) => setNameType(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {NAME_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <select value={nameStyle} onChange={(e) => setNameStyle(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {NAME_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <button onClick={naming} disabled={!projectId || running === "naming"}
            className="px-3 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">
            {running === "naming" ? "생성 중..." : "이름 생성"}
          </button>
        </div>

        {/* Unblock */}
        <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
          <h3 className="font-semibold mb-3 flex items-center gap-2"><span>🚀</span> 작가의 벽 돌파</h3>
          <div className="flex gap-2 mb-2">
            <input value={chapterNum} onChange={(e) => setChapterNum(e.target.value)} placeholder="챕터" type="number"
              className="w-20 bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm" />
            <input value={stuckPoint} onChange={(e) => setStuckPoint(e.target.value)} placeholder="막힌 지점 설명"
              className="flex-1 bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm" />
          </div>
          <button onClick={unblock} disabled={!projectId || running === "unblock"}
            className="px-3 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">
            {running === "unblock" ? "분석 중..." : "돌파 제안"}
          </button>
        </div>

        {/* Quick Tools */}
        <div className="bg-[var(--card)] rounded-xl p-4 border border-[var(--border)]">
          <h3 className="font-semibold mb-3 flex items-center gap-2"><span>⚡</span> 빠른 도구</h3>
          <div className="flex flex-wrap gap-2">
            <button onClick={summary} disabled={!projectId || running === "summary"}
              className="px-3 py-1.5 bg-emerald-600 text-white rounded text-sm disabled:opacity-50">
              {running === "summary" ? "..." : `챕터 ${chapterNum} 요약`}
            </button>
            <button onClick={timeline} disabled={!projectId || running === "timeline"}
              className="px-3 py-1.5 bg-purple-600 text-white rounded text-sm disabled:opacity-50">
              {running === "timeline" ? "..." : "타임라인 추출"}
            </button>
          </div>
        </div>
      </div>

      {/* Result */}
      {result && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
          <h3 className="font-semibold mb-3">결과</h3>
          <pre className="text-xs bg-[var(--surface)] rounded p-4 max-h-96 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
