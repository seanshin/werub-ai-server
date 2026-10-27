/**
 * WeRU.B Image API Client
 * Connects to the AI server's image generation endpoints
 */

const DEFAULT_BASE = "http://localhost:8585";

let _base = localStorage.getItem("image-gen-base") || DEFAULT_BASE;
let _apiKey = localStorage.getItem("image-gen-key") || "";

export function getBase() { return _base; }
export function setBase(url) {
  _base = url.replace(/\/+$/, "");
  localStorage.setItem("image-gen-base", _base);
}
export function setApiKey(key) {
  _apiKey = key;
  localStorage.setItem("image-gen-key", key);
}
export function getApiKey() { return _apiKey; }

function headers() {
  const h = { "Content-Type": "application/json" };
  if (_apiKey) h["Authorization"] = `Bearer ${_apiKey}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(`${_base}${path}`, {
    ...opts,
    headers: { ...headers(), ...opts.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Image Generation ──

export async function generate(params) {
  return api("/api/image/generate", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function getStatus(jobId) {
  return api(`/api/image/status/${jobId}`);
}

export async function pollUntilDone(jobId, { interval = 2000, onProgress } = {}) {
  while (true) {
    const data = await getStatus(jobId);
    if (onProgress) onProgress(data);
    if (data.status === "completed" || data.status === "done") return data;
    if (data.status === "failed" || data.status === "error") throw new Error(data.error || "Generation failed");
    await new Promise((r) => setTimeout(r, interval));
  }
}

// ── Models & Styles ──

export async function getModels() {
  return api("/api/image/models");
}

// ── Gallery ──

export async function getGallery() {
  return api("/api/image/gallery");
}

export async function deleteImage(filename) {
  return api(`/api/image/delete/${filename}`, { method: "DELETE" });
}

export function imageUrl(filename) {
  return `${_base}/api/image/view/${filename}`;
}

// ── Storage ──

export async function getStorage() {
  return api("/api/image/storage");
}

// ── Characters ──

export async function getCharacters() {
  return api("/api/image/characters");
}

export async function getCharacter(id) {
  return api(`/api/image/character/${id}`);
}

export function characterImageUrl(id) {
  return `${_base}/api/image/character/${id}/view`;
}
