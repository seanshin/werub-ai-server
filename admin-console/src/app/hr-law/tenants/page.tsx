"use client";

import { useState, useEffect } from "react";
import { gpuFetch } from "@/lib/api";

interface Tenant {
  id: string;
  name: string;
  plan: string;
  api_key: string;
  contact_email?: string;
  created_at?: string;
  key_regenerated_at?: string | null;
  rules_count?: number;
  queries_count?: number;
}

interface Pagination {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

interface TenantStats {
  total_queries: number;
  daily: { date: string; count: number }[];
  by_category: { category: string; count: number }[];
  recent_queries: { id: string; question: string; category: string | null; created_at: string; user: string }[];
}

export default function Page() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, per_page: 10, total: 0, total_pages: 1 });
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({ name: "", contact_email: "", plan: "basic" });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [stats, setStats] = useState<TenantStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  const fetchTenants = async (page = 1) => {
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "10" });
      if (search) params.set("search", search);
      const data = await gpuFetch(`/api/hr-law/tenants?${params}`);
      setTenants(data.tenants || []);
      if (data.pagination) setPagination(data.pagination);
    } catch {
      setError("테넌트 목록 로딩 실패");
    }
    setLoading(false);
  };

  useEffect(() => { fetchTenants(); }, []);

  const register = async () => {
    if (!form.name.trim() || !form.contact_email.trim()) { setError("이름과 이메일은 필수입니다."); return; }
    setSubmitting(true); setError(""); setSuccess("");
    try {
      const data = await gpuFetch("/api/hr-law/tenant/register", { method: "POST", body: form });
      setSuccess(`등록 완료! API Key: ${data.api_key || "발급됨"}`);
      setForm({ name: "", contact_email: "", plan: "basic" });
      fetchTenants(1);
    } catch { setError("등록 실패"); }
    setSubmitting(false);
  };

  const deleteTenant = async (t: Tenant) => {
    if (!confirm(`"${t.name}" 회사를 삭제합니다.\n관련 규칙과 질의 이력도 함께 삭제됩니다.`)) return;
    setError(""); setSuccess("");
    try {
      const data = await gpuFetch(`/api/hr-law/tenant/${t.id}`, { method: "DELETE" });
      setSuccess(`"${t.name}" 삭제 완료 (규칙 ${data.deleted_rules ?? 0}건, 질의 ${data.deleted_queries ?? 0}건)`);
      setExpandedId(null);
      fetchTenants(pagination.page);
    } catch { setError("삭제 실패"); }
  };

  const regenerateKey = async (t: Tenant) => {
    if (!confirm(`"${t.name}" API 키를 재발급합니다.\n기존 키는 즉시 무효화됩니다.`)) return;
    setError(""); setSuccess("");
    try {
      const data = await gpuFetch(`/api/hr-law/tenant/${t.id}/regenerate-key`, { method: "POST" });
      setSuccess(`"${t.name}" 새 API Key: ${data.api_key}`);
      fetchTenants(pagination.page);
    } catch { setError("키 재발급 실패"); }
  };

  const toggleStats = async (t: Tenant) => {
    if (expandedId === t.id) {
      setExpandedId(null);
      setStats(null);
      return;
    }
    setExpandedId(t.id);
    setStatsLoading(true);
    setStats(null);
    try {
      const data = await gpuFetch(`/api/hr-law/tenant/${t.id}/stats`);
      setStats(data);
    } catch {
      setStats(null);
    }
    setStatsLoading(false);
  };

  const handleSearch = () => { setLoading(true); fetchTenants(1); };
  const goPage = (p: number) => { setLoading(true); fetchTenants(p); };

  const fmtDate = (d?: string | null) => {
    if (!d) return "-";
    return d.replace("T", " ").slice(0, 16);
  };

  const fmtShortDate = (d: string) => d.slice(5, 10);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">회사(테넌트) 관리</h1>

      {/* 등록 폼 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">새 회사 등록</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="회사명" className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <input value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
            placeholder="담당자 이메일" type="email" className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]" />
          <select value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value })}
            className="px-4 py-3 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]">
            <option value="basic">Basic</option>
            <option value="standard">Standard</option>
            <option value="premium">Premium</option>
          </select>
        </div>
        <button onClick={register} disabled={submitting}
          className="px-6 py-3 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-80 disabled:opacity-50">
          {submitting ? "등록 중..." : "등록"}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 text-red-400 text-sm">{error}</div>}
      {success && <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-4 mb-6 text-green-400 text-sm">{success}</div>}

      {/* 목록 */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">등록된 회사 목록 <span className="text-xs text-[var(--muted)] font-normal ml-1">({pagination.total}건)</span></h2>
          <div className="flex gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="회사명 검색" className="px-3 py-1.5 bg-[var(--bg)] text-[var(--text)] border border-[var(--border)] rounded-lg text-xs w-40 focus:outline-none focus:border-[var(--accent)]" />
            <button onClick={handleSearch} className="px-3 py-1.5 bg-[var(--surface)] text-[var(--text)] rounded-lg text-xs hover:opacity-80">검색</button>
          </div>
        </div>

        {loading ? (
          <p className="text-[var(--muted)] text-sm">로딩 중...</p>
        ) : tenants.length === 0 ? (
          <p className="text-[var(--muted)] text-sm">등록된 회사가 없습니다.</p>
        ) : (
          <div className="space-y-0">
            {/* 헤더 */}
            <div className="grid grid-cols-[1fr_80px_1fr_100px_100px_60px_60px_140px] gap-1 px-3 py-2 border-b border-[var(--border)] text-[var(--muted)] text-xs font-medium">
              <span>회사명</span><span>플랜</span><span>API Key</span><span>발급일</span><span>재발급일</span>
              <span className="text-right">규칙</span><span className="text-right">질의</span><span className="text-right">관리</span>
            </div>

            {tenants.map((t) => (
              <div key={t.id}>
                {/* 행 */}
                <div className="grid grid-cols-[1fr_80px_1fr_100px_100px_60px_60px_140px] gap-1 px-3 py-3 border-b border-[var(--border)]/50 hover:bg-[var(--surface)] items-center text-sm cursor-pointer"
                  onClick={() => toggleStats(t)}>
                  <div>
                    <div className="text-[var(--text)] flex items-center gap-1">
                      <span className="text-[10px]">{expandedId === t.id ? "▼" : "▶"}</span> {t.name}
                    </div>
                    {t.contact_email && <div className="text-[10px] text-[var(--muted)] ml-3">{t.contact_email}</div>}
                  </div>
                  <div>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      t.plan === "premium" ? "bg-purple-500/20 text-purple-400" :
                      t.plan === "standard" ? "bg-blue-500/20 text-blue-400" :
                      "bg-[var(--surface)] text-[var(--muted)]"
                    }`}>{t.plan}</span>
                  </div>
                  <div className="font-mono text-[10px] text-[var(--muted)] truncate">{t.api_key}</div>
                  <div className="text-xs text-[var(--muted)]">{fmtDate(t.created_at)}</div>
                  <div className="text-xs text-[var(--muted)]">{fmtDate(t.key_regenerated_at)}</div>
                  <div className="text-right text-[var(--text)]">{t.rules_count ?? 0}</div>
                  <div className="text-right text-[var(--text)]">{t.queries_count ?? 0}</div>
                  <div className="text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => regenerateKey(t)}
                      className="text-xs px-2 py-1 rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 mr-1">
                      키 재발급
                    </button>
                    <button onClick={() => deleteTenant(t)}
                      className="text-xs px-2 py-1 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30">
                      삭제
                    </button>
                  </div>
                </div>

                {/* 통계 패널 (펼침) */}
                {expandedId === t.id && (
                  <div className="bg-[var(--bg)] border-b border-[var(--border)] px-6 py-4">
                    {statsLoading ? (
                      <p className="text-[var(--muted)] text-xs">통계 로딩 중...</p>
                    ) : !stats ? (
                      <p className="text-[var(--muted)] text-xs">통계를 불러올 수 없습니다.</p>
                    ) : (
                      <div className="space-y-4">
                        {/* 요약 */}
                        <div className="flex gap-6 text-xs">
                          <div className="bg-[var(--card)] rounded-lg px-4 py-3 border border-[var(--border)]">
                            <div className="text-[var(--muted)]">총 질의</div>
                            <div className="text-xl font-bold text-[var(--text)]">{stats.total_queries}</div>
                          </div>
                          <div className="bg-[var(--card)] rounded-lg px-4 py-3 border border-[var(--border)]">
                            <div className="text-[var(--muted)]">카테고리</div>
                            <div className="text-xl font-bold text-[var(--text)]">{stats.by_category.length}</div>
                          </div>
                          <div className="bg-[var(--card)] rounded-lg px-4 py-3 border border-[var(--border)]">
                            <div className="text-[var(--muted)]">최근 30일</div>
                            <div className="text-xl font-bold text-[var(--text)]">{stats.daily.reduce((s, d) => s + d.count, 0)}</div>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                          {/* 일별 호출 (바 차트) */}
                          {stats.daily.length > 0 && (
                            <div className="bg-[var(--card)] rounded-lg p-4 border border-[var(--border)]">
                              <h4 className="text-xs font-medium text-[var(--muted)] mb-3">일별 호출</h4>
                              <div className="flex items-end gap-1 h-20">
                                {stats.daily.slice(0, 14).reverse().map((d) => {
                                  const max = Math.max(...stats.daily.map(x => x.count), 1);
                                  const h = Math.max(4, (d.count / max) * 100);
                                  return (
                                    <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                                      <span className="text-[9px] text-[var(--muted)]">{d.count}</span>
                                      <div className="w-full bg-blue-500/60 rounded-t" style={{ height: `${h}%` }} title={`${d.date}: ${d.count}건`} />
                                      <span className="text-[8px] text-[var(--muted)]">{fmtShortDate(d.date)}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          {/* 카테고리별 */}
                          {stats.by_category.length > 0 && (
                            <div className="bg-[var(--card)] rounded-lg p-4 border border-[var(--border)]">
                              <h4 className="text-xs font-medium text-[var(--muted)] mb-3">카테고리별</h4>
                              <div className="space-y-2">
                                {stats.by_category.map((c) => {
                                  const max = Math.max(...stats.by_category.map(x => x.count), 1);
                                  const w = (c.count / max) * 100;
                                  return (
                                    <div key={c.category} className="flex items-center gap-2 text-xs">
                                      <span className="w-20 text-[var(--muted)] truncate">{c.category}</span>
                                      <div className="flex-1 bg-[var(--surface)] rounded-full h-2">
                                        <div className="bg-violet-500/60 h-2 rounded-full" style={{ width: `${w}%` }} />
                                      </div>
                                      <span className="text-[var(--text)] w-8 text-right">{c.count}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>

                        {/* 최근 질의 목록 */}
                        {stats.recent_queries.length > 0 && (
                          <div className="bg-[var(--card)] rounded-lg p-4 border border-[var(--border)]">
                            <h4 className="text-xs font-medium text-[var(--muted)] mb-3">최근 질의</h4>
                            <div className="space-y-1 max-h-40 overflow-y-auto">
                              {stats.recent_queries.map((q) => (
                                <div key={q.id} className="flex items-center gap-3 text-xs py-1 border-b border-[var(--border)]/30">
                                  <span className="text-[var(--muted)] w-28 shrink-0">{fmtDate(q.created_at)}</span>
                                  {q.category && <span className="px-1.5 py-0.5 rounded bg-[var(--surface)] text-[var(--muted)] text-[10px]">{q.category}</span>}
                                  <span className="text-[var(--text)] truncate">{q.question}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {stats.total_queries === 0 && (
                          <p className="text-[var(--muted)] text-xs text-center py-4">아직 호출 기록이 없습니다.</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 페이지네이션 */}
        {pagination.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2 mt-4 pt-4 border-t border-[var(--border)]">
            <button onClick={() => goPage(pagination.page - 1)} disabled={pagination.page <= 1}
              className="px-3 py-1.5 text-xs rounded-lg bg-[var(--surface)] text-[var(--text)] disabled:opacity-30 hover:opacity-80">
              이전
            </button>
            {Array.from({ length: pagination.total_pages }, (_, i) => i + 1)
              .filter(p => Math.abs(p - pagination.page) <= 2 || p === 1 || p === pagination.total_pages)
              .map((p, idx, arr) => (
                <span key={p}>
                  {idx > 0 && arr[idx - 1] !== p - 1 && <span className="text-[var(--muted)] text-xs px-1">...</span>}
                  <button onClick={() => goPage(p)}
                    className={`px-3 py-1.5 text-xs rounded-lg ${p === pagination.page
                      ? "bg-[var(--accent)] text-white"
                      : "bg-[var(--surface)] text-[var(--text)] hover:opacity-80"}`}>
                    {p}
                  </button>
                </span>
              ))}
            <button onClick={() => goPage(pagination.page + 1)} disabled={pagination.page >= pagination.total_pages}
              className="px-3 py-1.5 text-xs rounded-lg bg-[var(--surface)] text-[var(--text)] disabled:opacity-30 hover:opacity-80">
              다음
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
