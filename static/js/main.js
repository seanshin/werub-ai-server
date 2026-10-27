// WeRU.B AI Server — 관리 콘솔 프런트엔드
// __author__: 신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com> · License: Proprietary — see LICENSE
// ⚠️ 정적 파일이라 이 주석은 인증 없이 누구나 받아 볼 수 있다(/static/js/main.js 외부 200).
//    설계 경위: docs/01-plan/features/admin-menu-settings-cleanup.plan.md · 짝 템플릿 templates/admin.html

let chatHistory = [];

// 탭 전환 — 메인 메뉴 그룹 드롭다운 + URL hash(admin-menu-settings-cleanup Phase 1).
// 활성 표시는 data-tab **정확 일치**(예전 `.tab[onclick*="name"]` 부분 문자열 매칭은 이름이 겹치면
// 엉뚱한 탭을 켰다). hash는 `#<tab>` 또는 `#<tab>/<section>`(section은 Phase 2 Settings 절용).
// 메뉴에 있는 탭만 연다 — 역할 권한으로 메뉴에서 빠진 탭은 hash로도 열지 않는다(D6).
// ⚠️ 이건 **화면 편의**다 — 보안 경계는 서버(app.py `is_admin_request`·`_tab_permission_check`)에 있다.
//    메뉴 항목은 템플릿이 역할별 `allowed_tabs`로 렌더하므로 "메뉴에 있다 = 이 역할이 볼 수 있다".
function _tabAllowed(t) {
  return !!(t && document.getElementById('panel-' + t) && document.querySelector(`#mainNav [data-tab="${t}"]`));
}

// hash의 첫 조각만 탭 이름이다(`#settings/engine` → settings). 허용되지 않은 탭이면 null.
function _tabFromHash() {
  const t = decodeURIComponent((location.hash || '').replace(/^#/, '')).split('/')[0];
  return _tabAllowed(t) ? t : null;
}

// opts.fromHash — hashchange/초기 복원에서 불렸다는 뜻. 이때는 hash를 다시 쓰지 않는다
//   (쓰면 hashchange가 또 돌아 두 번 로드된다). 탭별 로더 호출 순서는 예전 코드를 그대로 뒀다
//   (image·training·users는 패널 표시 전, 나머지는 후) — 순서가 결과에 영향을 주는지는 검증하지 않았다.
function switchTab(name, opts = {}) {
  // Settings를 떠날 때 저장하지 않은 변경이 있으면 먼저 묻는다(아래 '미저장 변경 경고').
  // 취소하면 hash 이동(뒤로 가기 등)으로 바뀐 주소를 현재 절로 되돌린다 — 화면과 주소가 어긋나지 않게.
  const curSec = _currentSettingsSection();
  if (curSec && name !== 'settings' && !opts.force && settingsDirtyCards(curSec).length) {
    confirmLeaveSettings(curSec).then(ok => {
      if (ok) switchTab(name, {...opts, force: true});
      else if (opts.fromHash) history.replaceState(null, '', '#settings/' + curSec);
    });
    return;
  }
  if (!_tabAllowed(name)) name = 'dashboard';  // 알 수 없거나 권한 밖 → Dashboard
  if (name === 'image') { try { loadImageGallery(); } catch {} }
  if (name === 'training') { loadTrainingStatus(); try { loadFileList(); loadTargetModels(); loadCollectionStats(); } catch {} }
  if (name === 'users') loadUsers();
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.querySelectorAll('#mainNav [data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  // 드롭다운 안에 숨은 탭이 활성이면 그룹 버튼에 "운영 · Logs"처럼 현재 위치를 적는다 — 메뉴를 닫아도 어디인지 보이게.
  document.querySelectorAll('#mainNav .tab-group').forEach(g => {
    const cur = g.querySelector(`[data-tab="${name}"]`);
    g.classList.toggle('active', !!cur);
    const label = g.querySelector('.tab-group-cur');
    if (label) label.textContent = cur ? '· ' + cur.textContent.trim() : '';
  });
  closeTabGroups();
  if (!opts.fromHash && _tabFromHash() !== name) {
    history.replaceState(null, '', '#' + name);  // replace — 탭 전환마다 뒤로가기 기록을 쌓지 않는다
  }
  if (name === 'logs') loadLogs();
  if (name === 'models') { loadModels(); }  // 기본 모델·초기화 설정은 Settings › 모델·엔진으로 이관
  if (name === 'activity') loadActivityServers();
  if (name === 'queue') { loadQueueStatus(); loadBatches(); loadQueuePerf(); }
  // 링크가 지정한 절(openSettings)이 현재 hash보다 먼저 — 예전 순서는 이미 #settings/engine이면 배지가 system으로 못 갔다
  if (name === 'settings') showSettingsSection(_pendingSettingsSection || _settingsSectionFromHash() || _lastSettingsSection, {fromHash: !!opts.fromHash});
  if (name === 'knowledge') { loadRagStats(); }
  if (name === 'image') { loadImageModels(); }
}

// 열린 드롭다운을 닫는다. except = 방금 여는 그룹(한 번에 하나만 열리게 나머지만 닫는다).
// aria-expanded를 함께 맞춘다 — 스크린리더가 열림 상태를 읽는다.
function closeTabGroups(except) {
  document.querySelectorAll('#mainNav .tab-group.open').forEach(g => {
    if (g === except) return;
    g.classList.remove('open');
    const b = g.querySelector('.tab-group-btn'); if (b) b.setAttribute('aria-expanded', 'false');
  });
}

// 그룹 버튼 클릭 — hover로 열지 않는 이유: 터치 화면과 키보드 사용자가 열 수 없다(기획 §3.1).
// 열면 첫 항목에 포커스를 줘서 Tab/Enter만으로 고를 수 있게 한다.
function toggleTabGroup(btn) {
  const g = btn.closest('.tab-group');
  const open = !g.classList.contains('open');
  closeTabGroups(g);
  g.classList.toggle('open', open);
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) { const first = g.querySelector('.tab-item'); if (first) first.focus(); }
}

// 메뉴 밖을 누르면 닫고, Esc는 닫은 뒤 포커스를 그룹 버튼으로 돌려준다(키보드 사용자가 길을 잃지 않게).
document.addEventListener('click', e => { if (!e.target.closest('#mainNav .tab-group')) closeTabGroups(); });
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const g = document.querySelector('#mainNav .tab-group.open');
  if (g) { closeTabGroups(); const b = g.querySelector('.tab-group-btn'); if (b) b.focus(); }
});
// 뒤로가기·주소창 수정·다른 곳의 `location.hash = …`를 탭 전환으로 잇는다. 허용 안 된 hash는 무시.
window.addEventListener('hashchange', () => { const t = _tabFromHash(); if (t) switchTab(t, {fromHash: true}); });

// ── Settings 6개 절 (Phase 2) — `#settings/<절>`. 절마다 필요한 것만 불러온다 ─────────────
// 절 이름은 템플릿의 `data-section`과 1:1 — 여기에 없는 이름은 security로 떨어진다(회귀가 양쪽 일치를 검사).
const SETTINGS_SECTIONS = ['security', 'engine', 'alerts', 'storage', 'integration', 'system'];
// 마지막으로 본 절 — hash 없이 Settings로 돌아오면 그 절을 다시 연다.
let _lastSettingsSection = 'security';
// openSettings()가 요청한 절. switchTab('settings')가 **현재 hash보다 먼저** 이것을 쓴다
// (반대 순서면 이미 #settings/engine일 때 배지 → system 이동이 무시됐다 — v2.125.59 수정).
let _pendingSettingsSection = null;

function _settingsSectionFromHash() {
  const [t, sec] = decodeURIComponent((location.hash || '').replace(/^#/, '')).split('/');
  return t === 'settings' && SETTINGS_SECTIONS.includes(sec) ? sec : null;
}

// 다른 탭에서 "설정에서 변경 →" 링크가 부른다(대시보드 상주 카드·Models 이관 안내·버전 배지).
function openSettings(section) {
  _pendingSettingsSection = SETTINGS_SECTIONS.includes(section) ? section : null;
  switchTab('settings');
}

// 한 번에 한 절만 보이고, **그 절에 필요한 것만** 불러온다 — 예전엔 Settings를 열면 키·성능·이미지 저장소를
// 전부 조회했다. 절마다 로더가 다르다는 점에 주의: 카드를 다른 절로 옮기면 아래 매핑도 옮겨야 값이 채워진다.
// (security·alerts가 둘 다 loadSettings인 이유: 인증 체크박스와 webhook 상태를 한 번에 읽는 기존 함수라서.)
function showSettingsSection(sec, opts = {}) {
  if (!SETTINGS_SECTIONS.includes(sec)) sec = 'security';
  const leaving = _currentSettingsSection();
  if (leaving && !opts.force && settingsDirtyCards(leaving).length) {
    // 같은 절을 다시 누른 경우: 로더가 입력칸을 서버 값으로 **덮어써** 편집이 경고 없이 사라진다 → 아무것도 안 한다.
    if (leaving === sec) return;
    confirmLeaveSettings(leaving).then(ok => {
      if (ok) showSettingsSection(sec, {...opts, force: true});
      else if (opts.fromHash) history.replaceState(null, '', '#settings/' + leaving);
    });
    return;
  }
  _pendingSettingsSection = null;
  _lastSettingsSection = sec;
  document.querySelectorAll('#settingsNav .settings-nav-item').forEach(b => b.classList.toggle('active', b.dataset.section === sec));
  document.querySelectorAll('#panel-settings .settings-section').forEach(x => x.classList.toggle('active', x.dataset.section === sec));
  if (!opts.fromHash && location.hash !== '#settings/' + sec) history.replaceState(null, '', '#settings/' + sec);
  if (sec === 'security' || sec === 'alerts') loadSettings();
  if (sec === 'engine') { refreshResidency(true); loadDefaultModelConfig(); loadInitConfig(); loadPerfSettings(); }
  if (sec === 'alerts') loadSettingsAlertSummary();
  if (sec === 'storage') { loadImageStorage(); loadSettingsS3Status(); }
  if (sec === 'system') { loadChangelog(); loadConfigOverview(); }
  if (sec === 'integration') loadConfigOverview();
}

// ── 미저장 변경 경고 (admin-menu-settings-cleanup §4.1 잔여) ─────────────
// 🔑 '저장됨'의 기준 = **그 입력칸을 마지막으로 채운 서버 값**(로더가 채운 직후·저장 성공 직후에 기록).
//    절마다 저장 방식이 달라(저장 버튼·즉시 동작 버튼·읽기 전용) 기준부터 정해야 했다(09-29 이월 사유):
//  · 추적: 저장 버튼이 있는 설정 입력칸 — 인증 2 · 큐 기본 모델 · 모델 초기화 7 · Ollama 성능(쓰기 가능 환경만) · 이미지 보관 3.
//  · webhook 3종은 시크릿이라 서버 값이 화면에 없다(D2 — 칸은 늘 비어 있다). 비어 있으면 '변경 없음', 뭔가 쓰였으면 미저장.
//  · 추적 안 함: 비밀번호 칸(설정이 아니라 1회 동작 — 브라우저 자동완성이 가짜 경고를 만든다) · API 키 이름 ·
//    상주 모델 선택(누르는 버튼이 곧 동작이라 '저장'이 없다) · 읽기 전용 절(연동·시스템).
// ⚠️ 새 설정 입력칸을 추가하면 그 로더 끝과 저장 성공 지점에 markSettingsClean을 넣어야 추적된다
//    (기준값이 없는 칸은 조용히 추적 밖이다 — 회귀 test_settings_unsaved_guard_wired가 현재 목록만 검사한다).
const _settingsBaseline = new Map();   // 입력 요소 → 마지막 서버 값
const _SETTINGS_SECRET_INPUTS = ['cfgSlack', 'cfgDiscord', 'cfgWebhook'];

function _fieldValue(el) { return el.type === 'checkbox' ? el.checked : el.value; }

// ids: 요소 id 또는 요소 목록. 로더가 값을 채운 직후·저장 성공 직후에 부른다.
function markSettingsClean(ids) {
  _settingsBaseline.forEach((_v, el) => { if (!el.isConnected) _settingsBaseline.delete(el); });  // 성능 카드는 로드마다 새 요소
  ids.forEach(x => {
    const el = typeof x === 'string' ? document.getElementById(x) : x;
    if (el) _settingsBaseline.set(el, _fieldValue(el));
  });
}

function _settingsRoot(sec) {
  return sec ? document.querySelector(`#panel-settings .settings-section[data-section="${sec}"]`)
             : document.getElementById('panel-settings');
}

// 저장하지 않은 변경이 있는 카드 제목 목록(sec 없으면 Settings 전체). 비활성(읽기 전용) 칸은 세지 않는다.
function settingsDirtyCards(sec) {
  const root = _settingsRoot(sec);
  if (!root) return [];
  const dirty = [];
  _settingsBaseline.forEach((v, el) => {
    if (el.isConnected && !el.disabled && root.contains(el) && _fieldValue(el) !== v) dirty.push(el);
  });
  _SETTINGS_SECRET_INPUTS.forEach(id => {
    const el = document.getElementById(id);
    if (el && root.contains(el) && el.value.trim()) dirty.push(el);
  });
  return [...new Set(dirty.map(el => {
    const h = el.closest('.card') && el.closest('.card').querySelector('h3');
    return h && h.firstChild ? h.firstChild.textContent.trim() : '설정';
  }))];
}

// 편집을 버린다 = 입력칸을 기준값으로 되돌린다. 되돌리지 않으면 Settings에 다시 들어올 때
// 숨은 절에 남은 편집 때문에 같은 경고가 또 뜬다.
function _discardSettings(sec) {
  const root = _settingsRoot(sec);
  _settingsBaseline.forEach((v, el) => {
    if (!root || !root.contains(el)) return;
    if (el.type === 'checkbox') el.checked = v; else el.value = v;
  });
  _SETTINGS_SECRET_INPUTS.forEach(id => { const el = document.getElementById(id); if (el && root && root.contains(el)) el.value = ''; });
}

// 지금 보고 있는 Settings 절(Settings 탭이 아니면 null).
function _currentSettingsSection() {
  const p = document.getElementById('panel-settings');
  return p && p.classList.contains('active') ? _lastSettingsSection : null;
}

async function confirmLeaveSettings(sec) {
  const cards = settingsDirtyCards(sec);
  if (!cards.length) return true;
  const ok = await showConfirm(`저장하지 않은 변경이 있습니다: ${cards.join(', ')}.\n저장하지 않고 이동하면 변경은 버려집니다.`,
                               '저장하지 않은 변경', '버리고 이동');
  if (ok) _discardSettings(sec);
  return ok;
}

// 새로고침·창 닫기·로그아웃 링크 — 브라우저 기본 확인창(문구는 브라우저가 정한다).
window.addEventListener('beforeunload', e => {
  if (settingsDirtyCards().length) { e.preventDefault(); e.returnValue = ''; }
});

// ── 고급 설정 표 + 접속 정보 (Phase 3) — /api/config/overview ─────────────
// 마지막 응답을 들고 있다가 키 검색(renderConfigOverview)은 서버를 다시 부르지 않고 이것만 거른다.
// 연동·연결 절(접속 정보)과 시스템 정보 절(고급 설정 표)이 같은 응답을 나눠 쓴다.
let _cfgOverview = null;

async function loadConfigOverview() {
  try {
    const r = await fetch(api('/api/config/overview'));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    _cfgOverview = await r.json();
  } catch (e) {
    const t = document.getElementById('cfgOverviewTable');
    if (t) t.innerHTML = `<tr><td colspan="4" style="color:var(--red)">조회 실패: ${escHtml(e.message)}</td></tr>`;
    const c = document.getElementById('settingsConnection');
    if (c) c.textContent = '조회 실패: ' + e.message;
    return;
  }
  renderConfigOverview();
  renderConnectionInfo();
}

// 표의 '현재값' 칸. 시크릿은 서버가 이미 {set, hint}로 가려서 준다(D2) — 원문은 이 화면에 오지 않는다.
// 빈 값·null을 "(비어 있음)"으로 구분해 보여 준다(0·false와 헷갈리지 않게 문자열 비교만 한다).
function _fmtCfgValue(k) {
  const v = k.value;
  if (k.secret) return v && v.set ? `설정됨${v.hint ? ' (' + escHtml(v.hint) + ')' : ''}` : '<span style="color:var(--muted)">미설정</span>';
  if (v === null || v === undefined || v === '') return '<span style="color:var(--muted)">(비어 있음)</span>';
  return '<code>' + escHtml(typeof v === 'string' ? v : JSON.stringify(v)) + '</code>';
}

// '코드 기본값'이 여러 개면 코드 곳곳이 같은 키에 서로 다른 기본값을 쓴다는 뜻 → ⚠ 표시.
// 지금은 config에 값이 있어 무증상이지만 키가 빠진 신규 설치에서 경로마다 다르게 동작한다(v2.125.59 첫 스캔 4키).
// '바꾸는 곳'이 설정 절이 아니면(API·config.json 직접) 흐리게 — 화면에서 못 바꾼다는 신호.
function renderConfigOverview() {
  const t = document.getElementById('cfgOverviewTable');
  if (!t || !_cfgOverview) return;
  const q = (document.getElementById('cfgOverviewFilter').value || '').trim().toLowerCase();
  const rows = _cfgOverview.keys.filter(k => !q || k.key.toLowerCase().includes(q));
  t.innerHTML = rows.map(k => {
    const d = k.defaults || [];
    const dflt = d.length === 0 ? '' : d.map(x => '<code>' + escHtml(x) + '</code>').join(' ')
      + (d.length > 1 ? ' <span style="color:var(--yellow)" title="코드 곳마다 기본값이 다르다">⚠ ' + d.length + '곳 상이</span>' : '');
    const edit = k.edit.startsWith('설정') ? k.edit : `<span style="color:var(--muted)">${escHtml(k.edit)}</span>`;
    return `<tr><td style="font-size:12px;"><code>${escHtml(k.key)}</code></td><td style="font-size:12px;">${_fmtCfgValue(k)}</td>`
      + `<td style="font-size:12px;">${dflt}</td><td style="font-size:12px;">${edit}</td></tr>`;
  }).join('') || '<tr><td colspan="4" style="color:var(--muted)">일치하는 키 없음</td></tr>';
}

// 접속 정보 — 예전 하드코딩 4줄(localhost:8585 등)은 2026-09-09 유입 경로 재편 뒤 틀린 채 남아 있었다.
// 외부 주소는 **지금 이 화면을 연 주소**(location.origin + BASE)로 만든다 — 서버는 앞단 프록시가 어떤
// 공개 주소로 부르는지 확실히 모른다. 내부 바인드는 서버가 실제로 받은 SERVER_NAME:PORT.
function renderConnectionInfo() {
  const el = document.getElementById('settingsConnection');
  if (!el || !_cfgOverview) return;
  const c = _cfgOverview.connection || {};
  const pub = location.origin + (BASE || '') + '/';
  const worker = c.apple_worker_enabled
    ? `활성 · 위임 ${escHtml(JSON.stringify(c.apple_worker_delegate || []))} · 상태는 <a class="settings-link" onclick="switchTab('dashboard')">Dashboard</a>`
    : '비활성';
  el.innerHTML = [
    ['이 화면 주소', `<code>${escHtml(pub)}</code>`],
    ['Swagger', `<code>${escHtml(pub + 'docs')}</code>`],
    ['내부 바인드', `<code>${escHtml(c.bind || '-')}</code>`],
    ['Ollama 엔진', `<code>${escHtml(c.ollama_base || '-')}</code>`],
    ['유입 모드', `<code>${escHtml(c.ingress_mode || '-')}</code>`],
    ['iMac 워커', worker],
  ].map(([k, v]) => `<div><strong>${k}:</strong> ${v}</div>`).join('');
}

// 알림 절의 '최근 알림' 5건 — webhook 설정 옆에서 "지금 알림이 실제로 나가고 있나"를 바로 보려고.
// channel이 'log'뿐이면 webhook이 설정되지 않아 로그에만 남는다는 뜻. 전체 이력은 Logs 탭.
async function loadSettingsAlertSummary() {
  const el = document.getElementById('settingsAlertSummary');
  if (!el) return;
  try {
    const d = await fetch(api('/api/alerts/history?limit=5')).then(r => r.json());
    const a = d.alerts || [];
    el.innerHTML = a.length
      ? a.map(x => `<div>${formatDate(x.ts)} · <span style="color:${x.level === 'critical' ? 'var(--red)' : 'var(--yellow)'}">${x.level}</span> · ${escHtml(x.title)} · <span style="color:var(--muted)">${x.channel || '-'}</span></div>`).join('')
      : '최근 알림 없음';
  } catch (e) { el.textContent = '조회 실패: ' + e.message; }
}

// 저장소 절 — 오브젝트 스토리지(MinIO, 2026-09 Wasabi에서 전환) 연결 상태. 읽기 전용: 접속 정보·키는 config.json에서만 바꾼다.
async function loadSettingsS3Status() {
  const el = document.getElementById('settingsS3Status');
  if (!el) return;
  try {
    const d = await fetch(api('/api/image/s3/status')).then(r => r.json());
    el.innerHTML = !d.enabled ? '비활성'
      : `${d.accessible ? '<span style="color:var(--green)">접속 정상</span>' : '<span style="color:var(--red)">접속 불가</span>'}`
        + ` · 버킷 <code>${escHtml(d.bucket || '-')}</code> · 접두 <code>${escHtml(d.prefix || '')}</code>`
        + ` · 엔드포인트 <code>${escHtml(d.endpoint || '-')}</code> · 공개 <code>${escHtml(d.public_endpoint || '-')}</code>`;
  } catch (e) { el.textContent = '조회 실패: ' + e.message; }
}

function toast(msg, error) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (error ? ' error' : '');
  setTimeout(() => t.className = 'toast', 3000);
}

// 저장 요청 공용 — 응답대로 알린다(admin-menu-settings-cleanup D4).
// 예전엔 저장 3곳이 응답을 안 보고 성공 토스트를 띄웠다(세션 만료 403에도 "saved").
// 성공이면 응답 JSON, 실패면 null.
async function saveJSON(url, body, okMsg, method = 'POST') {
  let r, d = {};
  try {
    r = await fetch(api(url), {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    try { d = await r.json(); } catch (e) {}
  } catch (e) { toast('저장 실패: ' + e.message, true); return null; }
  if (!r.ok || d.ok === false) {
    const why = (r.status === 401 || (r.status === 403 && d.error === 'unauthorized'))
      ? '세션 만료 — 다시 로그인하세요'
      : (d.reason || d.error || ('HTTP ' + r.status));
    toast('저장 실패: ' + why, true);
    return null;
  }
  if (okMsg) toast(okMsg);
  return d;
}

function formatSize(bytes) {
  if (!bytes) return 'N/A';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return bytes.toFixed(1) + ' ' + units[i];
}

// 일부 레거시 타임스탬프가 '...+00:00Z'(이중 접미사)로 저장돼 Date 파싱이 실패함 → 보정
function _cleanTs(s) {
  return (typeof s === 'string') ? s.replace(/([+-]\d{2}:\d{2})Z$/, '$1') : s;
}
function formatDate(d) {
  if (!d) return 'N/A';
  const dt = new Date(_cleanTs(d));
  return isNaN(dt.getTime()) ? 'N/A' : dt.toLocaleString('ko-KR');
}

function shortId(id) { return id ? id.substring(0, 8) : ''; }

function escHtml(s) {
  return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
}

// Base path 자동 감지 — /ollama/ 등 리버스 프록시 경로 대응
const BASE = (() => {
  const p = window.location.pathname.replace(/\/+$/, '');
  // pathname이 / 이면 base 없음, /ollama 등이면 그것이 base
  return p || '';
})();
function api(path) { return BASE + path; }

// --- Status & Dashboard ---

async function loadSystemInfo() {
  try {
    const r = await fetch(api('/api/system/info'));
    const d = await r.json();
    const el = document.getElementById('systemInfoBadge');
    if (!el) return;
    const gpu = d.gpu_type === 'cuda'  ? 'CUDA ' + (d.gpu_name||'') :
                d.gpu_type === 'metal' ? 'Metal GPU · ' + (d.gpu_name||'Apple Silicon') +
                                         (d.gpu_cores ? ' · ' + d.gpu_cores + '-core GPU' : '') : 'CPU only';
    const rec = d.recommended || {};
    el.innerHTML =
      '<span style="font-size:11px;color:var(--muted);">' +
      d.os + ' ' + d.arch + ' | ' + gpu + ' | RAM ' + d.ram_gb + 'GB | ' +
      d.cpu_count + ' cores | rec: ' + (rec.model||'-') +
      (rec.training_capable ? ' | train: ' + (rec.training_model||'-') : '') +
      '</span>';
  } catch {}
}

async function refreshStatus() {
  try {
    const r = await fetch(api('/api/status'));
    const data = await r.json();
    const badge = document.getElementById('statusBadge');
    if (data.running) {
      badge.textContent = 'Online';
      badge.className = 'status-badge status-online';
      document.getElementById('statStatus').textContent = 'Online';
      document.getElementById('statStatus').style.color = 'var(--green)';
    } else {
      badge.textContent = 'Offline';
      badge.className = 'status-badge status-offline';
      document.getElementById('statStatus').textContent = 'Offline';
      document.getElementById('statStatus').style.color = 'var(--red)';
    }
    const models = data.models || [];
    document.getElementById('statModels').textContent = models.length;
    const totalSize = models.reduce((s, m) => s + (m.size || 0), 0);
    document.getElementById('statSize').textContent = formatSize(totalSize);
    const running = data.running_models || [];
    document.getElementById('statRunning').textContent = running.length;

    const tbody = document.getElementById('runningTable');
    if (running.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="color:var(--muted)">No running models</td></tr>';
    } else {
      tbody.innerHTML = running.map(m => `<tr>
        <td>${m.name}</td><td>${formatSize(m.size)}</td>
        <td>${formatSize(m.size_vram)}</td>
        <td>${m.expires_at ? formatDate(m.expires_at) : 'persistent'}</td>
      </tr>`).join('');
    }

    // 기본 모델 설정 가져오기
    let defaultModel = '';
    try {
      const cfgR = await fetch(api('/api/config'));
      const cfgD = await cfgR.json();
      defaultModel = cfgD.default_model || '';
    } catch(e) {}

    const selects = ['chatModel', 'genModel', 'queueModel'];
    selects.forEach(id => {
      const sel = document.getElementById(id);
      const cur = sel.value;
      sel.innerHTML = models.map(m => {
        const isDefault = m.name === defaultModel;
        return `<option value="${m.name}" ${isDefault ? 'selected' : ''}>${m.name}${isDefault ? ' ★' : ''}</option>`;
      }).join('');
      // 사용자가 이미 선택한 값이 있으면 유지
      if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
    });
  } catch(e) {
    document.getElementById('statusBadge').textContent = 'Error';
    document.getElementById('statusBadge').className = 'status-badge status-offline';
  }

  // Queue stats for dashboard
  try {
    const qr = await fetch(api('/api/queue/status'));
    const qs = await qr.json();
    document.getElementById('statQPending').textContent = qs.pending || 0;
    document.getElementById('statQRunning').textContent = qs.running || 0;
    document.getElementById('statQCompleted').textContent = qs.completed || 0;
    document.getElementById('statQFailed').textContent = qs.failed || 0;
    // Also update queue panel if visible
    const qp = document.getElementById('qPending');
    if (qp) {
      document.getElementById('qPending').textContent = qs.pending || 0;
      document.getElementById('qRunning').textContent = qs.running || 0;
      document.getElementById('qCompleted').textContent = qs.completed || 0;
      document.getElementById('qFailed').textContent = qs.failed || 0;
    }
    if (typeof updatePauseUI === 'function') updatePauseUI(!!qs.paused);
  } catch(e) {}
}

// --- Queue ---
async function loadQueueStatus() {
  try {
    const r = await fetch(api('/api/queue/status'));
    const data = await r.json();
    document.getElementById('qPending').textContent = data.pending || 0;
    document.getElementById('qRunning').textContent = data.running || 0;
    document.getElementById('qCompleted').textContent = data.completed || 0;
    document.getElementById('qFailed').textContent = data.failed || 0;
  } catch(e) {}
}

async function loadBatches() {
  try {
    const r = await fetch(api('/api/queue/batches'));
    const data = await r.json();
    const batches = data.batches || [];
    const tbody = document.getElementById('batchesTable');
    if (batches.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">No batches</td></tr>';
      return;
    }
    tbody.innerHTML = batches.map(b => {
      const cPct = b.total ? Math.round(b.completed/b.total*100) : 0;
      const rPct = b.total ? Math.round(b.running/b.total*100) : 0;
      const fPct = b.total ? Math.round(b.failed/b.total*100) : 0;
      const pPct = 100 - cPct - rPct - fPct;
      return `<tr>
        <td><code style="font-size:12px;">${shortId(b.batch_id)}</code></td>
        <td>${b.total}</td>
        <td style="min-width:200px;">
          <div style="font-size:12px;color:var(--muted);">${b.completed}/${b.total} done${b.failed?' ('+b.failed+' fail)':''}</div>
          <div class="batch-progress">
            <div class="bp-completed" style="width:${cPct}%"></div>
            <div class="bp-running" style="width:${rPct}%"></div>
            <div class="bp-failed" style="width:${fPct}%"></div>
            <div class="bp-pending" style="width:${pPct}%"></div>
          </div>
        </td>
        <td>${formatDate(b.created_at)}</td>
        <td style="white-space:nowrap;">
          <button class="btn btn-outline btn-sm" onclick="viewBatch('${b.batch_id}')">View</button>
          <a class="btn btn-outline btn-sm" href="/api/queue/batch/${b.batch_id}/results" target="_blank">JSONL</a>
          ${b.pending > 0 ? `<button class="btn btn-danger btn-sm" onclick="cancelBatch('${b.batch_id}')">Cancel</button>` : ''}
        </td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

async function loadQueuePerf() {
  try {
    const r = await fetch(api('/api/queue/stats'));
    const s = await r.json();

    // Performance summary
    const perfDiv = document.getElementById('perfStats');
    if (s.total > 0) {
      perfDiv.style.display = '';
      document.getElementById('perfTotal').textContent = s.total.toLocaleString();
      document.getElementById('perfTokens').textContent = s.total_tokens.toLocaleString();
      document.getElementById('perfSpeed').textContent = s.avg_tok_per_sec;
      document.getElementById('perfDuration').textContent = (s.avg_duration_ms/1000).toFixed(1)+'s';
      // ETA
      if (s.eta_sec > 0) {
        document.getElementById('etaStat').style.display = '';
        const m = Math.floor(s.eta_sec/60), sec = s.eta_sec%60;
        document.getElementById('perfETA').textContent = m > 0 ? m+'m '+sec+'s' : sec+'s';
      } else {
        document.getElementById('etaStat').style.display = 'none';
      }
    }

    // Model performance table
    const models = s.jobs_per_model || {};
    const modelKeys = Object.keys(models);
    const mpCard = document.getElementById('modelPerfCard');
    if (modelKeys.length > 0) {
      mpCard.style.display = '';
      document.getElementById('modelPerfTable').innerHTML = modelKeys.map(k => {
        const m = models[k];
        return `<tr>
          <td><strong>${k}</strong></td>
          <td>${m.count}</td>
          <td>${m.tokens.toLocaleString()}</td>
          <td>${Math.round(m.avg_tokens)}</td>
          <td>${(m.avg_duration_ms/1000).toFixed(1)}s</td>
          <td><strong>${m.tok_per_sec} tok/s</strong></td>
        </tr>`;
      }).join('');
    }

    // Recent jobs
    const jobs = s.recent_jobs || [];
    const tbody = document.getElementById('recentJobsTable');
    if (jobs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted)">No jobs yet</td></tr>';
      return;
    }
    tbody.innerHTML = jobs.map(j => {
      const statusCls = j.status === 'completed' ? 'green' : j.status === 'failed' ? 'red' : j.status === 'running' ? 'accent' : 'yellow';
      const isImage = j.job_type === 'image';
      const typeLabel = isImage
        ? '<span style="font-size:11px;padding:1px 5px;background:rgba(139,92,246,0.2);color:#a78bfa;border-radius:3px;">img</span>'
        : '<span style="font-size:11px;padding:1px 5px;background:rgba(59,130,246,0.15);color:#60a5fa;border-radius:3px;">llm</span>';
      const prompt = escHtml((j.prompt||'').substring(0, 50));

      let respCell = '-';
      if (j.error) {
        respCell = `<span style="color:var(--red);font-size:12px;">${escHtml(j.error.substring(0,60))}</span>`;
      } else if (isImage && j.response) {
        try {
          const img = JSON.parse(j.response);
          const url = api('/api/image/view/' + encodeURIComponent(img.filename)) + (img.subfolder ? '?subfolder=' + encodeURIComponent(img.subfolder) : '');
          respCell = `<a href="${url}" target="_blank"><img src="${url}" style="height:36px;width:36px;object-fit:cover;border-radius:3px;vertical-align:middle;" onerror="this.outerHTML='<span style=\'color:var(--red);font-size:11px;\'>삭제됨</span>'"></a>`;
        } catch { respCell = escHtml(j.response.substring(0, 50)); }
      } else if (j.response) {
        respCell = `<span class="truncate" title="${escHtml(j.response)}" style="font-size:12px;">${escHtml(j.response.substring(0,50))}</span>`;
      }

      const tokCell = isImage ? '<span style="color:var(--muted)">—</span>' : (j.tokens || '-');
      const durCell = j.duration_ms ? (j.duration_ms/1000).toFixed(1)+'s' : '-';

      return `<tr>
        <td><span style="color:var(--${statusCls});font-weight:600;font-size:12px;">${j.status}</span></td>
        <td>${typeLabel}</td>
        <td style="font-size:12px;">${escHtml(j.model||'')}</td>
        <td class="truncate" title="${escHtml(j.prompt||'')}" style="font-size:12px;">${prompt}</td>
        <td>${respCell}</td>
        <td>${tokCell}</td>
        <td>${durCell}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

async function quickSubmit() {
  const model = document.getElementById('queueModel').value;
  const prompt = document.getElementById('queuePrompt').value.trim();
  if (!model || !prompt) { toast('Model and prompt required', true); return; }
  const system = document.getElementById('queueSystem').value.trim();
  const payload = { model, prompt };
  if (system) payload.system = system;
  const temp = parseFloat(document.getElementById('queueTemp').value);
  const topP = parseFloat(document.getElementById('queueTopP').value);
  const numPredict = parseInt(document.getElementById('queueNumPredict').value);
  const options = {};
  if (!isNaN(temp)) options.temperature = temp;
  if (!isNaN(topP)) options.top_p = topP;
  if (!isNaN(numPredict)) options.num_predict = numPredict;
  if (Object.keys(options).length > 0) payload.options = options;
  try {
    const r = await fetch(api('/api/queue/submit'), {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (data.job_id) {
      toast(`Job submitted: ${shortId(data.job_id)} (position: ${data.position})`);
      document.getElementById('queuePrompt').value = '';
      loadQueueStatus();
    } else {
      toast(data.error || 'Submit failed', true);
    }
  } catch(e) { toast('Submit error', true); }
}

async function viewBatch(batchId) {
  try {
    const r = await fetch(`${BASE}/api/queue/batch/${batchId}`);
    const data = await r.json();
    const jobs = data.jobs || [];
    const modal = document.getElementById('jobModal');
    modal.style.display = 'block';
    modal.innerHTML = `<div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
          <h3>Batch ${shortId(batchId)}</h3>
          <div>
            <span style="font-size:13px;color:var(--muted);">${data.completed}/${data.total} done</span>
            <button class="btn btn-outline btn-sm" onclick="closeModal()" style="margin-left:8px;">Close</button>
          </div>
        </div>
        <table>
          <thead><tr><th>Status</th><th>Prompt</th><th>Response</th><th>Tokens</th><th>Time</th></tr></thead>
          <tbody>${jobs.map(j => {
            const isImg = j.job_type === 'image';
            let respCell = escHtml((j.response||j.error||'').substring(0,60));
            if (isImg && j.response && !j.error) {
              try {
                const img = JSON.parse(j.response);
                const url = api('/api/image/view/' + encodeURIComponent(img.filename)) + (img.subfolder ? '?subfolder=' + encodeURIComponent(img.subfolder) : '');
                respCell = `<a href="${url}" target="_blank"><img src="${url}" style="height:32px;width:32px;object-fit:cover;border-radius:3px;vertical-align:middle;"></a>`;
              } catch {}
            }
            return `<tr>
              <td><span class="badge badge-${j.status}">${j.status}</span></td>
              <td class="truncate" title="${escHtml(j.prompt)}">${escHtml(j.prompt?.substring(0,60))}</td>
              <td>${respCell}</td>
              <td>${isImg ? '—' : (j.tokens||'-')}</td>
              <td>${j.duration_ms ? (j.duration_ms/1000).toFixed(1)+'s' : '-'}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>
    </div>`;
  } catch(e) { toast('Error loading batch', true); }
}

function closeModal() {
  document.getElementById('jobModal').style.display = 'none';
  document.getElementById('jobModal').innerHTML = '';
}

// --- Custom Confirm Modal ---
let _confirmResolve = null;

function showConfirm(msg, title = '확인', okLabel = '삭제') {
  return new Promise(resolve => {
    _confirmResolve = resolve;
    document.getElementById('confirmMsg').textContent = msg;
    document.getElementById('confirmTitle').textContent = title;
    document.getElementById('confirmOkBtn').textContent = okLabel;
    document.getElementById('confirmModal').style.display = 'block';
  });
}

function closeConfirm(result) {
  document.getElementById('confirmModal').style.display = 'none';
  if (_confirmResolve) { _confirmResolve(result); _confirmResolve = null; }
}

let _promptResolve = null;
function showPrompt(msg, title = '입력', defaultVal = '', okLabel = '확인') {
  return new Promise(resolve => {
    _promptResolve = resolve;
    document.getElementById('promptMsg').textContent = msg;
    document.getElementById('promptTitle').textContent = title;
    document.getElementById('promptOkBtn').textContent = okLabel;
    const inp = document.getElementById('promptInput');
    inp.value = defaultVal;
    inp.onkeydown = (e) => {
      if (e.key === 'Enter') closePromptOk();
      else if (e.key === 'Escape') closePrompt(null);
    };
    document.getElementById('promptModal').style.display = 'block';
    setTimeout(() => { inp.focus(); inp.select(); }, 50);
  });
}
function closePrompt(val) {
  document.getElementById('promptModal').style.display = 'none';
  if (_promptResolve) { _promptResolve(val); _promptResolve = null; }
}
function closePromptOk() {
  closePrompt(document.getElementById('promptInput').value);
}

async function cancelBatch(batchId) {
  if (!await showConfirm('이 배치의 대기 중 작업을 모두 취소할까요?', '배치 취소', '취소하기')) return;
  await fetch(`${BASE}/api/queue/cancel/batch/${batchId}`, {method:'POST'});
  toast('Batch cancelled');
  loadBatches();
  loadQueueStatus();
}

async function purgeCompleted() {
  if (!await showConfirm('완료 및 실패 작업을 모두 삭제할까요?', '작업 삭제', '삭제')) return;
  const r = await fetch(api('/api/queue/purge'), {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  const data = await r.json();
  toast(`Purged ${data.purged} jobs`);
  loadBatches();
  loadQueueStatus();
}

// --- Model Catalog ---
const MODEL_CATALOG = [
  // Korean
  {name:'solar:10.7b', size:'6.1GB', params:'10.7B', cat:['korean','general'],
   desc:'Upstage Solar — 한국어 최적화 모델, 한국 기업이 개발하여 한국어 이해도가 가장 높음. 번역, 요약, 문서 작성에 강점'},
  {name:'qwen2.5:7b', size:'4.4GB', params:'7.6B', cat:['korean','general'],
   desc:'Alibaba Qwen 2.5 — 한국어 성능 우수, 속도와 품질의 균형이 좋은 범용 모델. 일상 대화, Q&A에 적합'},
  {name:'qwen2.5:14b', size:'8.4GB', params:'14.8B', cat:['korean','general'],
   desc:'Alibaba Qwen 2.5 — 최고 품질 한국어 지원, 복잡한 추론과 긴 문서 처리에 강점. 메모리 많이 사용'},
  {name:'qwen2.5:3b', size:'1.9GB', params:'3.1B', cat:['korean','small'],
   desc:'Qwen 2.5 경량 — 기본적인 한국어 처리 가능, 빠른 응답이 필요한 간단한 작업에 적합'},
  {name:'qwen2.5:1.5b', size:'0.9GB', params:'1.5B', cat:['korean','small'],
   desc:'Qwen 2.5 초경량 — 가장 가벼운 한국어 모델, 단순 분류나 키워드 추출 등 기초 작업용'},
  // General
  {name:'llama3.2:3b', size:'1.9GB', params:'3.2B', cat:['general','small'],
   desc:'Meta Llama 3.2 — Meta가 개발한 소형 범용 모델, 영어 기반 일반 대화와 텍스트 생성에 적합'},
  {name:'llama3.1:8b', size:'4.7GB', params:'8.0B', cat:['general'],
   desc:'Meta Llama 3.1 — 강력한 범용 모델, 128K 컨텍스트 지원으로 긴 문서 분석 가능. 영어 성능 최상급'},
  {name:'gemma2:2b', size:'1.5GB', params:'2.6B', cat:['general','small'],
   desc:'Google Gemma 2 — 빠른 텍스트 분류, 감성 분석, 짧은 요약에 최적. 응답 속도가 매우 빠름'},
  {name:'gemma2:9b', size:'5.4GB', params:'9.2B', cat:['general'],
   desc:'Google Gemma 2 — 중형 고품질 모델, 구조화된 출력(JSON 등)과 지시 따르기에 강점'},
  {name:'mistral:7b', size:'4.1GB', params:'7.2B', cat:['general'],
   desc:'Mistral AI — 프랑스 AI 기업 개발, 빠른 추론 속도와 다국어 지원. 유럽어 특히 강점'},
  {name:'phi3:3.8b', size:'2.0GB', params:'3.8B', cat:['general','small','code'],
   desc:'Microsoft Phi-3 — 소형이지만 논리적 추론과 수학, 코드 이해력이 뛰어남. 크기 대비 최고 성능'},
  {name:'yi:9b', size:'5.0GB', params:'9.0B', cat:['general'],
   desc:'01.AI Yi — 중국어/영어 이중언어 모델, 학술 텍스트와 복잡한 질문 답변에 강점'},
  {name:'tinyllama:1.1b', size:'0.6GB', params:'1.1B', cat:['small'],
   desc:'초경량 모델 — 600MB로 가장 작음. 빠른 프로토타이핑이나 단순 텍스트 처리용. 품질은 제한적'},
  {name:'stablelm2:1.6b', size:'0.9GB', params:'1.6B', cat:['small'],
   desc:'Stability AI — 안정적인 소형 모델, 간단한 대화와 텍스트 완성에 적합. 일관된 출력'},
  {name:'smollm2:1.7b', size:'1.0GB', params:'1.7B', cat:['small'],
   desc:'HuggingFace SmolLM2 — 효율적 소형 모델, 엣지 디바이스 배포를 고려해 설계됨'},
  // Code
  {name:'qwen2.5-coder:7b', size:'4.4GB', params:'7.6B', cat:['code'],
   desc:'Qwen 2.5 Coder — 코드 생성 전문, Python/JS/Go 등 주요 언어 지원. 코드 리뷰와 버그 수정에 강점'},
  {name:'qwen2.5-coder:3b', size:'1.9GB', params:'3.1B', cat:['code','small'],
   desc:'Qwen 2.5 Coder 경량 — 빠른 코드 자동완성과 간단한 함수 생성에 적합'},
  {name:'codellama:7b', size:'3.8GB', params:'6.7B', cat:['code'],
   desc:'Meta Code Llama — Llama 기반 코드 특화, 코드 인필링(빈칸 채우기)과 설명 생성에 강점'},
  {name:'deepseek-coder:6.7b', size:'3.8GB', params:'6.7B', cat:['code'],
   desc:'DeepSeek Coder — 87개 프로그래밍 언어 학습, 코드 생성 벤치마크에서 상위권 성능'},
  {name:'starcoder2:3b', size:'1.7GB', params:'3.0B', cat:['code','small'],
   desc:'BigCode StarCoder2 — 코드 자동완성 전문, 600개 이상 언어 지원. IDE 플러그인 연동에 적합'},
  // Multimodal
  {name:'llava:7b', size:'4.5GB', params:'7.0B', cat:['multimodal'],
   desc:'LLaVA — 이미지를 보고 설명하거나 질문에 답변. 사진 분석, 차트 해석, OCR 대체 가능'},
  {name:'moondream:1.8b', size:'1.0GB', params:'1.8B', cat:['multimodal','small'],
   desc:'Moondream — 초경량 비전 모델, 빠른 이미지 캡셔닝과 간단한 시각적 질문 답변용'},
  // Embedding
  {name:'nomic-embed-text', size:'0.3GB', params:'137M', cat:['embedding'],
   desc:'Nomic Embed — RAG/시맨틱 검색용 텍스트 임베딩, 8192 토큰 컨텍스트. 문서 검색 파이프라인에 필수'},
  {name:'mxbai-embed-large', size:'0.7GB', params:'335M', cat:['embedding'],
   desc:'Mixed Bread Embed — 고품질 임베딩 모델, MTEB 벤치마크 상위. 유사도 검색과 클러스터링에 적합'},
  {name:'snowflake-arctic-embed:33m', size:'0.07GB', params:'33M', cat:['embedding','small'],
   desc:'Snowflake Arctic — 70MB 초경량 임베딩, 리소스 제한 환경에서 빠른 벡터화가 필요할 때 사용'},
];

const CAT_LABELS = {
  korean:{label:'Korean',cls:'cat-korean'},
  general:{label:'General',cls:'cat-general'},
  code:{label:'Code',cls:'cat-code'},
  small:{label:'Small',cls:'cat-small'},
  multimodal:{label:'Multimodal',cls:'cat-multimodal'},
  embedding:{label:'Embedding',cls:'cat-embedding'},
};

let installedModelNames = [];
let currentCatFilter = 'all';

function catTags(cats) {
  return cats.map(c => {
    const info = CAT_LABELS[c] || {label:c,cls:''};
    return `<span class="cat-tag ${info.cls}">${info.label}</span>`;
  }).join('');
}

function getModelCategory(name) {
  const entry = MODEL_CATALOG.find(m => m.name === name);
  return entry ? entry.cat : ['general'];
}

function filterCatalog(cat) {
  currentCatFilter = cat;
  document.querySelectorAll('.cat-filter').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  renderCatalog();
}

function renderCatalog() {
  const list = document.getElementById('catalogList');
  const filtered = currentCatFilter === 'all'
    ? MODEL_CATALOG
    : MODEL_CATALOG.filter(m => m.cat.includes(currentCatFilter));

  list.innerHTML = filtered.map(m => {
    const isInstalled = installedModelNames.includes(m.name);
    return `<div class="catalog-card ${isInstalled ? 'installed' : ''}" data-cats="${m.cat.join(',')}">
      <div class="catalog-info">
        <div class="catalog-name">${m.name} ${catTags(m.cat)}</div>
        <div class="catalog-desc">${m.desc}</div>
        <div class="catalog-meta">${m.params} params &middot; ${m.size}</div>
      </div>
      <div>
        ${isInstalled
          ? '<span class="btn btn-green btn-sm" style="cursor:default;">Installed</span>'
          : `<button class="btn btn-primary btn-sm" onclick="installCatalogModel('${m.name}')">Install</button>`}
      </div>
    </div>`;
  }).join('');
}

async function installCatalogModel(name) {
  document.getElementById('pullModelName').value = name;
  switchTab('models');
  pullModel();
}

// --- Models ---
async function loadDefaultModelConfig() {
  try {
    const r = await fetch(api('/api/config'));
    const cfg = await r.json();
    const defaultModel = cfg.default_model || '';

    // 설치된 모델 목록으로 드롭다운 채우기
    const r2 = await fetch(api('/api/models'));
    const d2 = await r2.json();
    const models = d2.models || [];
    const sel = document.getElementById('cfgDefaultModel');
    sel.innerHTML = '<option value="">없음 (자동 해제)</option>' +
      models.map(m => `<option value="${m.name}" ${m.name === defaultModel ? 'selected' : ''}>${m.name} (${(m.size/1024/1024/1024).toFixed(1)}GB)</option>`).join('');
    markSettingsClean(['cfgDefaultModel']);   // 저장 성공 시에도 이 로더를 다시 부른다(saveDefaultModel)

    // 현재 로드 상태 표시
    const r3 = await fetch(api('/api/status'));
    const d3 = await r3.json();
    const running = (d3.running_models || []).map(m => m.name);
    const statusEl = document.getElementById('defaultModelStatus');
    if (defaultModel) {
      const isLoaded = running.includes(defaultModel);
      statusEl.innerHTML = `현재 큐 기본 모델: <strong style="color:var(--accent);">${defaultModel}</strong>` +
        (isLoaded ? ' · <span style="color:var(--muted);">지금 GPU에 있음</span>' : '');
    } else {
      statusEl.innerHTML = '큐 기본 모델이 설정되지 않았습니다(요청에 model 필수).';
    }
  } catch(e) {}
}

async function saveDefaultModel() {
  const model = document.getElementById('cfgDefaultModel').value;
  const msg = document.getElementById('defaultModelMsg');
  msg.textContent = 'Saving...';
  try {
    // 예전엔 저장 뒤 keep_alive:-1로 적재까지 했다 — 상주 모델과 다른 모델을 고르면 그 모델이
    // VRAM에 영구히 남아 상주 정책을 우회했다(D5). 적재·상주는 /api/models/residency만 맡는다.
    const d = await saveJSON('/api/config', {default_model: model},
      model ? `큐 기본 모델: ${model}` : '큐 기본 모델 해제됨');
    msg.textContent = '';
    if (d) loadDefaultModelConfig();
  } catch(e) { msg.textContent = 'Error: ' + e.message; }
}

async function loadModels() {
  loadDefaultModelConfig();
  const r = await fetch(api('/api/models'));
  const data = await r.json();
  const models = data.models || [];
  installedModelNames = models.map(m => m.name);
  const tbody = document.getElementById('modelsTable');
  if (models.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">No models installed</td></tr>';
    renderCatalog();
    return;
  }
  tbody.innerHTML = models.map(m => {
    const quant = m.details?.quantization_level || 'N/A';
    const cats = getModelCategory(m.name);
    const isEmbed = cats.includes('embedding');
    return `<tr>
      <td><strong>${m.name}</strong><br><span style="font-size:12px;color:var(--muted)">${m.details?.family || ''} / ${m.details?.parameter_size || ''}</span></td>
      <td>${catTags(cats)}</td>
      <td>${formatSize(m.size)}</td>
      <td>${quant}</td>
      <td style="white-space:nowrap;">
        ${!isEmbed ? `<button class="btn btn-outline btn-sm" onclick="initModel('${m.name}')" title="시스템 컨텍스트로 모델 초기화">Init</button> ` : ''}
        <button class="btn btn-danger btn-sm" onclick="deleteModel('${m.name}')">Delete</button>
      </td>
    </tr>`;
  }).join('');
  renderCatalog();
}

async function pullModel() {
  const name = document.getElementById('pullModelName').value.trim();
  if (!name) return;
  const prog = document.getElementById('pullProgress');
  const bar = document.getElementById('pullProgressBar');
  const status = document.getElementById('pullStatusText');
  prog.style.display = 'block'; bar.style.width = '0%'; status.textContent = 'Starting...';
  const r = await fetch(api('/api/pull'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model:name}) });
  const reader = r.body.getReader(); const decoder = new TextDecoder(); let buf = '';
  while(true) {
    const {done, value} = await reader.read(); if (done) break;
    buf += decoder.decode(value, {stream:true}); const lines = buf.split('\n'); buf = lines.pop();
    for (const line of lines) { if (!line.trim()) continue; try { const j = JSON.parse(line);
      if (j.total && j.completed) { const pct = Math.round(j.completed/j.total*100); bar.style.width=pct+'%';
        status.textContent = `${j.status} — ${pct}% (${formatSize(j.completed)} / ${formatSize(j.total)})`;
      } else { status.textContent = j.status||JSON.stringify(j); if(j.status==='success') bar.style.width='100%'; }
    } catch(e) {} }
  }
  toast('Model pull complete'); refreshStatus(); loadModels();
  setTimeout(() => prog.style.display='none', 3000);
}

async function deleteModel(name) {
  if (!await showConfirm(`"${name}" 모델을 삭제할까요?`, '모델 삭제')) return;
  await fetch(api('/api/delete'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model:name}) });
  toast(`Deleted ${name}`); refreshStatus(); loadModels();
}

// --- Model Initialization ---
async function initModel(name) {
  const area = document.getElementById('initResultArea');
  const content = document.getElementById('initResultContent');
  area.style.display = 'block';
  content.innerHTML = `<div style="color:var(--muted);font-size:13px;">⏳ <strong>${name}</strong> 초기화 중... (웜업에 몇 초 걸릴 수 있습니다)</div>`;
  area.scrollIntoView({behavior:'smooth', block:'nearest'});
  try {
    const r = await fetch(api('/api/models/init'), {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({model: name})
    });
    const d = await r.json();
    if (!d.ok) {
      content.innerHTML = `<div style="color:var(--red);font-size:13px;">❌ 초기화 실패: ${escHtml(d.error||'unknown error')}</div>`;
      return;
    }
    const sysLines = d.system_prompt.split('\n').map(l => `<div style="font-size:11px;color:var(--muted);font-family:monospace;">${escHtml(l)}</div>`).join('');
    content.innerHTML = `
      <div style="margin-bottom:12px;">
        <span style="color:var(--green);font-weight:600;">✓ ${escHtml(name)} 초기화 완료</span>
        <span style="color:var(--muted);font-size:12px;margin-left:8px;">${d.duration_ms}ms</span>
      </div>
      <div style="margin-bottom:8px;font-size:13px;font-weight:500;">모델 응답:</div>
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;font-size:13px;margin-bottom:12px;">${escHtml(d.response)}</div>
      <details style="font-size:12px;">
        <summary style="cursor:pointer;color:var(--muted);margin-bottom:6px;">적용된 시스템 프롬프트 보기</summary>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;">${sysLines}</div>
      </details>`;
    toast(`${name} 초기화 완료 (${d.duration_ms}ms)`);
  } catch(e) {
    content.innerHTML = `<div style="color:var(--red);font-size:13px;">❌ 요청 실패: ${escHtml(e.message)}</div>`;
  }
}

const _INIT_FIELDS = ['initCountry', 'initRegion', 'initTimezone', 'initLanguage', 'initBaseInstructions', 'initCustomCommands', 'initWarmUpPrompt'];

async function loadInitConfig() {
  try {
    const r = await fetch(api('/api/models/init-config'));
    const d = await r.json();
    document.getElementById('initCountry').value = d.country || '';
    document.getElementById('initRegion').value = d.region || '';
    document.getElementById('initTimezone').value = d.timezone || '';
    document.getElementById('initLanguage').value = d.language || '';
    document.getElementById('initBaseInstructions').value = d.base_instructions || '';
    document.getElementById('initCustomCommands').value = d.custom_commands || '';
    document.getElementById('initWarmUpPrompt').value = d.warm_up_prompt || '';
    markSettingsClean(_INIT_FIELDS);
  } catch(e) {}
}

async function saveInitConfig() {
  const cfg = {
    country: document.getElementById('initCountry').value.trim(),
    region: document.getElementById('initRegion').value.trim(),
    timezone: document.getElementById('initTimezone').value.trim(),
    language: document.getElementById('initLanguage').value.trim(),
    base_instructions: document.getElementById('initBaseInstructions').value,
    custom_commands: document.getElementById('initCustomCommands').value,
    warm_up_prompt: document.getElementById('initWarmUpPrompt').value.trim(),
  };
  // saveJSON — 예전엔 try가 없어 응답이 JSON이 아니면(세션 만료 리다이렉트 등) 예외만 나고 아무 표시가 없었다(D4 잔여).
  const d = await saveJSON('/api/models/init-config', cfg, '초기화 설정이 저장되었습니다');
  if (d) {
    markSettingsClean(_INIT_FIELDS);
    if (document.getElementById('initPreview').style.display !== 'none') {
      document.getElementById('initPreview').textContent = d.preview;
    }
  }
}

async function previewInitPrompt() {
  const preview = document.getElementById('initPreview');
  if (preview.style.display !== 'none') { preview.style.display='none'; return; }
  try {
    const r = await fetch(api('/api/models/init-config'));
    const d = await r.json();
    preview.textContent = d.preview;
    preview.style.display = 'block';
  } catch(e) { toast('미리보기 실패', true); }
}

// --- Service ---
async function serviceAction(action) {
  toast(`${action}ing Ollama...`);
  const r = await fetch(`${BASE}/api/service/${action}`, {method:'POST'});
  const data = await r.json();
  toast(data.ok ? `Ollama ${action}ed` : `Error: ${data.output}`, !data.ok);
  setTimeout(refreshStatus, 2000);
}

async function toggleQueuePause() {
  const btn = document.getElementById('btnPause');
  const isPaused = btn.textContent.includes('Resume');
  const ep = isPaused ? '/api/queue/resume' : '/api/queue/pause';
  const r = await fetch(api(ep), {method:'POST'});
  const data = await r.json();
  updatePauseUI(data.paused);
  toast(data.paused ? 'Queue paused' : 'Queue resumed');
}

function updatePauseUI(paused) {
  const btn = document.getElementById('btnPause');
  const badge = document.getElementById('queuePausedBadge');
  if (paused) {
    btn.textContent = 'Resume Queue';
    btn.className = 'btn btn-green';
    badge.style.display = '';
  } else {
    btn.textContent = 'Pause Queue';
    btn.className = 'btn btn-yellow';
    badge.style.display = 'none';
  }
}

// --- Chat ---
function clearChat() { chatHistory = [];
  document.getElementById('chatMessages').innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Start a conversation</div>'; }

async function sendChat() {
  const input = document.getElementById('chatInput'); const msg = input.value.trim(); if (!msg) return;
  const model = document.getElementById('chatModel').value;
  if (!model) { toast('Select a model first', true); return; } input.value = '';
  const system = document.getElementById('chatSystem').value.trim();
  chatHistory.push({role:'user', content:msg});
  const container = document.getElementById('chatMessages');
  if (chatHistory.length === 1) container.innerHTML = '';
  container.innerHTML += `<div class="chat-msg chat-user">${escHtml(msg)}</div>`;
  const assistantDiv = document.createElement('div');
  assistantDiv.className = 'chat-msg chat-assistant'; assistantDiv.textContent = '...';
  container.appendChild(assistantDiv); container.scrollTop = container.scrollHeight;
  const messages = system ? [{role:'system',content:system},...chatHistory] : [...chatHistory];
  const r = await fetch(api('/api/chat'), { method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({model, messages}) });
  const reader = r.body.getReader(); const decoder = new TextDecoder(); let buf='', full='';
  while(true) { const {done,value} = await reader.read(); if (done) break;
    buf += decoder.decode(value,{stream:true}); const lines=buf.split('\n'); buf=lines.pop();
    for (const line of lines) { if (!line.trim()) continue; try { const j=JSON.parse(line);
      if (j.message?.content) { full+=j.message.content; assistantDiv.textContent=full; container.scrollTop=container.scrollHeight; }
    } catch(e) {} } }
  chatHistory.push({role:'assistant',content:full});
}

// --- Generate ---
async function doGenerate() {
  const model = document.getElementById('genModel').value;
  const prompt = document.getElementById('genPrompt').value.trim();
  if (!model||!prompt) { toast('Model and prompt required', true); return; }
  const system = document.getElementById('genSystem').value.trim();
  const output = document.getElementById('genOutput');
  const result = document.getElementById('genResult'); const meta = document.getElementById('genMeta');
  output.style.display='block'; result.textContent='...'; meta.textContent='';
  const payload = {model,prompt}; if (system) payload.system=system;
  const r = await fetch(api('/api/generate'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const reader = r.body.getReader(); const decoder = new TextDecoder(); let buf='', full='', lastData={};
  while(true) { const {done,value} = await reader.read(); if (done) break;
    buf += decoder.decode(value,{stream:true}); const lines=buf.split('\n'); buf=lines.pop();
    for (const line of lines) { if (!line.trim()) continue; try { const j=JSON.parse(line);
      if (j.response) { full+=j.response; result.textContent=full; } if (j.done) lastData=j;
    } catch(e) {} } }
  if (lastData.total_duration) { const dur=(lastData.total_duration/1e9).toFixed(1);
    const tokens=lastData.eval_count||0; const tps=lastData.eval_duration?(tokens/(lastData.eval_duration/1e9)).toFixed(1):'?';
    meta.textContent = `${dur}s / ${tokens} tokens / ${tps} tok/s`; }
}

// --- Logs ---
let _rawLogContent = '';
async function loadLogs() {
  const lines = document.getElementById('logLines').value;
  const source = document.getElementById('logSource').value;
  const r = await fetch(`${BASE}/api/logs?lines=${lines}&source=${source}`);
  const data = await r.json();
  _rawLogContent = data.logs || data.error || 'No logs';
  filterLogContent();
  // Load alert history
  try {
    const ar = await fetch(api('/api/alerts/history?limit=50'));
    const ad = await ar.json();
    const tbody = document.getElementById('alertsTable');
    const alerts = ad.alerts || [];
    if (alerts.length === 0) { tbody.innerHTML='<tr><td colspan="4" style="color:var(--muted)">No alerts</td></tr>'; return; }
    tbody.innerHTML = alerts.map(a => {
      const lvlCls = a.level==='critical'?'color:var(--red)':'color:var(--yellow)';
      return `<tr><td style="font-size:12px;">${formatDate(a.ts)}</td><td style="${lvlCls};font-weight:600;font-size:12px;">${a.level}</td><td style="font-size:12px;">${escHtml(a.title)}</td><td style="font-size:11px;color:var(--muted);">${a.channel||'-'}</td></tr>`;
    }).join('');
  } catch(e){}
}
function filterLogContent() {
  const level = document.getElementById('logLevel').value;
  const el = document.getElementById('logContent');
  if (!level) { el.textContent = _rawLogContent; }
  else { el.textContent = _rawLogContent.split('\n').filter(l => l.includes(level)).join('\n') || `No ${level} entries`; }
  el.scrollTop = el.scrollHeight;
}

// --- Settings ---
async function loadChangelog() {
  try {
    const r = await fetch(api('/api/version?changelog=true'));
    const d = await r.json();

    // 헤더 배지 업데이트
    const badge = document.getElementById('versionBadge');
    if (badge) badge.textContent = 'v' + d.version;

    // 체인지로그 렌더링
    const container = document.getElementById('changelogContainer');
    if (!container) return;
    const logs = d.changelog || [];
    container.innerHTML = logs.map((v, idx) => {
      const isCurrent = idx === 0;
      return `<div style="display:flex;gap:16px;margin-bottom:${isCurrent?'20':'16'}px;padding-bottom:${isCurrent?'20':'16'}px;
                           border-bottom:1px solid var(--border);">
        <!-- 버전 배지 -->
        <div style="min-width:80px;text-align:right;padding-top:2px;">
          <span style="font-size:13px;font-weight:700;font-family:monospace;
                       color:${isCurrent?'#fff':'var(--muted)'};
                       background:${isCurrent?'var(--accent)':'var(--surface2)'};
                       border:1px solid ${isCurrent?'var(--accent)':'var(--border)'};
                       border-radius:4px;padding:2px 8px;white-space:nowrap;">
            v${escHtml(v.version)}
          </span>
          <div style="font-size:10px;color:var(--muted);margin-top:4px;">${escHtml(v.date)}</div>
        </div>
        <!-- 내용 -->
        <div style="flex:1;">
          <div style="font-weight:600;font-size:14px;margin-bottom:6px;
                      color:${isCurrent?'var(--fg)':'var(--muted)'};">
            ${isCurrent?'<span style="color:var(--accent);margin-right:6px;">●</span>':''}${escHtml(v.title)}
          </div>
          <ul style="margin:0;padding-left:16px;font-size:12px;color:var(--muted);line-height:1.8;">
            ${(v.items||[]).map(item => `<li>${escHtml(item)}</li>`).join('')}
          </ul>
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function loadSettings() {
  try {
    const r = await fetch(api('/api/config')); const cfg = await r.json();
    document.getElementById('cfgAuthEnabled').checked = cfg.auth_enabled;
    document.getElementById('cfgLocalBypass').checked = cfg.local_bypass;
    markSettingsClean(['cfgAuthEnabled', 'cfgLocalBypass']);
    _renderSecretState('cfgSlack', 'alert_slack_url', cfg.alert_slack_url);
    _renderSecretState('cfgDiscord', 'alert_discord_url', cfg.alert_discord_url);
    _renderSecretState('cfgWebhook', 'alert_webhook_url', cfg.alert_webhook_url);
  } catch(e) {}
  loadKeys();
}

async function changeAdminPassword() {
  const current = document.getElementById('pwCurrent').value;
  const newPw   = document.getElementById('pwNew').value;
  const confirm = document.getElementById('pwConfirm').value;
  if (!current || !newPw || !confirm) { toast('모든 항목을 입력하세요', true); return; }
  if (newPw !== confirm) { toast('새 비밀번호가 일치하지 않습니다', true); return; }
  if (newPw.length < 8) { toast('비밀번호는 최소 8자 이상이어야 합니다', true); return; }
  const r = await fetch(BASE+'/api/admin/change-password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({current_password: current, new_password: newPw})
  });
  const d = await r.json();
  if (d.ok) {
    toast('비밀번호가 변경되었습니다. 다시 로그인하세요.');
    setTimeout(() => location.href=BASE+'/logout', 1500);
  } else {
    toast(d.error || '변경 실패', true);
  }
  document.getElementById('pwCurrent').value = '';
  document.getElementById('pwNew').value = '';
  document.getElementById('pwConfirm').value = '';
}

// 서버는 시크릿(webhook URL 등)을 {set, hint}로만 준다(D2). 입력칸은 비워 두고 상태 줄로 보여 준다.
// 빈 칸 저장 = 변경 없음 · 삭제는 '지우기'({clear:true})로만.
function _renderSecretState(inputId, key, masked) {
  const input = document.getElementById(inputId);
  const state = document.getElementById(inputId + 'State');
  input.value = '';
  if (!state) return;
  if (masked && masked.set) {
    state.innerHTML = `설정됨${masked.hint ? ' (' + masked.hint + ')' : ''} — 바꾸려면 새 값을 입력 <a onclick="clearSecretConfig('${key}')">지우기</a>`;
  } else {
    state.textContent = '미설정';
  }
}

async function clearSecretConfig(key) {
  if (!await showConfirm(`${key} 값을 지웁니다.`, '설정 삭제', '지우기')) return;
  if (await saveJSON('/api/config', {[key]: {clear: true}}, '삭제됨')) loadSettings();
}

async function saveAlertConfig() {
  const cfg = {};
  [['cfgSlack', 'alert_slack_url'], ['cfgDiscord', 'alert_discord_url'], ['cfgWebhook', 'alert_webhook_url']]
    .forEach(([id, key]) => { const v = document.getElementById(id).value.trim(); if (v) cfg[key] = v; });
  if (!Object.keys(cfg).length) { toast('변경할 값이 없습니다'); return; }
  if (await saveJSON('/api/config', cfg, 'Alert config saved')) loadSettings();
}

// 인증 설정 — 체크박스 즉시 저장을 없애고 확인을 거친다(D3). 보안을 약하게 하는 변경은
// 서버가 localhost 요청만 받는다(외부 세션이면 403 local_only).
async function saveSettings() {
  const cfg = {
    auth_enabled: document.getElementById('cfgAuthEnabled').checked,
    local_bypass: document.getElementById('cfgLocalBypass').checked,
  };
  if (!cfg.auth_enabled && !await showConfirm(
      'API Key 인증을 끄면 외부(example.org/ollama)에 모든 API가 무인증으로 열립니다. 계속할까요?',
      '인증 끄기', '끄기')) { loadSettings(); return; }
  const d = await saveJSON('/api/config', cfg, '인증 설정 저장됨');
  if (d) markSettingsClean(['cfgAuthEnabled', 'cfgLocalBypass']);
  else loadSettings();  // 거부되면 화면을 서버 값으로 되돌린다
}

// Ollama 엔진 설정 카드(설정 › 모델·엔진) — v2.125.54 D1.
// 서버 `current`는 **Ollama가 실제로 받는 값**이다: systemd drop-in 위에 `/etc/default/ollama`(EnvironmentFile)를
// 덮은 결과. 예전엔 drop-in만 읽어 화면이 MAX_LOADED=1, 실제는 3이었다. `effective[키]`에 출처와 가려진 값이 온다.
// GPU 서버는 앱 계정이 두 파일 모두 쓸 수 없어(root 0644) `writable:false` → 선택 상자 비활성·적용 버튼 숨김.
async function loadPerfSettings() {
  try {
    const r = await fetch(api('/api/ollama/perf'));
    const data = await r.json();
    const opts = data.options || [];
    const cur = data.current || {};
    const eff = data.effective || {};
    const writable = data.writable !== false;
    const srv = data.server || {};
    const container = document.getElementById('perfSettings');

    // 서버 환경 정보 헤더
    let envHtml = '';
    if (srv.has_gpu) {
      envHtml = `<div style="margin-bottom:16px;padding:12px;background:rgba(99,102,241,0.1);border:1px solid var(--accent);border-radius:var(--radius);">
        <div style="font-size:13px;font-weight:bold;color:var(--accent);margin-bottom:4px;">🖥 GPU Server</div>
        <div style="font-size:12px;color:var(--muted);">${srv.gpu_name} · VRAM ${Math.round(srv.gpu_vram_mb/1024)}GB · RAM ${srv.ram_gb}GB · ${srv.platform}</div>
      </div>`;
    } else {
      envHtml = `<div style="margin-bottom:16px;padding:12px;background:rgba(34,197,94,0.1);border:1px solid var(--green);border-radius:var(--radius);">
        <div style="font-size:13px;font-weight:bold;color:var(--green);margin-bottom:4px;">🍎 CPU Server</div>
        <div style="font-size:12px;color:var(--muted);">RAM ${srv.ram_gb}GB · ${srv.platform}</div>
      </div>`;
    }

    // 쓸 수 없는 환경(GPU 서버 — 설정 파일이 root 소유)은 읽기 전용으로 보여 준다(D1).
    if (!writable) {
      envHtml += `<div style="margin-bottom:16px;padding:10px 12px;border:1px solid var(--yellow);border-radius:var(--radius);font-size:12px;color:var(--yellow);">
        읽기 전용 — ${data.read_only_reason || '설정 파일 쓰기 불가'}. 표시값은 Ollama가 실제로 받는 값입니다.</div>`;
    }
    const applyBtn = document.getElementById('perfApplyBtn');
    if (applyBtn) applyBtn.style.display = writable ? '' : 'none';

    container.innerHTML = envHtml + opts.map(o => {
      // set=false: 어느 파일에도 없는 키 → Ollama 자체 기본값으로 돈다. 권장값을 '현재값'처럼 보이지 않게 문구로 구분.
      const set = cur[o.key] !== undefined;
      const val = set ? cur[o.key] : o.default;
      const e = eff[o.key] || {};
      // 실제 값이 선택지 목록 밖이면(예: 운영자가 파일에 직접 쓴 값) 목록 앞에 넣는다 — 안 그러면 첫 선택지가 선택된 것처럼 보인다.
      const vals = o.values.includes(val) ? o.values : [val, ...o.values];
      const options = vals.map(v =>
        `<option value="${v}" ${v===val?'selected':''}>${v}</option>`
      ).join('');
      let srcLine = set ? `출처: ${e.source || '-'}` : `미설정 — Ollama 기본값 (표시값 ${o.default}은 권장값)`;
      if (e.shadowed) srcLine += ` · <span style="color:var(--yellow);">⚠ ${e.shadowed.source}의 ${e.shadowed.value}을(를) 덮어씀</span>`;
      return `<div style="margin-bottom:16px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <strong style="font-size:14px;">${o.label}</strong>
          <select id="perf_${o.key}" style="width:auto;min-width:100px;padding:6px 10px;" ${writable ? '' : 'disabled'}>${options}</select>
        </div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:4px;">${o.desc}</div>
        <div style="font-size:11px;color:var(--accent);">Effect: ${o.effect}</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">${srcLine}</div>
      </div>`;
    }).join('');
    markSettingsClean([...document.querySelectorAll('[id^="perf_OLLAMA_"]')]);
  } catch(e) {
    document.getElementById('perfSettings').innerHTML =
      '<div style="color:var(--muted);font-size:13px;">로컬 접속에서만 설정 가능합니다.</div>';
  }
}

async function applyPerfSettings() {
  const inputs = document.querySelectorAll('[id^="perf_OLLAMA_"]');
  const data = {};
  inputs.forEach(el => {
    const key = el.id.replace('perf_','');
    data[key] = el.value;
  });
  const msg = document.getElementById('perfMessage');
  msg.textContent = 'Saving...';
  try {
    // 서버는 쓸 수 없거나(권한) 가려진 키(/etc/default/ollama에 있음)면 409+이유를 준다 — saveJSON이 그 이유를 띄운다.
    // 저장이 됐을 때만 재시작한다. 예전엔 상태를 안 보고 r.json()을 해서, 권한 오류 500(HTML 본문)이면
    // "Error: Unexpected token …"만 뜨고 **왜 안 되는지**(파일 권한)가 보이지 않았다.
    const result = await saveJSON('/api/ollama/perf', data, null);
    if (!result) { msg.textContent = ''; return; }
    markSettingsClean([...inputs]);
    if (result.restart_required) {
      msg.textContent = 'Restarting Ollama...';
      const rr = await fetch(api('/api/ollama/restart'), {method:'POST'});
      const rdata = await rr.json();
      if (rdata.ok) {
        msg.textContent = '';
        toast('Ollama restarted with new settings');
        setTimeout(refreshStatus, 3000);
      } else {
        msg.textContent = 'Restart failed: ' + rdata.output;
      }
    } else {
      msg.textContent = '';
      toast('No changes');
    }
  } catch(e) { msg.textContent = 'Error: ' + e.message; }
}

// Image Storage Management
async function loadImageStorage() {
  try {
    const r = await fetch(api('/api/image/storage'));
    const d = await r.json();
    const s = d.stats || {};
    const cfg = d.settings || {};
    const el = document.getElementById('imgStorageStats');
    if (s.dir) {
      el.innerHTML = `
        <div style="display:flex;gap:24px;flex-wrap:wrap;">
          <div><span style="color:var(--muted);">파일 수:</span> <strong>${s.count}</strong>개</div>
          <div><span style="color:var(--muted);">사용 용량:</span> <strong>${s.size_mb}MB</strong></div>
          <div><span style="color:var(--muted);">가장 오래된:</span> ${s.oldest || '-'}</div>
          <div><span style="color:var(--muted);">가장 최신:</span> ${s.newest || '-'}</div>
        </div>`;
    } else {
      el.innerHTML = '<span style="color:var(--muted);">이미지 디렉토리를 찾을 수 없습니다.</span>';
    }
    // 설정값 반영 — 🔴 `??`여야 한다. 0은 '무제한'(app.py _cleanup_images가 >0일 때만 정리)인데
    // 예전 `||`는 0을 7일·500개·2GB로 바꿔 보여 줬다 → 그 화면에서 다시 저장하면 한도가 되살아나 일일 정리가
    // 이미지를 지운다(라이브는 30·5000·10000이라 무증상이었다 · 2026-09-30 미저장 경고 작업 중 발견).
    document.getElementById('imgRetentionDays').value = cfg.image_retention_days ?? 7;
    document.getElementById('imgMaxCount').value = cfg.image_max_count ?? 500;
    document.getElementById('imgMaxSizeMb').value = cfg.image_max_size_mb ?? 2000;
    markSettingsClean(_IMG_FIELDS);
  } catch(e) {}
}

const _IMG_FIELDS = ['imgRetentionDays', 'imgMaxCount', 'imgMaxSizeMb'];

async function saveImageSettings() {
  const data = {
    image_retention_days: parseInt(document.getElementById('imgRetentionDays').value),
    image_max_count: parseInt(document.getElementById('imgMaxCount').value),
    image_max_size_mb: parseInt(document.getElementById('imgMaxSizeMb').value),
  };
  // saveJSON — 실패 사유(세션 만료 등)를 토스트로 알린다(D4 잔여 — 예전엔 JSON이 아니면 예외 문구만 작게 떴다).
  if (await saveJSON('/api/image/settings', data, '이미지 관리 설정 저장됨')) {
    markSettingsClean(_IMG_FIELDS);
    document.getElementById('imgStorageMessage').textContent = '';
  }
}

async function cleanupImagesNow() {
  if (!confirm('설정 기준에 따라 이미지를 정리합니다. 계속할까요?')) return;
  const msg = document.getElementById('imgStorageMessage');
  msg.textContent = 'Cleaning...';
  try {
    const data = {
      retention_days: parseInt(document.getElementById('imgRetentionDays').value),
      max_count: parseInt(document.getElementById('imgMaxCount').value),
      max_size_mb: parseInt(document.getElementById('imgMaxSizeMb').value),
    };
    const r = await fetch(api('/api/image/cleanup'), {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
    });
    const d = await r.json();
    toast(`${d.deleted}개 삭제, ${d.freed_mb}MB 확보`);
    msg.textContent = '';
    loadImageStorage();
  } catch(e) { msg.textContent = e.message; }
}

async function loadKeys() {
  try {
    const r = await fetch(api('/api/keys')); const data = await r.json();
    const keys = data.keys || [];
    window._apiKeys = keys;            // 기간 변경 다이얼로그에서 이름 조회용
    const tbody = document.getElementById('keysTable');
    if (keys.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">No API keys</td></tr>';
      return;
    }
    tbody.innerHTML = keys.map(k => {
      const expired = k.expires_at && new Date(_cleanTs(k.expires_at)) < new Date();
      const expLabel = k.expires_at ? (expired ? '<span style="color:var(--red)">expired</span>' : formatDate(k.expires_at)) : 'never';
      const statusLabel = expired ? 'expired' : (k.active ? 'active' : 'disabled');
      const statusCls = expired ? 'badge-failed' : (k.active ? 'badge-completed' : 'badge-failed');
      return `<tr>
      <td>${escHtml(k.name)}</td>
      <td><code>${k.key_prefix}...</code></td>
      <td>${expLabel}</td>
      <td>${k.last_used_at ? formatDate(k.last_used_at) : 'never'}</td>
      <td><span class="badge ${statusCls}">${statusLabel}</span></td>
      <td style="white-space:nowrap;">
        <button class="btn btn-outline btn-sm" onclick="setKeyExpiry(${k.id})">기간</button>
        <button class="btn btn-outline btn-sm" onclick="toggleKey(${k.id})">${k.active ? 'Disable' : 'Enable'}</button>
        <button class="btn btn-danger btn-sm" onclick="deleteKey(${k.id})">Delete</button>
      </td>
    </tr>`}).join('');
  } catch(e) {}
}

async function createKey() {
  const name = document.getElementById('newKeyName').value.trim();
  if (!name) { toast('Enter a name for the key', true); return; }
  const noExpiry = document.getElementById('newKeyNoExpiry').checked;
  const payload = noExpiry ? {name, expires_days: 0} : {name};
  const data = await saveJSON('/api/keys', payload, null);
  if (data && data.key) {
    document.getElementById('newKeyDisplay').style.display = 'block';
    document.getElementById('newKeyValue').textContent = data.key;
    document.getElementById('newKeyName').value = '';
    toast('API key created');
    loadKeys();
  }
}

async function toggleKey(id) {
  try {
    const r = await fetch(api(`/api/keys/${id}/toggle`), {method:'POST'});
    if (r.status === 401 || r.status === 403) { toast('인증 오류 — 다시 로그인하세요', true); return; }
    const d = await r.json();
    if (!d.ok) { toast('Toggle 실패', true); return; }
    loadKeys();
  } catch(e) { toast('요청 실패: ' + e.message, true); }
}

async function deleteKey(id) {
  if (!await showConfirm('이 API 키를 삭제할까요? 이 작업은 되돌릴 수 없습니다.', 'API 키 삭제')) return;
  try {
    const r = await fetch(api(`/api/keys/${id}`), {method:'DELETE'});
    if (r.status === 401 || r.status === 403) { toast('인증 오류 — 다시 로그인하세요', true); return; }
    const d = await r.json();
    if (d.ok === false) { toast('삭제 실패', true); return; }
    toast('API 키가 삭제되었습니다');
    loadKeys();
  } catch(e) { toast('요청 실패: ' + e.message, true); }
}

async function setKeyExpiry(id) {
  const k = (window._apiKeys || []).find(x => x.id === id);
  const name = k ? k.name : '';
  const cur = k && k.expires_at ? `현재: ${formatDate(k.expires_at)}` : '현재: 무제한';
  const ans = await showPrompt(
    `"${name}" 키를 지금부터 며칠 후 만료시킬까요?\n무제한으로 바꾸려면 0 을 입력하세요.\n${cur}`,
    'API 키 기간 변경', '90', '적용');
  if (ans === null) return;                         // 취소
  const trimmed = String(ans).trim();
  if (trimmed === '') return;
  const n = parseInt(trimmed, 10);
  if (isNaN(n) || n < 0 || String(n) !== trimmed) { toast('0 이상의 정수를 입력하세요', true); return; }
  const payload = (n === 0) ? {never: true} : {days: n};
  try {
    const r = await fetch(api(`/api/keys/${id}/expiry`), {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    if (r.status === 401 || r.status === 403) { toast('인증 오류 — 다시 로그인하세요', true); return; }
    const d = await r.json();
    if (!d.ok) { toast(d.error || '변경 실패', true); return; }
    toast(d.expires_at ? `만료일이 ${formatDate(d.expires_at)} 로 변경되었습니다` : '무제한으로 변경되었습니다');
    loadKeys();
  } catch(e) { toast('요청 실패: ' + e.message, true); }
}

// --- Image Generation ---
async function loadImageModels() {
  try {
    const r = await fetch(api('/api/image/models'));
    const d = await r.json();
    const badge = document.getElementById('imgStatusBadge');
    const offline = document.getElementById('imgOfflineBadge');
    const modelBadge = document.getElementById('imgModelBadge');
    if (d.online) {
      badge.style.display = 'inline';
      offline.style.display = 'none';
      const sel = document.getElementById('imgModel');
      window._imgModels = d.models || [];
      sel.innerHTML = d.models.map(m =>
        `<option value="${escHtml(m.id)}">${escHtml(m.name)} — ${escHtml(m.quality)} / ${escHtml(m.speed)}</option>`
      ).join('');
      modelBadge.textContent = `${d.models.length}개 모델`;

      // 스타일 드롭다운 채우기
      const styleSel = document.getElementById('imgStyle');
      if (d.styles) {
        styleSel.innerHTML = Object.entries(d.styles).map(([k,v]) =>
          `<option value="${escHtml(k)}">${escHtml(k === 'none' ? '없음 (원본)' : k)} — ${escHtml(v.description)}</option>`
        ).join('');
      }

      onImageModelChange();
    } else {
      badge.style.display = 'none';
      offline.style.display = 'inline';
      modelBadge.textContent = 'ComfyUI 연결 필요';
    }
  } catch(e) {
    document.getElementById('imgOfflineBadge').style.display = 'inline';
  }
}

function onImageModelChange() {
  const selId = document.getElementById('imgModel').value;
  const model = (window._imgModels || []).find(m => m.id === selId);
  if (!model) return;

  // 설명 표시
  document.getElementById('imgModelDesc').textContent = model.description || '';

  // Steps 옵션 업데이트
  const stepsSel = document.getElementById('imgSteps');
  const defaultSteps = model.default_steps || 20;
  const maxSteps = model.max_steps || 50;
  const stepOptions = [];
  for (let s = 1; s <= maxSteps; s++) {
    if (s <= 8 || s === 10 || s === 15 || s === 20 || s === 25 || s === 30 || s === 40 || s === 50) {
      stepOptions.push(s);
    }
  }
  stepsSel.innerHTML = stepOptions.map(s =>
    `<option value="${s}" ${s === defaultSteps ? 'selected' : ''}>${s}</option>`
  ).join('');

  // SDXL이면 negative prompt 표시
  const negRow = document.getElementById('imgNegativeRow');
  negRow.style.display = (model.family === 'sdxl') ? 'block' : 'none';
}

let _imgPollTimer = null;

let _previewFilename = '';

// 이 내용을 main.js의 loadImageGallery 함수 교체용으로 사용

// 선택된 이미지 추적
const _selectedImages = new Set();

function toggleImgSelect(name, el) {
  const cb = el.querySelector('input[type=checkbox]');
  if (_selectedImages.has(name)) {
    _selectedImages.delete(name);
    el.style.outline = 'none';
    if (cb) cb.checked = false;
  } else {
    _selectedImages.add(name);
    el.style.outline = '3px solid var(--accent)';
    if (cb) cb.checked = true;
  }
  const cnt = document.getElementById('imgSelectCount');
  const bar = document.getElementById('imgBulkBar');
  if (_selectedImages.size > 0) {
    cnt.textContent = _selectedImages.size + '장 선택';
    bar.style.display = 'flex';
  } else {
    bar.style.display = 'none';
  }
}

function selectAllImages() {
  document.querySelectorAll('.img-card').forEach(el => {
    const name = el.dataset.name;
    if (name) {
      _selectedImages.add(name);
      el.style.outline = '3px solid var(--accent)';
      const cb = el.querySelector('input[type=checkbox]');
      if (cb) cb.checked = true;
    }
  });
  document.getElementById('imgSelectCount').textContent = _selectedImages.size + '장 선택';
  document.getElementById('imgBulkBar').style.display = 'flex';
}

function deselectAllImages() {
  _selectedImages.clear();
  document.querySelectorAll('.img-card').forEach(el => {
    el.style.outline = 'none';
    const cb = el.querySelector('input[type=checkbox]');
    if (cb) cb.checked = false;
  });
  document.getElementById('imgBulkBar').style.display = 'none';
}

async function deleteSelectedImages() {
  if (_selectedImages.size === 0) return;
  const total = _selectedImages.size;
  if (!confirm(total + '장을 삭제하시겠습니까?')) return;

  const btn = document.querySelector('#imgBulkBar button');
  const countEl = document.getElementById('imgSelectCount');
  const names = [..._selectedImages];
  let done = 0;

  // 프로그레스바 표시
  countEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;">' +
    '<span>삭제 중... 0/' + total + '</span>' +
    '<div style="width:120px;height:6px;background:var(--bg);border-radius:3px;overflow:hidden;">' +
    '<div id="imgDelProgress" style="height:100%;width:0%;background:#ef4444;border-radius:3px;transition:width 0.2s;"></div></div></div>';
  if (btn) btn.disabled = true;

  for (const name of names) {
    await fetch(BASE+'/api/image/delete/'+name, {method:'DELETE'});
    done++;
    const pct = Math.round(done / total * 100);
    const bar = document.getElementById('imgDelProgress');
    if (bar) bar.style.width = pct + '%';
    countEl.querySelector('span').textContent = '삭제 중... ' + done + '/' + total;
    // 삭제된 카드 페이드아웃
    const card = document.querySelector('.img-card[data-name="'+name+'"]');
    if (card) card.style.opacity = '0.3';
  }

  _selectedImages.clear();
  countEl.textContent = '✅ ' + total + '장 삭제 완료';
  setTimeout(() => {
    document.getElementById('imgBulkBar').style.display = 'none';
    if (btn) btn.disabled = false;
    loadImageGallery();
  }, 800);
}


async function loadImageGallery() {
  try {
    const r = await fetch(BASE+'/api/image/gallery').then(r=>r.json());
    document.getElementById('imgGalleryCount').textContent = '('+r.count+'장, '+r.size_mb+'MB)';
    // 디스크 용량 프로그레스바
    const diskEl = document.getElementById('imgDiskBar');
    if (diskEl) {
      try {
        const dr = await fetch(BASE+'/api/server/stats').then(r2=>r2.json());
        const disk = dr.disk || {};
        const usedPct = disk.usage_pct || 0;
        diskEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);">' +
          '<span>💾 디스크</span>' +
          '<div style="flex:1;height:8px;background:var(--bg);border-radius:4px;overflow:hidden;">' +
            '<div style="height:100%;width:'+usedPct+'%;background:'+(usedPct>80?'#ef4444':usedPct>60?'#eab308':'#22c55e')+';border-radius:4px;"></div>' +
          '</div>' +
          '<span>'+(disk.used_gb||0)+'G / '+(disk.total_gb||0)+'G ('+usedPct+'%)</span>' +
        '</div>';
      } catch {}
    }
    document.getElementById('imgGalleryStats').textContent = r.count > 0 ?
      '최신: '+(r.newest||'').split('T')[0]+' | 보관: '+(r.dir||'').split('/').pop() : '';

    const el = document.getElementById('imgGallery');
    if (!r.files || r.files.length === 0) {
      el.innerHTML = '<div style="color:var(--muted);font-size:12px;grid-column:1/-1;text-align:center;padding:20px;">생성된 이미지가 없습니다</div>';
      return;
    }

    // 최신순 정렬
    const files = r.files.sort((a,b) => b.mtime - a.mtime);
    el.innerHTML = files.map(f => {
      const sizeMB = (f.size / 1024 / 1024).toFixed(1);
      const date = f.mtime_iso.split('T')[0].slice(5);
      return '<div class="img-card" data-name="'+f.name+'" style="cursor:pointer;border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--surface);transition:transform 0.2s;position:relative;" onclick="previewImage(\x27'+f.name+'\x27)" onmouseover="this.style.transform=\x27scale(1.03)\x27" onmouseout="this.style.transform=\x27scale(1)\x27">' +
        '<div style="position:absolute;top:4px;left:4px;z-index:1;" onclick="event.stopPropagation();toggleImgSelect(\x27'+f.name+'\x27,this.parentElement)"><input type="checkbox" style="width:16px;height:16px;cursor:pointer;accent-color:var(--accent);" onclick="event.stopPropagation();toggleImgSelect(\x27'+f.name+'\x27,this.parentElement.parentElement)" /></div>' +
        '<img src="'+BASE+'/api/image/view/'+f.name+'" style="width:100%;aspect-ratio:1;object-fit:cover;" loading="lazy" />' +
        '<div style="padding:4px 6px;font-size:10px;color:var(--muted);display:flex;justify-content:space-between;">' +
        '<span>'+date+'</span><span>'+sizeMB+'MB</span></div></div>';
    }).join('');
  } catch(e) {
    document.getElementById('imgGallery').innerHTML = '<div style="color:var(--muted);grid-column:1/-1;text-align:center;">로드 실패</div>';
  }
}

function previewImage(filename) {
  _previewFilename = filename;
  document.getElementById('imgPreviewImg').src = BASE+'/api/image/view/'+filename;
  document.getElementById('imgPreviewDownload').href = BASE+'/api/image/view/'+filename;
  document.getElementById('imgPreviewDownload').download = filename;
  document.getElementById('imgPreviewMeta').textContent = filename;
  document.getElementById('imgPreviewModal').style.display = 'flex';
}

function closeImgPreview() {
  document.getElementById('imgPreviewModal').style.display = 'none';
}

async function deletePreviewImage() {
  if (!_previewFilename || !confirm(_previewFilename+' 삭제?')) return;
  await fetch(BASE+'/api/image/delete/'+_previewFilename, {method:'DELETE'});
  closeImgPreview();
  loadImageGallery();
}

async function cleanupImages() {
  if (!confirm('보관 기간 초과 이미지를 정리합니다.')) return;
  const r = await fetch(BASE+'/api/image/cleanup', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json());
  alert('삭제: '+r.deleted+'장, 확보: '+r.freed_mb+'MB');
  loadImageGallery();
}

// ESC로 미리보기 닫기
document.addEventListener('keydown', e => { if(e.key==='Escape') closeImgPreview(); });

async function generateImage() {
  const prompt = document.getElementById('imgPrompt').value.trim();
  if (!prompt) { toast('프롬프트를 입력하세요', true); return; }

  const btn = document.getElementById('imgGenBtn');
  const statusEl = document.getElementById('imgGenStatus');
  btn.disabled = true;
  btn.textContent = '생성 중...';
  document.getElementById('imgResultCard').style.display = 'none';

  const body = {
    prompt,
    model:  document.getElementById('imgModel').value,
    style:  document.getElementById('imgStyle').value,
    width:  parseInt(document.getElementById('imgWidth').value),
    height: parseInt(document.getElementById('imgHeight').value),
    steps:  parseInt(document.getElementById('imgSteps').value),
  };
  const seedVal = document.getElementById('imgSeed').value;
  if (seedVal) body.seed = parseInt(seedVal);
  const negVal = document.getElementById('imgNegative').value.trim();
  if (negVal) body.negative_prompt = negVal;

  const t0 = Date.now();
  try {
    const r = await fetch(api('/api/image/generate'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!d.job_id) { toast(d.error || '생성 실패', true); resetImgBtn(); return; }

    statusEl.textContent = '큐 대기 중...';
    _imgPollTimer = setInterval(async () => {
      const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
      statusEl.textContent = `생성 중... ${elapsed}s`;
      try {
        const sr = await fetch(api(`/api/queue/job/${d.job_id}`));
        const sd = await sr.json();
        if (sd.status === 'completed' && sd.response) {
          clearInterval(_imgPollTimer);
          const elapsed2 = ((Date.now() - t0) / 1000).toFixed(1);
          const imgData = typeof sd.response === 'string' ? JSON.parse(sd.response) : sd.response;
          const imgUrl = api(`/api/image/view/${imgData.filename}`) + (imgData.subfolder ? `?subfolder=${imgData.subfolder}` : '');
          document.getElementById('imgResult').src = imgUrl;
          document.getElementById('imgDownloadBtn').href = imgUrl;
          document.getElementById('imgDownloadBtn').download = imgData.filename;
          document.getElementById('imgResultMeta').textContent =
            `${body.width}×${body.height} · steps ${body.steps} · ${elapsed2}s` + (body.seed ? ` · seed ${body.seed}` : '');
          document.getElementById('imgResultCard').style.display = 'block';
          statusEl.textContent = `완료 (${elapsed2}s)`;
          resetImgBtn();
        } else if (sd.status === 'failed') {
          clearInterval(_imgPollTimer);
          toast(sd.error || '생성 오류', true);
          statusEl.textContent = '';
          resetImgBtn();
        }
      } catch(e) {}
    }, 2000);
  } catch(e) {
    toast('요청 실패: ' + e.message, true);
    resetImgBtn();
  }
}

function resetImgBtn() {
  const btn = document.getElementById('imgGenBtn');
  btn.disabled = false;
  btn.textContent = 'Generate';
}

// --- Activity Log ---
let _actRefreshTimer = null;
let _actOffset = 0;
const _ACT_LIMIT = 100;
let _actCurrentView = 'servers';  // 'servers' | 'logs'

const _methodColor = {GET:'#6366f1', POST:'#22c55e', DELETE:'#ef4444', PUT:'#f59e0b', PATCH:'#f59e0b'};
function _statusBadge(code) {
  const cls = code >= 500 ? 'badge-failed' : code >= 400 ? 'badge-warning' : 'badge-completed';
  return `<span class="badge ${cls}">${code}</span>`;
}
function _methodBadge(m) {
  const c = _methodColor[m] || '#71717a';
  return `<span style="color:${c};font-weight:700;font-size:11px;">${m}</span>`;
}
function _durationBadge(ms) {
  if (!ms && ms !== 0) return '-';
  const cls = ms > 5000 ? 'color:var(--red)' : ms > 1000 ? 'color:var(--yellow)' : 'color:var(--green)';
  return `<span style="${cls};font-weight:600;">${ms >= 1000 ? (ms/1000).toFixed(1)+'s' : ms+'ms'}</span>`;
}

function switchActView(view) {
  _actCurrentView = view;
  const isServers = (view === 'servers');
  document.getElementById('actServersView').style.display = isServers ? '' : 'none';
  document.getElementById('actLogsView').style.display    = isServers ? 'none' : '';
  document.getElementById('actViewBtnServers').style.background = isServers ? 'var(--accent)' : '';
  document.getElementById('actViewBtnServers').style.color      = isServers ? '#fff' : '';
  document.getElementById('actViewBtnServers').className = 'btn btn-sm' + (isServers ? '' : ' btn-outline');
  document.getElementById('actViewBtnLogs').style.background = isServers ? '' : 'var(--accent)';
  document.getElementById('actViewBtnLogs').style.color      = isServers ? '' : '#fff';
  document.getElementById('actViewBtnLogs').className = 'btn btn-sm' + (isServers ? ' btn-outline' : '');
  refreshActView();
}

function refreshActView() {
  if (_actCurrentView === 'servers') loadActivityServers();
  else loadActivity();
}

function _fmtBytes(b) {
  if (!b) return '0B';
  if (b < 1024) return b + 'B';
  if (b < 1048576) return (b/1024).toFixed(1) + 'KB';
  return (b/1048576).toFixed(1) + 'MB';
}

function _miniTimeline(timeline, buckets) {
  if (!timeline || !timeline.length) return '';
  const maxCnt = Math.max(...timeline.map(t => t.cnt), 1);
  const bars = Array.from({length: buckets}, (_, i) => {
    const t = timeline.find(x => x.bucket === i);
    const cnt = t ? t.cnt : 0;
    const h = Math.max(2, Math.round((cnt / maxCnt) * 32));
    const col = cnt === 0 ? 'var(--border)' : 'var(--accent)';
    return `<div title="${cnt}건" style="width:${Math.floor(100/buckets)-1}%;height:${h}px;background:${col};border-radius:2px;align-self:flex-end;"></div>`;
  });
  return `<div style="display:flex;gap:2px;height:36px;align-items:flex-end;margin-top:6px;">${bars.join('')}</div>`;
}

async function loadActivityServers() {
  const hours = document.getElementById('actPeriod').value;
  try {
    const r = await fetch(api('/api/activity/servers?hours=' + hours));
    if (!r.ok) return;
    const d = await r.json();
    const servers = d.servers || [];

    // 요약 통계 업데이트
    const totalCalls  = servers.reduce((s, x) => s + (x.total_calls||0), 0);
    const totalErrors = servers.reduce((s, x) => s + (x.client_errors||0) + (x.server_errors||0), 0);
    const avgMs = servers.length
      ? Math.round(servers.reduce((s, x) => s + (x.avg_ms||0) * (x.total_calls||0), 0) / Math.max(totalCalls, 1))
      : 0;
    document.getElementById('actSrvCount').textContent     = servers.length;
    document.getElementById('actSrvTotalCalls').textContent = totalCalls.toLocaleString();
    document.getElementById('actSrvAvgMs').textContent      = avgMs ? avgMs + 'ms' : '-';
    document.getElementById('actSrvErrors').textContent     = totalErrors;

    const container = document.getElementById('actServerCards');
    if (!servers.length) {
      container.innerHTML = '<div class="card" style="color:var(--muted);text-align:center;padding:32px;">기록 없음</div>';
      return;
    }

    container.innerHTML = servers.map(sv => {
      const keyName  = escHtml(sv.key_name || 'unknown');
      const ips      = escHtml(sv.ips || '-');
      const isLocal  = sv.key_name === 'local';
      const isWeb    = (sv.key_name || '').startsWith('web:');
      const keyLabel = isLocal ? '🖥 localhost' : isWeb ? '🌐 ' + keyName : '🔑 ' + keyName;
      const successRate = sv.success_rate ?? 0;
      const rateCol = successRate >= 95 ? 'var(--green)' : successRate >= 80 ? 'var(--yellow)' : 'var(--red)';
      const errTotal = (sv.client_errors||0) + (sv.server_errors||0);
      const lastSeen = sv.last_seen ? sv.last_seen.replace('T',' ').substring(0,16) : '-';
      const firstSeen = sv.first_seen ? sv.first_seen.replace('T',' ').substring(0,16) : '-';
      const buckets = 6;

      const topPathsHtml = (sv.top_paths && sv.top_paths.length)
        ? `<table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:8px;">
            <thead><tr>
              <th style="text-align:left;padding:3px 6px;color:var(--muted);font-weight:600;">경로</th>
              <th style="text-align:center;padding:3px 4px;color:var(--muted);">M</th>
              <th style="text-align:right;padding:3px 4px;color:var(--muted);">횟수</th>
              <th style="text-align:right;padding:3px 4px;color:var(--muted);">avg</th>
              <th style="text-align:right;padding:3px 4px;color:var(--muted);">오류</th>
            </tr></thead>
            <tbody>${sv.top_paths.map(p => `<tr>
              <td style="padding:3px 6px;font-family:monospace;overflow:hidden;max-width:220px;text-overflow:ellipsis;white-space:nowrap;"
                  title="${escHtml(p.path)}">${escHtml(p.path)}</td>
              <td style="text-align:center;padding:3px 4px;">${_methodBadge(p.method)}</td>
              <td style="text-align:right;padding:3px 4px;font-weight:600;">${p.cnt}</td>
              <td style="text-align:right;padding:3px 4px;">${_durationBadge(p.avg_ms)}</td>
              <td style="text-align:right;padding:3px 4px;color:${p.errors>0?'var(--red)':'var(--muted)'};">${p.errors||0}</td>
            </tr>`).join('')}</tbody>
           </table>`
        : '<div style="font-size:11px;color:var(--muted);margin-top:6px;">경로 정보 없음</div>';

      return `<div class="card" style="margin-bottom:12px;">
        <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;">
          <!-- 키 이름 / 기본 정보 -->
          <div style="min-width:180px;">
            <div style="font-size:15px;font-weight:700;color:var(--accent);">${keyLabel}</div>
            <div style="font-size:11px;color:var(--muted);font-family:monospace;margin-top:2px;">IP: ${ips}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;">첫 호출: ${firstSeen}</div>
            <div style="font-size:11px;color:var(--muted);">최근 호출: ${lastSeen}</div>
          </div>

          <!-- 핵심 지표 -->
          <div style="display:flex;gap:20px;flex-wrap:wrap;flex:1;">
            <div style="text-align:center;min-width:64px;">
              <div style="font-size:22px;font-weight:700;">${(sv.total_calls||0).toLocaleString()}</div>
              <div style="font-size:11px;color:var(--muted);">총 호출</div>
            </div>
            <div style="text-align:center;min-width:64px;">
              <div style="font-size:22px;font-weight:700;color:${rateCol};">${successRate}%</div>
              <div style="font-size:11px;color:var(--muted);">성공률</div>
            </div>
            <div style="text-align:center;min-width:64px;">
              <div style="font-size:22px;font-weight:700;">${sv.avg_ms||0}ms</div>
              <div style="font-size:11px;color:var(--muted);">평균</div>
            </div>
            <div style="text-align:center;min-width:64px;">
              <div style="font-size:22px;font-weight:700;color:${errTotal>0?'var(--red)':'inherit'};">${errTotal}</div>
              <div style="font-size:11px;color:var(--muted);">오류</div>
            </div>
            <div style="text-align:center;min-width:64px;">
              <div style="font-size:14px;font-weight:600;">${_fmtBytes(sv.total_res_bytes)}</div>
              <div style="font-size:11px;color:var(--muted);">응답 용량</div>
            </div>
          </div>

          <!-- 타임라인 -->
          <div style="min-width:120px;flex:0 0 120px;">
            <div style="font-size:11px;color:var(--muted);margin-bottom:2px;">시간대별 호출</div>
            ${_miniTimeline(sv.timeline, buckets)}
          </div>
        </div>

        <!-- Top Paths -->
        <div style="margin-top:4px;border-top:1px solid var(--border);padding-top:8px;">
          <div style="font-size:11px;color:var(--muted);font-weight:600;margin-bottom:2px;">TOP 경로</div>
          ${topPathsHtml}
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function loadActivity() {
  const hours  = document.getElementById('actPeriod').value;
  const method = document.getElementById('actFilterMethod').value;
  const pathQ  = document.getElementById('actFilterPath').value.trim();
  const status = document.getElementById('actFilterStatus').value;
  const modelQ = document.getElementById('actFilterModel').value.trim();
  const params = new URLSearchParams({limit:_ACT_LIMIT, offset:_actOffset, hours});
  if (method)  params.set('method', method);
  if (pathQ)   params.set('path',   pathQ);
  if (status)  params.set('status', status);
  if (modelQ)  params.set('model',  modelQ);

  try {
    const r = await fetch(api('/api/activity?' + params));
    if (!r.ok) return;
    const d = await r.json();

    // 상위 경로 태그
    const s = d.stats || {};
    const topEl = document.getElementById('actTopPaths');
    if (s.top_paths_1h && s.top_paths_1h.length) {
      topEl.innerHTML = '<span style="font-size:11px;color:var(--muted);margin-right:4px;">Top:</span>' +
        s.top_paths_1h.map(p =>
          `<span class="cat-tag cat-general" style="cursor:pointer;" onclick="document.getElementById('actFilterPath').value='${escHtml(p.path)}';loadActivity();">`
          + `${escHtml(p.path)} <strong>${p.cnt}</strong></span>`
        ).join('');
    } else { topEl.innerHTML = ''; }

    // 로그 테이블
    const tbody = document.getElementById('actBody');
    if (!d.logs || d.logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:24px;">기록 없음</td></tr>';
    } else {
      tbody.innerHTML = d.logs.map(row => {
        const t = row.ts ? row.ts.replace('T',' ').substring(0,19) : '-';
        return `<tr>
          <td style="font-size:11px;color:var(--muted);white-space:nowrap;">${t}</td>
          <td>${_methodBadge(row.method)}</td>
          <td style="font-size:12px;font-family:monospace;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
              title="${escHtml(row.path)}">${escHtml(row.path)}</td>
          <td>${_statusBadge(row.status_code)}</td>
          <td style="text-align:right;">${_durationBadge(row.duration_ms)}</td>
          <td style="font-size:11px;color:var(--muted);">${escHtml(row.model||'-')}</td>
          <td style="font-size:11px;color:var(--accent);font-weight:600;">${escHtml(row.key_name||'-')}</td>
          <td style="font-size:11px;color:var(--muted);font-family:monospace;">${escHtml(row.ip||'-')}</td>
        </tr>`;
      }).join('');
    }

    // 페이지네이션 안내
    document.getElementById('actPagination').textContent =
      `전체 ${d.total}건 중 ${_actOffset+1}~${Math.min(_actOffset+_ACT_LIMIT, d.total)}건 표시`;
  } catch(e) {}
}

function toggleActRefresh() {
  if (document.getElementById('actAutoRefresh').checked) {
    _actRefreshTimer = setInterval(refreshActView, 30000);
  } else {
    clearInterval(_actRefreshTimer);
    _actRefreshTimer = null;
  }
}

// --- Knowledge (RAG) ---
async function loadRagStats() {
  try {
    const r = await fetch(api('/api/rag/stats'));
    const d = await r.json();
    document.getElementById('ragCount').textContent = d.count || 0;
  } catch(e) { document.getElementById('ragCount').textContent = '-'; }
}

async function ragSearch() {
  const q = document.getElementById('ragQuery').value.trim();
  if (!q) return;
  document.getElementById('ragAnswer').style.display = 'none';
  const r = await fetch(api('/api/rag/search'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:q, n_results:5})});
  const d = await r.json();
  const results = d.results || [];
  document.getElementById('ragResults').innerHTML = results.length === 0
    ? '<div style="color:var(--muted);padding:12px;">검색 결과 없음. 문서를 먼저 색인해주세요.</div>'
    : results.map((r,i) => `<div class="catalog-card"><div class="catalog-info">
        <div class="catalog-name">${escHtml(r.id)} <span class="cat-tag cat-general">score: ${r.score}</span></div>
        <div class="catalog-desc">${escHtml(r.text.substring(0,150))}...</div>
      </div></div>`).join('');
}

async function ragAsk() {
  const q = document.getElementById('ragQuery').value.trim();
  if (!q) return;
  document.getElementById('ragAnswer').style.display = 'block';
  document.getElementById('ragAnswerText').textContent = 'Thinking...';
  document.getElementById('ragSources').innerHTML = '';
  document.getElementById('ragResults').innerHTML = '';
  const r = await fetch(api('/api/rag/ask'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:q})});
  const d = await r.json();
  document.getElementById('ragAnswerText').textContent = d.answer || 'No answer';
  const sources = d.sources || [];
  document.getElementById('ragSources').innerHTML = sources.length > 0
    ? 'Sources: ' + sources.map(s => `<span class="cat-tag cat-small">${escHtml(s.id)} (${s.score})</span>`).join(' ')
    : '';
}

async function ragIndexDoc() {
  const id = document.getElementById('ragDocId').value.trim();
  const text = document.getElementById('ragDocText').value.trim();
  if (!id || !text) { toast('ID and content required', true); return; }
  const category = document.getElementById('ragDocCategory').value.trim();
  const meta = category ? {category} : {};
  const r = await fetch(api('/api/rag/index'), {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id, text, metadata:meta})});
  const d = await r.json();
  if (d.ok) { toast('Document indexed'); document.getElementById('ragDocText').value=''; loadRagStats(); }
  else { toast(d.error||'Index failed', true); }
}

// --- Charts ---
let chartThroughput = null, chartSpeed = null, chartGpu = null, chartLatency = null;

async function loadDashboardCharts() {
  try {
    const r = await fetch(api('/api/stats/history?hours=24'));
    const data = await r.json();
    const pts = data.points || [];
    if (pts.length < 2 || typeof Chart === 'undefined') return;
    const labels = pts.map(p => {
      // ts가 "...+00:00Z"(오프셋+Z 이중) 형식 → new Date Invalid → NaN:NaN. Z 정규화.
      const d = new Date((p.ts || '').replace('+00:00Z', 'Z'));
      if (isNaN(d)) return '';
      return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');
    });
    const chartOpts = {
      responsive: true, animation: false,
      scales: {x:{ticks:{maxTicksLimit:12,color:'#71717a',font:{size:10}},grid:{color:'#2a2d3a'}},
               y:{beginAtZero:true,ticks:{color:'#71717a'},grid:{color:'#2a2d3a'}}},
      plugins:{legend:{labels:{color:'#e4e4e7',font:{size:11}}}}
    };

    const ctx1 = document.getElementById('chartThroughput');
    if (chartThroughput) chartThroughput.destroy();
    chartThroughput = new Chart(ctx1, {type:'line', data:{
      labels, datasets:[
        {label:'Completed', data:pts.map(p=>p.completed), borderColor:'#22c55e', backgroundColor:'rgba(34,197,94,0.1)', fill:true, tension:0.3, pointRadius:0},
        {label:'Failed', data:pts.map(p=>p.failed), borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,0.1)', fill:true, tension:0.3, pointRadius:0},
        {label:'Pending', data:pts.map(p=>p.pending), borderColor:'#eab308', tension:0.3, pointRadius:0},
      ]
    }, options:chartOpts});

    const ctx2 = document.getElementById('chartSpeed');
    if (chartSpeed) chartSpeed.destroy();
    chartSpeed = new Chart(ctx2, {type:'line', data:{
      labels, datasets:[
        {label:'tok/s', data:pts.map(p=>p.avg_speed), borderColor:'#6366f1', backgroundColor:'rgba(99,102,241,0.1)', fill:true, tension:0.3, pointRadius:0},
      ]
    }, options:chartOpts});

    // ── 성능 모니터링 (P4): 현재값 카드 + GPU/지연 트렌드 (동일 데이터, 신규 폴링 없음) ──
    const last = pts[pts.length - 1] || {};
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    if (last.vram_used_mb != null && last.vram_total_mb) {
      const pct = (last.vram_used_mb / last.vram_total_mb * 100).toFixed(0);
      set('perfVram', `${pct}% (${(last.vram_used_mb/1024).toFixed(1)}/${(last.vram_total_mb/1024).toFixed(1)}G)`);
    }
    set('perfTemp', last.gpu_temp_c != null ? `${last.gpu_temp_c}°C` : '--');
    set('perfP95', last.latency_p95_ms != null ? `${last.latency_p95_ms}ms` : '--');
    set('perfErr', last.error_rate_pct != null ? `${last.error_rate_pct}%` : '--');
    set('perfThreads', last.worker_threads != null ? `${last.worker_threads} (peak ${last.worker_threads_peak ?? '--'})` : '--');
    set('perfSse', last.sse_subscribers != null ? last.sse_subscribers : '--');
    set('perfRecycles', last.worker_recycles != null ? last.worker_recycles : '--');
    if (last.worker_uptime_s != null) {
      const m = Math.floor(last.worker_uptime_s / 60);
      set('perfUptime', m >= 60 ? `${(m/60).toFixed(1)}h` : `${m}m`);
    }
    set('perfModel', last.model_resident || '');
    // GPU 상주 비율 (CPU 폴백 감지, v2.124.34) — 1.0=완전 GPU·0=CPU 폴백
    {
      const el = document.getElementById('perfVramRatio');
      const r = last.model_vram_ratio;
      if (el) {
        if (r == null) { el.textContent = '--'; el.style.color = ''; }
        else if (r <= 0.01) { el.textContent = 'CPU 폴백!'; el.style.color = '#f43f5e'; }
        else if (r >= 0.99) { el.textContent = 'GPU 100%'; el.style.color = ''; }
        else { el.textContent = `GPU ${(r*100).toFixed(0)}%`; el.style.color = r < 0.5 ? '#f43f5e' : '#f59e0b'; }
      }
    }

    const ctxG = document.getElementById('chartGpu');
    if (ctxG) {
      if (chartGpu) chartGpu.destroy();
      chartGpu = new Chart(ctxG, {type:'line', data:{
        labels, datasets:[
          {label:'VRAM %', data:pts.map(p=> p.vram_total_mb ? Math.round(p.vram_used_mb/p.vram_total_mb*100) : null), borderColor:'#06b6d4', backgroundColor:'rgba(6,182,212,0.1)', fill:true, tension:0.3, pointRadius:0, yAxisID:'y'},
          {label:'GPU °C', data:pts.map(p=>p.gpu_temp_c), borderColor:'#f97316', tension:0.3, pointRadius:0, yAxisID:'y1'},
        ]
      }, options:{...chartOpts, scales:{...chartOpts.scales,
          y:{...chartOpts.scales.y, max:100, title:{display:true,text:'VRAM%',color:'#71717a'}},
          y1:{position:'right',beginAtZero:false,ticks:{color:'#71717a'},grid:{drawOnChartArea:false},title:{display:true,text:'°C',color:'#71717a'}}}}});
    }

    const ctxL = document.getElementById('chartLatency');
    if (ctxL) {
      if (chartLatency) chartLatency.destroy();
      chartLatency = new Chart(ctxL, {type:'line', data:{
        labels, datasets:[
          {label:'지연 p95 (ms)', data:pts.map(p=>p.latency_p95_ms), borderColor:'#a855f7', backgroundColor:'rgba(168,85,247,0.1)', fill:true, tension:0.3, pointRadius:0},
          {label:'에러율 5xx (%)', data:pts.map(p=>p.error_rate_pct), borderColor:'#ef4444', tension:0.3, pointRadius:0},
        ]
      }, options:chartOpts});
    }
  } catch(e) {}
}

// ── LLM 품질 평가 카드 (eval Phase 4) — 야간 배치 결과, 로드 시 1회 ──
async function loadEvalStatus() {
  try {
    const d = await fetch(api('/api/eval/status')).then(r => r.json());
    if (!d.available) return;
    const card = document.getElementById('evalCard');
    if (!card) return;
    card.style.display = '';
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    if (d.faithfulness) {
      set('evalFaith', d.faithfulness.mean.toFixed(3));
      set('evalHalluc', `${d.faithfulness.hallucinations}/${d.faithfulness.n}`);
      set('evalTs', (d.faithfulness.ts || '').slice(0, 10));
    }
    if (d.quality_index) {
      set('evalQi', `${d.quality_index.score.toFixed(1)} (${d.quality_index.grade})`);
      const qiEl = document.getElementById('evalQi');
      if (qiEl) qiEl.style.color = ({A: '#16a34a', B: '#0284c7', C: '#f59e0b', D: '#f43f5e'})[d.quality_index.grade] || '';
    }
    if (d.quality) {
      set('evalOverall', `${d.quality.overall}/5`);
      set('evalAcc', `${d.quality.accuracy}/5`);
      set('evalSafe', `${d.quality.safety}/5`);
      set('evalKo', `${(d.quality.korean_ratio * 100).toFixed(0)}%`);
    }
    // v2.120.0 세밀 지표 행 — 교차검증(MiniCheck)·체크리스트. 미측정 배치면 숨김 유지
    const f = d.faithfulness || {}, q = d.quality || {};
    if (f.mc_agree != null || q.checks_rate != null) {
      const row = document.getElementById('evalFineRow');
      if (row) row.style.display = 'grid';
      if (f.mc_agree != null) {
        set('evalMcAgree', `${(f.mc_agree * 100).toFixed(0)}%`);
        set('evalMcConflicts', `${f.mc_conflicts}건`);
        const mcEl = document.getElementById('evalMcConflicts');
        if (mcEl) mcEl.style.color = f.mc_conflicts > 0 ? 'var(--warn,#f59e0b)' : '';
        if (f.strict != null) set('evalStrict', f.strict.toFixed(3));
      }
      if (q.checks_rate != null) set('evalChecks', `${(q.checks_rate * 100).toFixed(0)}%`);
    }
    // 실패 체크 항목(빈도순) — 곧 개선 지점
    if (q.checks_failed_top && q.checks_failed_top.length) {
      const el = document.getElementById('evalFailedChecks');
      if (el) {
        el.style.display = '';
        el.textContent = '개선 지점: ' + q.checks_failed_top
          .map(([item, n]) => `${item}${n > 1 ? ' ×' + n : ''}`).join(' · ');
      }
    }
    // 질문별 세부 진단 테이블(최신 배치, 충실도·품질 질문 기준 병합)
    const fq = f.questions || [], qByQ = {};
    (q.questions || []).forEach(x => { qByQ[x.q] = x; });
    if (fq.length) {
      const body = document.getElementById('evalDetailBody');
      const wrap = document.getElementById('evalDetail');
      if (body && wrap) {
        wrap.style.display = '';
        body.innerHTML = fq.map(x => {
          const qq = qByQ[x.q] || {};
          const mc = x.mc_agree != null
            ? `${(x.mc_agree * 100).toFixed(0)}%${x.mc_conflicts ? ' ⚠' + x.mc_conflicts : ''}` : '—';
          const failed = (qq.checks_failed || []).join(', ') || '—';
          const warn = (x.mc_conflicts > 0 || x.contradicted > 0 || (qq.checks_failed || []).length)
            ? 'color:var(--warn,#f59e0b);' : '';
          const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
            c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
          return `<tr style="border-top:1px solid var(--surface2,#2a2d3a);">
            <td style="padding:3px 4px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(x.q)}">${esc(x.q)}</td>
            <td style="padding:3px 4px;">${x.faith != null ? x.faith.toFixed(3) : '—'}</td>
            <td style="padding:3px 4px;">${x.claims ?? '—'}</td>
            <td style="padding:3px 4px;${warn}">${mc}</td>
            <td style="padding:3px 4px;">${qq.overall != null ? qq.overall + '/5' : '—'}</td>
            <td style="padding:3px 4px;${warn}max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(failed)}">${esc(failed)}</td>
          </tr>`;
        }).join('');
      }
    }
    if (d.benchmarks && d.benchmarks.length) {
      document.getElementById('evalBench').textContent =
        '벤치: ' + d.benchmarks.slice(0, 4).map(b => `${b.model.split('/').pop()} ${b.accuracy}%`).join(' · ');
    }
    // v2.120.2 관리 영역 — 세밀 지표 시계열 2×2 (배치별, A/B 제외).
    // 팔레트: 다크 서피스 #1a1d27 검증 통과(#16a34a·#a855f7·#0284c7·#f43f5e — CVD/대비 PASS).
    // 미측정 배치=null → spanGaps:false로 결측 구간 표시(허위 연속성 방지).
    const tf = d.trend_faithfulness || [], tq = d.trend_quality || [];
    if ((tf.length >= 2 || tq.length >= 2) && typeof Chart !== 'undefined') {
      const box = document.getElementById('evalSparks');
      if (box) box.style.display = 'grid';
      const lineOpts = (yMin, yMax) => ({
        responsive: true, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true, labels: { color: '#a1a1aa', font: { size: 9 },
                             boxWidth: 8, boxHeight: 8, padding: 6 } } },
        scales: { x: { ticks: { color: '#71717a', font: { size: 8 }, maxTicksLimit: 4 },
                       grid: { display: false } },
                  y: { min: yMin, max: yMax, ticks: { color: '#71717a', font: { size: 9 }, maxTicksLimit: 3 },
                       grid: { color: '#2a2d3a' } } },
      });
      const ds = (label, data, color, extra) => Object.assign({
        label, data, borderColor: color, backgroundColor: color,
        borderWidth: 2, tension: 0.3, pointRadius: 2, spanGaps: false, fill: false,
      }, extra || {});
      const lbl = a => a.map(p => (p.ts || '').slice(5, 10));
      if (tf.length >= 2) {
        // 신뢰성: 충실도(주 KPI)+strict(보수)+교차검증 일치 — 전부 0~1 단일 축
        new Chart(document.getElementById('sparkFaith'), { type: 'line', data: {
          labels: lbl(tf), datasets: [
            ds('충실도', tf.map(p => p.mean), '#16a34a',
               { backgroundColor: 'rgba(22,163,74,0.08)', fill: true }),
            ds('strict', tf.map(p => p.strict), '#0284c7'),
            ds('교차 일치', tf.map(p => p.mc_agree), '#a855f7'),
          ],
        }, options: lineOpts(0, 1) });
        // 리뷰 트리거(건): 환각 플래그 + 교차검증 불일치 — 그룹 막대
        const trigEl = document.getElementById('sparkTriggers');
        if (trigEl) {
          new Chart(trigEl, { type: 'bar', data: {
            labels: lbl(tf), datasets: [
              { label: '환각 플래그', data: tf.map(p => p.hallucinations), backgroundColor: '#f43f5e',
                borderRadius: 3, maxBarThickness: 14 },
              { label: '교차 불일치', data: tf.map(p => p.mc_conflicts), backgroundColor: '#0284c7',
                borderRadius: 3, maxBarThickness: 14 },
            ],
          }, options: { responsive: true, animation: false,
            plugins: { legend: { display: true, labels: { color: '#a1a1aa', font: { size: 9 },
                                 boxWidth: 8, boxHeight: 8, padding: 6 } } },
            scales: { x: { ticks: { color: '#71717a', font: { size: 8 }, maxTicksLimit: 4 },
                           grid: { display: false } },
                      y: { beginAtZero: true, ticks: { color: '#71717a', font: { size: 9 },
                           maxTicksLimit: 3, precision: 0 }, grid: { color: '#2a2d3a' } } } } });
        }
      }
      if (tq.length >= 2) {
        // 품질 차원(1~5): 4차원 개별 추세 — overall은 타일·차원 평균으로 파생이라 중복 생략
        new Chart(document.getElementById('sparkQuality'), { type: 'line', data: {
          labels: lbl(tq), datasets: [
            ds('정확성', tq.map(p => p.accuracy), '#16a34a'),
            ds('도움됨', tq.map(p => p.helpfulness), '#0284c7'),
            ds('안전성', tq.map(p => p.safety), '#a855f7'),
            ds('자연성', tq.map(p => p.fluency), '#f43f5e'),
          ],
        }, options: lineOpts(1, 5) });
        // 체크리스트 통과율(0~1): 단일 계열 — 범례 불요(제목이 명명)
        const ckEl = document.getElementById('sparkChecks');
        if (ckEl) {
          const o = lineOpts(0, 1); o.plugins.legend.display = false;
          new Chart(ckEl, { type: 'line', data: {
            labels: lbl(tq), datasets: [
              ds('체크리스트 통과율', tq.map(p => p.checks_rate), '#a855f7',
                 { backgroundColor: 'rgba(168,85,247,0.08)', fill: true }),
            ],
          }, options: o });
        }
      }
    }
  } catch (e) {}
}
loadEvalStatus();

// ---------------------------------------------------------------------------
// Apple Silicon 실시간 모니터
// ---------------------------------------------------------------------------

const _asPanel = document.getElementById('asPanel');

async function loadAppleSiliconMetrics() {
  if (!_asPanel) return;
  try {
    const r = await fetch(BASE + '/api/system/apple-silicon');
    if (!r.ok) return;
    const d = await r.json();
    if (d.error) return;

    // 칩 정보
    const chip = d.chip || {};
    const chipNameEl = document.getElementById('asChipName');
    if (chipNameEl && chip.name) {
      const gpuStr = chip.cores_gpu ? ` · GPU ${chip.cores_gpu}코어` : '';
      const aneStr = chip.ane_tops ? ` · ANE ${chip.ane_tops}TOPS` : '';
      chipNameEl.textContent = `${chip.name} · ${chip.memory_gb}GB${gpuStr}${aneStr}`;
    }

    // CPU
    const cpu = d.cpu || {};
    const cpuTotalEl = document.getElementById('asCpuTotal');
    if (cpuTotalEl) cpuTotalEl.textContent = cpu.total != null ? cpu.total + '%' : '--';

    const pCoreEl = document.getElementById('asPCore');
    const eCoreEl = document.getElementById('asECore');
    if (pCoreEl) pCoreEl.textContent = cpu.p_cores_avg != null ? cpu.p_cores_avg + '%' : '--';
    if (eCoreEl) eCoreEl.textContent = cpu.e_cores_avg != null ? cpu.e_cores_avg + '%' : '--';

    // 코어별 미니 바
    const barEl = document.getElementById('asCoreBar');
    if (barEl && cpu.per_core && cpu.per_core.length) {
      barEl.innerHTML = cpu.per_core.map((v, i) => {
        const isP = cpu.n_p && i < cpu.n_p;
        const color = isP ? 'var(--accent)' : 'var(--green)';
        const h = Math.max(2, Math.round(v / 100 * 8));
        return `<div title="${isP ? 'P' : 'E'}-Core ${i}: ${v}%"
          style="flex:1;background:${color};opacity:${0.4 + v/100*0.6};border-radius:2px;align-self:flex-end;height:${h}px;"></div>`;
      }).join('');
    }

    // GPU
    const gpu = d.gpu || {};
    const gpuCoresEl = document.getElementById('asGpuCores');
    if (gpuCoresEl) gpuCoresEl.textContent = chip.cores_gpu ? `${chip.cores_gpu}코어` : '';
    const gpuRendEl = document.getElementById('asGpuRenderer');
    const gpuTilEl  = document.getElementById('asGpuTiler');
    const gpuDevEl  = document.getElementById('asGpuDevice');
    if (gpu.available) {
      if (gpuRendEl) gpuRendEl.textContent = gpu.renderer_util + '%';
      if (gpuTilEl)  gpuTilEl.textContent  = gpu.tiler_util + '%';
      if (gpuDevEl)  gpuDevEl.textContent  = gpu.device_util + '%';
    } else {
      // powermetrics 값 폴백
      const pm = d.powermetrics || {};
      const val = pm.available ? pm.gpu_active_pct + '%' : 'N/A';
      if (gpuRendEl) gpuRendEl.textContent = val;
      if (gpuTilEl)  gpuTilEl.textContent  = val;
      if (gpuDevEl)  gpuDevEl.textContent  = val;
    }

    // Unified Memory
    const mem = d.memory || {};
    const memLabelEl = document.getElementById('asMemLabel');
    const memBarEl   = document.getElementById('asMemBar');
    if (mem.total_gb) {
      if (memLabelEl) memLabelEl.textContent = `${mem.used_gb} / ${mem.total_gb} GB (${mem.percent}%)`;
      if (memBarEl) {
        memBarEl.style.width = mem.percent + '%';
        const barColor = mem.pressure === 'critical' ? 'var(--red)'
                       : mem.pressure === 'warning'  ? 'var(--yellow)'
                       : 'var(--green)';
        memBarEl.style.background = barColor;
      }
      const setEl = (id, val) => { const e = document.getElementById(id); if(e) e.textContent = val; };
      setEl('asMemWired',  mem.wired_gb      != null ? mem.wired_gb + 'GB'      : '--');
      setEl('asMemActive', mem.active_gb     != null ? mem.active_gb + 'GB'     : '--');
      setEl('asMemComp',   mem.compressed_gb != null ? mem.compressed_gb + 'GB' : '--');
      setEl('asMemFree',   mem.free_gb       != null ? mem.free_gb + 'GB'       : '--');
    }

    // ANE
    const aneEl = document.getElementById('asAne');
    if (aneEl) {
      const pm = d.powermetrics || {};
      if (pm.available && pm.ane_power_mw != null) {
        aneEl.textContent = pm.ane_power_mw > 0 ? `${pm.ane_power_mw}mW` : 'Idle';
      } else {
        aneEl.textContent = chip.ane_tops ? `${chip.ane_tops} TOPS` : '--';
      }
    }

    // Thermal
    const therm = d.thermal || {};
    const thermEl = document.getElementById('asThermal');
    if (thermEl && therm.label) {
      const colors = {green:'var(--green)', yellow:'var(--yellow)', orange:'#f97316', red:'var(--red)'};
      thermEl.textContent = `${therm.throttling ? '⚠ ' : ''}Thermal: ${therm.label}`;
      thermEl.style.color = colors[therm.color] || 'var(--muted)';
    }

    // Core ML 상태 (한 번만 조회)
    const cmlEl = document.getElementById('asCoreMl');
    if (cmlEl && cmlEl.textContent === '--') {
      fetch(BASE + '/api/embed/coreml/status').then(r => r.ok ? r.json() : {}).then(s => {
        if (s.available && s.converted_models && s.converted_models.length > 0) {
          cmlEl.textContent = 'Ready';
          cmlEl.style.color = 'var(--green)';
        } else if (s.available) {
          cmlEl.textContent = '미변환';
          cmlEl.style.color = 'var(--yellow)';
        } else {
          cmlEl.textContent = '미설치';
          cmlEl.style.color = 'var(--muted)';
        }
      }).catch(() => { cmlEl.textContent = '--'; });
    }

  } catch(e) {}
}

async function loadCacheStatus() {
  const el = document.getElementById('asCacheStatus');
  if (!el) return;
  el.style.display = 'block';
  el.innerHTML = '<span style="color:var(--muted);font-size:12px;">loading...</span>';
  try {
    const r = await fetch(BASE + '/api/models/cache-status');
    const d = await r.json();
    const models = d.loaded_models || [];
    const mem = d.memory || {};
    let html = `<div style="font-size:12px;color:var(--muted);margin-bottom:4px;">
      로드된 모델: <strong style="color:var(--fg)">${models.length}개</strong>
      ${mem.used_gb ? `| 메모리: <strong style="color:var(--fg)">${mem.used_gb}/${mem.total_gb}GB</strong>` : ''}
    </div>`;
    if (models.length) {
      html += '<div style="display:flex;flex-direction:column;gap:4px;">' +
        models.map(m => `<div style="display:flex;justify-content:space-between;align-items:center;background:var(--surface2);border-radius:4px;padding:4px 8px;font-size:12px;">
          <span>${m.name}</span>
          <div style="display:flex;gap:6px;align-items:center;">
            <span style="color:var(--muted)">${m.size_vram ? (m.size_vram/1e9).toFixed(1)+'GB' : ''}</span>
            <button class="btn" style="font-size:10px;padding:2px 8px;" onclick="unloadModel('${m.name}')">Unload</button>
          </div>
        </div>`).join('') + '</div>';
    } else {
      html += '<span style="font-size:12px;color:var(--muted)">메모리에 로드된 모델 없음</span>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<span style="color:var(--red);font-size:12px;">오류</span>';
  }
}

async function unloadModel(model) {
  if (!confirm(`${model} 메모리에서 해제하시겠습니까?`)) return;
  try {
    const r = await fetch(BASE + '/api/models/unload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model})
    });
    const d = await r.json();
    if (d.ok) { showToast(model + ' 해제됨'); loadCacheStatus(); }
    else showToast('오류: ' + (d.error || ''), true);
  } catch(e) { showToast('오류', true); }
}

function showModelWarmDialog() {
  const model = prompt('예열할 모델명 입력 (예: qwen2.5:7b)');
  if (!model) return;
  const keepAlive = prompt('keep_alive 시간 (예: 1h, 30m, -1=영구)', '1h');
  if (keepAlive === null) return;
  fetch(BASE + '/api/models/warm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model, keep_alive: keepAlive})
  }).then(r => r.json()).then(d => {
    if (d.ok) { showToast(`${model} 예열 완료 (${keepAlive})`); loadCacheStatus(); }
    else showToast('오류: ' + (d.error || ''), true);
  }).catch(() => showToast('오류', true));
}

// Auto-refresh
{ const t = _tabFromHash(); if (t && t !== 'dashboard') switchTab(t, {fromHash: true}); }  // 새로고침·링크로 같은 탭
refreshStatus();
loadDashboardCharts();
loadSystemInfo();
loadChangelog();  // 헤더 버전 배지 초기화
setInterval(refreshStatus, 10000);
setInterval(loadDashboardCharts, 60000); // 차트 1분마다

// Apple Silicon 모니터 (5초마다, iMac에서만 실행)
if (document.getElementById('asPanel')) {
  loadAppleSiliconMetrics();
  setInterval(loadAppleSiliconMetrics, 5000);
}

// --- Training Tab ---

let _wizardMode = 'balanced';

function showWizardStep(n) {
  for (let i=1;i<=4;i++){
    const el=document.getElementById('wStep'+i);
    const tab=document.getElementById('wTab'+i);
    if(el)el.style.display=i===n?'block':'none';
    if(tab){tab.style.background=i===n?'var(--accent)':'var(--surface)';tab.style.color=i===n?'#fff':'var(--muted)';}
  }
  if(n===2) loadWizardPreCheck();
  if(n===3) loadWizardResult();
  if(n===4) loadWizardDeploy();
}

function selectMode(mode) {
  _wizardMode=mode;
  ['wMode1','wMode2','wMode3'].forEach((id,i)=>{
    document.getElementById(id).style.borderColor=['fast','balanced','precise'][i]===mode?'var(--accent)':'var(--border)';
  });
}

function toggleAdvanced() {
  const el=document.getElementById('wAdvanced');
  el.style.display=el.style.display==='none'?'block':'none';
}

async function loadWizardPreCheck() {
  try {
    const gpu=await fetch(BASE+'/api/training/gpu').then(r=>r.json());
    const t={fast:'~10분',balanced:'~30분',precise:'~60분'};
    document.getElementById('wPreCheck').innerHTML=
      '📊 학습 재료: 준비됨 ✅<br>🖥️ GPU: '+gpu.gpu_pct+'% '+(gpu.idle?'유휴 ✅':'사용중 ⚠️')+'<br>⏰ 예상: '+t[_wizardMode]+'<br>📌 학습 중에도 기존 차단은 계속 작동';
  } catch {}
}

async function wizardStartTraining() {
  const selModel = document.getElementById('wModelSelect')?.value || 'qwen2.5:1.5b';
  const m={fast:selModel,balanced:selModel,precise:selModel};
  document.getElementById('wProgress').style.display='block';
  document.getElementById('wProgressBar').style.width='10%';
  document.getElementById('wProgressText').textContent='데이터 준비 중...';
  try {
    const r=await fetch(BASE+'/api/training/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_model:m[_wizardMode],trigger_type:'wizard-'+_wizardMode})}).then(r=>r.json());
    if(r.status==='running'){
      document.getElementById('wProgressText').textContent='🧠 AI가 배우고 있습니다... (Run #'+r.run_id+')';
      document.getElementById('wProgressBar').style.width='30%';
      const iv=setInterval(async()=>{
        try{const s=await fetch(BASE+'/api/training/status').then(r=>r.json());
        if(!s.running){clearInterval(iv);document.getElementById('wProgressBar').style.width='100%';document.getElementById('wProgressBar').style.background='#22c55e';document.getElementById('wProgressText').textContent='✅ 학습 완료! ③ 결과 확인으로 이동하세요.';}}catch{}
      },5000);
    } else {
      document.getElementById('wProgressText').textContent='⏳ '+r.status+': '+(r.reason||'');
    }
  } catch(e){document.getElementById('wProgressText').textContent='❌ '+e;}
}

async function loadWizardResult() {
  try {
    const prog = await fetch(BASE+'/api/training/progress').then(r=>r.json());
    const el = document.getElementById('wResult');
    const diagEl = document.getElementById('wDiagnosis');
    const misEl = document.getElementById('wMisclass');

    if (prog.eval) {
      const sc = Math.round((prog.eval.f1_score||0)*100);
      const clr = prog.eval.passed ? '#22c55e' : '#ef4444';
      const icon = prog.eval.passed ? '🎉' : '❌';

      // 점수 + 메트릭
      let html = '<div style="text-align:center;padding:16px;background:var(--surface);border-radius:10px;margin-bottom:12px;">';
      html += '<div style="font-size:28px;font-weight:800;color:'+clr+';">'+icon+' '+sc+'점 / 100점</div>';
      html += '<div style="background:var(--bg);border-radius:4px;height:10px;margin:12px auto;max-width:300px;overflow:hidden;"><div style="height:100%;width:'+Math.max(sc,2)+'%;background:'+clr+';border-radius:4px;"></div></div>';
      html += '</div>';

      // 상세 메트릭
      html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;">';
      html += '<div style="text-align:center;padding:8px;background:var(--surface);border-radius:8px;"><div style="font-size:16px;font-weight:700;color:#e4e4e7;">'+Math.round((prog.eval.recall||0)*100)+'%</div><div style="font-size:10px;color:var(--muted);">찾는 능력</div></div>';
      html += '<div style="text-align:center;padding:8px;background:var(--surface);border-radius:8px;"><div style="font-size:16px;font-weight:700;color:#e4e4e7;">'+Math.round((prog.eval.precision||0)*100)+'%</div><div style="font-size:10px;color:var(--muted);">정확도</div></div>';
      html += '<div style="text-align:center;padding:8px;background:var(--surface);border-radius:8px;"><div style="font-size:16px;font-weight:700;color:#e4e4e7;">'+(prog.eval.fn||0)+'</div><div style="font-size:10px;color:var(--muted);">놓친 건수</div></div>';
      html += '<div style="text-align:center;padding:8px;background:var(--surface);border-radius:8px;"><div style="font-size:16px;font-weight:700;color:#e4e4e7;">'+(prog.eval.fp||0)+'</div><div style="font-size:10px;color:var(--muted);">실수 건수</div></div>';
      html += '</div>';

      // 학습 정보
      if (prog.train) {
        html += '<div style="font-size:11px;color:var(--muted);padding:8px;background:var(--surface);border-radius:6px;margin-bottom:8px;">';
        html += 'Loss: '+(prog.train.loss||0).toFixed(4)+' | 소요: '+Math.round(prog.train.runtime_sec||0)+'초 | 데이터: '+(prog.train.samples||0)+'건 | 에포크: '+(prog.train.epochs||0);
        html += '</div>';
      }

      el.innerHTML = html;

      // 진단
      try {
        const status = await fetch(BASE+'/api/training/status').then(r=>r.json());
        const latestRun = (status.recent_runs||[])[0];
        if (latestRun) {
          const report = await fetch(BASE+'/api/training/runs/'+latestRun.id+'/report').then(r=>r.json());
          if (report.diagnosis) {
            let dHtml = '<div style="padding:12px;background:var(--surface);border-radius:8px;border-left:3px solid '+(report.diagnosis.passed?'#22c55e':'#ef4444')+'">';
            dHtml += '<div style="font-size:12px;font-weight:700;color:#e4e4e7;margin-bottom:6px;">'+(report.diagnosis.passed?'✅ 정상':'⚠️ 문제 진단')+'</div>';
            for (const issue of report.diagnosis.issues||[]) {
              dHtml += '<div style="font-size:11px;color:var(--muted);padding:2px 0;">• '+issue+'</div>';
            }
            if ((report.diagnosis.recommendations||[]).length) {
              dHtml += '<div style="font-size:12px;font-weight:700;color:var(--accent);margin-top:8px;">💡 권장 조치</div>';
              for (const rec of report.diagnosis.recommendations) {
                dHtml += '<div style="font-size:11px;color:var(--muted);padding:2px 0;">→ '+rec+'</div>';
              }
            }
            dHtml += '</div>';
            diagEl.innerHTML = dHtml;
            diagEl.style.display = 'block';
          }

          // 오분류 미리보기
          const mis = await fetch(BASE+'/api/training/runs/'+latestRun.id+'/misclassified?limit=5').then(r=>r.json());
          if (mis.total > 0) {
            let mHtml = '<div style="font-size:12px;font-weight:700;color:#e4e4e7;margin-bottom:6px;">❌ 오분류 '+mis.total+'건 (상위 5건)</div>';
            mHtml += '<table style="width:100%;font-size:11px;"><thead><tr style="color:var(--muted);"><th style="text-align:left;padding:4px;">텍스트</th><th>정답</th><th>AI</th><th>유형</th></tr></thead><tbody>';
            for (const m of mis.misclassified) {
              const tc = m.type==='FP'?'color:#eab308':'color:#ef4444';
              mHtml += '<tr style="border-bottom:1px solid rgba(30,33,48,0.2);"><td style="padding:4px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);">'+((m.text||'').slice(0,50))+'</td><td style="text-align:center;color:var(--muted);">'+m.expected+'</td><td style="text-align:center;color:var(--muted);">'+m.predicted+'</td><td style="text-align:center;'+tc+';font-weight:600;">'+m.type+'</td></tr>';
            }
            mHtml += '</tbody></table>';
            misEl.innerHTML = mHtml;
            misEl.style.display = 'block';
          }
        }
      } catch {}

    } else if (prog.train) {
      el.innerHTML = '<div style="color:var(--muted);">학습 완료 (loss: '+((prog.train.loss||0).toFixed(4))+'), 평가 대기 중...</div>';
    } else {
      el.innerHTML = '<div style="color:var(--muted);">학습 완료 후 결과가 표시됩니다.</div>';
    }
  } catch {}
}

function downloadReport() {
  fetch(BASE+'/api/training/status').then(r=>r.json()).then(s=>{
    const run = (s.recent_runs||[])[0];
    if (run) window.open(BASE+'/api/training/runs/'+run.id+'/report');
  });
}
function downloadMisclass() {
  fetch(BASE+'/api/training/status').then(r=>r.json()).then(s=>{
    const run = (s.recent_runs||[])[0];
    if (run) window.open(BASE+'/api/training/runs/'+run.id+'/misclassified?limit=200');
  });
}


async function loadWizardDeploy() {
  try {
    const p=await fetch(BASE+'/api/training/progress').then(r=>r.json());
    const el=document.getElementById('wDeploy');
    if(p.deploy) el.innerHTML='<div style="text-align:center;padding:16px;color:#22c55e;font-weight:700;">🎉 '+p.deploy.model_name+' 적용 완료</div>';
    else if(p.eval?.passed) el.innerHTML='<div style="text-align:center;"><button class="btn" onclick="wizardDeploy()" style="padding:12px 40px;background:var(--accent);color:#fff;border-radius:10px;">🚀 새 모델 적용하기</button></div>';
    else el.innerHTML='<div style="color:var(--muted);">학습+평가 통과 후 적용 가능합니다.</div>';
  } catch {}
}

async function wizardDeploy() {
  const s=await fetch(BASE+'/api/training/status').then(r=>r.json());
  const r=(s.recent_runs||[]).find(r=>r.status==='completed');
  if(r){await fetch(BASE+'/api/training/deploy/'+r.id,{method:'POST'});loadWizardDeploy();loadTrainingStatus();}
}

async function loadTargetModels() {
  try {
    const r = await fetch(BASE+'/api/models').then(r=>r.json());
    const sel = document.getElementById('wTargetModel');
    const sel2 = document.getElementById('wModelSelect');
    if (!r.models) return;
    const current = sel?.value;
    [sel, sel2].forEach(s => {
      if (!s) return;
      const cur = s.value;
      s.innerHTML = '';
      for (const m of r.models) {
        if (m.name.includes('embed')) continue;
        const sz = (m.size / 1024 / 1024 / 1024).toFixed(1);
        const opt = document.createElement('option');
        opt.value = m.name;
        opt.textContent = m.name + ' (' + sz + 'GB)';
        if (m.name === cur) opt.selected = true;
        s.appendChild(opt);
      }
    });
    updateTargetModelInfo();
  } catch {}
}

const PRESETS = {
  balanced:  {label:"🛡️ 종합 균형",weights:{profanity:2,spam:2,hate_speech:2,harassment:2,sexual:2,misinfo:2,self_harm:2},prompt:"당신은 커뮤니티 콘텐츠 모더레이터입니다.\n텍스트에서 유해 콘텐츠를 감지하되, 정상적인 의견 표현은 보호하세요.\n정치적 의견, 비판적 토론, 풍자, 감정 표현은 유해하지 않습니다."},
  profanity: {label:"🤬 욕설 특화",weights:{profanity:6,spam:2,hate_speech:2,harassment:2,sexual:2,misinfo:1,self_harm:2},prompt:"당신은 욕설/비속어 전문 감지기입니다.\n한국어 욕설의 모든 변형(초성 분리, 숫자 치환, 띄어쓰기, 외국어 혼합)을 감지하세요.\n맥락상 친근한 표현과 실제 욕설을 구분하세요."},
  spam:      {label:"📢 스팸 차단",weights:{profanity:2,spam:6,hate_speech:2,harassment:2,sexual:2,misinfo:2,self_harm:1},prompt:"당신은 스팸/광고 감지 전문가입니다.\n광고, 도배, 피싱 링크, 홍보성 글을 감지하세요.\n일반 리뷰나 추천은 스팸이 아닙니다."},
  hate:      {label:"🔥 혐오 감지",weights:{profanity:2,spam:2,hate_speech:6,harassment:4,sexual:2,misinfo:2,self_harm:2},prompt:"당신은 혐오 표현 감지 전문가입니다.\n인종, 성별, 종교, 장애 등에 대한 차별과 혐오 표현을 감지하세요.\n비판적 의견과 혐오 표현을 구분하세요."},
  corporate: {label:"🏢 기업/공식",weights:{profanity:4,spam:2,hate_speech:4,harassment:4,sexual:6,misinfo:2,self_harm:2},prompt:"당신은 기업 콘텐츠 심사관입니다.\n비속어, 은어, 성적 암시, 차별 표현을 엄격하게 차단하세요.\n전문적이고 공식적인 표현만 허용합니다."},
  news:      {label:"📰 뉴스/미디어",weights:{profanity:2,spam:4,hate_speech:2,harassment:2,sexual:2,misinfo:6,self_harm:2},prompt:"당신은 뉴스 콘텐츠 검증관입니다.\n명백한 허위 정보, 선동, 미검증 의료/법률 정보를 감지하세요.\n의견과 사실을 구분하세요."},
  gaming:    {label:"🎮 게임/캐주얼",weights:{profanity:1,spam:2,hate_speech:4,harassment:4,sexual:2,misinfo:1,self_harm:2},prompt:"당신은 게임 채팅 모더레이터입니다.\n가벼운 비속어는 허용하되, 심한 인신공격, 차별 발언, 성적 콘텐츠는 차단하세요."},
  custom:    {label:"✏️ 직접 설정",weights:{profanity:2,spam:2,hate_speech:2,harassment:2,sexual:2,misinfo:2,self_harm:2},prompt:""},
};
const WEIGHT_MAP = [0.5, 0.75, 1, 1.5, 2, 2.5, 3];
const WEIGHT_LABELS = ["×0.5","×0.75","×1","×1.5","×2","×2.5","×3"];
let _currentPreset = "balanced";

function selectPreset(name) {
  _currentPreset = name;
  const p = PRESETS[name];
  if (!p) return;
  // 카드 선택 표시
  document.querySelectorAll('#wPresets > div').forEach(d => d.style.borderColor = 'var(--border)');
  const card = document.getElementById('preset-'+name);
  if (card) card.style.borderColor = 'var(--accent)';
  document.getElementById('wPresetLabel').textContent = p.label;
  // 가중치 설정
  for (const [k,v] of Object.entries(p.weights)) {
    const slider = document.getElementById('w-'+k);
    if (slider) slider.value = v;
  }
  onWeightChange();
  // 프롬프트 (custom이 아닌 경우만)
  if (name !== 'custom' && p.prompt) {
    document.getElementById('wSystemPrompt').value = p.prompt;
  }
}

function onWeightChange() {
  for (const k of ['profanity','spam','hate_speech','harassment','sexual','misinfo','self_harm']) {
    const slider = document.getElementById('w-'+k);
    const label = document.getElementById('wv-'+k);
    if (slider && label) {
      const idx = parseInt(slider.value);
      label.textContent = WEIGHT_LABELS[idx] || '×1';
    }
  }
}

function resetPrompt() {
  const p = PRESETS[_currentPreset];
  if (p && p.prompt) document.getElementById('wSystemPrompt').value = p.prompt;
  else document.getElementById('wSystemPrompt').value = PRESETS.balanced.prompt;
}

// ⚠️ 현재 화면에서 호출되지 않는다(저장 버튼 비활성 — v2.125.64). 서버는 preset·system_prompt·category_weights를
//    저장하지 않고 이제 400(unknown_keys)으로 거절한다. 예전엔 응답을 안 보고 '✅ 저장됨'을 띄웠다(04-21~09-30, 저장 0회).
//    실제로 배선할 때: 서버 허용 키 추가 + 학습 판정 프롬프트가 이 값을 읽게 + 이 함수를 saveJSON으로.
async function saveDirection() {
  const weights = {};
  for (const k of ['profanity','spam','hate_speech','harassment','sexual','misinfo','self_harm']) {
    const slider = document.getElementById('w-'+k);
    if (slider) weights[k] = WEIGHT_MAP[parseInt(slider.value)] || 1;
  }
  const prompt = document.getElementById('wSystemPrompt').value;
  const d = await saveJSON('/api/training/config',
    {preset: _currentPreset, system_prompt: prompt, category_weights: JSON.stringify(weights)}, '방향 설정 저장됨');
  document.getElementById('wDirMsg').textContent = d ? '✅ 저장됨' : '❌ 저장 안 됨';
}

function updateTargetModelInfo() {
  const sel = document.getElementById('wTargetModel');
  const info = document.getElementById('wTargetInfo');
  const status = document.getElementById('wTargetStatus');
  if (!sel || !info) return;
  const v = sel.value;
  // Step2 모델도 동기화
  const sel2 = document.getElementById('wModelSelect');
  if (sel2) sel2.value = v;

  info.textContent = '이 모델에 아래 재료를 학습시킵니다';
  if (v.includes('1.5b')) status.textContent = '⚡ 가볍고 빠름 · VRAM ~3GB · 처음 학습에 추천';
  else if (v.includes('3b')) status.textContent = '⚖️ 균형 · VRAM ~5GB · 일반적 사용';
  else if (v.includes('7b')) status.textContent = '🎯 정확 · VRAM ~8GB · 고품질';
  else if (v.includes('14b')) status.textContent = '⚠️ 최고 정확도 · VRAM ~12GB · 학습 시 서비스 중단 필요';
  else status.textContent = '';
}

async function loadOllamaModels() {
  try {
    const r = await fetch(BASE+'/api/models').then(r=>r.json());
    const sel = document.getElementById('wModelSelect');
    if (!sel || !r.models) return;
    const current = sel.value;
    sel.innerHTML = '';
    for (const m of r.models) {
      const sz = (m.size / 1024 / 1024 / 1024).toFixed(1);
      const opt = document.createElement('option');
      opt.value = m.name;
      opt.textContent = m.name + ' (' + sz + 'GB)';
      if (m.name === current) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {}
}

function updateModelInfo() {
  const sel = document.getElementById('wModelSelect');
  const info = document.getElementById('wModelInfo');
  if (!sel || !info) return;
  const v = sel.value;
  if (v.includes('1.5b')) info.textContent = '💡 가볍고 빠름 (~10분). 처음 학습에 추천.';
  else if (v.includes('3b')) info.textContent = '💡 균형 잡힌 성능 (~30분). 일반적 사용에 적합.';
  else if (v.includes('7b')) info.textContent = '💡 높은 정확도 (~60분). VRAM 8GB+ 필요.';
  else if (v.includes('14b')) info.textContent = '⚠️ 최고 정확도지만 학습 시 다른 서비스 중단 필요 (VRAM 12GB+).';
  else info.textContent = '💡 선택한 모델로 학습합니다.';
}

async function loadCollectionStats() {
  try {
    const r = await fetch(BASE+'/api/mod/stats').then(r=>r.json());
    document.getElementById('wCollRaw').textContent = r.raw?.total || 0;
    document.getElementById('wCollRefined').textContent = r.refined?.total || 0;
    document.getElementById('wCollPending').textContent = r.raw?.pending || 0;
    document.getElementById('wCollCorrections').textContent = r.raw?.corrections || 0;
  } catch {}
}

async function runRefine() {
  document.getElementById('wRefineMsg').textContent = '정제 중...';
  try {
    const r = await fetch(BASE+'/api/mod/refine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'run'})}).then(r=>r.json());
    document.getElementById('wRefineMsg').textContent = '✅ 입력 '+r.input+'→출력 '+r.output+' (정제 '+r.refined_total+'건)';
    loadCollectionStats();
  } catch { document.getElementById('wRefineMsg').textContent = '❌ 실패'; }
  setTimeout(()=>document.getElementById('wRefineMsg').textContent='', 5000);
}

async function loadFileList() {
  try {
    const r = await fetch(BASE+'/api/training/files').then(r=>r.json());
    const el = document.getElementById('wFileList');
    if (!r.files || r.files.length === 0) {
      el.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:8px;">파일 없음 — 위에서 업로드하세요</div>';
      return;
    }
    el.innerHTML = '<table style="width:100%;font-size:11px;"><thead><tr style="color:var(--muted);"><th style="text-align:left;padding:4px;">파일명</th><th>크기</th><th>줄 수</th><th>수정일</th><th>액션</th></tr></thead><tbody>' +
      r.files.map(f =>
        '<tr style="border-bottom:1px solid rgba(30,33,48,0.3);">' +
        '<td style="padding:4px;color:#e4e4e7;font-family:monospace;">' + f.name + '</td>' +
        '<td style="text-align:center;color:var(--muted);">' + f.size_display + '</td>' +
        '<td style="text-align:center;color:var(--muted);">' + f.lines + '</td>' +
        '<td style="text-align:center;color:var(--muted);">' + f.modified + '</td>' +
        '<td style="text-align:center;">' +
          '<a href="'+BASE+'/api/training/files/download/'+f.name+'" style="color:var(--accent);margin-right:6px;font-size:10px;">⬇</a>' +
          '<span onclick="previewFile(\''+f.name+'\')" style="color:var(--accent);margin-right:6px;cursor:pointer;font-size:10px;">👁</span>' +
          '<span onclick="deleteFile(\''+f.name+'\')" style="color:#ef4444;cursor:pointer;font-size:10px;">🗑</span>' +
        '</td></tr>'
      ).join('') + '</tbody></table>';
  } catch(e) { console.error(e); }
}

async function wizardUploadFile() {
  const input = document.getElementById('wFileInput');
  if (!input.files.length) return;
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const r = await fetch(BASE+'/api/training/files/upload', {method:'POST', body:fd}).then(r=>r.json());
    if (r.ok) {
      const out = document.getElementById('cliOutput');
      out.textContent += '\n📤 업로드 완료: ' + r.name + ' (' + r.lines + '줄)\n';
      out.scrollTop = out.scrollHeight;
      loadFileList();
    } else { alert(r.error || '업로드 실패'); }
  } catch(e) { alert('업로드 오류: '+e); }
  input.value = '';
}

async function deleteFile(name) {
  if (!confirm(name + ' 삭제?')) return;
  await fetch(BASE+'/api/training/files/'+name, {method:'DELETE'});
  loadFileList();
}

async function previewFile(name) {
  const r = await fetch(BASE+'/api/training/files/preview/'+name).then(r=>r.json());
  const out = document.getElementById('cliOutput');
  out.textContent += '\n📄 ' + name + ' 미리보기 (첫 ' + r.count + '줄):\n' + r.preview.join('\n') + '\n';
  out.scrollTop = out.scrollHeight;
}

// loadWizardData에서 파일 목록도 로드
const _origLoadWizardData = typeof loadWizardData === 'function' ? loadWizardData : null;


async function wizardAddSample() {
  const t=document.getElementById('wAddText').value.trim(),l=document.getElementById('wAddLabel').value;
  if(!t)return;
  document.getElementById('wAddText').value='';
  const out=document.getElementById('cliOutput');
  out.textContent+='\n❯ 재료 추가: "'+t+'" → '+l+'\n추가 완료\n';
  out.scrollTop=out.scrollHeight;
}

async function wizardGenerate() {
  const w=document.getElementById('wGenWord').value.trim();
  if(!w)return;
  const r=await fetch(BASE+'/api/training/cli',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:'패턴 제안 '+w})}).then(r=>r.json());
  const el=document.getElementById('wGenResult');
  el.style.display='block';
  el.textContent=r.output||'';
}

async function loadTrainingStatus() {
  try {
    const [status, gpu] = await Promise.all([
      fetch(BASE+'/api/training/status').then(r=>r.json()),
      fetch(BASE+'/api/training/gpu').then(r=>r.json()),
    ]);
    // ⚠️ #trainingStatus·#gpuStatus는 학습 패널 재설계로 템플릿에서 빠졌다 — 예전엔 여기서 null에 써서
    // 예외 → 아래 설정값·학습 이력 표가 **한 번도 채워지지 않았다**(탭을 열 때마다 콘솔 예외 · 2026-09-29 발견).
    const el = document.getElementById('trainingStatus');
    const running = status.running ? '<span style="color:#eab308;font-weight:bold;">RUNNING</span>' : '<span style="color:#22c55e;">Idle</span>';
    const paused = status.paused ? ' (PAUSED)' : '';
    if (el) el.innerHTML = `Status: ${running}${paused} | Run: #${status.run_id||'-'}`;

    const gel = document.getElementById('gpuStatus');
    if (gel) gel.innerHTML = `GPU: ${gpu.gpu_pct}% | Idle: ${gpu.idle?'<span style="color:#22c55e">Yes</span>':'<span style="color:#ef4444">No</span>'} | Queue: ${gpu.queue_pending} pending | Hours OK: ${gpu.allowed_hours?'Yes':'No'}`;

    // Config
    if (status.config) {
      document.getElementById('cfgHours').value = status.config.allowed_hours||'00-06';
      document.getElementById('cfgGpuMax').value = status.config.gpu_max_pct||80;
      document.getElementById('cfgThreshold').value = status.config.auto_trigger_threshold||50;
      document.getElementById('cfgMaxDur').value = status.config.max_duration_min||120;
    }

    // Progress
    try {
      const prog = await fetch(BASE+'/api/training/progress').then(r=>r.json());
      const pEl = document.getElementById('trainingProgress');
      if (!pEl) throw new Error('no progress panel');  // 진행률 패널도 템플릿에 없다 — 이 블록만 건너뛴다
      if (status.running || prog.train || prog.eval) {
        pEl.style.display = 'block';
        if (prog.train) {
          document.getElementById('tpStep').textContent = prog.eval ? (prog.deploy ? '배포 완료' : '평가 완료') : '학습 완료';
          document.getElementById('tpLoss').textContent = (prog.train.loss || 0).toFixed(4);
          document.getElementById('tpTime').textContent = Math.round(prog.train.runtime_sec || 0) + '초';
          document.getElementById('tpBar').style.width = prog.eval ? '100%' : '60%';
        }
        if (prog.eval) {
          document.getElementById('tpF1').textContent = prog.eval.f1_score || '-';
          document.getElementById('tpF1').style.color = prog.eval.passed ? '#22c55e' : '#ef4444';
          document.getElementById('tpStep').textContent = prog.eval.passed ? (prog.deploy ? '✅ 배포 완료' : '✅ 평가 통과') : '❌ 평가 미통과';
          document.getElementById('tpBar').style.width = prog.deploy ? '100%' : '80%';
          document.getElementById('tpBar').style.background = prog.eval.passed ? '#22c55e' : '#ef4444';
        }
        if (prog.deploy) {
          document.getElementById('tpStep').textContent = '✅ ' + (prog.deploy.model_name || '') + ' 배포 완료';
          document.getElementById('tpBar').style.width = '100%';
          document.getElementById('tpBar').style.background = '#22c55e';
        }
        if (status.running && !prog.train) {
          document.getElementById('tpStep').textContent = '⏳ 학습 진행 중...';
          document.getElementById('tpBar').style.width = '30%';
        }
        // VRAM
        try {
          const gpu = await fetch(BASE+'/api/training/gpu').then(r=>r.json());
          document.getElementById('tpVram').textContent = gpu.gpu_pct + '%';
        } catch {}
      } else {
        pEl.style.display = 'none';
      }
    } catch {}

    // Runs
    const runs = status.recent_runs || [];
    const tbody = document.getElementById('trainingRuns');
    if (runs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#71717a;">학습 이력 없음</td></tr>';
    } else {
      tbody.innerHTML = runs.map(r => {
        const sc = r.status==='deployed'?'color:#22c55e':r.status==='failed'?'color:#ef4444':r.status==='running'?'color:#eab308':'';
        const actions = (r.status==='completed'?`<button class="btn btn-sm" onclick="deployRun(${r.id})">배포</button> `:'')
          + (r.status==='deployed'?`<button class="btn btn-sm" onclick="rollbackRun(${r.id})" style="background:#ef4444;">롤백</button> `:'')
          + `<button class="btn btn-sm" onclick="deleteRun(${r.id})" style="background:#71717a;font-size:10px;padding:2px 6px;">삭제</button>`;
        return `<tr><td>${r.id}</td><td style="${sc};font-weight:600;">${r.status}</td><td>${r.base_model||'-'}</td><td>${r.f1_score||'-'}</td><td>${r.sample_count||0}</td><td>${r.created_at||''}</td><td>${actions}</td></tr>`;
      }).join('');
    }
  } catch(e) {
    const el = document.getElementById('trainingStatus') || document.getElementById('trainingRuns');
    if (el) el.textContent = '학습 상태 조회 실패: ' + e.message;
    console.warn('loadTrainingStatus', e);
  }
}

async function trainingAction(action) {
  const url = action==='force'?'/api/training/start':'/api/training/'+action;
  const body = action==='force'?JSON.stringify({force:true,trigger_type:'web-admin'}):action==='start'?JSON.stringify({trigger_type:'web-admin'}):undefined;
  try {
    const r = await fetch(BASE+url, {method:'POST',headers:{'Content-Type':'application/json'},body});
    const d = await r.json();
    alert(JSON.stringify(d));
    loadTrainingStatus();
  } catch(e) { alert('Error: '+e); }
}

// 예전엔 응답을 안 보고 alert('Saved')였다 — 세션 만료·권한 거부(v2.125.61 탭 권한 403)에도 "Saved"(D4 같은 클래스).
async function saveTrainingConfig() {
  await saveJSON('/api/training/config', {
    allowed_hours: document.getElementById('cfgHours').value,
    gpu_max_pct: document.getElementById('cfgGpuMax').value,
    auto_trigger_threshold: document.getElementById('cfgThreshold').value,
    max_duration_min: document.getElementById('cfgMaxDur').value,
  }, '학습 설정 저장됨');
}

async function deployRun(id) {
  if (!confirm('Deploy run #'+id+'?')) return;
  await fetch(BASE+'/api/training/deploy/'+id, {method:'POST'});
  loadTrainingStatus();
}

async function viewReport(id) {
  const r = await fetch(BASE+'/api/training/runs/'+id+'/report').then(r=>r.json());
  const out = document.getElementById('cliOutput');
  let text = '\n📊 학습 리포트 — Run #'+id+'\n';
  text += '모델: '+(r.base_model||'-')+' | 상태: '+r.status+'\n';
  if (r.train_detail) text += 'Loss: '+(r.train_detail.loss||0).toFixed(4)+' | 소요: '+Math.round(r.train_detail.runtime_sec||0)+'초 | 데이터: '+(r.train_detail.samples||0)+'건\n';
  if (r.eval_detail) text += 'F1: '+r.eval_detail.f1_score+' | Precision: '+r.eval_detail.precision+' | Recall: '+r.eval_detail.recall+'\n오분류: '+r.eval_detail.misclassified_count+'건\n';
  if (r.diagnosis) {
    text += '\n⚠️ 진단:\n';
    for (const i of r.diagnosis.issues||[]) text += '  • '+i+'\n';
    text += '💡 권장:\n';
    for (const r2 of r.diagnosis.recommendations||[]) text += '  → '+r2+'\n';
  }
  out.textContent += text;
  out.scrollTop = out.scrollHeight;
}

async function deleteRun(id) {
  if (!confirm('Run #'+id+' 삭제?')) return;
  await fetch(BASE+'/api/training/runs/'+id, {method:'DELETE'});
  loadTrainingStatus();
}

async function rollbackRun(id) {
  if (!confirm('Rollback to run #'+id+'?')) return;
  await fetch(BASE+'/api/training/rollback/'+id, {method:'POST'});
  loadTrainingStatus();
}

// Auto-load when Training tab is shown

let _editPermUserId = null;

function showAddUser() { document.getElementById('addUserForm').style.display='block'; }

async function loadUsers() {
  try {
    const r = await fetch(BASE+'/api/admin/users').then(r=>r.json());
    const tb = document.getElementById('usersBody');
    if (!r.users || r.users.length===0) { tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);">사용자 없음</td></tr>'; return; }
    // 🔴 escape — v2.125.67 전에는 username·display_name·role을 innerHTML에 그대로, 권한 버튼 onclick엔 따옴표만 바꿔 넣었다.
    //    생성 API가 username 형식을 검사하지 않아 `<img onerror>`·`'`가 든 값이면 이 표(관리자 화면)에서 실행·깨짐.
    //    onclick 인자는 JSON 문자열을 HTML 속성용으로 이스케이프한다(_jsArg).
    const _jsArg = v => escHtml(JSON.stringify(String(v == null ? '' : v))).replace(/"/g, '&quot;');
    tb.innerHTML = r.users.map(u => {
      const roleColor = u.role==='admin'?'color:#ef4444':u.role==='operator'?'color:#eab308':'color:#22c55e';
      const active = u.is_active ? '<span style="color:#22c55e;">활성</span>' : '<span style="color:#ef4444;">비활성</span>';
      return '<tr style="border-bottom:1px solid rgba(30,33,48,0.3);">'+
        '<td style="padding:6px;color:var(--muted);">'+u.id+'</td>'+
        '<td style="padding:6px;color:#e4e4e7;font-weight:600;">'+escHtml(u.username)+'</td>'+
        '<td style="padding:6px;color:var(--muted);">'+(escHtml(u.display_name)||'-')+'</td>'+
        '<td style="padding:6px;text-align:center;"><span style="'+roleColor+';font-weight:600;">'+escHtml(u.role)+'</span></td>'+
        '<td style="padding:6px;text-align:center;">'+active+'</td>'+
        '<td style="padding:6px;text-align:center;color:var(--muted);font-size:11px;">'+(u.last_login||'없음')+'</td>'+
        '<td style="padding:6px;text-align:center;">'+
          (u.username!=='admin'?'<button class="btn btn-sm" onclick="toggleUserActive('+u.id+','+(!u.is_active)+')" style="font-size:10px;margin-right:4px;">'+(u.is_active?'비활성':'활성')+'</button>':'')+
          '<button class="btn btn-sm" onclick="openPermModal('+Number(u.id)+','+_jsArg(u.username)+','+_jsArg(u.role)+','+_jsArg(u.permissions||'{}')+')" style="font-size:10px;margin-right:4px;">권한</button>'+
          (u.username!=='admin'?'<button class="btn btn-sm" onclick="deleteUser('+u.id+')" style="font-size:10px;background:#ef4444;">삭제</button>':'')+
        '</td></tr>';
    }).join('');
  } catch(e) { console.error(e); }
}

async function createUser() {
  const u=document.getElementById('nuUser').value.trim();
  const p=document.getElementById('nuPass').value;
  const n=document.getElementById('nuName').value.trim();
  const r=document.getElementById('nuRole').value;
  if(!u||!p){alert('아이디와 비밀번호 필수');return;}
  const res=await fetch(BASE+'/api/admin/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p,display_name:n||u,role:r})}).then(r=>r.json());
  if(res.ok){document.getElementById('addUserForm').style.display='none';document.getElementById('nuUser').value='';document.getElementById('nuPass').value='';document.getElementById('nuName').value='';loadUsers();}
  else alert(res.error||'실패');
}

async function toggleUserActive(uid, active) {
  await fetch(BASE+'/api/admin/users/'+uid,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_active:active})});
  loadUsers();
}

async function deleteUser(uid) {
  if(!confirm('삭제?'))return;
  await fetch(BASE+'/api/admin/users/'+uid,{method:'DELETE'});
  loadUsers();
}

const MENU_LABELS = {dashboard:'대시보드',queue:'큐',models:'모델',chat:'채팅',generate:'생성',knowledge:'지식',image:'이미지',activity:'활동',settings:'설정',logs:'로그',training:'학습'};

function openPermModal(uid, username, role, permsStr) {
  _editPermUserId = uid;
  document.getElementById('permTitle').textContent = username + ' 권한 설정 (' + role + ')';
  let perms = {};
  try { perms = JSON.parse(permsStr); } catch { perms = ROLE_DEFAULTS[role] || {}; }
  const el = document.getElementById('permChecks');
  el.innerHTML = Object.entries(MENU_LABELS).map(([k,v]) =>
    '<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#e4e4e7;cursor:pointer;">'+
    '<input type="checkbox" class="permCb" data-key="'+k+'" '+(perms[k]?'checked':'')+' style="width:14px;height:14px;" />'+
    v+'</label>'
  ).join('');
  document.getElementById('permModal').style.display = 'flex';
}

function closePermModal() { document.getElementById('permModal').style.display='none'; }

async function savePermissions() {
  const perms = {};
  document.querySelectorAll('.permCb').forEach(cb => { perms[cb.dataset.key] = cb.checked; });
  await fetch(BASE+'/api/admin/users/'+_editPermUserId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({permissions:perms})});
  closePermModal();
  loadUsers();
}


const _origSwitchTab = typeof switchTab === 'function' ? switchTab : null;

const _cliHist = [];
let _cliIdx = -1;

function cliCmd(cmd) {
  document.getElementById('cliInput').value = cmd;
  cliRun();
}

async function cliRun() {
  const input = document.getElementById('cliInput');
  const out = document.getElementById('cliOutput');
  const cmd = input.value.trim();
  if (!cmd) return;
  _cliHist.unshift(cmd);
  _cliIdx = -1;
  out.textContent += '\n\n❯ ' + cmd + '\n';
  input.value = '';
  input.disabled = true;
  try {
    const r = await fetch(BASE+'/api/training/cli',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
    const d = await r.json();
    out.textContent += (d.output||'(응답 없음)') + '\n';
  } catch(e) { out.textContent += '오류: '+e+'\n'; }
  input.disabled = false;
  input.focus();
  out.scrollTop = out.scrollHeight;
  if(['상태','학습','GPU'].some(k=>cmd.includes(k))) loadTrainingStatus();
}

document.addEventListener('keydown',function(e){
  const el = document.getElementById('cliInput');
  if(document.activeElement!==el) return;
  if(e.key==='ArrowUp'){e.preventDefault();if(_cliIdx<_cliHist.length-1){_cliIdx++;el.value=_cliHist[_cliIdx];}}
  if(e.key==='ArrowDown'){e.preventDefault();if(_cliIdx>0){_cliIdx--;el.value=_cliHist[_cliIdx];}else{_cliIdx=-1;el.value='';}}
});


// === HR Law (근태/인사 법률) ===

async function hrLoadStats() {
  try {
    const r = await fetch(api('/api/hr-law/stats'));
    if (!r.ok) return;
    const d = await r.json();
    const el = (id) => document.getElementById(id);
    if (el('hrStatDocs')) el('hrStatDocs').textContent = d.total_documents || 0;
    if (el('hrStatIndexed')) el('hrStatIndexed').textContent = d.indexed || 0;
    if (el('hrStatCategories')) el('hrStatCategories').textContent = Object.keys(d.by_category || {}).length;
    if (el('hrStatQueries')) el('hrStatQueries').textContent = d.queries_total || 0;
  } catch(e) { console.error('HR stats error:', e); }
}

async function hrAsk() { hrChatSend(); }

function hrChatAddMsg(type, content) {
  var box = document.getElementById('hrChatMessages');
  var div = document.createElement('div');
  div.className = 'hr-msg hr-msg-' + type;
  div.innerHTML = content;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function hrChatClear() {
  var box = document.getElementById('hrChatMessages');
  box.innerHTML = '<div class="hr-msg hr-msg-system">대화가 초기화되었습니다. 새로운 질문을 입력해주세요.</div>';
}

async function hrChatSend() {
  var q = document.getElementById('hrQuestion').value.trim();
  if (!q) return;
  var cat = document.getElementById('hrCategory').value;

  // 사용자 메시지 표시
  hrChatAddMsg('user', q);
  document.getElementById('hrQuestion').value = '';
  document.getElementById('hrSendBtn').disabled = true;

  // 로딩 표시
  var loading = hrChatAddMsg('loading', '⏳ 답변 생성 중...');

  try {
    var r = await fetch(api('/api/hr-law/ask'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q, category: cat || undefined})
    });
    var d = await r.json();

    // 로딩 제거
    loading.remove();

    // AI 답변 표시
    var answer = d.answer || '답변을 생성하지 못했습니다.';
    var srcHtml = '';
    if (d.sources && d.sources.length > 0) {
      srcHtml = '<div class="hr-msg-sources">📎 ' + d.sources.map(function(s) {
        return s.law + ' ' + s.article;
      }).join(', ') + '</div>';
    }
    hrChatAddMsg('ai', answer + srcHtml);

    hrLoadStats();
    hrLoadHistory();
  } catch(e) {
    loading.remove();
    hrChatAddMsg('ai', '❌ 오류: ' + e.message);
  }
  document.getElementById('hrSendBtn').disabled = false;
  document.getElementById('hrQuestion').focus();
}

function hrShowCalc(type) {
  document.getElementById('hrCalcArea').style.display = 'block';
  ['Leave','Overtime','Severance'].forEach(function(t) {
    document.getElementById('hrCalc'+t).style.display = t.toLowerCase() === type ? 'block' : 'none';
  });
}

async function hrCalcLeave() {
  var start = document.getElementById('hrLeaveStart').value;
  if (!start) return alert('입사일을 입력하세요');
  var r = await fetch(api('/api/hr-law/calculate/annual-leave'), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({start_date: start})
  });
  var d = await r.json();
  document.getElementById('hrLeaveResult').innerHTML = d.error
    ? '<div style="color:#e74c3c">' + d.error + '</div>'
    : '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border)">'
      + '<div style="font-size:24px;font-weight:bold;color:var(--accent)">' + d.total_leave + '일</div>'
      + '<div style="margin-top:8px;color:var(--muted)">' + d.note + '</div>'
      + '<div style="margin-top:4px;font-size:12px;color:var(--muted)">📎 ' + d.legal_basis + '</div></div>';
}

async function hrCalcOvertime() {
  var r = await fetch(api('/api/hr-law/calculate/overtime'), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      hourly_wage: +document.getElementById('hrOtWage').value,
      extended_hours: +document.getElementById('hrOtExt').value,
      night_hours: +document.getElementById('hrOtNight').value,
      holiday_hours: +document.getElementById('hrOtHoliday').value,
      holiday_over8: +document.getElementById('hrOtHolidayO8').value,
    })
  });
  var d = await r.json();
  if (d.error) { document.getElementById('hrOvertimeResult').innerHTML = '<div style="color:#e74c3c">' + d.error + '</div>'; return; }
  var b = d.breakdown;
  document.getElementById('hrOvertimeResult').innerHTML =
    '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border)">'
    + '<div style="font-size:24px;font-weight:bold;color:#e67e22">' + d.total_overtime_pay.toLocaleString() + '원</div>'
    + '<table style="margin-top:8px;width:100%;font-size:13px"><tr><th>구분</th><th>시간</th><th>배율</th><th>금액</th></tr>'
    + '<tr><td>연장근로</td><td>' + b.extended.hours + 'h</td><td>' + b.extended.rate + '</td><td>' + b.extended.amount.toLocaleString() + '원</td></tr>'
    + '<tr><td>야간근로</td><td>' + b.night.hours + 'h</td><td>' + b.night.rate + '</td><td>' + b.night.amount.toLocaleString() + '원</td></tr>'
    + '<tr><td>휴일(8h이내)</td><td>' + b.holiday_8h.hours + 'h</td><td>' + b.holiday_8h.rate + '</td><td>' + b.holiday_8h.amount.toLocaleString() + '원</td></tr>'
    + '<tr><td>휴일(8h초과)</td><td>' + b.holiday_over8.hours + 'h</td><td>' + b.holiday_over8.rate + '</td><td>' + b.holiday_over8.amount.toLocaleString() + '원</td></tr>'
    + '</table>'
    + '<div style="margin-top:4px;font-size:12px;color:var(--muted)">📎 ' + d.legal_basis + '</div></div>';
}

async function hrCalcSeverance() {
  var r = await fetch(api('/api/hr-law/calculate/severance'), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      start_date: document.getElementById('hrSevStart').value,
      end_date: document.getElementById('hrSevEnd').value,
      avg_daily_wage: +document.getElementById('hrSevWage').value,
    })
  });
  var d = await r.json();
  document.getElementById('hrSeveranceResult').innerHTML = d.error
    ? '<div style="color:#e74c3c">' + d.error + '</div>'
    : '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border)">'
      + '<div style="font-size:24px;font-weight:bold;color:#27ae60">' + d.severance_pay.toLocaleString() + '원</div>'
      + '<div style="margin-top:8px;color:var(--muted)">근속 ' + d.years + '년 (' + d.working_days + '일) | 평균임금 ' + d.avg_daily_wage.toLocaleString() + '원/일</div>'
      + '<div style="margin-top:4px;font-size:12px;color:var(--muted)">📎 ' + d.legal_basis + ' | 산식: ' + d.formula + '</div></div>';
}

async function hrSearch() {
  var q = document.getElementById('hrSearchQuery').value.trim();
  if (!q) return;
  var r = await fetch(api('/api/hr-law/search'), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q})
  });
  var d = await r.json();
  if (!d.results || d.results.length === 0) {
    document.getElementById('hrSearchResults').innerHTML = '<div style="color:var(--muted)">검색 결과 없음</div>';
    return;
  }
  document.getElementById('hrSearchResults').innerHTML = d.results.map(function(r) {
    return '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border);margin-bottom:8px">'
      + '<div style="font-weight:bold;color:var(--accent)">' + r.law_name + ' ' + r.article + ' — ' + r.title + '</div>'
      + '<div style="margin-top:6px;font-size:13px;white-space:pre-wrap;max-height:200px;overflow:auto">' + r.content + '</div>'
      + '<div style="margin-top:4px;font-size:11px;color:var(--muted)">카테고리: ' + r.category + '</div></div>';
  }).join('');
}

async function hrLoadHistory() {
  try {
    var r = await fetch(api('/api/hr-law/history?limit=5'));
    if (!r.ok) return;
    var d = await r.json();
    var el = document.getElementById('hrHistory');
    if (!el) return;
    if (!d.queries || d.queries.length === 0) {
      el.innerHTML = '<div style="color:var(--muted)">아직 질의 이력이 없습니다</div>';
      return;
    }
    el.innerHTML = d.queries.map(function(q) {
      return '<div style="background:var(--bg);padding:10px;border-radius:6px;border:1px solid var(--border);margin-bottom:6px;cursor:pointer" onclick="document.getElementById(\'hrQuestion\').value=\''+q.question.replace(/'/g,"\\'")+'\';hrAsk()">'
        + '<div style="font-weight:bold;font-size:13px">❓ ' + q.question + '</div>'
        + '<div style="margin-top:4px;font-size:12px;color:var(--muted);max-height:60px;overflow:hidden">' + (q.answer||'').substring(0,150) + '...</div>'
        + '<div style="margin-top:2px;font-size:11px;color:var(--muted)">' + (q.created_at || '') + '</div></div>';
    }).join('');
  } catch(e) {}
}


// === Game NPC Management ===
var _gmGameKeys = {};

function gmSubTab(name) {
  ['tenants','templates','shared','heroines','logs','test'].forEach(function(t) {
    var el = document.getElementById('gmSub-' + t);
    var btn = document.getElementById('gmTab-' + t);
    if (el) el.style.display = t === name ? 'block' : 'none';
    if (btn) btn.style.background = t === name ? 'var(--accent)' : 'var(--surface)';
  });
  if (name === 'templates') gmLoadTemplates();
  if (name === 'shared') gmLoadShared();
  if (name === 'heroines') gmLoadHeroines();
  if (name === 'test') gmLoadTestNPCList();
}

async function gmLoad() {
  try {
    var r = await fetch(api('/api/game/tenants'));
    var d = await r.json();
    var tenants = d.tenants || [];
    document.getElementById('gmStatTenants').textContent = tenants.length;
    var html = '';
    _gmGameKeys = {};
    _gmAllKeys = [];
    tenants.forEach(function(t) {
      _gmGameKeys[t.id] = t.api_key;
      _gmAllKeys.push(t.api_key);
      html += '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border);margin-bottom:8px">'
        + '<div style="display:flex;justify-content:space-between;align-items:center">'
        + '<div><b>' + t.name + '</b> <span style="color:var(--muted);font-size:12px">(' + t.plan + ')</span></div>'
        + '<code style="font-size:11px;color:var(--muted)">' + t.api_key.substring(0,20) + '...</code></div>'
        + '<div style="font-size:12px;color:var(--muted);margin-top:4px">ID: ' + t.id + '</div></div>';
    });
    document.getElementById('gmTenantList').innerHTML = html || '<div style="color:var(--muted)">등록된 게임사 없음</div>';
    ['gmTplGameSelect','gmHeroineGameSelect'].forEach(function(sid) {
      var sel = document.getElementById(sid);
      if (!sel) return;
      sel.innerHTML = '<option value="">게임사 선택</option>';
      tenants.forEach(function(t) {
        sel.innerHTML += '<option value="' + t.api_key + '">' + t.name + '</option>';
      });
    });
    // 전체 통계 수집
    var totalTpl = 0, totalShared = 0, totalHeroine = 0;
    for (var i = 0; i < _gmAllKeys.length; i++) {
      try {
        var tr = await fetch(api('/api/game/npc/templates'), {headers:{'X-Game-Key':_gmAllKeys[i]}});
        var td = await tr.json();
        totalTpl += (td.templates||[]).length;
      } catch(e) {}
      try {
        var sr = await fetch(api('/api/game/shared/npcs'), {headers:{'X-Game-Key':_gmAllKeys[i]}});
        var sd = await sr.json();
        totalShared += (sd.shared_npcs||[]).length;
      } catch(e) {}
      try {
        var hr = await fetch(api('/api/game/heroine/list'), {headers:{'X-Game-Key':_gmAllKeys[i]}});
        var hd = await hr.json();
        totalHeroine += (hd.heroines||[]).length;
      } catch(e) {}
    }
    document.getElementById('gmStatTemplates').textContent = totalTpl;
    document.getElementById('gmStatShared').textContent = totalShared;
    document.getElementById('gmStatHeroines').textContent = totalHeroine;
    // 첫 게임사 있으면 자동 선택
    if (_gmAllKeys.length > 0) {
      document.getElementById('gmTplGameSelect').value = _gmAllKeys[0];
      document.getElementById('gmHeroineGameSelect').value = _gmAllKeys[0];
      gmLoadTemplates();
      gmLoadShared();
      gmLoadHeroines();
    }
  } catch(e) { console.error(e); }
}

async function gmRegisterTenant() {
  var name = document.getElementById('gmNewTenantName').value.trim();
  if (!name) { alert('게임/프로젝트명을 입력하세요'); return; }
  var plan = document.getElementById('gmNewTenantPlan').value;
  try {
    var r = await fetch(api('/api/game/register'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, plan:plan})});
    var d = await r.json();
    if (d.error) { alert('등록 실패: ' + d.error); return; }
    if (d.api_key) {
      alert('등록 완료!\n\nAPI Key:\n' + d.api_key + '\n\n이 키를 X-Game-Key 헤더로 사용합니다.');
      document.getElementById('gmNewTenantName').value = '';
      gmLoad();
    }
  } catch(e) { alert('오류: ' + e.message); }
}

async function gmLoadTemplates() {
  var gkey = document.getElementById('gmTplGameSelect').value;
  if (!gkey) return;
  var r = await fetch(api('/api/game/npc/templates'), {headers:{'X-Game-Key':gkey}});
  var d = await r.json();
  var templates = d.templates || [];
  document.getElementById('gmStatTemplates').textContent = templates.length;
  var html = '';
  templates.forEach(function(t) {
    var p = t.personality || {};
    html += '<div style="background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border);margin-bottom:8px">'
      + '<div style="display:flex;justify-content:space-between">'
      + '<b>' + t.name + '</b> <span style="color:var(--muted)">' + (t.class||'') + ' Lv.' + (t.base_level||1) + '</span></div>'
      + '<div style="font-size:12px;margin-top:4px">성격: ' + (p.traits||[]).join(', ') + '</div>'
      + '<div style="font-size:12px;color:var(--muted)">말투: ' + (p.speech_style||'') + '</div>'
      + '<div style="font-size:11px;color:var(--muted);margin-top:4px">ID: ' + t.id + ' | 공유: ' + (t.is_shared?'O':'X') + '</div></div>';
  });
  document.getElementById('gmTemplateList').innerHTML = html || '<div style="color:var(--muted)">NPC 없음</div>';
}

async function gmAutoGenNPC() {
  var gkey = document.getElementById('gmTplGameSelect').value;
  if (!gkey) return alert('게임사를 선택하세요');
  var cls = prompt('클래스를 입력하세요 (기사, 마법사, 도적 등):');
  if (!cls) return;
  var el = document.getElementById('gmTemplateList');
  el.innerHTML = '<div style="color:var(--muted)">AI 생성 중...</div>';
  var r = await fetch(api('/api/game/npc/template/generate'), {method:'POST', headers:{'Content-Type':'application/json','X-Game-Key':gkey}, body:JSON.stringify({class:cls,genre:'fantasy'})});
  var d = await r.json();
  if (d.generated) {
    if (confirm('생성된 NPC:\n' + d.generated.name + ' (' + cls + ')\n\n등록할까요?')) {
      d.generated.class = cls;
      await fetch(api('/api/game/npc/template'), {method:'POST', headers:{'Content-Type':'application/json','X-Game-Key':gkey}, body:JSON.stringify(d.generated)});
      gmLoadTemplates();
      gmLoad();
    }
  } else {
    el.innerHTML = '<div style="color:#e74c3c">생성 실패: ' + (d.error||'') + '</div>';
  }
}

async function gmAutoGenHeroine() {
  var gkey = document.getElementById('gmHeroineGameSelect').value;
  if (!gkey) return alert('게임사를 선택하세요');
  var archetype = prompt('히로인 유형 (츤데레, 쿨뷰티, 소꿉친구, 후배, 얀데레 등):');
  if (!archetype) return;
  var el = document.getElementById('gmHeroineList');
  el.innerHTML = '<div style="color:var(--muted)">AI 생성 중...</div>';
  var r = await fetch(api('/api/game/heroine/generate'), {method:'POST', headers:{'Content-Type':'application/json','X-Game-Key':gkey}, body:JSON.stringify({archetype:archetype})});
  var d = await r.json();
  if (d.generated) {
    if (confirm('생성된 히로인:\n' + d.generated.name + ' (' + archetype + ')\n성격: ' + (d.generated.personality||'').substring(0,50) + '\n\n등록할까요?')) {
      await fetch(api('/api/game/heroine/create'), {method:'POST', headers:{'Content-Type':'application/json','X-Game-Key':gkey}, body:JSON.stringify(d.generated)});
      alert('등록 완료!');
      gmLoadHeroines();
      gmLoad();
    }
  } else {
    el.innerHTML = '<div style="color:#e74c3c">생성 실패: ' + JSON.stringify(d) + '</div>';
  }
}

async function gmLoadLogs() {
  var pid = document.getElementById('gmLogPlayer').value.trim();
  if (!pid) return alert('player_id 입력');
  var gkeys = Object.values(_gmGameKeys);
  if (!gkeys.length) return alert('게임사가 없습니다');
  var html = '';
  for (var i = 0; i < gkeys.length; i++) {
    try {
      var r = await fetch(api('/api/game/dialogue/history?player_id=' + pid + '&limit=10'), {headers:{'X-Game-Key':gkeys[i]}});
      var d = await r.json();
      (d.dialogues||[]).forEach(function(dlg) {
        html += '<div style="background:var(--bg);padding:10px;border-radius:6px;border:1px solid var(--border);margin-bottom:6px">'
          + '<div style="font-size:11px;color:var(--muted)">' + (dlg.created_at||'') + '</div>';
        (dlg.turns||[]).forEach(function(t) {
          html += '<div style="margin:4px 0"><b>' + (t.speaker||t.name||'?') + ':</b> ' + (t.text||'').substring(0,100) + '</div>';
        });
        html += '</div>';
      });
    } catch(e) {}
  }
  document.getElementById('gmLogList').innerHTML = html || '<div style="color:var(--muted)">로그 없음</div>';
}

function gmTestTypeChange() {
  document.getElementById('gmTestChat').innerHTML = '<div class="hr-msg hr-msg-system">NPC/히로인을 선택하고 대화를 시작하세요.</div>';
  gmLoadTestNPCList();
}

async function gmLoadTestNPCList() {
  var type = document.getElementById('gmTestType').value;
  var sel = document.getElementById('gmTestNPC');
  sel.innerHTML = '<option value="">로딩중...</option>';
  if (!_gmAllKeys || !_gmAllKeys.length) { sel.innerHTML = '<option value="">게임사 없음</option>'; return; }
  var gkey = _gmAllKeys[0];
  var options = '<option value="">선택하세요</option>';
  try {
    if (type === 'heroine') {
      var r = await fetch(api('/api/game/heroine/list'), {headers:{'X-Game-Key':gkey}});
      var d = await r.json();
      (d.heroines||[]).forEach(function(h) { options += '<option value="' + h.id + '">💕 ' + h.name + ' (' + (h.personality||'').substring(0,15) + ')</option>'; });
    } else if (type === 'shared') {
      var r = await fetch(api('/api/game/shared/npcs'), {headers:{'X-Game-Key':gkey}});
      var d = await r.json();
      (d.shared_npcs||[]).forEach(function(n) { options += '<option value="' + n.id + '">🌐 ' + n.name + ' (' + (n.mood||'평온') + ')</option>'; });
    } else {
      var r = await fetch(api('/api/game/npc/templates'), {headers:{'X-Game-Key':gkey}});
      var d = await r.json();
      (d.templates||[]).forEach(function(t) { options += '<option value="' + t.id + '">⚔️ ' + t.name + ' (' + (t.class||'') + ')</option>'; });
    }
  } catch(e) {}
  sel.innerHTML = options;
}

async function gmTestSend() {
  var type = document.getElementById('gmTestType').value;
  var pid = document.getElementById('gmTestPlayer').value.trim();
  var npcId = document.getElementById('gmTestNPC').value.trim();
  var msg = document.getElementById('gmTestMsg').value.trim();
  var pname = document.getElementById('gmTestPlayerName').value.trim() || '테스터';
  if (!npcId || !msg) return;

  var gkeys = Object.values(_gmGameKeys);
  if (!gkeys.length) return alert('게임사를 먼저 등록하세요');
  var gkey = gkeys[0];

  // 사용자 메시지 표시
  hrChatAddMsgTo('gmTestChat', 'user', msg);
  document.getElementById('gmTestMsg').value = '';
  var loading = hrChatAddMsgTo('gmTestChat', 'loading', '⏳ 응답 생성 중...');

  var endpoint, body;
  if (type === 'heroine') {
    endpoint = '/api/game/heroine/talk';
    body = {player_id:pid, heroine_id:npcId, message:msg, player_name:pname};
  } else if (type === 'shared') {
    endpoint = '/api/game/shared/interact';
    body = {player_id:pid, npc_id:npcId, message:msg, action_type:'talk', player_name:pname};
  } else {
    endpoint = '/api/game/dialogue/player';
    body = {player_id:pid, npc_id:npcId, message:msg, player_name:pname};
  }

  try {
    var r = await fetch(api(endpoint), {method:'POST', headers:{'Content-Type':'application/json','X-Game-Key':gkey}, body:JSON.stringify(body)});
    var d = await r.json();
    loading.remove();
    var name = d.npc || d.heroine || 'NPC';
    var resp = d.response || '';
    var extra = '';
    if (d.affinity) extra += ' | 호감: ' + (d.affinity.value||d.affinity.after||'?') + '(' + (d.affinity.stage||'') + ')';
    if (d.exp_earned) extra += ' | EXP+' + d.exp_earned;
    if (d.mood) extra += ' | 기분: ' + (d.mood.label||'');
    if (d.reputation) extra += ' | 평판: ' + (d.reputation.after||'') + '(' + (d.reputation.title||'') + ')';
    hrChatAddMsgTo('gmTestChat', 'ai', '<b>' + name + '</b>: ' + resp + (extra ? '<div style="font-size:11px;color:var(--muted);margin-top:6px">' + extra + '</div>' : ''));
    if (d.level_up || d.stage_change) {
      hrChatAddMsgTo('gmTestChat', 'system', '🎉 ' + JSON.stringify(d.level_up || d.stage_change));
    }
  } catch(e) {
    loading.remove();
    hrChatAddMsgTo('gmTestChat', 'ai', '❌ 오류: ' + e.message);
  }
}

function hrChatAddMsgTo(containerId, type, content) {
  var box = document.getElementById(containerId);
  var div = document.createElement('div');
  div.className = 'hr-msg hr-msg-' + type;
  div.innerHTML = content;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

// ── iMac 연계 패널 (imac-link-console Phase A, v2.82.0) ──────────────
async function refreshImacLink() {
  const card = document.getElementById('imacLinkCard');
  if (!card) return;  // 카드 없는 페이지에선 무동작
  try {
    const r = await fetch(BASE + '/api/imac-link/status');
    const d = await r.json();
    const dot = (el, ok) => {
      el.style.color = ok === true ? 'var(--green)' : ok === false ? 'var(--red)' : 'var(--muted)';
    };
    const t = d.tunnels || {};
    const e86 = document.getElementById('ilT8586'), e87 = document.getElementById('ilT8587'), e90 = document.getElementById('ilT11435');
    dot(e86, t['8586_ollama'] && t['8586_ollama'].ok);
    dot(e87, t['8587_worker'] && t['8587_worker'].ok);
    dot(e90, t['11435_reverse']);
    e86.textContent = '8586 ●' + (t['8586_ollama'] && t['8586_ollama'].latency_ms != null ? ` ${t['8586_ollama'].latency_ms}ms` : '');
    e87.textContent = '8587 ●' + (t['8587_worker'] && t['8587_worker'].latency_ms != null ? ` ${t['8587_worker'].latency_ms}ms` : '');

    const w = d.worker || {};
    document.getElementById('ilWorker').textContent = w.version
      ? `v${w.version} · mem ${(w.memory && w.memory.pressure) || '?'} (${(w.memory && w.memory.free_gb) ?? '?'}GB) · ${w.thermal || '?'} · [${(w.capabilities || []).join(', ')}]${(w.locks && w.locks.heavy_task) ? ' · 작업중:' + w.locks.heavy_task : ''}`
      : '연결 안 됨';

    const dl = d.delegation || {};
    document.getElementById('ilDelegation').textContent =
      `${dl.enabled ? '✅ 활성' : '⬜ 비활성'} · delegate: [${(dl.delegate || []).join(', ') || '-'}] · token ${dl.token_set ? '설정됨' : '미설정'}`;

    const st = (dl.stats) || {};
    // local = 노드 상태가 차기 전(워커 재생성 직후) 로컬로 간 건수 — 설계상 비차단 폴백.
    // 이 값이 없으면 "위임 0"이 진짜 0인지 폴백 창인지 구분되지 않는다.
    document.getElementById('ilStats').textContent =
      `위임 ${st.delegated ?? 0} · 폴백 ${st.fallback ?? 0} · 로컬 ${st.local ?? 0}` +
      (st.recent && st.recent[0]
        ? ` · 최근: ${st.recent[0].kind}(${st.recent[0].capability}${st.recent[0].ms != null ? ' ' + st.recent[0].ms + 'ms' : ''})`
        : '');

    const of = d.offload || {};
    document.getElementById('ilOffload').textContent =
      `대상 [${(of.lightweight_models || []).join(', ') || '-'}] · iMac 보유 ${(of.imac_models || []).length}종 · 즉시가용 [${(of.models_ready || []).join(', ') || '-'}]`;
  } catch (e) {
    ['ilT8586', 'ilT8587', 'ilT11435'].forEach(id => { const el = document.getElementById(id); if (el) el.style.color = 'var(--muted)'; });
    const w = document.getElementById('ilWorker'); if (w) w.textContent = '상태 조회 실패';
  }
}
if (document.getElementById('imacLinkCard')) {
  refreshImacLink();
  setInterval(refreshImacLink, 30000);
}

// ── 오케스트레이터 패널 (node-orchestrator Phase 1, v2.84.0) ──────────────
async function refreshOrchestrator() {
  const card = document.getElementById('orchestratorCard');
  if (!card) return;
  try {
    const r = await fetch(BASE + '/api/orchestrator/nodes');
    const d = await r.json();
    const col = (ok) => ok === true ? 'var(--green)' : ok === false ? 'var(--red)' : 'var(--muted)';

    // 노드: 도달/breaker/inflight/지연
    const nodes = d.nodes || {};
    const nParts = Object.keys(nodes).map(id => {
      const n = nodes[id];
      const lats = n.latency_ewma_ms && Object.keys(n.latency_ewma_ms).length
        ? ' ' + Math.round(Object.values(n.latency_ewma_ms)[0]) + 'ms' : '';
      const br = (n.breaker && n.breaker !== 'closed') ? ` [${n.breaker}]` : '';
      return `<span style="color:${col(n.reachable)}">${id} ●</span> ${n.inflight || 0}건${lats}${br}`;
    });
    document.getElementById('orNodes').innerHTML = nParts.join(' &nbsp;·&nbsp; ') || '--';

    // 능력: policy
    const caps = d.capabilities || {};
    document.getElementById('orCaps').textContent =
      Object.keys(caps).map(k => `${k}(${caps[k].policy})`).join(' · ') || '-';

    // 라우팅 실적 집계 (cap|node|decision → cap별 합산)
    const counts = (d.metrics && d.metrics.counts) || {};
    const agg = {};
    Object.keys(counts).forEach(key => {
      const p = key.split('|'); const cap = p[0], dec = p[2];
      agg[cap] = agg[cap] || { delegated: 0, local: 0, fallback: 0, error: 0 };
      agg[cap][dec] = (agg[cap][dec] || 0) + counts[key];
    });
    document.getElementById('orRouting').textContent =
      Object.keys(agg).map(c => {
        const a = agg[c];
        return `${c}: 위임 ${a.delegated} · 로컬 ${a.local} · 폴백 ${a.fallback} · 오류 ${a.error}`;
      }).join('  /  ') || '아직 없음';

    // 최근 결정
    const rec = d.metrics && d.metrics.recent && d.metrics.recent[0];
    document.getElementById('orRecent').textContent = rec
      ? `${rec.cap}→${rec.node} ${rec.decision} ${rec.ms}ms`
      : (d.darwin_guard ? 'Darwin(위임 개시 안 함)' : '-');
  } catch (e) {
    const el = document.getElementById('orNodes'); if (el) el.textContent = '상태 조회 실패';
  }
}
if (document.getElementById('orchestratorCard')) {
  refreshOrchestrator();
  setInterval(refreshOrchestrator, 30000);
}

// ── 의료법 RAG 코퍼스 카드 (medical-law-rag P1) ──────────────────────────
async function refreshMedlawCorpus() {
  const card = document.getElementById('medlawCorpusCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const r = await fetch(BASE + '/api/medical-law/corpus-status');
    const d = await r.json();
    if (d.available === false) { set('mlTotal', '미가용'); return; }
    const n = (x) => (x || 0).toLocaleString();
    set('mlTotal', `${n(d.total_chunks)} 청크 · ${n(d.distinct_sources)}개 문서`);
    const o = d.by_origin || {};
    set('mlOrigin', Object.keys(o).map(k => `${k} ${n(o[k])}`).join(' · ') || '-');
    set('mlLaws', (d.law_documents || []).join(', ') || '-');
    const wm = d.khidi_watermark || {};
    set('mlWatermark', wm.last_no1 ? `최종 게시번호 ${wm.last_no1}` : '미수집');
  } catch (e) {
    set('mlTotal', '조회 실패');
  }
}
if (document.getElementById('medlawCorpusCard')) {
  refreshMedlawCorpus();
  setInterval(refreshMedlawCorpus, 300000); // 5분 (코퍼스 변동 느림 + 전체 metadata 조회 비용)
}

// ── 약물 데이터 스택 카드 (약가·대체조제·크로스워크·DUR) ──────────────────
async function refreshDrugData() {
  const card = document.getElementById('drugDataCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const n = (x) => (x || 0).toLocaleString();
  try {
    const d = await fetch(BASE + '/api/notice/drug-data/status').then(r => r.json());
    const f = d.fee_codes || {}, s = d.drug_substitution || {}, m = d.drug_master || {}, u = d.dur || {};
    const cats = f.by_category || {};
    set('ddFee', `${n(f.total)} (${Object.keys(cats).map(k => `${k} ${n(cats[k])}`).join(' · ') || '-'})`);
    set('ddSub', `${n(s.total)} · 장려금대상 ${n(s.eligible)}`);
    set('ddMaster', `${n(m.total_codes)} 코드 · 제품 ${n(m.distinct_product_codes)}종`);
    set('ddDur', u.error ? '미가용' : `병용금기 룰 ${n(u.interactions)} · 품목 ${n(u.products)}`);
  } catch (e) {
    set('ddFee', '조회 실패');
  }
}
if (document.getElementById('drugDataCard')) {
  refreshDrugData();
  setInterval(refreshDrugData, 300000);
}

// ── 식약처 외부 연동(DUR·e약은요) 동기화 상태 카드 ─────────────────────────
async function refreshDurSync() {
  const card = document.getElementById('durSyncCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const d = await fetch(BASE + '/api/medical/dur/sync-status').then(r => r.json());
    set('dsConfigured', d.configured ? '✅ 키 설정됨' : '❌ 키 없음');
    set('dsLast', (d.last_sync_at || '--').replace('T', ' ').slice(0, 19) + (d.running ? ' (동기화중)' : ''));
    const srcs = d.sources || [];
    const ok = srcs.filter(s => s.status === 'ok').length;
    const bad = srcs.filter(s => s.status !== 'ok');
    set('dsSources', srcs.length
      ? `${ok}/${srcs.length} ok${bad.length ? ' · 주의: ' + bad.map(s => s.source).join(',') : ''}`
      : '미동기화');
  } catch (e) {
    set('dsConfigured', '조회 실패');
  }
}
if (document.getElementById('durSyncCard')) {
  refreshDurSync();
  setInterval(refreshDurSync, 300000);
}

// ── 수집 로그·보고서 카드 (HIRA 게시판·data.go.kr 수집 이력 관측) ────────────
async function refreshCollect() {
  const card = document.getElementById('collectCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const st = await fetch(BASE + '/api/notice/hira-drug/sync-status').then(r => r.json());
    const srcs = st.sources || [];
    const lbl = srcs.map(s => {
      const m = s.meta || {};
      const mark = m.status === 'ok' ? '✅' : m.status === 'skip' ? '✅' :
                   m.status === 'denied' ? '⏸' : m.status ? '⚠️' : '—';
      return `${s.source}${mark}`;
    }).join(' ');
    set('clSources', lbl || '--');
  } catch (e) { set('clSources', '조회 실패'); }
  try {
    const lg = await fetch(BASE + '/api/notice/collect/logs?status=error&since_days=7&limit=50')
      .then(r => r.json());
    set('clErrors', `${lg.count || 0}건`);
  } catch (e) { set('clErrors', '--'); }
  try {
    const r = await fetch(BASE + '/api/notice/collect/report');
    if (r.status === 404) { set('clReportTs', '없음(생성 대기)'); set('clSummary', '--'); return; }
    const rep = await r.json();
    set('clReportTs', (rep.ts || '--').replace('T', ' ').slice(0, 19));
    set('clSummary', rep.summary || '--');
  } catch (e) { set('clReportTs', '--'); }
}
async function generateCollectReport() {
  try {
    await fetch(BASE + '/api/notice/collect/report', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    refreshCollect();
  } catch (e) { /* 표시 갱신 실패만 — 다음 폴링서 복구 */ }
}
if (document.getElementById('collectCard')) {
  refreshCollect();
  setInterval(refreshCollect, 300000);
}

// ── SANA 이미지(무스왑 fast-lane) 상태 카드 ────────────────────────────────
async function refreshSana() {
  const card = document.getElementById('sanaCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const d = await fetch(BASE + '/api/image/sana/status').then(r => r.json());
    set('sanaEnabled', d.enabled ? '✅ 활성' : '⏸️ 비활성 (comfyui)');
    set('sanaReach', d.reachable ? '✅ 도달' : '❌ 미도달');
    const sc = d.sidecar || {};
    if (d.reachable) {
      const loaded = sc.loaded ? (sc.mode || 'loaded') : 'idle (언로드)';
      set('sanaMode', loaded + (sc.idle_s != null ? ` · idle ${Math.round(sc.idle_s)}s` : ''));
      const v = sc.vram;
      set('sanaVram', v ? `${v.free_mb}/${v.total_mb} MB` : '--');
    } else {
      set('sanaMode', '--');
      set('sanaVram', '--');
    }
  } catch (e) {
    set('sanaEnabled', '조회 실패');
  }
}
if (document.getElementById('sanaCard')) {
  refreshSana();
  setInterval(refreshSana, 300000);
}

// ── 사전 적재(상주) 모델 카드 ─────────────────────────────────────────────
// 상주 = 첫 토큰 지연 0 대신 VRAM 상시 점유. 파인튜닝처럼 VRAM이 필요한 작업과 맞바꾼다.
let _rsState = null;

async function refreshResidency(keepMsg) {
  const card = document.getElementById('residencyCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const d = await fetch(BASE + '/api/models/residency').then(r => r.json());
    _rsState = d;
    set('rsMode', d.resident ? '사전 적재' : '요청 시 탑재');
    set('rsPolicy', d.resident ? '✅ 사전 적재(상주) — 첫 토큰 지연 없음'
      : '⏸️ 요청 시 탑재 — 유휴 시 자동 반납(첫 요청 +2~4초)');
    set('rsModel', d.model + (d.explicit_model ? ' (명시 지정)' : ' (채팅 기본값 따름)'));
    const ld = d.loaded_models || [];
    set('rsLoaded', ld.length
      ? ld.map(m => `${m.name} ${m.vram_gb}GB${(m.expires_at || '').startsWith('2') && !(m.expires_at || '').startsWith('23') ? '' : ' · 무기한'}`).join(', ')
      : '없음 (VRAM 비어 있음)');
    const sel = document.getElementById('rsSelect');
    if (sel && (d.available_models || []).length) {
      sel.innerHTML = (d.available_models || [])
        .map(m => `<option value="${m.name}"${m.name === d.model ? ' selected' : ''}>${m.name} · ${m.size_gb}GB</option>`).join('');
    }
    const btn = document.getElementById('rsToggle');
    if (btn) btn.textContent = d.resident ? '요청 시 탑재로' : '사전 적재로';
    if (!keepMsg) set('rsMsg', '');
  } catch (e) {
    set('rsPolicy', '조회 실패');
  }
}

async function applyResidency(payload, busyText) {
  const msg = document.getElementById('rsMsg');
  if (msg) msg.textContent = busyText;
  try {
    const r = await fetch(BASE + '/api/models/residency', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    if (msg) msg.textContent = `적용됨: ${d.applied || '-'} · ${d.note}`;
  } catch (e) {
    if (msg) msg.textContent = '실패: ' + e.message;
  }
  await refreshResidency(true);
}

// ⚠️ 구조 주의(v2.125.58): 상주 모델 **조작 버튼**(#rsSelect·#rsToggle·#rsUnload·#rsMsg)은 설정 › 모델·엔진으로
//    옮겼지만, 리스너는 여전히 이 블록이 붙인다 — 조건은 대시보드 상태 카드(#residencyCard) 존재 여부다.
//    대시보드 카드를 지우거나 이 블록을 옮기면 Settings의 버튼이 **조용히 죽는다**(클릭해도 아무 일 없음).
//    refreshResidency()는 두 곳(대시보드 상태 줄 + Settings 선택 상자)을 함께 채운다.
if (document.getElementById('residencyCard')) {
  refreshResidency();
  setInterval(() => refreshResidency(true), 300000);
  document.getElementById('rsToggle').addEventListener('click', () => {
    const want = !(_rsState && _rsState.resident);
    const model = document.getElementById('rsSelect').value;
    applyResidency({ resident: want, model },
      want ? '사전 적재 중… (모델 적재까지 수 초)' : '반납 중…');
  });
  document.getElementById('rsUnload').addEventListener('click', () => {
    // 상주를 끈 상태에서도 실트래픽이 모델을 올려두므로, 파인튜닝 직전 즉시 비우는 버튼.
    const model = (_rsState && _rsState.model) || document.getElementById('rsSelect').value;
    if (!model) return;
    const msg = document.getElementById('rsMsg');
    if (msg) msg.textContent = '반납 요청 중…';
    fetch(BASE + '/api/models/unload', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    }).then(r => r.json()).then(d => {
      if (msg) msg.textContent = d.ok ? `${model} 반납 완료 (상주 정책은 그대로)` : ('실패: ' + (d.error || ''));
      refreshResidency(true);
    }).catch(e => { if (msg) msg.textContent = '실패: ' + e.message; });
  });
}

// ── Heavy Tier(하이브리드 대형 모델) 상태 카드 ────────────────────────────
async function refreshHeavy() {
  const card = document.getElementById('heavyCard');
  if (!card) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  try {
    const d = await fetch(BASE + '/api/heavy/status').then(r => r.json());
    set('hvEnabled', d.enabled ? '✅ 활성' : '⏸️ 비활성');
    set('hvModel', d.heavy_model + (d.num_gpu != null ? ` · num_gpu ${d.num_gpu}` : ' · 자동 오프로드'));
    set('hvRunning', d.running ? '🔵 실행중 (상주 축출)' : `대기 · 상주 ${d.resident_model || '-'}`);
    const big = (d.loaded || []).find(m => m.name === d.heavy_model);
    set('hvLoaded', big ? `${big.name} ${big.vram_gb}G VRAM · CPU ${big.cpu_offload_pct}%`
      : (d.loaded && d.loaded.length ? d.loaded.map(m => m.name).join(', ') : '미로드'));
  } catch (e) {
    set('hvEnabled', '조회 실패');
  }
}
if (document.getElementById('heavyCard')) {
  refreshHeavy();
  setInterval(refreshHeavy, 300000);
}
