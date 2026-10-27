import * as api from "./api.js";

// ── State ──
let models = [];
let styles = [];
let gallery = [];
let currentJob = null;
let previewImage = null;

// ── Render ──

function render() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="header">
      <h1>🎨 WeRU.B Image Gen</h1>
      <div class="status" id="serverStatus">Connecting...</div>
    </div>
    <div class="sidebar">
      ${renderSidebar()}
    </div>
    <div class="main">
      ${renderProgress()}
      ${renderGallery()}
    </div>
    ${previewImage ? renderPreview() : ""}
  `;
  bindEvents();
  checkServer();
}

function renderSidebar() {
  const modelOpts = models.map((m) =>
    `<option value="${m.id || m.name}">${m.name || m.id}</option>`
  ).join("");

  const styleOpts = styles.map((s) =>
    `<option value="${s}">${s}</option>`
  ).join("");

  return `
    <h3>Prompt</h3>
    <textarea id="prompt" placeholder="A beautiful landscape at sunset, detailed, 8k..."></textarea>

    <label>Negative Prompt</label>
    <textarea id="negPrompt" rows="2" placeholder="blur, low quality, watermark..."></textarea>

    <h3>Model & Style</h3>
    <label>Model</label>
    <select id="model">
      <option value="flux-schnell">flux-schnell (default)</option>
      ${modelOpts}
    </select>

    <label>Style</label>
    <select id="style">
      <option value="none">none</option>
      ${styleOpts}
    </select>

    <h3>Settings</h3>
    <div class="size-row">
      <div>
        <label>Width</label>
        <input id="width" type="number" value="1024" step="64" min="256" max="2048" />
      </div>
      <div>
        <label>Height</label>
        <input id="height" type="number" value="1024" step="64" min="256" max="2048" />
      </div>
    </div>

    <div class="size-row">
      <div>
        <label>Steps</label>
        <input id="steps" type="number" value="" placeholder="auto" min="1" max="100" />
      </div>
      <div>
        <label>CFG</label>
        <input id="cfg" type="number" value="7" step="0.5" min="1" max="30" />
      </div>
    </div>

    <label>Seed (-1 = random)</label>
    <input id="seed" type="number" value="-1" />

    <button class="btn" id="btnGenerate" ${currentJob ? "disabled" : ""}>
      ${currentJob ? "Generating..." : "🎨 Generate"}
    </button>

    <h3>Connection</h3>
    <label>Server URL</label>
    <input id="serverUrl" value="${api.getBase()}" />
    <label>API Key</label>
    <input id="apiKey" type="password" value="${api.getApiKey()}" placeholder="sk-..." />
    <button class="btn btn-secondary" id="btnConnect">Connect</button>

    <h3>Storage</h3>
    <div id="storageInfo" style="font-size:11px;color:var(--muted);">--</div>
    <button class="btn btn-secondary" id="btnRefresh" style="margin-top:8px;">Refresh Gallery</button>
  `;
}

function renderProgress() {
  if (!currentJob) return "";
  return `
    <div class="progress-bar">
      <div class="label">
        <span class="badge badge-${currentJob.status === "completed" ? "done" : currentJob.status === "failed" ? "error" : "running"}">
          ${currentJob.status}
        </span>
        <span style="margin-left:8px;font-size:11px;color:var(--muted);">${currentJob.job_id || ""}</span>
      </div>
      <div class="bar"><div class="fill" style="width:${currentJob.progress || 0}%"></div></div>
    </div>
  `;
}

function renderGallery() {
  if (gallery.length === 0) {
    return `
      <div class="empty">
        <div class="icon">🖼️</div>
        <p>No images yet. Generate your first image!</p>
      </div>
    `;
  }
  return `
    <div class="gallery">
      ${gallery.map((img) => `
        <div class="gallery-item" data-file="${img.filename}">
          <img src="${api.imageUrl(img.filename)}" alt="${img.prompt || ""}" loading="lazy" />
          <div class="meta">
            <span>${img.model || "unknown"}</span>
            <span>${img.size || ""}</span>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderPreview() {
  return `
    <div class="preview-overlay" id="previewOverlay">
      <img src="${previewImage}" />
    </div>
  `;
}

// ── Events ──

function bindEvents() {
  const $ = (id) => document.getElementById(id);

  $("btnGenerate")?.addEventListener("click", handleGenerate);
  $("btnConnect")?.addEventListener("click", () => {
    api.setBase($("serverUrl").value);
    api.setApiKey($("apiKey").value);
    checkServer();
    loadGallery();
  });
  $("btnRefresh")?.addEventListener("click", loadGallery);
  $("previewOverlay")?.addEventListener("click", () => {
    previewImage = null;
    render();
  });

  document.querySelectorAll(".gallery-item").forEach((el) => {
    el.addEventListener("click", () => {
      previewImage = api.imageUrl(el.dataset.file);
      render();
    });
  });

  // Enter key on prompt
  $("prompt")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleGenerate();
  });
}

// ── Actions ──

async function checkServer() {
  const el = document.getElementById("serverStatus");
  try {
    const data = await api.getModels();
    models = data.models || [];
    styles = data.styles || data.style_presets || [
      "photorealistic", "illustration", "anime", "oil-painting",
      "concept-art", "watercolor", "pixel-art", "logo", "high-detail",
      "lcm-fast", "flux-realism",
    ];
    if (el) el.textContent = `Connected — ${models.length} models`;
    if (el) el.style.color = "var(--green)";

    // Load storage info
    try {
      const storage = await api.getStorage();
      const si = document.getElementById("storageInfo");
      if (si) si.textContent = `${storage.count || 0} images · ${storage.size_human || "--"}`;
    } catch { /* ignore */ }

    render(); // re-render with models
  } catch {
    if (el) el.textContent = "Disconnected";
    if (el) el.style.color = "var(--red)";
  }
}

async function loadGallery() {
  try {
    const data = await api.getGallery();
    gallery = data.images || data.gallery || [];
    render();
  } catch { /* ignore */ }
}

async function handleGenerate() {
  const $ = (id) => document.getElementById(id);
  const prompt = $("prompt")?.value?.trim();
  if (!prompt) return;

  const params = {
    prompt,
    negative_prompt: $("negPrompt")?.value?.trim() || undefined,
    model: $("model")?.value || "flux-schnell",
    style: $("style")?.value || "none",
    width: parseInt($("width")?.value) || 1024,
    height: parseInt($("height")?.value) || 1024,
    cfg: parseFloat($("cfg")?.value) || 7,
  };

  const steps = parseInt($("steps")?.value);
  if (steps > 0) params.steps = steps;

  const seed = parseInt($("seed")?.value);
  if (seed >= 0) params.seed = seed;

  try {
    currentJob = { status: "pending", progress: 0 };
    render();

    const result = await api.generate(params);
    currentJob = { ...currentJob, job_id: result.job_id, status: "running", progress: 10 };
    render();

    const final = await api.pollUntilDone(result.job_id, {
      interval: 1500,
      onProgress: (data) => {
        currentJob = {
          ...currentJob,
          status: data.status,
          progress: data.status === "running" ? 50 : data.status === "completed" ? 100 : currentJob.progress,
        };
        render();
      },
    });

    currentJob = null;
    await loadGallery();
  } catch (e) {
    currentJob = { ...currentJob, status: "failed", error: e.message };
    render();
    setTimeout(() => { currentJob = null; render(); }, 3000);
  }
}

// ── Init ──
render();
loadGallery();
