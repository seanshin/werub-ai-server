"use client";

import { useState } from "react";
import { gpuFetch } from "@/lib/api";

const STYLES = ["novel","game","script","fairy_tale","horror","romance","epic","noir","comedy"];
const TONES = ["melancholy","heroic","humorous","dark","warm","tense","dreamy","nostalgic","cynical"];
const POVS = ["first_person","third_person","omniscient"];
const LENGTHS = ["short","medium","long","chapter"];

export default function Page() {
  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("novel");
  const [tone, setTone] = useState("warm");
  const [pov, setPov] = useState("third_person");
  const [length, setLength] = useState("medium");

  const [story, setStory] = useState<Record<string, unknown> | null>(null);
  const [chain, setChain] = useState<Array<Record<string, unknown>>>([]);
  const [running, setRunning] = useState(false);

  const generate = async () => {
    if (!prompt.trim()) return;
    setRunning(true);
    const lastId = chain.length > 0 ? chain[chain.length - 1].story_id : undefined;
    const data = await gpuFetch("/api/story/generate", {
      method: "POST",
      body: { prompt, style, tone, pov, length, story_id: lastId },
    });
    setStory(data);
    if (data.story_id) setChain((prev) => [...prev, data]);
    setRunning(false);
  };

  const continueStory = async (direction: string) => {
    if (!story?.story_id) return;
    setRunning(true);
    const data = await gpuFetch("/api/story/continue", {
      method: "POST",
      body: { story_id: story.story_id, direction, length },
    });
    setStory(data);
    if (data.story_id) setChain((prev) => [...prev, data]);
    setRunning(false);
    setPrompt("");
  };

  const rewrite = async () => {
    if (!story?.story) return;
    setRunning(true);
    const data = await gpuFetch("/api/story/rewrite", {
      method: "POST",
      body: { text: story.story, target_style: style, target_tone: tone, expand: true },
    });
    if (data.rewritten) setStory({ ...story, story: data.rewritten });
    setRunning(false);
  };

  const branches = (story?.branch_suggestions || []) as string[];

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">스토리 에디터</h1>

      <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)] mb-6">
        <div className="grid grid-cols-4 gap-3 mb-4">
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">스타일</label>
            <select value={style} onChange={(e) => setStyle(e.target.value)}
              className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">톤</label>
            <select value={tone} onChange={(e) => setTone(e.target.value)}
              className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">시점</label>
            <select value={pov} onChange={(e) => setPov(e.target.value)}
              className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {POVS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-[var(--muted)] block mb-1">길이</label>
            <select value={length} onChange={(e) => setLength(e.target.value)}
              className="w-full bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1.5 text-sm">
              {LENGTHS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        </div>

        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="스토리 주제를 입력하세요..." rows={3}
          className="w-full bg-[var(--surface)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm mb-3" />

        <div className="flex gap-2">
          <button onClick={generate} disabled={running || !prompt.trim()}
            className="px-6 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {running ? "생성 중..." : chain.length > 0 ? "새 스토리" : "생성"}
          </button>
          {story && (
            <button onClick={rewrite} disabled={running}
              className="px-4 py-2 bg-[var(--surface)] border border-[var(--border)] text-[var(--muted)] rounded-lg text-sm hover:text-[var(--text)]">
              문체 변환
            </button>
          )}
        </div>
      </div>

      {story && (
        <div className="space-y-4">
          {/* Story text */}
          <div className="bg-[var(--card)] rounded-xl p-6 border border-[var(--border)]">
            <div className="text-sm leading-relaxed whitespace-pre-wrap">{String(story.story || story.content || "")}</div>
            <div className="text-xs text-[var(--muted)] mt-4 pt-3 border-t border-[var(--border)]">
              {String((story.metadata as Record<string, unknown>)?.word_count || 0)}자 |
              {String((story.metadata as Record<string, unknown>)?.model || "")} |
              {String((story.metadata as Record<string, unknown>)?.duration_ms || 0)}ms |
              체인: {chain.length}편
            </div>
          </div>

          {/* Branch suggestions */}
          {branches.length > 0 && (
            <div className="bg-[var(--card)] rounded-xl p-5 border border-[var(--border)]">
              <h4 className="text-sm font-bold mb-3">다음 전개 선택</h4>
              <div className="space-y-2">
                {branches.map((b, i) => (
                  <button key={i} onClick={() => continueStory(b)} disabled={running}
                    className="w-full text-left p-3 rounded-lg text-sm bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--accent)] transition-colors disabled:opacity-50">
                    {String.fromCharCode(65 + i)}. {b}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
