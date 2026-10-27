"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

export default function Home() {
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);

  useEffect(() => {
    gpuFetch("/api/status")
      .then((d) => setServerOnline(!!d?.running))
      .catch(() => setServerOnline(false));
  }, []);

  const consoles = [
    {
      href: "/medical/dashboard/",
      icon: "🏥",
      title: "Medical AI",
      desc: "추론 체인, 앙상블, 예측, 약물, 모델 관리, 의료법",
      color: "border-emerald-500/30 hover:border-emerald-500",
      badge: "8 pages",
    },
    {
      href: "/story/editor/",
      icon: "📖",
      title: "Story / Novel",
      desc: "스토리 생성, 세계관/인물, 소설 프로젝트",
      color: "border-amber-500/30 hover:border-amber-500",
      badge: "3 pages",
    },
    {
      href: "/orchestration/editor/",
      icon: "⚡",
      title: "Orchestration",
      desc: "파이프라인 에디터, 실행 이력, SSE 모니터링",
      color: "border-orange-500/30 hover:border-orange-500",
      badge: "2 pages",
    },
    {
      href: "/hr-law/chat/",
      icon: "⚖️",
      title: "HR Law",
      desc: "법률 상담, 검색, 계산기, 취업규칙, 법위반 검토",
      color: "border-blue-500/30 hover:border-blue-500",
      badge: "6 pages",
    },
    {
      href: "/game/dashboard/",
      icon: "🎮",
      title: "Game NPC",
      desc: "NPC 대화, 히로인, 시나리오, 일러스트, 세계관",
      color: "border-purple-500/30 hover:border-purple-500",
      badge: "8 pages",
    },
  ];

  return (
    <div className="max-w-3xl mx-auto mt-12">
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold mb-2">Admin Console</h1>
        <p className="text-[var(--muted)] text-sm">
          WeRU.B AI Server
          {serverOnline != null && (
            <span className={`ml-2 inline-flex items-center gap-1`}>
              <span className={`w-2 h-2 rounded-full ${serverOnline ? "bg-emerald-400" : "bg-red-400"}`} />
              {serverOnline ? "Online" : "Offline"}
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {consoles.map((c) => (
          <Link key={c.href} href={c.href}
            className={`block p-6 bg-[var(--card)] border-2 rounded-xl transition-all no-underline ${c.color}`}>
            <div className="text-4xl mb-4">{c.icon}</div>
            <h2 className="text-lg font-bold text-[var(--text)] mb-2">{c.title}</h2>
            <p className="text-[var(--muted)] text-sm leading-relaxed">{c.desc}</p>
            <div className="mt-3 text-xs text-[var(--muted)]">{c.badge}</div>
          </Link>
        ))}
      </div>

      <div className="text-center mt-8">
        <a href="/" className="text-xs text-[var(--muted)] hover:text-[var(--text)] no-underline">
          ← Ollama Admin
        </a>
      </div>
    </div>
  );
}
