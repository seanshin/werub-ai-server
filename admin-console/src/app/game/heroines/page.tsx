"use client";

import { useState, useEffect, useCallback } from "react";
import { gpuFetch, gameHeaders } from "@/lib/api";

interface Heroine {
  id: string;
  name: string;
  age?: number;
  personality?: string;
  likes?: string;
  dislikes?: string;
  ideal_type?: string;
  voice_style?: string;
  background?: string;
  appearance?: string;
}

interface PlayerAffinity {
  player_id: string;
  affinity: number;
  stage: string;
  trust: number;
  date_count: number;
  confession_count: number;
  interactions?: number;
}

const STAGES: Record<string, string> = {
  enemy: "원수", dislike: "싫어함", stranger: "낯선 사람", acquaintance: "아는 사이",
  friend: "친구", crush: "설렘", lover: "연인", soulmate: "운명의 상대",
};

export default function Page() {
  const [gameKey, setGameKey] = useState("");
  const [heroines, setHeroines] = useState<Heroine[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [archetype, setArchetype] = useState("츤데레");
  const [hTraits, setHTraits] = useState({family:"",hobby:"",trauma:"",romance_style:"",speech:"",appearance_trait:"",secret:"",charm_point:""});
  const [extraPrompt, setExtraPrompt] = useState("");
  const [customName, setCustomName] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [players, setPlayers] = useState<PlayerAffinity[]>([]);
  const [eventSummary, setEventSummary] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [editData, setEditData] = useState<Partial<Heroine>>({});
  const [illustrating, setIllustrating] = useState<string | null>(null);
  const [illPose, setIllPose] = useState("portrait");
  const [illStyle, setIllStyle] = useState("anime");
  const [illScene, setIllScene] = useState("");
  const [illResult, setIllResult] = useState<{job_id?:string; prompt_used?:string; npc?:string} | null>(null);
  const [illLoading, setIllLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchKey = useCallback(async () => {
    try {
      const data = await gpuFetch("/api/game/tenants");
      if (data.tenants?.length) { setGameKey(data.tenants[0].api_key); return data.tenants[0].api_key; }
    } catch {}
    setLoading(false);
    return "";
  }, []);

  const fetchHeroines = useCallback(async (key: string) => {
    if (!key) return;
    setLoading(true);
    try {
      const data = await gpuFetch("/api/game/heroine/list", { headers: gameHeaders(key) });
      setHeroines(data.heroines || []);
    } catch { setError("로딩 실패"); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchKey().then((k) => { if (k) fetchHeroines(k); }); }, [fetchKey, fetchHeroines]);

  const generate = async () => {
    if (!gameKey) return;
    setGenerating(true);
    try {
      const body: Record<string,string> = { archetype, ...hTraits, extra_prompt: extraPrompt };
      if (customName.trim()) body.name = customName.trim();
      const d = await gpuFetch("/api/game/heroine/generate", { method: "POST", body, headers: gameHeaders(gameKey) });
      if (d.generated) {
        await gpuFetch("/api/game/heroine/create", { method: "POST", body: d.generated, headers: gameHeaders(gameKey) });
        fetchHeroines(gameKey);
      }
    } catch { setError("생성 실패"); }
    setGenerating(false);
  };

  const deleteHeroine = async (id: string, name: string) => {
    if (!confirm(`"${name}" 히로인을 삭제할까요? 모든 호감도/이벤트 데이터도 삭제됩니다.`)) return;
    await gpuFetch(`/api/game/heroine/${id}`, { method: "DELETE", headers: gameHeaders(gameKey) });
    fetchHeroines(gameKey);
  };

  const loadPlayers = async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    try {
      const d = await gpuFetch(`/api/game/heroine/${id}/players`, { headers: gameHeaders(gameKey) });
      setPlayers(d.players || []);
      setEventSummary(d.event_summary || {});
    } catch { setPlayers([]); }
  };

  const startEdit = (h: Heroine) => {
    setEditing(h.id);
    setEditData({ name: h.name, personality: h.personality, likes: h.likes, dislikes: h.dislikes, ideal_type: h.ideal_type, voice_style: h.voice_style, background: h.background });
  };

  const saveEdit = async () => {
    if (!editing) return;
    await gpuFetch(`/api/game/heroine/${editing}`, { method: "PUT", body: editData, headers: gameHeaders(gameKey) });
    setEditing(null);
    fetchHeroines(gameKey);
  };

  const generateIllust = async (h: Heroine) => {
    if (illLoading) return;
    setIllResult(null);
    setIllLoading(true);
    try {
      // 히로인은 heroine_profiles에 있으므로, NPC 템플릿이 아닌 직접 이미지 생성
      const desc = `${h.name}, ${h.personality || ""}, ${h.appearance || "anime girl"}`;
      const sceneDesc = illScene || `${h.name} ${illPose}`;
      const r = await gpuFetch("/api/game/illustrate/scene", {
        method: "POST",
        headers: gameHeaders(gameKey),
        body: {
          description: `${sceneDesc}, ${desc}`,
          style: illStyle,
          model: illStyle === "anime" ? "animagine" : "dreamshaper",
          mood: "",
          width: 768,
          height: 1024,
        },
      });
      setIllResult(r);
    } catch { setError("일러스트 생성 실패"); }
    setIllLoading(false);
  };

  const stageColor = (stage: string) => {
    if (["lover", "soulmate"].includes(stage)) return "text-pink-400";
    if (["crush"].includes(stage)) return "text-red-400";
    if (["friend"].includes(stage)) return "text-green-400";
    if (["enemy", "dislike"].includes(stage)) return "text-red-500";
    return "text-[var(--muted)]";
  };

  const inputCls = "w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">💕 히로인 관리</h1>

      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">AI 자동 생성</h2>
        <div className="flex gap-3 items-end">
          <div>
            <label className="block text-xs text-[var(--muted)] mb-1">이름 <span className="text-[var(--muted)]">(비우면 랜덤 생성)</span></label>
            <input value={customName} onChange={(e) => setCustomName(e.target.value)} placeholder="히로인 이름"
              className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm w-40" />
          </div>
          <select value={archetype} onChange={(e) => setArchetype(e.target.value)}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
            {["츤데레","쿨뷰티","얀데레","소꿉친구","후배","선배","아가씨","보이시"].map((a) => <option key={a}>{a}</option>)}
          </select>
          <button onClick={generate} disabled={generating}
            className="px-6 py-3 bg-pink-600 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {generating ? "⏳ 생성 중..." : "🤖 AI 자동 생성"}
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
          {([
            ["family","가정환경",["","부유한가정","빈곤","편부모","고아","대가족","독립생활","재벌","평범"]],
            ["hobby","취미",["","독서","요리","운동","음악","그림","게임","원예","댄스"]],
            ["trauma","트라우마",["","배신경험","가족상실","왕따","사고","버림받음","실연","폭력","없음"]],
            ["romance_style","연애스타일",["","적극적","소극적","질투형","헌신형","자유연애","츤데레","독점적","느긋"]],
            ["speech","말투",["","반말","존댓말","사투리","무뚝뚝","애교","도도","당당","속삭임"]],
            ["appearance_trait","외형",["","안경","포니테일","단발","긴머리","메이드복","교복","캐주얼","고스로리"]],
            ["secret","비밀",["","아이돌","재벌딸","마법소녀","전학생","쌍둥이","외국인","천재","전생기억"]],
            ["charm_point","매력포인트",["","갭모에","웃는얼굴","눈물","당당함","천연","수줍음","강인함","모성애"]],
          ] as [string, string, string[]][]).map(([key, label, options]) => (
            <div key={key}>
              <label className="block text-xs text-[var(--muted)] mb-1">{label}</label>
              <select value={hTraits[key as keyof typeof hTraits]} onChange={(e) => setHTraits({...hTraits, [key]: e.target.value})}
                className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs">
                {options.map((o) => <option key={o} value={o}>{o || `${label} 선택`}</option>)}
              </select>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <label className="block text-xs text-[var(--muted)] mb-1">추가 프롬프트 (자유 입력)</label>
          <input value={extraPrompt} onChange={(e) => setExtraPrompt(e.target.value)}
            placeholder="예: 전생에 용사였던 기억이 있음, 고양이귀 수인, 학교 밴드 보컬..."
            className="w-full px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm" />
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}

      {loading ? <p className="text-[var(--muted)]">로딩 중...</p> : heroines.length === 0 ? (
        <div className="bg-[var(--card)] rounded-xl p-6 text-center text-[var(--muted)]">히로인이 없습니다.</div>
      ) : (
        <div className="flex flex-col gap-4">
          {heroines.map((h) => (
            <div key={h.id} className="bg-[var(--card)] border border-[var(--border)] rounded-xl overflow-hidden">
              <div className="p-5">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <h3 className="text-lg font-semibold" style={{ color: "#ec4899" }}>{h.name}</h3>
                    <span className="text-sm text-[var(--muted)]">{h.age || "?"}세</span>
                    {h.voice_style && <span className="text-xs px-2 py-0.5 rounded-full bg-pink-500/20 text-pink-400">{h.voice_style}</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setIllustrating(illustrating === h.id ? null : h.id)} className="text-xs px-3 py-1 rounded-lg border border-purple-500/30 text-purple-400 hover:bg-purple-500/10">🎨 일러스트</button>
                    <button onClick={() => startEdit(h)} className="text-xs px-3 py-1 rounded-lg border border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]">수정</button>
                    <button onClick={() => loadPlayers(h.id)} className="text-xs px-3 py-1 rounded-lg border border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]">
                      {expanded === h.id ? "접기" : "상세 ▼"}
                    </button>
                    <button onClick={() => deleteHeroine(h.id, h.name)} className="text-xs px-3 py-1 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10">삭제</button>
                  </div>
                </div>

                {editing === h.id ? (
                  <div className="space-y-2 mt-3">
                    <input value={editData.name || ""} onChange={(e) => setEditData({...editData, name: e.target.value})} placeholder="이름" className={inputCls} />
                    <textarea value={editData.personality || ""} onChange={(e) => setEditData({...editData, personality: e.target.value})} placeholder="성격" className={inputCls} rows={2} />
                    <div className="grid grid-cols-2 gap-2">
                      <input value={editData.likes || ""} onChange={(e) => setEditData({...editData, likes: e.target.value})} placeholder="좋아하는 것" className={inputCls} />
                      <input value={editData.dislikes || ""} onChange={(e) => setEditData({...editData, dislikes: e.target.value})} placeholder="싫어하는 것" className={inputCls} />
                      <input value={editData.ideal_type || ""} onChange={(e) => setEditData({...editData, ideal_type: e.target.value})} placeholder="이상형" className={inputCls} />
                      <input value={editData.voice_style || ""} onChange={(e) => setEditData({...editData, voice_style: e.target.value})} placeholder="말투" className={inputCls} />
                    </div>
                    <textarea value={editData.background || ""} onChange={(e) => setEditData({...editData, background: e.target.value})} placeholder="배경" className={inputCls} rows={2} />
                    <div className="flex gap-2">
                      <button onClick={saveEdit} className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm">저장</button>
                      <button onClick={() => setEditing(null)} className="px-4 py-2 bg-[var(--surface)] text-[var(--muted)] rounded-lg text-sm border border-[var(--border)]">취소</button>
                    </div>
                  </div>
                ) : (
                  <>
                    {h.personality && <p className="text-sm text-[var(--text)] mb-3">{h.personality}</p>}
                    <div className="space-y-1 text-sm">
                      {h.likes && <div><span className="text-green-400">♥</span> <span className="text-[var(--muted)]">{h.likes}</span></div>}
                      {h.dislikes && <div><span className="text-red-400">✗</span> <span className="text-[var(--muted)]">{h.dislikes}</span></div>}
                      {h.ideal_type && <div><span className="text-[var(--accent)]">★</span> <span className="text-[var(--muted)]">이상형: {h.ideal_type}</span></div>}
                    </div>
                    {h.background && <p className="text-xs text-[var(--muted)] mt-3 pt-3 border-t border-[var(--border)]">{h.background}</p>}
                  </>
                )}
              </div>

              {illustrating === h.id && (
                <div className="border-t border-[var(--border)] p-5 bg-purple-500/5">
                  <h4 className="text-sm font-semibold mb-3">🎨 {h.name} 일러스트 생성</h4>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                    <div>
                      <label className="block text-xs text-[var(--muted)] mb-1">포즈/장면</label>
                      <select value={illPose} onChange={(e) => setIllPose(e.target.value)}
                        className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs">
                        {["portrait","full body","sitting","running","battle stance","school uniform","casual outfit","kimono","swimsuit","sleeping","cooking","reading","crying","laughing","angry","shy"].map(p => <option key={p}>{p}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-[var(--muted)] mb-1">스타일</label>
                      <select value={illStyle} onChange={(e) => setIllStyle(e.target.value)}
                        className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs">
                        {["anime","illustration","concept-art","pixel-art-character","photorealistic"].map(s => <option key={s}>{s}</option>)}
                      </select>
                    </div>
                    <div className="col-span-2">
                      <label className="block text-xs text-[var(--muted)] mb-1">장면 설명 (선택)</label>
                      <input value={illScene} onChange={(e) => setIllScene(e.target.value)} placeholder="벚꽃 아래에서, 카페에서, 비 오는 날..."
                        className="w-full px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs" />
                    </div>
                  </div>
                  <button onClick={() => generateIllust(h)} disabled={illLoading}
                    className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                    {illLoading ? "⏳ 생성 중..." : "🎨 생성"}
                  </button>
                  {illResult && (
                    <div className="mt-3 p-3 bg-[var(--bg)] rounded-lg text-xs">
                      {illResult.job_id ? (
                        <>
                          <div className="text-green-400 mb-1">✅ 생성 요청 완료!</div>
                          <div className="text-[var(--muted)]">Job: {illResult.job_id}</div>
                          <div className="text-[var(--muted)]">프롬프트: {illResult.prompt_used}</div>
                        </>
                      ) : (
                        <div className="text-red-400">{JSON.stringify(illResult)}</div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {expanded === h.id && (
                <div className="border-t border-[var(--border)] p-5 bg-[var(--bg)]">
                  <div className="text-xs text-[var(--muted)] mb-3">ID: {h.id}</div>

                  {/* 이벤트 요약 */}
                  {Object.keys(eventSummary).length > 0 && (
                    <div className="flex gap-2 mb-4">
                      {Object.entries(eventSummary).map(([type, count]) => (
                        <span key={type} className="text-xs px-2 py-1 rounded-full bg-[var(--surface)] text-[var(--muted)]">
                          {type === "stage_change" ? "🎭" : type === "date" ? "💑" : type === "gift" ? "🎁" : type === "confession" ? "💌" : "💬"} {type}: {count}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* 플레이어별 호감도 */}
                  <h4 className="text-sm font-semibold mb-2">플레이어별 호감도</h4>
                  {players.length === 0 ? (
                    <div className="text-sm text-[var(--muted)]">아직 상호작용한 플레이어가 없습니다.</div>
                  ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {players.map((p, i) => (
                        <div key={i} className="bg-[var(--card)] rounded-lg p-3 border border-[var(--border)]">
                          <div className="flex justify-between items-center mb-1">
                            <span className="text-sm font-medium">{p.player_id}</span>
                            <span className={`text-xs font-bold ${stageColor(p.stage)}`}>
                              {STAGES[p.stage] || p.stage} ({p.affinity})
                            </span>
                          </div>
                          <div className="w-full bg-[var(--bg)] rounded-full h-2 mb-1">
                            <div className="h-full rounded-full transition-all" style={{
                              width: `${Math.max(0, Math.min(100, (p.affinity + 100) / 2))}%`,
                              background: p.affinity > 60 ? "#ec4899" : p.affinity > 30 ? "#6366f1" : p.affinity > 0 ? "#71717a" : "#ef4444",
                            }} />
                          </div>
                          <div className="text-xs text-[var(--muted)]">
                            신뢰: {p.trust} | 데이트: {p.date_count}회 | 고백: {p.confession_count}회
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
