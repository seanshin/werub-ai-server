"use client";

import { useState } from "react";
import ChatBox from "@/components/ChatBox";

export default function HrLawChat() {
  const [category, setCategory] = useState("");
  const [language, setLanguage] = useState("ko");

  const GPU_BASE = typeof window !== "undefined"
    ? window.location.pathname.replace(/\/admin-console.*$/, "").replace(/\/+$/, "")
    : "";

  const handleStream = (message: string) => ({
    url: `${GPU_BASE}/api/hr-law/ask/stream`,
    body: { question: message, category: category || undefined, language },
  });

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">💬 법률 상담</h1>
      <div className="flex gap-4 mb-4">
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          className="px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
          <option value="">전체 카테고리</option>
          <option value="work_hours">근로시간</option><option value="rest">휴게/휴일</option>
          <option value="annual_leave">연차휴가</option><option value="wage">임금</option>
          <option value="overtime_pay">초과근무수당</option><option value="severance">퇴직급여</option>
          <option value="dismissal">해고</option><option value="discipline">징계</option>
          <option value="maternity">모성보호</option><option value="harassment">직장내괴롭힘</option>
        </select>
        <select value={language} onChange={(e) => setLanguage(e.target.value)}
          className="px-3 py-2 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm">
          <option value="ko">한국어</option><option value="en">English</option>
          <option value="vi">Tiếng Việt</option><option value="zh">中文</option><option value="ja">日本語</option>
        </select>
      </div>
      <ChatBox onStream={handleStream} placeholder="법률 질문을 입력하세요..." systemMessage="근태/인사 관련 법률 질문에 답변해드립니다. 답변이 실시간으로 표시됩니다." />
    </div>
  );
}
