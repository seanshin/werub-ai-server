"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const CONSOLES = [
  {
    id: "medical",
    title: "Medical AI",
    icon: "🏥",
    color: "text-emerald-400",
    prefix: "/medical",
    items: [
      { href: "/medical/dashboard", icon: "📊", label: "대시보드" },
      { href: "/medical/reasoning", icon: "🧠", label: "추론 체인" },
      { href: "/medical/ensemble", icon: "🔬", label: "앙상블" },
      { href: "/medical/predict", icon: "🔮", label: "예측 분석" },
      { href: "/medical/drugs", icon: "💊", label: "약물 검색" },
      { href: "/medical/triage", icon: "🚑", label: "증상 분류" },
      { href: "/medical/models", icon: "🤖", label: "모델 관리" },
      { href: "/medical/law", icon: "⚖️", label: "의료법" },
    ],
  },
  {
    id: "hr-law",
    title: "HR Law",
    icon: "⚖️",
    color: "text-blue-400",
    prefix: "/hr-law",
    items: [
      { href: "/hr-law/chat", icon: "💬", label: "법률 상담" },
      { href: "/hr-law/search", icon: "🔍", label: "법률 검색" },
      { href: "/hr-law/calc", icon: "🧮", label: "계산기" },
      { href: "/hr-law/rules", icon: "📋", label: "취업규칙" },
      { href: "/hr-law/review", icon: "🔎", label: "법위반 검토" },
      { href: "/hr-law/tenants", icon: "🏢", label: "회사 관리" },
    ],
  },
  {
    id: "story",
    title: "Story / Novel",
    icon: "📖",
    color: "text-amber-400",
    prefix: "/story",
    items: [
      { href: "/story/editor", icon: "✍️", label: "스토리 에디터" },
      { href: "/story/world", icon: "🌍", label: "세계관/인물" },
      { href: "/story/novel", icon: "📕", label: "소설 프로젝트" },
      { href: "/story/decompose", icon: "🔬", label: "소설 분해기" },
      { href: "/story/series", icon: "📚", label: "시리즈 관리" },
      { href: "/story/analyze", icon: "📊", label: "소설 분석" },
      { href: "/story/recipe", icon: "🧪", label: "레시피" },
      { href: "/story/assistant", icon: "💡", label: "집필 보조" },
    ],
  },
  {
    id: "orchestration",
    title: "Orchestration",
    icon: "⚡",
    color: "text-orange-400",
    prefix: "/orchestration",
    items: [
      { href: "/orchestration/editor", icon: "🔧", label: "파이프라인 에디터" },
      { href: "/orchestration/runs", icon: "📋", label: "실행 이력" },
    ],
  },
  {
    id: "game",
    title: "Game NPC",
    icon: "🎮",
    color: "text-purple-400",
    prefix: "/game",
    items: [
      { href: "/game/dashboard", icon: "📊", label: "대시보드" },
      { href: "/game/npcs", icon: "⚔️", label: "NPC 관리" },
      { href: "/game/heroines", icon: "💕", label: "히로인" },
      { href: "/game/chat", icon: "💬", label: "NPC 대화" },
      { href: "/game/heroine-chat", icon: "💗", label: "히로인 대화" },
      { href: "/game/scenario", icon: "📜", label: "시나리오" },
      { href: "/game/illustrate", icon: "🎨", label: "일러스트" },
      { href: "/game/world", icon: "🌍", label: "세계관" },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();

  // Determine which console is active based on pathname
  const activeConsole = CONSOLES.find((c) => pathname?.includes(c.prefix));

  // If on home page, show console selector
  if (!activeConsole) {
    return (
      <aside className="fixed top-0 left-0 bottom-0 w-60 bg-[var(--surface)] border-r border-[var(--border)] overflow-y-auto">
        <div className="p-5 border-b border-[var(--border)]">
          <Link href="/" className="text-lg font-bold text-[var(--accent)] no-underline">
            🎛️ Admin Console
          </Link>
        </div>
        <div className="p-4 space-y-3">
          {CONSOLES.map((c) => (
            <Link key={c.id} href={c.items[0].href}
              className={`flex items-center gap-3 p-4 rounded-xl border border-[var(--border)] no-underline hover:border-[var(--accent)] transition-colors`}>
              <span className="text-2xl">{c.icon}</span>
              <div>
                <div className={`font-semibold ${c.color}`}>{c.title}</div>
                <div className="text-xs text-[var(--muted)]">{c.items.length} pages</div>
              </div>
            </Link>
          ))}
        </div>
        <div className="mt-auto p-5 border-t border-[var(--border)]">
          <a href="/" className="text-xs text-[var(--muted)] hover:text-[var(--text)] no-underline">
            ← Ollama Admin
          </a>
        </div>
      </aside>
    );
  }

  return (
    <aside className="fixed top-0 left-0 bottom-0 w-60 bg-[var(--surface)] border-r border-[var(--border)] overflow-y-auto flex flex-col">
      <div className="p-5 border-b border-[var(--border)]">
        <div className={`text-lg font-bold ${activeConsole.color}`}>
          {activeConsole.icon} {activeConsole.title}
        </div>
      </div>

      <div className="pt-2 flex-1">
        {activeConsole.items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-3 px-5 py-2.5 text-sm no-underline transition-colors ${
              pathname === item.href || pathname?.startsWith(item.href + "/")
                ? "bg-[rgba(99,102,241,0.15)] text-[var(--accent)] border-r-[3px] border-[var(--accent)]"
                : "text-[var(--muted)] hover:bg-[rgba(99,102,241,0.1)] hover:text-[var(--text)]"
            }`}
          >
            <span className="w-5 text-center">{item.icon}</span>
            <span>{item.label}</span>
          </Link>
        ))}
      </div>

      {/* Switch to other consoles */}
      <div className="border-t border-[var(--border)] p-3">
        <div className="text-[10px] uppercase text-[var(--muted)] tracking-wider mb-2 px-2">Switch</div>
        <div className="space-y-1">
          {CONSOLES.filter((c) => c.id !== activeConsole.id).map((c) => (
            <Link key={c.id} href={c.items[0].href}
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-[var(--muted)] hover:bg-[rgba(99,102,241,0.1)] hover:text-[var(--text)] no-underline transition-colors">
              <span>{c.icon}</span>
              <span>{c.title}</span>
            </Link>
          ))}
        </div>
      </div>

      <div className="p-4 border-t border-[var(--border)]">
        <a href="/" className="text-xs text-[var(--muted)] hover:text-[var(--text)] no-underline">
          ← Ollama Admin
        </a>
      </div>
    </aside>
  );
}
