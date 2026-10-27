"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

const GENRES = ["fantasy", "sf", "romance", "mystery", "horror", "literary", "web_novel"];

export default function Page() {
  const [projects, setProjects] = useState<Record<string, unknown>[]>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  // Create form
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("fantasy");
  const [logline, setLogline] = useState("");
  const [chapters, setChapters] = useState("12");
  const [showCreate, setShowCreate] = useState(false);

  const [running, setRunning] = useState(false);

  const loadProjects = () => {
    gpuFetch("/api/novel/projects").then((d) => {
      setProjects(d.projects || []);
      setLoading(false);
    });
  };

  useEffect(() => { loadProjects(); }, []);

  const loadProject = (id: string) => {
    gpuFetch(`/api/novel/project/${id}`).then((d) => setSelected(d));
  };

  const createProject = async () => {
    if (!title.trim()) return;
    setRunning(true);
    const data = await gpuFetch("/api/novel/project/create", {
      method: "POST",
      body: { title, genre, logline, estimated_chapters: parseInt(chapters) },
    });
    if (data.project_id) {
      loadProjects();
      loadProject(data.project_id);
      setShowCreate(false);
      setTitle(""); setLogline("");
    }
    setRunning(false);
  };

  const generateSynopsis = async () => {
    if (!selected) return;
    setRunning(true);
    await gpuFetch("/api/novel/synopsis/generate", {
      method: "POST",
      body: { project_id: selected.id, depth: "standard" },
    });
    loadProject(String(selected.id));
    setRunning(false);
  };

  const generatePlot = async () => {
    if (!selected) return;
    setRunning(true);
    await gpuFetch("/api/novel/plot/generate", {
      method: "POST",
      body: { project_id: selected.id, structure: "three_act" },
    });
    loadProject(String(selected.id));
    setRunning(false);
  };

  const generateOutline = async () => {
    if (!selected) return;
    setRunning(true);
    await gpuFetch("/api/novel/outline/generate", {
      method: "POST",
      body: { project_id: selected.id },
    });
    loadProject(String(selected.id));
    setRunning(false);
  };

  const writeChapter = async (chNum: number) => {
    if (!selected) return;
    setRunning(true);
    await gpuFetch("/api/novel/chapter/write", {
      method: "POST",
      body: { project_id: selected.id, chapter_num: chNum, quality: "quick" },
    });
    loadProject(String(selected.id));
    setRunning(false);
  };

  const statusColors: Record<string, string> = {
    planning: "text-blue-400",
    writing: "text-amber-400",
    revising: "text-purple-400",
    completed: "text-emerald-400",
  };

  const chStatusColors: Record<string, string> = {
    outline: "bg-gray-500/20 text-gray-400",
    draft: "bg-amber-500/20 text-amber-400",
    revised: "bg-purple-500/20 text-purple-400",
    final: "bg-emerald-500/20 text-emerald-400",
  };

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">소설 프로젝트</h1>
        <button onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm">
          + 새 프로젝트
        </button>
      </div>

      {showCreate && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
          <div className="grid grid-cols-3 gap-3 mb-3">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="소설 제목"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            <select value={genre} onChange={(e) => setGenre(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
              {GENRES.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
            <input value={chapters} onChange={(e) => setChapters(e.target.value)} placeholder="챕터 수" type="number"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
          </div>
          <input value={logline} onChange={(e) => setLogline(e.target.value)} placeholder="로그라인 (한 줄 핵심)"
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm mb-3" />
          <button onClick={createProject} disabled={running || !title.trim()}
            className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">생성</button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        {/* Project List */}
        <div className="space-y-2">
          {loading ? <div className="text-[var(--muted)] text-sm">로딩 중...</div> :
            projects.map((p) => (
              <button key={String(p.id)} onClick={() => loadProject(String(p.id))}
                className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                  selected?.id === p.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                  : "border-[var(--border)] bg-[var(--card)]"
                }`}>
                <div className="font-medium">{String(p.title)}</div>
                <div className="text-xs text-[var(--muted)]">
                  {String(p.genre)} | <span className={statusColors[String(p.status)] || ""}>{String(p.status)}</span> | {String(p.completed_chapters || 0)}/{String(p.total_chapters)}ch
                </div>
              </button>
            ))}
          {!loading && projects.length === 0 && <div className="text-sm text-[var(--muted)]">프로젝트 없음</div>}
        </div>

        {/* Project Detail */}
        <div className="col-span-2">
          {selected ? (
            <div className="space-y-4">
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h3 className="font-bold text-lg mb-1">{String(selected.title)}</h3>
                <div className="text-sm text-[var(--muted)] mb-3">
                  {String(selected.genre)} | {String(selected.total_words || 0)}자 |
                  <span className={`ml-1 ${statusColors[String(selected.status)] || ""}`}>{String(selected.status)}</span>
                </div>
                {Boolean(selected.logline) && <div className="text-sm italic text-[var(--muted)]">{String(selected.logline)}</div>}

                {/* Action buttons */}
                <div className="flex gap-2 mt-4">
                  <button onClick={generateSynopsis} disabled={running}
                    className="px-3 py-1.5 bg-[var(--surface)] border border-[var(--border)] rounded text-xs hover:border-[var(--accent)] disabled:opacity-50">
                    {running ? "..." : "시놉시스 생성"}
                  </button>
                  <button onClick={generatePlot} disabled={running}
                    className="px-3 py-1.5 bg-[var(--surface)] border border-[var(--border)] rounded text-xs hover:border-[var(--accent)] disabled:opacity-50">
                    플롯 설계
                  </button>
                  <button onClick={generateOutline} disabled={running}
                    className="px-3 py-1.5 bg-[var(--surface)] border border-[var(--border)] rounded text-xs hover:border-[var(--accent)] disabled:opacity-50">
                    아웃라인 생성
                  </button>
                </div>
              </div>

              {/* Chapters */}
              {(selected.chapters as Array<Record<string, unknown>>)?.length > 0 && (
                <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                  <h4 className="text-sm font-bold mb-3">챕터</h4>
                  <div className="space-y-2">
                    {(selected.chapters as Array<Record<string, unknown>>).map((ch) => (
                      <div key={String(ch.chapter_num)} className="flex items-center gap-3 bg-[var(--surface)] rounded-lg p-3">
                        <span className="text-sm font-bold w-8">#{String(ch.chapter_num)}</span>
                        <span className="text-sm flex-1">{String(ch.title || `Chapter ${ch.chapter_num}`)}</span>
                        <span className={`text-[10px] px-2 py-0.5 rounded-full ${chStatusColors[String(ch.status)] || ""}`}>
                          {String(ch.status)}
                        </span>
                        <span className="text-xs text-[var(--muted)]">{String(ch.word_count || 0)}자</span>
                        {ch.status === "outline" && (
                          <button onClick={() => writeChapter(Number(ch.chapter_num))} disabled={running}
                            className="px-2 py-1 bg-[var(--accent)] text-white rounded text-xs disabled:opacity-50">
                            집필
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-[var(--muted)] text-center mt-20">왼쪽에서 프로젝트를 선택하세요</div>
          )}
        </div>
      </div>
    </div>
  );
}
