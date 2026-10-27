"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

const GENRES = ["fantasy", "sf", "romance", "mystery", "horror", "literary", "web_novel"];

export default function Page() {
  const [sources, setSources] = useState<Record<string, unknown>[]>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [components, setComponents] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  // Upload form
  const [showUpload, setShowUpload] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [genre, setGenre] = useState("literary");
  const [text, setText] = useState("");

  const loadSources = () => {
    gpuFetch("/api/novel/decompose/sources").then((d) => {
      setSources(d.sources || []);
      setLoading(false);
    });
  };

  useEffect(() => { loadSources(); }, []);

  const loadSource = (id: string) => {
    gpuFetch(`/api/novel/decompose/source/${id}`).then((d) => setSelected(d));
    gpuFetch(`/api/novel/components/source/${id}`).then((d) => setComponents(d.components || []));
  };

  const upload = async () => {
    if (!title.trim() || !text.trim()) return;
    setRunning(true);
    const d = await gpuFetch("/api/novel/decompose/upload", {
      method: "POST",
      body: { title, author, genre, text },
    });
    if (d.source_id) {
      loadSources();
      loadSource(d.source_id);
      setShowUpload(false);
      setTitle(""); setAuthor(""); setText("");
    }
    setRunning(false);
  };

  const runDecompose = async (id: string) => {
    setRunning(true);
    const d = await gpuFetch(`/api/novel/decompose/run/${id}`, {
      method: "POST",
      body: { depth: "full" },
    });
    if (d.status === "completed") {
      loadSource(id);
    }
    setRunning(false);
  };

  const deleteSource = async (id: string) => {
    await gpuFetch(`/api/novel/decompose/source/${id}`, { method: "DELETE" });
    setSelected(null);
    setComponents([]);
    loadSources();
  };

  const statusColors: Record<string, string> = {
    uploaded: "text-blue-400",
    running: "text-amber-400",
    completed: "text-emerald-400",
    failed: "text-red-400",
  };

  const typeIcons: Record<string, string> = {
    character: "👤", worldbuilding: "🌍", plot_point: "📌", foreshadowing: "🔮",
    key_scene: "🎬", dialogue_pattern: "💬", writing_style: "✒️", theme: "💡",
  };

  return (
    <div className="max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">소설 분해기</h1>
        <button onClick={() => setShowUpload(!showUpload)}
          className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm">
          + 소설 등록
        </button>
      </div>

      {showUpload && (
        <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
          <div className="grid grid-cols-3 gap-3 mb-3">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="소설 제목"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="저자"
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            <select value={genre} onChange={(e) => setGenre(e.target.value)}
              className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
              {GENRES.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="소설 전체 텍스트를 붙여넣으세요..."
            rows={8} className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-2 text-sm mb-3 font-mono" />
          <div className="flex gap-2">
            <button onClick={upload} disabled={running || !title.trim() || !text.trim()}
              className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">등록</button>
            <span className="text-xs text-[var(--muted)] self-center">{text.length.toLocaleString()}자</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-4 gap-6">
        {/* Source List */}
        <div className="space-y-2">
          {loading ? <div className="text-[var(--muted)] text-sm">로딩 중...</div> :
            sources.map((s) => (
              <button key={String(s.id)} onClick={() => loadSource(String(s.id))}
                className={`w-full text-left p-3 rounded-lg text-sm border transition-colors ${
                  selected?.id === s.id ? "border-[var(--accent)] bg-[rgba(99,102,241,0.1)]"
                  : "border-[var(--border)] bg-[var(--card)]"
                }`}>
                <div className="font-medium">{String(s.title)}</div>
                <div className="text-xs text-[var(--muted)]">
                  {String(s.genre)} | {Number(s.word_count).toLocaleString()}자
                </div>
                <span className={`text-xs ${statusColors[String(s.decompose_status)] || ""}`}>
                  {String(s.decompose_status)}
                </span>
              </button>
            ))}
        </div>

        {/* Detail + Components */}
        <div className="col-span-3">
          {selected ? (
            <div className="space-y-4">
              <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold">{String(selected.title)}</h2>
                  <div className="flex gap-2">
                    {selected.decompose_status === "uploaded" && (
                      <button onClick={() => runDecompose(String(selected.id))} disabled={running}
                        className="px-3 py-1.5 bg-emerald-600 text-white rounded text-sm disabled:opacity-50">
                        {running ? "분해 중..." : "분해 실행"}
                      </button>
                    )}
                    <button onClick={() => deleteSource(String(selected.id))}
                      className="px-3 py-1.5 bg-red-600/20 text-red-400 rounded text-sm">삭제</button>
                  </div>
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {selected.author && <span>{String(selected.author)} | </span>}
                  {String(selected.genre)} | {Number(selected.word_count).toLocaleString()}자
                  {selected.components_count !== undefined && <span> | 컴포넌트 {Number(selected.components_count)}개</span>}
                </div>
                {selected.text_preview && (
                  <div className="mt-3 text-xs text-[var(--muted)] bg-[var(--surface)] rounded p-3 max-h-32 overflow-auto">
                    {String(selected.text_preview)}
                  </div>
                )}
              </div>

              {/* Components */}
              {components.length > 0 && (
                <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
                  <h3 className="font-semibold mb-3">추출된 컴포넌트 ({components.length}개)</h3>
                  <div className="grid grid-cols-2 gap-2">
                    {components.map((c) => (
                      <div key={String(c.id)} className="p-3 bg-[var(--surface)] rounded-lg text-sm">
                        <div className="flex items-center gap-2 mb-1">
                          <span>{typeIcons[String(c.type)] || "📄"}</span>
                          <span className="font-medium">{String(c.name || c.type)}</span>
                          <span className="text-xs px-1.5 py-0.5 bg-[var(--border)] rounded">{String(c.type)}</span>
                        </div>
                        {c.data && typeof c.data === "object" && (
                          <div className="text-xs text-[var(--muted)] truncate">
                            {JSON.stringify(c.data).slice(0, 120)}...
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center text-[var(--muted)] py-20">
              소설을 등록하고 분해하여 컴포넌트를 추출하세요
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
