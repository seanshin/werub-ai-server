"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface NPC {
  id: string;
  name: string;
  class: string;
  base_level?: number;
  personality?: { traits?: string[]; speech_style?: string; tone?: string };
  base_stats?: Record<string, number>;
  base_skills?: string[];
  background?: string;
  appearance?: { description?: string };
  is_shared?: number;
}

interface Instance {
  player_id: string;
  level: number;
  exp: number;
  next_level_exp: number;
  dialogue_count?: number;
  skills?: string[];
}

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [npcs, setNpcs] = useState<NPC[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [genClass, setGenClass] = useState("warrior");
  const [genGenre, setGenGenre] = useState("fantasy");
  const [traits, setTraits] = useState({origin:"",motivation:"",flaw:"",strength:"",speech:"",appearance_trait:"",secret:"",temperament:""});
  const [extraPrompt, setExtraPrompt] = useState("");
  const [customName, setCustomName] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [error, setError] = useState("");

  const fetchKey = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      if (data.tenants?.length) { setGameKey(data.tenants[0].api_key); return data.tenants[0].api_key; }
    } catch {}
    setLoading(false);
    return "";
  }, []);

  const fetchNPCs = useCallback(async (key: string) => {
    if (!key) return;
    setLoading(true);
    try {
      const data = await gpuFetch("/api/game/npc/templates", { headers: gameHeaders(key) });
      setNpcs(data.templates || []);
    } catch { setError("NPC 목록 로딩 실패"); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchKey().then((k) => { if (k) fetchNPCs(k); }); }, [fetchKey, fetchNPCs]);

  const generate = async () => {
    if (!gameKey) return;
    setGenerating(true);
    try {
      const d = await gpuFetch("/api/game/npc/template/generate", {
        method: "POST", body: { class: genClass, genre: genGenre, ...traits, extra_prompt: extraPrompt, ...(customName.trim() ? {name: customName.trim()} : {}) }, headers: gameHeaders(gameKey),
      });
      if (d.generated) {
        d.generated.class = genClass;
        await gpuFetch("/api/game/npc/template", { method: "POST", body: d.generated, headers: gameHeaders(gameKey) });
        fetchNPCs(gameKey);
      }
    } catch { setError("생성 실패"); }
    setGenerating(false);
  };

  const deleteNPC = async (id: string, name: string) => {
    if (!confirm(`"${name}" NPC를 삭제할까요? 모든 인스턴스도 삭제됩니다.`)) return;
    await gpuFetch(`/api/game/npc/template/${id}`, { method: "DELETE", headers: gameHeaders(gameKey) });
    fetchNPCs(gameKey);
  };

  const toggleShared = async (id: string, current: boolean) => {
    await gpuFetch(`/api/game/npc/template/${id}`, {
      method: "PUT", headers: gameHeaders(gameKey), body: { is_shared: !current },
    });
    fetchNPCs(gameKey);
  };

  const loadInstances = async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    try {
      const d = await gpuFetch(`/api/game/npc/template/${id}/instances`, { headers: gameHeaders(gameKey) });
      setInstances(d.instances || []);
    } catch { setInstances([]); }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">⚔️ NPC 관리</h1>

      {/* AI 생성 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">AI 자동 생성</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">이름 <span className="text-[var(--muted)]">(비우면 랜덤 생성)</span></label>
            <input value={customName} onChange={(e) => setCustomName(e.target.value)} placeholder="NPC 이름"
              className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm w-40" />
          </div>
          <select value={genClass} onChange={(e) => setGenClass(e.target.value)}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
            {["warrior","mage","rogue","healer","ranger","merchant","blacksmith","assassin","paladin","necromancer"].map((c) => <option key={c}>{c}</option>)}
          </select>
          <select value={genGenre} onChange={(e) => setGenGenre(e.target.value)}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
            {["fantasy","sci-fi","medieval","cyberpunk","modern"].map((g) => <option key={g}>{g}</option>)}
          </select>
          <button onClick={generate} disabled={generating}
            className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {generating ? "⏳ 생성 중..." : "🤖 AI 자동 생성"}
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
          {([
            ["origin","출신",["","귀족","평민","고아","유목민","도시빈민","시골출신","망명귀족","노예출신"]],
            ["motivation","동기",["","복수","명예","보호","탐험","속죄","부와권력","진실추구","자유"]],
            ["flaw","결함",["","오만","겁쟁이","분노조절","과묵","의심병","집착","무모함","냉혈"]],
            ["strength","강점",["","리더십","지략","용기","충성심","직감","카리스마","인내","창의력"]],
            ["speech","말투",["","격식체","반말","과묵","수다","고풍체","사투리","냉소적","쾌활"]],
            ["appearance_trait","외형",["","흉터","은발","안대","문신","미남/미녀","근육질","왜소","이색눈"]],
            ["secret","비밀",["","왕족혈통","저주받음","기억상실","이중스파이","봉인된힘","불사","예언자","복제인간"]],
            ["temperament","기질",["","냉정","열혈","온화","광기","우울","낙천","신비","잔혹"]],
          ] as [string, string, string[]][]).map(([key, label, options]) => (
            <div key={key}>
              <label className="block text-xs text-[var(--muted)] mb-1">{label}</label>
              <select value={traits[key as keyof typeof traits]} onChange={(e) => setTraits({...traits, [key]: e.target.value})}
                className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs">
                {options.map((o) => <option key={o} value={o}>{o || `${label} 선택`}</option>)}
              </select>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <label className="block text-xs text-[var(--muted)] mb-1">추가 프롬프트 (자유 입력)</label>
          <input value={extraPrompt} onChange={(e) => setExtraPrompt(e.target.value)}
            placeholder="예: 드래곤 슬레이어 출신, 한쪽 팔이 의수, 동료를 잃은 트라우마..."
            className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm" />
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {loading ? <p className="text-[var(--muted)]">로딩 중...</p> : npcs.length === 0 ? (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)]">NPC가 없습니다. AI 자동 생성을 사용하세요.</div>
      ) : (
        <div className="flex flex-col gap-4">
          {npcs.map((npc) => {
            const p = npc.personality || {};
            const stats = npc.base_stats || {};
            const isExpanded = expanded === npc.id;
            return (
              <div key={npc.id} className="bg-[var(--card)] border border-[var(--border)] rounded-xl overflow-hidden">
                {/* 헤더 */}
                <div className="p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <h3 className="text-lg font-semibold">{npc.name}</h3>
                      <span className="text-xs px-2 py-1 rounded-full bg-[var(--accent)]/20 text-[var(--accent)]">{npc.class}</span>
                      <span className="text-xs text-[var(--muted)]">Lv.{npc.base_level || 1}</span>
                      {npc.is_shared ? <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-green-400">🌐 공유</span> : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => toggleShared(npc.id, !!npc.is_shared)}
                        className={`text-xs px-3 py-1 rounded-lg border ${npc.is_shared ? "border-green-500/50 text-green-400" : "border-[var(--border)] text-[var(--muted)]"}`}>
                        {npc.is_shared ? "공유 해제" : "공유 지정"}
                      </button>
                      <button onClick={() => loadInstances(npc.id)}
                        className="text-xs px-3 py-1 rounded-lg border border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]">
                        {isExpanded ? "접기" : "상세 ▼"}
                      </button>
                      <button onClick={() => deleteNPC(npc.id, npc.name)}
                        className="text-xs px-3 py-1 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10">삭제</button>
                    </div>
                  </div>

                  {/* 성격 태그 */}
                  {p.traits && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {p.traits.map((t: string, j: number) => <span key={j} className="text-xs px-2 py-0.5 rounded-full bg-[var(--surface)] text-[var(--muted)]">{t}</span>)}
                      {p.speech_style && <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--accent)]/10 text-[var(--accent)]">{p.speech_style}</span>}
                    </div>
                  )}

                  {/* 스탯 */}
                  {Object.keys(stats).length > 0 && (
                    <div className="grid grid-cols-4 gap-2 mt-3">
                      {Object.entries(stats).map(([k, v]) => (
                        <div key={k} className="bg-[var(--bg)] rounded-lg p-2 text-center">
                          <div className="text-sm font-bold">{v as number}</div>
                          <div className="text-xs text-[var(--muted)]">{k.substring(0, 3).toUpperCase()}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 상세 (펼침) */}
                {isExpanded && (
                  <div className="border-t border-[var(--border)] p-5 bg-[var(--bg)]">
                    {npc.background && <div className="text-sm mb-3"><span className="text-[var(--muted)]">배경:</span> {npc.background}</div>}
                    {npc.base_skills && npc.base_skills.length > 0 && (
                      <div className="text-sm mb-3">
                        <span className="text-[var(--muted)]">스킬:</span>{" "}
                        {npc.base_skills.map((s: string, i: number) => <span key={i} className="inline-block px-2 py-0.5 rounded bg-[var(--surface)] text-xs mr-1">{s}</span>)}
                      </div>
                    )}
                    {npc.appearance?.description && <div className="text-sm mb-3"><span className="text-[var(--muted)]">외형:</span> {npc.appearance.description}</div>}
                    <div className="text-xs text-[var(--muted)] mb-4">ID: {npc.id}</div>

                    {/* 인스턴스 (플레이어별) */}
                    <h4 className="text-sm font-semibold mb-2">플레이어별 상태</h4>
                    {instances.length === 0 ? (
                      <div className="text-sm text-[var(--muted)]">아직 인스턴스가 없습니다.</div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                        {instances.map((inst, i) => (
                          <div key={i} className="bg-[var(--card)] rounded-lg p-3 border border-[var(--border)]">
                            <div className="flex justify-between items-center mb-1">
                              <span className="text-sm font-medium">{inst.player_id}</span>
                              <span className="text-xs text-[var(--accent)]">Lv.{inst.level}</span>
                            </div>
                            <div className="text-xs text-[var(--muted)]">
                              EXP: {inst.exp}/{inst.next_level_exp} | 대화: {inst.dialogue_count || 0}회
                            </div>
                            <div className="w-full bg-[var(--bg)] rounded-full h-1.5 mt-1">
                              <div className="h-full bg-[var(--accent)] rounded-full" style={{ width: `${Math.min(100, (inst.exp / inst.next_level_exp) * 100)}%` }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
