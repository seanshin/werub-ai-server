"use client";

import { useState, useRef, useEffect } from "react";

interface Props {
  value: string;
  onChange: (date: string) => void;
  label?: string;
}

export default function DateInput({ value, onChange, label }: Props) {
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (value) {
      const d = new Date(value);
      setYear(d.getFullYear());
      setMonth(d.getMonth());
    }
  }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = new Date(year, month, 1).getDay();
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,"0")}-${String(today.getDate()).padStart(2,"0")}`;

  const selectDate = (day: number) => {
    const d = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    onChange(d);
    setOpen(false);
  };

  const prevMonth = () => { if (month === 0) { setMonth(11); setYear(y => y - 1); } else setMonth(m => m - 1); };
  const nextMonth = () => { if (month === 11) { setMonth(0); setYear(y => y + 1); } else setMonth(m => m + 1); };

  const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
  const MONTHS = ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"];

  return (
    <div className="relative" ref={ref}>
      {label && <label className="text-sm text-[var(--muted)] mr-3">{label}</label>}
      <button
        onClick={() => setOpen(!open)}
        className="px-4 py-2.5 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm min-w-[200px] text-left flex items-center justify-between hover:border-[var(--accent)] transition"
      >
        <span>{value || "날짜 선택"}</span>
        <span className="text-[var(--muted)]">📅</span>
      </button>

      {open && (
        <div className="absolute z-50 mt-2 bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-2xl p-4 w-[300px]">
          {/* 헤더 */}
          <div className="flex items-center justify-between mb-3">
            <button onClick={() => setYear(y => y - 1)} className="px-2 py-1 text-[var(--muted)] hover:text-[var(--text)] text-xs">«</button>
            <button onClick={prevMonth} className="px-2 py-1 text-[var(--muted)] hover:text-[var(--text)]">‹</button>
            <span className="font-bold text-sm">{year}년 {MONTHS[month]}</span>
            <button onClick={nextMonth} className="px-2 py-1 text-[var(--muted)] hover:text-[var(--text)]">›</button>
            <button onClick={() => setYear(y => y + 1)} className="px-2 py-1 text-[var(--muted)] hover:text-[var(--text)] text-xs">»</button>
          </div>

          {/* 요일 */}
          <div className="grid grid-cols-7 gap-1 mb-1">
            {WEEKDAYS.map((d, i) => (
              <div key={d} className={`text-center text-xs py-1 ${i === 0 ? "text-red-400" : i === 6 ? "text-blue-400" : "text-[var(--muted)]"}`}>{d}</div>
            ))}
          </div>

          {/* 날짜 */}
          <div className="grid grid-cols-7 gap-1">
            {Array.from({ length: firstDay }).map((_, i) => <div key={`e${i}`} />)}
            {Array.from({ length: daysInMonth }).map((_, i) => {
              const day = i + 1;
              const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
              const isSelected = dateStr === value;
              const isToday = dateStr === todayStr;
              const dayOfWeek = new Date(year, month, day).getDay();

              return (
                <button
                  key={day}
                  onClick={() => selectDate(day)}
                  className={`w-9 h-9 rounded-lg text-sm flex items-center justify-center transition
                    ${isSelected ? "bg-[var(--accent)] text-white font-bold" :
                      isToday ? "border border-[var(--accent)] text-[var(--accent)]" :
                      dayOfWeek === 0 ? "text-red-400 hover:bg-[var(--surface)]" :
                      dayOfWeek === 6 ? "text-blue-400 hover:bg-[var(--surface)]" :
                      "text-[var(--text)] hover:bg-[var(--surface)]"}`}
                >
                  {day}
                </button>
              );
            })}
          </div>

          {/* 오늘 버튼 */}
          <div className="mt-3 flex justify-center">
            <button onClick={() => { onChange(todayStr); setOpen(false); setYear(today.getFullYear()); setMonth(today.getMonth()); }}
              className="text-xs text-[var(--accent)] hover:underline">오늘</button>
          </div>
        </div>
      )}
    </div>
  );
}
