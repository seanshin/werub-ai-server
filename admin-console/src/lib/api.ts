const GPU_BASE = typeof window !== "undefined"
  ? window.location.pathname.replace(/\/admin-console.*$/, "").replace(/\/+$/, "")
  : "";

export async function gpuFetch(path: string, options: {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
} = {}) {
  const headers: Record<string, string> = {
    ...options.headers,
  };
  if (options.body) headers["Content-Type"] = "application/json";

  const res = await fetch(`${GPU_BASE}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  return res.json();
}

export function gameHeaders(gameKey: string) {
  return { "X-Game-Key": gameKey };
}

export function tenantHeaders(tenantKey: string) {
  return { "X-Tenant-Key": tenantKey };
}
