"use client";

import { useState, useRef, useEffect, useCallback } from "react";

interface Message {
  role: "user" | "assistant" | "system";
  content: string;
  meta?: string;
}

interface Props {
  onSend?: (message: string) => Promise<{ response: string; meta?: string }>;
  onStream?: (message: string) => { url: string; body: unknown };
  placeholder?: string;
  systemMessage?: string;
}

export default function ChatBox({ onSend, onStream, placeholder, systemMessage }: Props) {
  const [messages, setMessages] = useState<Message[]>(
    systemMessage ? [{ role: "system", content: systemMessage }] : []
  );
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const scrollBottom = useCallback(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, []);

  useEffect(scrollBottom, [messages, scrollBottom]);

  const sendStreaming = async (msg: string) => {
    if (!onStream) return;
    const { url, body } = onStream(msg);
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);

    // 빈 assistant 메시지 추가 (스트리밍으로 채울 것)
    const assistantIdx = messages.length + 1; // user message 뒤
    setMessages((prev) => [...prev, { role: "assistant", content: "", meta: "" }]);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No reader");

      const decoder = new TextDecoder();
      let buffer = "";
      let fullText = "";
      let meta = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "token") {
              fullText += data.token;
              setMessages((prev) => {
                const updated = [...prev];
                updated[assistantIdx] = { role: "assistant", content: fullText, meta };
                return updated;
              });
              scrollBottom();
            } else if (data.type === "sources") {
              const sources = (data.sources || []).map((s: { law: string; article: string }) => `${s.law} ${s.article}`).join(", ");
              meta = sources ? `📎 ${sources}` : "";
            } else if (data.type === "done") {
              meta += meta ? ` | ${data.tokens} tokens` : `${data.tokens} tokens`;
              setMessages((prev) => {
                const updated = [...prev];
                updated[assistantIdx] = { role: "assistant", content: fullText, meta };
                return updated;
              });
            } else if (data.type === "error") {
              fullText += `\n❌ ${data.error}`;
              setMessages((prev) => {
                const updated = [...prev];
                updated[assistantIdx] = { role: "assistant", content: fullText, meta };
                return updated;
              });
            }
          } catch {}
        }
      }
    } catch (e: unknown) {
      const errorMessage = e instanceof Error ? e.message : "알 수 없는 오류";
      setMessages((prev) => {
        const updated = [...prev];
        if (updated[assistantIdx]) {
          updated[assistantIdx] = { role: "assistant", content: `❌ ${errorMessage}` };
        }
        return updated;
      });
    }
    setLoading(false);
  };

  const sendNormal = async (msg: string) => {
    if (!onSend) return;
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const result = await onSend(msg);
      setMessages((prev) => [...prev, { role: "assistant", content: result.response, meta: result.meta }]);
    } catch (e: unknown) {
      const errorMessage = e instanceof Error ? e.message : "알 수 없는 오류";
      setMessages((prev) => [...prev, { role: "assistant", content: `❌ ${errorMessage}` }]);
    }
    setLoading(false);
  };

  const send = async () => {
    if (!input.trim() || loading) return;
    const msg = input.trim();
    setInput("");
    if (onStream) {
      await sendStreaming(msg);
    } else {
      await sendNormal(msg);
    }
  };

  return (
    <div>
      <div ref={boxRef} className="h-[450px] overflow-y-auto bg-[var(--bg)] border border-[var(--border)] rounded-xl p-5 flex flex-col gap-3">
        {messages.map((m, i) => (
          <div key={i} className={`max-w-[85%] px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
            m.role === "user" ? "bg-[var(--accent)] text-white self-end rounded-br-sm" :
            m.role === "system" ? "text-[var(--muted)] text-xs text-center self-center" :
            "bg-[var(--surface)] border border-[var(--border)] self-start rounded-bl-sm"
          }`}>
            {m.content || (loading && i === messages.length - 1 ? "⏳ 답변 생성 중..." : "")}
            {m.meta && <div className="text-xs text-[var(--muted)] mt-2 pt-2 border-t border-[var(--border)]">{m.meta}</div>}
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder={placeholder || "메시지 입력..."}
          className="flex-1 px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]"
          disabled={loading}
        />
        <button onClick={send} disabled={loading} className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:bg-[var(--accent)]/80 disabled:opacity-50">
          {loading ? "⏳" : "전송"}
        </button>
      </div>
    </div>
  );
}
