"use client";

import { useState, useEffect } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface GameTenant {
  name: string;
  api_key: string;
  game_id?: string;
  templates_count?: number;
  shared_count?: number;
  heroines_count?: number;
}

export default function Page() {
  const [tenants, setTenants] = useState<GameTenant[]>([]);
  const [stats, setStats] = useState({ games: 0, npcs: 0, shared: 0, heroines: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const data = await gpuFetch("/api/game/tenants");
        const list: GameTenant[] = data.tenants || data || [];
        setTenants(list);

        let npcs = 0, shared = 0, heroines = 0;
        for (const t of list) {
          try {
            const h = gameHeaders(t.api_key);
            const [tpl, sh, hr] = await Promise.all([
              gpuFetch("/api/game/npc/templates", { headers: h }).catch(() => ({ templates: [] })),
              gpuFetch("/api/game/npc/shared", { headers: h }).catch(() => ({ templates: [] })),
              gpuFetch("/api/game/heroine/list", { headers: h }).catch(() => ({ heroines: [] })),
            ]);
            const nc = (tpl.templates || tpl || []).length;
            const sc = (sh.templates || sh || []).length;
            const hc = (hr.heroines || hr || []).length;
            t.templates_count = nc;
            t.shared_count = sc;
            t.heroines_count = hc;
            npcs += nc; shared += sc; heroines += hc;
          } catch { /* skip */ }
        }
        setStats({ games: list.length, npcs, shared, heroines });
      } catch {
        setError("데이터 로딩 실패");
      }
      setLoading(false);
    };
    load();
  }, []);

  const statCards = [
    { label: "등록 게임", value: stats.games, color: "text-blue-400" },
    { label: "NPC 템플릿", value: stats.npcs, color: "text-green-400" },
    { label: "공유 NPC", value: stats.shared, color: "text-yellow-400" },
    { label: "히로인", value: stats.heroines, color: "text-pink-400" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">게임 대시보드</h1>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {loading ? (
        <p className="text-[var(--muted)]">로딩 중...</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            {statCards.map((s) => (
              <div key={s.label} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 text-center">
                <div className={`text-3xl font-bold ${s.color}`}>{s.value}</div>
                <div className="text-sm text-[var(--muted)] mt-1">{s.label}</div>
              </div>
            ))}
          </div>

          <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
            <h2 className="text-lg font-semibold mb-4">등록된 게임</h2>
            {tenants.length === 0 ? (
              <p className="text-[var(--muted)] text-sm">등록된 게임이 없습니다.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {tenants.map((t, i) => (
                  <div key={i} className="bg-[var(--bg)] border border-[var(--border)] rounded-lg p-4">
                    <h3 className="font-semibold text-[var(--text)] mb-2">{t.name}</h3>
                    <div className="text-xs font-mono text-[var(--muted)] mb-3 truncate">Key: {t.api_key}</div>
                    <div className="grid grid-cols-3 gap-2 text-center text-sm">
                      <div><div className="font-bold text-green-400">{t.templates_count ?? 0}</div><div className="text-[var(--muted)] text-xs">NPC</div></div>
                      <div><div className="font-bold text-yellow-400">{t.shared_count ?? 0}</div><div className="text-[var(--muted)] text-xs">공유</div></div>
                      <div><div className="font-bold text-pink-400">{t.heroines_count ?? 0}</div><div className="text-[var(--muted)] text-xs">히로인</div></div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
