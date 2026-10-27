"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface NPC {
  template_id: string;
  name: string;
}

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [npcs, setNpcs] = useState<NPC[]>([]);
  const [type, setType] = useState<"character" | "scene">("character");

  // Character fields
  const [npcId, setNpcId] = useState("");
  const [pose, setPose] = useState("standing");
  const [charStyle, setCharStyle] = useState("anime");

  // Scene fields
  const [description, setDescription] = useState("");
  const [mood, setMood] = useState("peaceful");
  const [sceneStyle, setSceneStyle] = useState("fantasy");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ job_id?: string; prompt_used?: string } | null>(null);

  const init = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      const list = data.tenants || data || [];
      if (list.length > 0) {
        const key = list[0].api_key;
        setGameKey(key);
        const npcData = await gpuFetch("/api/game/npc/templates", { headers: gameHeaders(key) });
        const npcList = npcData.templates || npcData || [];
        setNpcs(npcList);
        if (npcList.length > 0) setNpcId(npcList[0].template_id);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { init(); }, [init]);

  const generate = async () => {
    if (!gameKey) { setError("Game Key가 없습니다."); return; }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      let data;
      if (type === "character") {
        data = await gpuFetch("/api/game/illustrate/character", {
          method: "POST",
          body: { npc_id: npcId, pose, style: charStyle },
          headers: gameHeaders(gameKey),
        });
      } else {
        data = await gpuFetch("/api/game/illustrate/scene", {
          method: "POST",
          body: { description, mood, style: sceneStyle },
          headers: gameHeaders(gameKey),
        });
      }
      setResult(data);
    } catch {
      setError("일러스트 생성 실패");
    }
    setLoading(false);
  };

  const poses = ["standing", "fighting", "sitting", "casting", "running", "portrait"];
  const styles = ["anime", "realistic", "pixel", "watercolor", "comic", "chibi"];
  const moods = ["peaceful", "dark", "epic", "mysterious", "romantic", "chaotic"];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">일러스트 생성</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <div className="flex gap-3 mb-6">
          <button onClick={() => setType("character")}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition ${type === "character" ? "bg-[var(--accent)] text-white" : "bg-[var(--bg)] text-[var(--muted)] border border-[var(--border)]"}`}>
            캐릭터
          </button>
          <button onClick={() => setType("scene")}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition ${type === "scene" ? "bg-[var(--accent)] text-white" : "bg-[var(--bg)] text-[var(--muted)] border border-[var(--border)]"}`}>
            씬
          </button>
        </div>

        {type === "character" ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">NPC</label>
              <select value={npcId} onChange={(e) => setNpcId(e.target.value)}
                className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
                {npcs.map((n) => <option key={n.template_id} value={n.template_id}>{n.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">포즈</label>
              <select value={pose} onChange={(e) => setPose(e.target.value)}
                className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
                {poses.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">스타일</label>
              <select value={charStyle} onChange={(e) => setCharStyle(e.target.value)}
                className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
                {styles.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 mb-4">
            <div>
              <label className="block text-xs text-[var(--muted)] mb-1">씬 설명</label>
              <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                placeholder="장면을 설명하세요..." rows={3}
                className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm resize-none focus:outline-none focus:border-[var(--accent)]" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-[var(--muted)] mb-1">분위기</label>
                <select value={mood} onChange={(e) => setMood(e.target.value)}
                  className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
                  {moods.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[var(--muted)] mb-1">스타일</label>
                <select value={sceneStyle} onChange={(e) => setSceneStyle(e.target.value)}
                  className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
                  {styles.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
          </div>
        )}

        <button onClick={generate} disabled={loading}
          className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
          {loading ? "생성 중..." : "일러스트 생성"}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {result && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">생성 결과</h2>
          {result.job_id && (
            <div className="mb-3">
              <span className="text-sm text-[var(--muted)]">Job ID: </span>
              <span className="font-mono text-sm text-[var(--accent)]">{result.job_id}</span>
            </div>
          )}
          {result.prompt_used && (
            <div>
              <span className="text-sm text-[var(--muted)] block mb-2">사용된 프롬프트:</span>
              <pre className="text-sm text-[var(--text)] whitespace-pre-wrap leading-relaxed bg-[var(--bg)] p-4 rounded-lg">{result.prompt_used}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
