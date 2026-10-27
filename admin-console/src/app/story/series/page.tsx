"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

export default function Page() {
  const [seriesList, setSeriesList] = useState<Record<string, unknown>[]>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [recap, setRecap] = useState("");

  // Create
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");

  const loadList = () => {
    gpuFetch("/api/novel/series").then((d) => {
      setSeriesList(d.series || []);
      setLoading(false);
    });
  };

  useEffect(() => { loadList(); }, []);

  const loadSeries = (id: string) => {
    gpuFetch(`/api/novel/series/${id}`).then((d) => { setSelected(d); setRecap(""); });
  };

  const create = async () => {
    if (!title.trim()) return;
    setRunning(true);
    const d = await gpuFetch("/api/novel/series/create", {
      method: "POST",
      body: { title, description: desc },
    });
    if (d.series_id) {
      loadList();
      loadSeries(d.series_id);
      setShowCreate(false);
      setTitle(""); setDesc("");
    }
    setRunning(false);
  };

  const addVolume = async (projectId: string) => {
    if (!selected) return;
    await gpuFetch(`/api/novel/series/${selected.id}/add`, {
      method: "PUT",
      body: { project_id: projectId },
    });
    loadSeries(String(selected.id));
  };

  const generateRecap = async () => {
    if (!selected) return;
    setRunning(true);
    const d = await gpuFetch("/api/novel/series/recap", {
      method: "POST",
      body: { series_id: selected.id, style: "narrator" },
    });
    setRecap(d.recap || d.error || "");
    setRunning(false);
  };

  const checkContinuity = async () => {
    if (!selected) return;
    setRunning(true);
    const d = await gpuFetch("/api/novel/series/continuity", {
      method: "POST",
      body: { series_id: selected.id },
    });
    setRecap(JSON.stringify(d, null, 2));
    setRunning(false);
  };

  const progress = selected?.progress as Record<string, number> | undefined;

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">시리즈 관리</h1>
        <button onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm">+ 새 시리즈</button>
      </div>

      {showCreate && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="시리즈 제목"
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm mb-2" />
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="설명"
            className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm mb-3" />
          <button onClick={create} disabled={running || !title.trim()}
            className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">생성</button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        <div className="space-y-2">
          {loading ? <div className="text-[var(--muted)] text-sm">로딩 중...</div> :
            seriesList.map((s) => (
              <button key={String(s.id)} onClick={() => loadSeries(String(s.id))}
                className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                  selected?.id === s.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                  : "border-[var(--border)] bg-[var(--card)]"
                }`}>
                <div className="font-medium">{String(s.title)}</div>
                <div className="text-xs text-[var(--muted)]">{Number(s.total_volumes)}권 | {String(s.status)}</div>
              </button>
            ))}
        </div>

        <div className="col-span-2">
          {selected ? (
            <div className="space-y-4">
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h2 className="text-lg font-semibold mb-2">{String(selected.title)}</h2>
                {selected.description && <p className="text-sm text-[var(--muted)] mb-3">{String(selected.description)}</p>}
                {progress && (
                  <div className="flex gap-4 text-sm">
                    <span>총 {progress.total_volumes}권</span>
                    <span className="text-emerald-400">완료 {progress.completed_volumes}권</span>
                    <span>{(progress.total_words || 0).toLocaleString()}자</span>
                  </div>
                )}
              </div>

              {/* Volumes */}
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h3 className="font-semibold mb-3">볼륨</h3>
                {(selected.volumes as Record<string, unknown>[] || []).map((v: Record<string, unknown>) => (
                  <div key={String(v.project_id)} className="flex items-center justify-between p-2 border-b border-[var(--border)] text-sm">
                    <div>
                      <span className="font-medium">{Number(v.volume_num)}권</span>
                      <span className="ml-2">{String(v.title || "(제목 없음)")}</span>
                    </div>
                    <span className="text-xs text-[var(--muted)]">{String(v.status || "")}</span>
                  </div>
                ))}
              </div>

              {/* Actions */}
              <div className="flex gap-2">
                <button onClick={generateRecap} disabled={running}
                  className="px-3 py-1.5 bg-emerald-600 text-white rounded text-sm disabled:opacity-50">
                  {running ? "생성 중..." : "리캡 생성"}
                </button>
                <button onClick={checkContinuity} disabled={running}
                  className="px-3 py-1.5 bg-purple-600 text-white rounded text-sm disabled:opacity-50">
                  연속성 검증
                </button>
              </div>

              {recap && (
                <div className="bg-[var(--surface)] rounded-xl p-4 text-sm whitespace-pre-wrap max-h-96 overflow-auto">
                  {recap}
                </div>
              )}
            </div>
          ) : (
            <div className="text-center text-[var(--muted)] py-20">시리즈를 선택하세요</div>
          )}
        </div>
      </div>
    </div>
  );
}
