"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";
import ChatBox from "@/components/ChatBox";

interface NPC {
  template_id: string;
  name: string;
  class: string;
}

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [npcs, setNpcs] = useState<NPC[]>([]);
  const [selectedNpc, setSelectedNpc] = useState("");
  const [playerId, setPlayerId] = useState("player_1");
  const [playerName, setPlayerName] = useState("Player");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
        if (npcList.length > 0) setSelectedNpc(npcList[0].template_id);
      }
    } catch {
      setError("초기화 실패");
    }
    setLoading(false);
  }, []);

  useEffect(() => { init(); }, [init]);

  const handleSend = async (message: string) => {
    const data = await gpuFetch("/api/game/dialogue/player", {
      method: "POST",
      body: { player_id: playerId, npc_id: selectedNpc, message, player_name: playerName },
      headers: gameHeaders(gameKey),
    });

    const meta: string[] = [];
    if (data.exp_earned != null) meta.push(`EXP +${data.exp_earned}`);
    if (data.level != null) meta.push(`Lv.${data.level}`);
    if (data.level_up) meta.push("LEVEL UP!");

    return { response: data.response || data.message || "", meta: meta.join(" | ") };
  };

  if (loading) return <p className="text-[var(--muted)]">로딩 중...</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">NPC 대화</h1>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">NPC 선택</label>
            <select value={selectedNpc} onChange={(e) => setSelectedNpc(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
              {npcs.map((n) => <option key={n.template_id} value={n.template_id}>{n.name} ({n.class})</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Player ID</label>
            <input value={playerId} onChange={(e) => setPlayerId(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Player Name</label>
            <input value={playerName} onChange={(e) => setPlayerName(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          </div>
        </div>
      </div>

      {selectedNpc && gameKey && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
          <ChatBox onSend={handleSend} placeholder="NPC에게 말하기..." />
        </div>
      )}
    </div>
  );
}
