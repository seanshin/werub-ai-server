"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

const GENRES = ["fantasy", "sf", "romance", "mystery", "horror", "literary", "web_novel"];

export default function Page() {
  const [recipes, setRecipes] = useState<Record<string, unknown>[]>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [execResult, setExecResult] = useState<Record<string, unknown> | null>(null);

  // Create
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [genre, setGenre] = useState("sf");
  const [desc, setDesc] = useState("");

  // Execute
  const [execTitle, setExecTitle] = useState("");

  const loadRecipes = () => {
    gpuFetch("/api/novel/recipes").then((d) => {
      setRecipes(d.recipes || []);
      setLoading(false);
    });
  };

  useEffect(() => { loadRecipes(); }, []);

  const create = async () => {
    if (!name.trim()) return;
    setRunning(true);
    const d = await gpuFetch("/api/novel/recipe/create", {
      method: "POST",
      body: { name, genre, description: desc, component_slots: {}, default_settings: {
        estimated_chapters: 12, chapter_length: 5000, tone: "warm", pov: "third_person"
      }},
    });
    if (d.recipe_id) {
      loadRecipes();
      setShowCreate(false);
      setName(""); setDesc("");
    }
    setRunning(false);
  };

  const deleteRecipe = async (id: string) => {
    await gpuFetch(`/api/novel/recipe/${id}`, { method: "DELETE" });
    setSelected(null);
    loadRecipes();
  };

  const execute = async () => {
    if (!selected || !execTitle.trim()) return;
    setRunning(true);
    const d = await gpuFetch(`/api/novel/recipe/${selected.id}/execute`, {
      method: "POST",
      body: { title: execTitle },
    });
    setExecResult(d);
    setRunning(false);
  };

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">레시피</h1>
        <button onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm">+ 새 레시피</button>
      </div>

      {showCreate && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
          <div className="grid grid-cols-3 gap-3 mb-3">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="레시피 이름"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            <select value={genre} onChange={(e) => setGenre(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
              {GENRES.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
            <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="설명"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
          </div>
          <button onClick={create} disabled={running || !name.trim()}
            className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">생성</button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        <div className="space-y-2">
          {loading ? <div className="text-[var(--muted)] text-sm">로딩 중...</div> :
            recipes.map((r) => (
              <button key={String(r.id)} onClick={() => { setSelected(r); setExecResult(null); }}
                className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                  selected?.id === r.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                  : "border-[var(--border)] bg-[var(--card)]"
                }`}>
                <div className="font-medium">{String(r.name)}</div>
                <div className="text-xs text-[var(--muted)]">{String(r.genre)} | 사용 {Number(r.usage_count)}회</div>
              </button>
            ))}
        </div>

        <div className="col-span-2">
          {selected ? (
            <div className="space-y-4">
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-lg font-semibold">{String(selected.name)}</h2>
                  <button onClick={() => deleteRecipe(String(selected.id))}
                    className="px-3 py-1.5 bg-red-600/20 text-red-400 rounded text-sm">삭제</button>
                </div>
                <p className="text-sm text-[var(--muted)]">{String(selected.description || "")}</p>
                <div className="text-xs text-[var(--muted)] mt-2">장르: {String(selected.genre)} | 사용 {Number(selected.usage_count)}회</div>
              </div>

              {/* Execute */}
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <h3 className="font-semibold mb-3">레시피 실행</h3>
                <div className="flex gap-2">
                  <input value={execTitle} onChange={(e) => setExecTitle(e.target.value)} placeholder="새 소설 제목"
                    className="flex-1 bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
                  <button onClick={execute} disabled={running || !execTitle.trim()}
                    className="px-4 py-1.5 bg-emerald-600 text-white rounded text-sm disabled:opacity-50">
                    {running ? "생성 중..." : "실행"}
                  </button>
                </div>
              </div>

              {execResult && (
                <div className="bg-[var(--surface)] rounded-xl p-4 text-sm">
                  {execResult.error ? (
                    <div className="text-red-400">{String(execResult.error)}</div>
                  ) : (
                    <div>
                      <div className="text-emerald-400 font-semibold mb-2">프로젝트 생성 완료</div>
                      <div className="text-xs text-[var(--muted)]">
                        프로젝트: {String(execResult.project_id)}<br />
                        인물: {(execResult.characters as Record<string, unknown>[])?.length || 0}명<br />
                        아웃라인: {String(execResult.outline_chapters)}챕터
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="text-center text-[var(--muted)] py-20">레시피를 선택하세요</div>
          )}
        </div>
      </div>
    </div>
  );
}
