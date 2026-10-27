"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface Objective {
  description: string;
  exp?: number;
}

interface Branch {
  description: string;
  risk?: string;
}

interface Scenario {
  title: string;
  synopsis: string;
  objectives?: Objective[];
  branches?: Branch[];
}

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [theme, setTheme] = useState("");
  const [difficulty, setDifficulty] = useState("normal");
  const [playerId, setPlayerId] = useState("player_1");
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchKey = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      const list = data.tenants || data || [];
      if (list.length > 0) setGameKey(list[0].api_key);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchKey(); }, [fetchKey]);

  const generate = async () => {
    if (!theme.trim()) { setError("테마를 입력하세요."); return; }
    if (!gameKey) { setError("Game Key가 없습니다."); return; }
    setLoading(true);
    setError("");
    setScenario(null);
    try {
      const data = await gpuFetch("/api/game/scenario/generate", {
        method: "POST",
        body: { theme: theme.trim(), difficulty, player_id: playerId },
        headers: gameHeaders(gameKey),
      });
      setScenario(data.scenario || data);
    } catch {
      setError("시나리오 생성 실패");
    }
    setLoading(false);
  };

  const riskColor = (risk?: string) => {
    if (!risk) return "bg-[var(--surface)] text-[var(--muted)]";
    const r = risk.toLowerCase();
    if (r === "high" || r === "높음") return "bg-red-500/20 text-red-400";
    if (r === "medium" || r === "보통") return "bg-yellow-500/20 text-yellow-400";
    return "bg-green-500/20 text-green-400";
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">시나리오 생성</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">테마</label>
            <input value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="예: 용의 둥지, 해적 섬"
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">난이도</label>
            <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
              <option value="easy">Easy</option>
              <option value="normal">Normal</option>
              <option value="hard">Hard</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">Player ID</label>
            <input value={playerId} onChange={(e) => setPlayerId(e.target.value)}
              className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          </div>
        </div>
        <button onClick={generate} disabled={loading || !theme.trim()}
          className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
          {loading ? "생성 중..." : "시나리오 생성"}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {scenario && (
        <div className="flex flex-col gap-6">
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
            <h2 className="text-xl font-bold text-[var(--text)] mb-3">{scenario.title}</h2>
            <p className="text-sm text-[var(--muted)] leading-relaxed whitespace-pre-wrap">{scenario.synopsis}</p>
          </div>

          {scenario.objectives && scenario.objectives.length > 0 && (
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h3 className="text-lg font-semibold mb-4">목표</h3>
              <div className="flex flex-col gap-3">
                {scenario.objectives.map((obj, i) => (
                  <div key={i} className="flex items-center justify-between bg-[var(--bg)] rounded-lg p-4">
                    <span className="text-sm text-[var(--text)]">{obj.description}</span>
                    {obj.exp != null && <span className="text-xs px-2 py-1 rounded-full bg-green-500/20 text-green-400 shrink-0 ml-3">+{obj.exp} EXP</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {scenario.branches && scenario.branches.length > 0 && (
            <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
              <h3 className="text-lg font-semibold mb-4">분기</h3>
              <div className="flex flex-col gap-3">
                {scenario.branches.map((b, i) => (
                  <div key={i} className="flex items-center justify-between bg-[var(--bg)] rounded-lg p-4">
                    <span className="text-sm text-[var(--text)]">{b.description}</span>
                    {b.risk && <span className={`text-xs px-2 py-1 rounded-full shrink-0 ml-3 ${riskColor(b.risk)}`}>{b.risk}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
