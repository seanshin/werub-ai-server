"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

export default function Page() {
  const [tab, setTab] = useState<"worlds" | "characters" | "chains">("worlds");
  const [worlds, setWorlds] = useState<Record<string, unknown>[]>([]);
  const [characters, setCharacters] = useState<Record<string, unknown>[]>([]);
  const [chains, setChains] = useState<Record<string, unknown>[]>([]);

  // Create world form
  const [worldName, setWorldName] = useState("");
  const [worldEra, setWorldEra] = useState("");
  const [worldGeo, setWorldGeo] = useState("");
  const [creating, setCreating] = useState(false);

  // Create character form
  const [charName, setCharName] = useState("");
  const [charTier, setCharTier] = useState("main");
  const [charType, setCharType] = useState("protagonist");
  const [charTraits, setCharTraits] = useState("");

  useEffect(() => {
    if (tab === "characters") {
      gpuFetch("/api/story/characters").then((d) => setCharacters(d.characters || []));
    } else if (tab === "chains") {
      gpuFetch("/api/story/chains").then((d) => setChains(d.chains || []));
    }
  }, [tab]);

  const createWorld = async () => {
    if (!worldName.trim()) return;
    setCreating(true);
    await gpuFetch("/api/story/world/create", {
      method: "POST",
      body: { name: worldName, era: worldEra, geography: worldGeo },
    });
    setWorldName(""); setWorldEra(""); setWorldGeo("");
    setCreating(false);
  };

  const createCharacter = async () => {
    if (!charName.trim()) return;
    setCreating(true);
    await gpuFetch("/api/story/character/create", {
      method: "POST",
      body: { name: charName, tier: charTier, type: charType, profile: { traits: charTraits } },
    });
    setCharName(""); setCharTraits("");
    gpuFetch("/api/story/characters").then((d) => setCharacters(d.characters || []));
    setCreating(false);
  };

  const tierColors: Record<string, string> = {
    main: "bg-amber-500/20 text-amber-400",
    supporting: "bg-blue-500/20 text-blue-400",
    minor: "bg-gray-500/20 text-gray-400",
    deceased: "bg-red-500/20 text-red-400",
    hidden: "bg-purple-500/20 text-purple-400",
  };

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">세계관 / 인물</h1>

      <div className="flex gap-2 mb-6">
        {([["worlds", "세계관"], ["characters", "인물"], ["chains", "시리즈"]] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === id ? "bg-[var(--accent)] text-white" : "bg-[var(--surface)] text-[var(--muted)]"
            }`}>{label}</button>
        ))}
      </div>

      {tab === "worlds" && (
        <div className="space-y-4">
          <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
            <h3 className="text-sm font-bold mb-3">세계관 등록</h3>
            <div className="grid grid-cols-3 gap-3 mb-3">
              <input value={worldName} onChange={(e) => setWorldName(e.target.value)} placeholder="세계 이름"
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
              <input value={worldEra} onChange={(e) => setWorldEra(e.target.value)} placeholder="시대 (예: 중세 판타지)"
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
              <input value={worldGeo} onChange={(e) => setWorldGeo(e.target.value)} placeholder="지리"
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            </div>
            <button onClick={createWorld} disabled={creating || !worldName.trim()}
              className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">등록</button>
          </div>
        </div>
      )}

      {tab === "characters" && (
        <div className="space-y-4">
          <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
            <h3 className="text-sm font-bold mb-3">인물 등록</h3>
            <div className="grid grid-cols-4 gap-3 mb-3">
              <input value={charName} onChange={(e) => setCharName(e.target.value)} placeholder="이름"
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
              <select value={charTier} onChange={(e) => setCharTier(e.target.value)}
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
                <option value="main">주요</option><option value="supporting">조연</option>
                <option value="minor">단역</option><option value="deceased">사망</option>
              </select>
              <select value={charType} onChange={(e) => setCharType(e.target.value)}
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm">
                <option value="protagonist">주인공</option><option value="heroine">히로인</option>
                <option value="antagonist">빌런</option><option value="mentor">멘토</option>
                <option value="npc">NPC</option>
              </select>
              <input value={charTraits} onChange={(e) => setCharTraits(e.target.value)} placeholder="성격 특성"
                className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1.5 text-sm" />
            </div>
            <button onClick={createCharacter} disabled={creating || !charName.trim()}
              className="px-4 py-1.5 bg-[var(--accent)] text-white rounded text-sm disabled:opacity-50">등록</button>
          </div>

          <div className="space-y-2">
            {characters.map((c, i) => (
              <div key={i} className="bg-[var(--card)] rounded-lg p-4 border border-[var(--border)] flex items-center gap-3">
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${tierColors[String(c.tier)] || "bg-gray-500/20"}`}>
                  {String(c.tier)}
                </span>
                <span className="font-medium text-sm">{String(c.name)}</span>
                <span className="text-xs text-[var(--muted)]">{String(c.type)}</span>
              </div>
            ))}
            {characters.length === 0 && <div className="text-sm text-[var(--muted)]">등록된 인물 없음</div>}
          </div>
        </div>
      )}

      {tab === "chains" && (
        <div className="space-y-2">
          {chains.map((c, i) => (
            <div key={i} className="bg-[var(--card)] rounded-lg p-4 border border-[var(--border)]">
              <div className="font-medium text-sm">{String(c.title || "무제")}</div>
              <div className="text-xs text-[var(--muted)]">
                에피소드: {String(c.episode_count)} | 총 {String(c.total_words)}자 | {String(c.style)}/{String(c.tone)}
              </div>
            </div>
          ))}
          {chains.length === 0 && <div className="text-sm text-[var(--muted)]">시리즈 없음 — 스토리 이어쓰기 시 자동 생성됩니다</div>}
        </div>
      )}
    </div>
  );
}
