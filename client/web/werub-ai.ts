/**
 * WeRU.B AI — 이미지 마킹/판별 API 클라이언트 (TypeScript)
 *
 * AI 기본법 §31 / 정보통신망법 대응:
 *  - 생성 이미지: 우리 API로 생성하면 가시 워터마크+PNG 메타+C2PA 서명이 자동 부착됨(별도 호출 불필요).
 *  - 판별: 임의 이미지가 AI 생성인지 detect (메타/C2PA 실판독 + ML 화소 분류).
 *
 * 사용처: Next.js 서버 라우트/Node 백엔드(권장 — API 키 보호). 브라우저 직접 호출 금지(키 노출).
 *
 * @example
 *   const ai = new WerubAI();                       // env OLLAMA_API_KEY 사용
 *   const r  = await ai.detectImage(fileBlob);
 *   const badge = aiBadge(r);                        // UI 배지
 */

export type Confidence = "high" | "medium" | "low" | "none";

export interface DetectResult {
  /** 종합 판단: AI 생성 여부 (확정 아님 — confidence 참고) */
  ai_generated: boolean;
  /** high=우리마킹/C2PA(확정), medium=타툴메타, low=ML추정만, none=신호없음 */
  confidence: Confidence;
  /** ML 화소 분류 확률(0~1) 또는 null(미실행) */
  ai_probability: number | null;
  /** 우리(WeRU.B) 마킹 판독 */
  our_marking: {
    marked: boolean;
    generator?: string;
    model?: string | null;
    created?: string;
    source?: string;
  };
  /** 외부 출처 신호 */
  provenance: {
    c2pa: boolean;
    ai_metadata: string[];
    software: string | null;
    hints: string[];
    c2pa_manifest?: { active_manifest?: string; claim_generator?: string } | null;
  };
  /** ML 분류기 상세 */
  ml: {
    available: boolean;
    ai_probability: number | null;
    label?: string;
    model?: string;
    scores?: { label: string; score: number }[];
    error?: string;
  };
  /** "metadata" 또는 "metadata+ml" */
  method: string;
  /** 법적/정확도 면책 — UI에 함께 노출 권장 */
  disclaimer: string;
}

export interface GenerateResult {
  job_id: string;
  status: string;
  position: number;
  model: string;
  model_name: string;
  style: string;
}

export interface WerubAIOptions {
  baseUrl?: string;
  apiKey?: string;
  /** fetch 타임아웃(ms). 기본 60s (ML 첫 호출 모델 로드 대비) */
  timeoutMs?: number;
}

export class WerubAIError extends Error {
  constructor(message: string, readonly status?: number, readonly body?: string) {
    super(message);
    this.name = "WerubAIError";
  }
}

export class WerubAI {
  private baseUrl: string;
  private apiKey: string;
  private timeoutMs: number;

  constructor(opts: WerubAIOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? process.env.OLLAMA_BASE_URL ?? "https://example.org/ollama").replace(/\/$/, "");
    this.apiKey = opts.apiKey ?? process.env.OLLAMA_API_KEY ?? "";
    this.timeoutMs = opts.timeoutMs ?? 60_000;
  }

  private async req(path: string, init: RequestInit): Promise<Response> {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeoutMs);
    try {
      const r = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        signal: ctrl.signal,
        headers: { Authorization: `Bearer ${this.apiKey}`, ...(init.headers ?? {}) },
      });
      if (!r.ok) throw new WerubAIError(`${path} failed`, r.status, await r.text().catch(() => ""));
      return r;
    } finally {
      clearTimeout(t);
    }
  }

  /** 파일/Blob 판별 (서버: Buffer→Blob, 브라우저 File 모두 가능) */
  async detectImage(image: Blob, opts: { ml?: boolean } = {}): Promise<DetectResult> {
    const fd = new FormData();
    fd.append("image", image);
    if (opts.ml !== undefined) fd.append("ml", String(opts.ml));
    return (await this.req("/api/detect/image", { method: "POST", body: fd })).json();
  }

  /** URL로 판별 (공개 http/https) */
  async detectUrl(url: string, opts: { ml?: boolean } = {}): Promise<DetectResult> {
    return (
      await this.req("/api/detect/image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, ml: opts.ml }),
      })
    ).json();
  }

  /** 이미지 생성(결과물은 자동 마킹+C2PA 서명됨). job_id 폴링 → imageStatus */
  async generateImage(
    prompt: string,
    opts: { model?: string; width?: number; height?: number; steps?: number; style?: string } = {},
  ): Promise<GenerateResult> {
    return (
      await this.req("/api/image/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, ...opts }),
      })
    ).json();
  }

  /** 생성 작업 상태/결과(s3_url 포함) */
  async imageStatus(jobId: string): Promise<{ status: string; images?: { s3_url?: string; filename: string }[]; error?: string }> {
    return (await this.req(`/api/image/status/${encodeURIComponent(jobId)}`, { method: "GET" })).json();
  }
}

/** UI 배지 헬퍼: 판별 결과 → 표시 라벨. ai_generated=false면 null. */
export function aiBadge(r: DetectResult): { label: string; level: "confirmed" | "likely" } | null {
  if (!r.ai_generated) return null;
  if (r.confidence === "high") return { label: "AI 생성물", level: "confirmed" };
  return { label: "AI 생성 추정", level: "likely" };
}
