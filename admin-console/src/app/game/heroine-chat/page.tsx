"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";
import ChatBox from "@/components/ChatBox";

interface HeroineOption { heroine_id: string; id?: string; name: string; }
interface AffinityInfo { value?: number; stage?: string; change?: number; trust?: number; }

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [heroines, setHeroines] = useState<HeroineOption[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [playerId, setPlayerId] = useState("player_1");
  const [playerName, setPlayerName] = useState("주인공");
  const [affinity, setAffinity] = useState<AffinityInfo | null>(null);
  const [giftName, setGiftName] = useState("");
  const [actionResult, setActionResult] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchKey = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      if (data.tenants?.length) { setGameKey(data.tenants[0].api_key); return data.tenants[0].api_key; }
    } catch {}
    setLoading(false);
    return "";
  }, []);

  useEffect(() => {
    fetchKey().then(async (k) => {
      if (!k) return;
      try {
        const data = await gpuFetch("/api/game/heroine/list", { headers: gameHeaders(k) });
        const list = (data.heroines || []).map((h: HeroineOption) => ({ ...h, heroine_id: h.heroine_id || h.id || "" }));
        setHeroines(list);
        if (list.length > 0) setSelectedId(list[0].heroine_id);
      } catch { setError("히로인 목록 로딩 실패"); }
      setLoading(false);
    });
  }, [fetchKey]);

  const handleSend = async (message: string) => {
    const data = await gpuFetch("/api/game/heroine/talk", {
      method: "POST",
      body: { player_id: playerId, heroine_id: selectedId, message, player_name: playerName },
      headers: gameHeaders(gameKey),
    });
    if (data.affinity) setAffinity({ value: data.affinity.value, stage: data.affinity.stage, change: data.affinity.change, trust: data.trust });

    const meta: string[] = [];
    if (data.affinity?.value != null) meta.push(`♥ ${data.affinity.value} (${data.affinity.stage || ""})`);
    if (data.affinity?.change != null) meta.push(`${data.affinity.change > 0 ? "+" : ""}${data.affinity.change}`);
    if (data.trust != null) meta.push(`신뢰: ${data.trust}`);
    if (data.stage_change) meta.push(`🎉 ${data.stage_change.from} → ${data.stage_change.to}`);

    return { response: data.response || "", meta: meta.join(" | ") };
  };

  const sendGift = async () => {
    if (!giftName.trim() || !selectedId) return;
    setActionResult("⏳ 반응 생성 중...");
    try {
      const data = await gpuFetch("/api/game/heroine/gift", {
        method: "POST",
        body: { player_id: playerId, heroine_id: selectedId, gift: giftName.trim(), player_name: playerName },
        headers: gameHeaders(gameKey),
      });
      const liked = data.liked ? "❤️ 좋아함" : "💔 별로";
      setActionResult(`🎁 ${data.heroine || "?"}: ${data.reaction || ""}\n\n${liked} | 호감도: ${data.affinity?.after ?? "?"} (${data.affinity?.stage || ""}) ${data.affinity?.change > 0 ? "+" : ""}${data.affinity?.change || 0}`);
      if (data.affinity) setAffinity({ value: data.affinity.after, stage: data.affinity.stage, change: data.affinity.change });
      setGiftName("");
    } catch { setActionResult("선물 전달 실패"); }
  };

  const confess = async () => {
    if (!selectedId) return;
    const msg = prompt("고백 메시지:", "좋아해");
    if (!msg) return;
    setActionResult("⏳ 고백 중...");
    try {
      const data = await gpuFetch("/api/game/heroine/confess", {
        method: "POST",
        body: { player_id: playerId, heroine_id: selectedId, confession_message: msg, player_name: playerName },
        headers: gameHeaders(gameKey),
      });
      const emoji = { accept: "💕 수락!", pending: "🤔 보류...", reject_gentle: "😢 거절", reject_hard: "💔 거절" }[data.result as string] || data.result;
      setActionResult(`💌 ${data.heroine || "?"}: ${data.response || ""}\n\n결과: ${emoji} | 호감도: ${data.affinity?.after ?? "?"} (${data.affinity?.stage || ""})`);
      if (data.affinity) setAffinity({ value: data.affinity.after, stage: data.affinity.stage });
    } catch { setActionResult("고백 실패"); }
  };

  if (loading) return <p className="text-[var(--muted)]">로딩 중...</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">💗 히로인 대화</h1>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {/* 설정 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">히로인 선택</label>
            <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
              {heroines.map((h) => <option key={h.heroine_id} value={h.heroine_id}>{h.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Player ID</label>
            <input value={playerId} onChange={(e) => setPlayerId(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm" />
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">이름</label>
            <input value={playerName} onChange={(e) => setPlayerName(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm" />
          </div>
        </div>
      </div>

      {/* 호감도 카드 */}
      {affinity && (
        <div className="grid grid-cols-4 gap-3 mb-4">
          <div className="bg-[var(--card)] border border-pink-500/30 rounded-xl p-3 text-center">
            <div className="text-xl font-bold text-pink-400">{affinity.value ?? "-"}</div>
            <div className="text-xs text-[var(--muted)]">호감도</div>
          </div>
          <div className="bg-[var(--card)] border border-purple-500/30 rounded-xl p-3 text-center">
            <div className="text-sm font-bold text-purple-400">{affinity.stage || "-"}</div>
            <div className="text-xs text-[var(--muted)]">단계</div>
          </div>
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-3 text-center">
            <div className={`text-xl font-bold ${(affinity.change || 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
              {(affinity.change || 0) > 0 ? "+" : ""}{affinity.change ?? "-"}
            </div>
            <div className="text-xs text-[var(--muted)]">변화</div>
          </div>
          <div className="bg-[var(--card)] border border-blue-500/30 rounded-xl p-3 text-center">
            <div className="text-xl font-bold text-blue-400">{affinity.trust ?? "-"}</div>
            <div className="text-xs text-[var(--muted)]">신뢰</div>
          </div>
        </div>
      )}

      {/* 대화 (메인) */}
      {selectedId && gameKey && (
        <ChatBox onSend={handleSend} placeholder="히로인에게 말하기..." systemMessage={`${heroines.find(h=>h.heroine_id===selectedId)?.name || "히로인"}과(와) 대화를 시작하세요.`} />
      )}

      {/* 선물/고백 */}
      <div className="flex gap-3 mt-4">
        <input value={giftName} onChange={(e) => setGiftName(e.target.value)} placeholder="선물 이름 (딸기 케이크, 꽃다발 등)"
          className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm" />
        <button onClick={sendGift} disabled={!giftName.trim()}
          className="px-5 py-3 bg-yellow-600 text-white rounded-lg text-sm font-medium disabled:opacity-50">🎁 선물</button>
        <button onClick={confess}
          className="px-5 py-3 bg-pink-600 text-white rounded-lg text-sm font-medium">💌 고백</button>
      </div>

      {actionResult && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 mt-4 text-sm text-[var(--text)] whitespace-pre-wrap">{actionResult}</div>
      )}
    </div>
  );
}
