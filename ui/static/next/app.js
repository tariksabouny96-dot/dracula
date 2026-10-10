'use strict';
/*
 * HOOD NEXT console — connected to the real Hood backend.
 *
 * Rules (see the HOOD NEXT core merge contract):
 *  - Every value comes from an authenticated API response; nothing is seeded or invented.
 *  - Unknown / missing data is shown as "unknown", never as zero or healthy.
 *  - Server text is only ever inserted with textContent (the h() helper); no HTML from data.
 *  - Safety controls are enforced by the server; the UI only asks.
 */
(() => {
  const PAGES = [
    ['Command', '⌂'], ['Missions', '◎'], ['Agents', '⬡'], ['Intelligence', '◌'], ['Integrations', '⊞'],
    ['Sentinel', '◇'], ['Memory', '▤'], ['Commerce', '◈'], ['Desktop', '▣'], ['Voice', '◉'], ['Settings', '⚙'],
  ];
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ DOM helper (no innerHTML with data)
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

  // ------------------------------------------------------------------ API layer
  const api = {
    csrf: null,
    async req(method, path, body) {
      const init = { method, credentials: 'same-origin', cache: 'no-store', headers: {} };
      if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      if (method !== 'GET' && this.csrf) init.headers['X-CSRF-Token'] = this.csrf;
      let res;
      try { res = await fetch(path, init); } catch (err) {
        return { ok: false, status: 0, data: null, error: 'Network error: ' + err.message };
      }
      let data = null;
      const type = res.headers.get('Content-Type') || '';
      if (type.includes('application/json')) { try { data = await res.json(); } catch (e) { data = null; } }
      if (res.status === 401 && !path.startsWith('/api/auth/')) session.expired();
      return { ok: res.ok, status: res.status, data, res,
        error: res.ok ? null : ((data && data.error) || ('Request failed (' + res.status + ')')) };
    },
    get(path) { return this.req('GET', path); },
    post(path, body) { return this.req('POST', path, body || {}); },
  };

  // ------------------------------------------------------------------ state badges (merge-contract taxonomy)
  const KIND = {
    good: ['verified_online', 'available', 'COMPLETED', 'PASS', 'HALTED', 'ATTACHED_UNVERIFIED_OK', 'active', 'online'],
    info: ['checking', 'QUEUED', 'RUNNING', 'VERIFYING', 'PLANNING', 'CREATED', 'running', 'configured'],
    warn: ['degraded', 'needs_pricing', 'awaiting_approval', 'AWAITING_PLAN_APPROVAL', 'BLOCKED', 'UNVERIFIED', 'PENDING', 'pending',
      'ATTACHED_UNVERIFIED', 'MODULE_ONLY'],
    bad: ['failed', 'FAILED', 'offline', 'unavailable', 'blocked', 'ERROR', 'REJECTED', 'engaged'],
    sim: ['simulated', 'SIMULATED', 'MIXED'],
  };
  function kindOf(state) {
    for (const [k, list] of Object.entries(KIND)) if (list.includes(state)) return k;
    return 'muted';
  }
  const badge = (state, label) => h('span', { class: 'state ' + kindOf(state) }, label || String(state || 'unknown').replace(/_/g, ' '));

  // ------------------------------------------------------------------ small UI utilities
  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove('show'), 4200);
  }
  let lastFocus = null;
  function modal(title, body) {
    lastFocus = document.activeElement;
    $('modalTitle').textContent = title;
    clear($('modalBody')).append(body);
    $('modalBackdrop').classList.remove('hidden');
    const first = $('modalBody').querySelector('button, input, textarea, select, a');
    (first || $('modalClose')).focus();
  }
  function closeModal() {
    $('modalBackdrop').classList.add('hidden');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function confirmDialog(title, lines, okLabel, onOk, extra) {
    const err = h('div', { class: 'error-box hidden', role: 'alert' });
    const ok = h('button', { class: 'btn primary', type: 'button' }, okLabel);
    ok.addEventListener('click', async () => {
      ok.disabled = true;
      const r = await onOk();
      ok.disabled = false;
      if (r && r.error) { err.textContent = r.error; err.classList.remove('hidden'); return; }
      closeModal();
    });
    modal(title, h('div', {}, lines.map((l) => h('p', {}, l)), extra || null, err,
      h('div', { class: 'form-actions' }, ok, h('button', { class: 'btn', type: 'button', onclick: closeModal }, 'Cancel'))));
  }
  const loading = (what) => h('div', { class: 'loading-box' }, 'Loading ' + what);
  const emptyBox = (text) => h('div', { class: 'empty-box' }, text);
  const errorBox = (text) => h('div', { class: 'error-box', role: 'alert' }, text);
  function unavailable(r, feature) {
    if (r.status === 404) return emptyBox(feature + ' is not built yet in this version of HOOD.');
    if (r.status === 503) return emptyBox(feature + ': ' + (r.error || 'not configured on this server.'));
    if (r.status === 403) return emptyBox(feature + ': your role does not have access.');
    return errorBox(feature + ': ' + r.error);
  }
  const fmtTime = (iso) => { if (!iso) return 'unknown'; const d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString(); };
  const money = (v) => (v === null || v === undefined) ? 'unknown' : '$' + Number(v).toFixed(4);

  // ------------------------------------------------------------------ session
  const session = {
    user: null, role: null,
    async start() {
      const r = await api.get('/api/auth/status');
      if (!r.ok) { this.showAuth('setup-unavailable', 'Cannot reach Hood: ' + r.error); return; }
      const s = r.data;
      if (!s.enabled) { this.showAuth('setup-unavailable', 'Identity service not configured on this server.'); return; }
      if (!s.initialized) { this.showAuth('setup'); return; }
      if (!s.authenticated) { this.showAuth('login'); return; }
      api.csrf = s.csrf_token;
      this.user = s.username; this.role = s.role;
      $('authOverlay').classList.add('hidden');
      $('userBtn').textContent = (s.username || '?').slice(0, 1).toUpperCase();
      $('userBtn').title = s.username + ' (' + s.role + ')';
      app.boot();
    },
    showAuth(mode, message) {
      const setup = mode === 'setup';
      $('authOverlay').classList.remove('hidden');
      $('authTitle').textContent = setup ? 'Create the Root Owner' : 'Sign in to HOOD';
      $('authHint').textContent = setup ? 'First run: this account owns Hood. Store the recovery key you receive offline.'
        : (message || 'Your session is checked on every request.');
      $('authSetupFields').classList.toggle('hidden', !setup);
      $('authSubmit').textContent = setup ? 'Create owner' : 'Sign in';
      $('authSubmit').disabled = mode === 'setup-unavailable';
      $('authOverlay').dataset.mode = mode;
      setTimeout(() => $('authUser').focus(), 30);
    },
    async submit(e) {
      e.preventDefault();
      const mode = $('authOverlay').dataset.mode;
      const err = $('authError');
      err.classList.add('hidden');
      const username = $('authUser').value.trim();
      const password = $('authPass').value;
      if (mode === 'setup') {
        const r = await api.post('/api/auth/init', { username, password, display_name: $('authDisplay').value || username });
        if (!r.ok) { err.textContent = r.error; err.classList.remove('hidden'); return; }
        modal('Recovery key (shown once)', h('div', {}, h('p', {}, 'Write this down and keep it offline. It resets the owner password.'),
          h('p', { class: 'mono' }, r.data.one_time_recovery_key)));
      }
      const r = await api.post('/api/auth/login', { username, password });
      if (!r.ok) { err.textContent = r.error; err.classList.remove('hidden'); return; }
      $('authPass').value = '';
      await this.start();
    },
    expired() {
      if (!$('authOverlay').classList.contains('hidden')) return;
      feed.stop();
      this.showAuth('login', 'Your session ended. Please sign in again.');
    },
    async logout() {
      await api.post('/api/auth/logout', {});
      location.reload();
    },
  };

  // ------------------------------------------------------------------ live event feed (SSE with cursor + fallback polling)
  const feed = {
    cursor: 0, events: [], source: null, pollTimer: null, retryTimer: null, listeners: new Set(), state: 'connecting',
    setState(state, text) {
      this.state = state;
      $('connText').textContent = 'Live feed: ' + text;
      $('connText').className = 'conn ' + (state === 'live' ? 'live' : state === 'down' ? 'down' : '');
    },
    push(ev) {
      if (ev.seq <= this.cursor) return;
      this.cursor = ev.seq;
      this.events.unshift(ev);
      this.events.length = Math.min(this.events.length, 200);
      this.listeners.forEach((fn) => { try { fn(ev); } catch (e) { /* widget gone */ } });
    },
    start() {
      this.stop();
      if (!window.EventSource) { this.poll(); return; }
      const src = new EventSource('/api/console/events/stream?cursor=' + this.cursor);
      this.source = src;
      src.addEventListener('open', () => this.setState('live', 'connected (server-sent events)'));
      src.addEventListener('mission_event', (m) => { try { this.push(JSON.parse(m.data)); } catch (e) { /* ignore */ } });
      src.addEventListener('error', () => {
        // The server closes streams every ~55 s by design; EventSource reconnects with Last-Event-ID,
        // but our cursor is a query parameter, so restart explicitly.
        src.close();
        this.source = null;
        this.setState('reconnecting', 'reconnecting');
        this.poll(true);
        clearTimeout(this.retryTimer);
        this.retryTimer = setTimeout(() => this.start(), 4000);
      });
    },
    async poll(once) {
      const r = await api.get('/api/console/events?cursor=' + this.cursor);
      if (r.ok) {
        r.data.events.forEach((ev) => this.push(ev));
        if (!this.source) this.setState('polling', 'polling every 5 s');
      } else if (r.status !== 401) {
        this.setState('down', 'unavailable (' + r.error + ')');
      }
      if (!once && !this.source) this.pollTimer = setTimeout(() => this.poll(), 5000);
    },
    stop() {
      if (this.source) this.source.close();
      this.source = null;
      clearTimeout(this.pollTimer);
      clearTimeout(this.retryTimer);
    },
    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
  };

  // ------------------------------------------------------------------ app shell
  const app = {
    page: 'Command', overview: null, cleanups: [],
    boot() {
      if (this.booted) { this.render(this.page); return; }
      this.booted = true;
      feed.start();
      this.refreshOverview();
      setInterval(() => this.refreshOverview(), 15000);
      setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString(); }, 1000);
      feed.subscribe(() => this.debouncedOverview());
      const fromHash = decodeURIComponent((location.hash || '').slice(1));
      this.render(PAGES.some(([p]) => p === fromHash) ? fromHash : 'Command');
    },
    debouncedOverview() { clearTimeout(this.ovTimer); this.ovTimer = setTimeout(() => this.refreshOverview(), 800); },
    async refreshOverview() {
      const r = await api.get('/api/console/overview');
      if (!r.ok) return;
      this.overview = r.data;
      const gem = (r.data.providers || []).find((p) => p.id === 'gemini') || { state: 'unknown' };
      const label = { verified_online: '● LIVE · GEMINI', degraded: '● PROVIDER DEGRADED', not_configured: '● NO AI PROVIDER',
        needs_pricing: '● AI PRICING NOT SET', configured: '● AI READY', unknown: '● PROVIDER NOT VERIFIED' }[gem.state]
        || '● PROVIDER ' + String(gem.state).toUpperCase();
      $('sourcePill').textContent = label;
      $('sourcePill').className = 'source-pill ' + kindOf(gem.state);
      $('envTitle').textContent = { verified_online: 'LIVE PROVIDER', configured: 'AI READY', needs_pricing: 'PRICING NOT SET',
        not_configured: 'NO API KEY' }[gem.state] || 'PROVIDER ' + String(gem.state).replace(/_/g, ' ').toUpperCase();
      $('envDetail').textContent = gem.state === 'degraded' && gem.last_error ? 'Last call failed: ' + String(gem.last_error).slice(0, 140)
        : (gem.state === 'not_configured' || gem.state === 'needs_pricing') ? 'Set it in Settings › Model provider'
        : gem.last_success_at ? 'Last successful AI call ' + fmtTime(gem.last_success_at)
        : 'Not used yet since HOOD started';
      $('envSignal').className = 'signal ' + (gem.state === 'verified_online' ? 'green' : 'amber');
      const pending = r.data.approvals_pending;
      $('approvalCount').textContent = pending === null || pending === undefined ? '?' : String(pending);
      $('approvalCount').classList.toggle('hidden', !pending);
      const stop = r.data.emergency_stop;
      $('stopBtn').textContent = stop && stop.engaged ? '■ STOPPED' : '■ STOP';
      $('stopBtn').classList.toggle('danger', !!(stop && stop.engaged));
      $('footerStatus').textContent = 'X: ' + ((r.data.x && r.data.x.state) || 'unknown') +
        ' · Spend (ledger): ' + money(r.data.spend_usd) + ' · Every action is checked by the server.';
      if (this.page === 'Command') widgets.refreshAll();
    },
    render(p) {
      this.cleanups.forEach((fn) => { try { fn(); } catch (e) { /* ignore */ } });
      this.cleanups = [];
      this.page = PAGES.some(([n]) => n === p) ? p : 'Command';
      if (location.hash.slice(1) !== this.page) history.replaceState(null, '', '#' + this.page);
      $('crumb').textContent = this.page.toUpperCase() + ' / ' + (this.page === 'Command' ? 'OVERVIEW' : 'WORKSPACE');
      const nav = clear($('nav'));
      for (const [name, icon] of PAGES) {
        nav.append(h('button', { class: 'nav-link' + (name === this.page ? ' active' : ''), 'data-page': name, type: 'button',
          'aria-current': name === this.page ? 'page' : null }, h('span', { class: 'nav-icon', 'aria-hidden': 'true' }, icon), name));
      }
      const main = clear($('main'));
      main.append(VIEWS[this.page]());
      main.scrollTop = 0;
      $('rail').classList.remove('open');
    },
    onCleanup(fn) { this.cleanups.push(fn); },
  };

  function head(title, sub, ...buttons) {
    return h('div', { class: 'page-head' },
      h('div', {}, h('div', { class: 'eyebrow' }, 'HOOD / ' + app.page.toUpperCase()), h('h1', {}, title), h('p', {}, sub)),
      h('div', { class: 'head-actions' }, buttons));
  }
  function card(title, body, opts) {
    return h('section', { class: 'card ' + ((opts && opts.cls) || '') },
      h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, title), (opts && opts.right) || null),
      h('div', { class: 'card-body' }, body));
  }
  // Load data into a container with loading / error / empty handling.
  async function fill(container, loader, render) {
    clear(container).append(loading(''));
    try {
      const out = await loader();
      clear(container).append(out instanceof Node ? out : render(out));
    } catch (err) {
      clear(container).append(errorBox(err.message || String(err)));
    }
  }

  // ------------------------------------------------------------------ avatar (static, data-free SVG)
  const AVATAR_SVG = '<svg viewBox="0 0 240 340" xmlns="http://www.w3.org/2000/svg" aria-label="Anonymous holographic HOOD avatar" role="img"><defs><linearGradient id="hair" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#55e9fe" stop-opacity=".8"/><stop offset=".55" stop-color="#588fff"/><stop offset="1" stop-color="#9963f0"/></linearGradient><linearGradient id="mask" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#96ecff" stop-opacity=".36"/><stop offset="1" stop-color="#143c8c" stop-opacity=".45"/></linearGradient><linearGradient id="body" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#3db7f4" stop-opacity=".54"/><stop offset="1" stop-color="#6520a5" stop-opacity=".8"/></linearGradient></defs><path d="M67 112 Q53 63 98 31 Q139 5 178 51 Q203 94 177 156 Q211 212 220 291 L184 322 Q158 273 157 218 L80 213 Q83 271 54 328 L19 303 Q30 220 60 157Z" fill="none" stroke="url(#hair)" stroke-width="8" opacity=".6"/><path d="M72 92 Q59 34 117 25 Q172 20 180 86 L169 172 L148 210 L92 205 L70 164Z" fill="url(#mask)" stroke="#89caff" stroke-opacity=".65" stroke-width="1.6"/><path d="M103 174 L98 211 Q55 217 29 251 L16 340 H225 L211 252 Q186 218 145 209 L142 176Z" fill="url(#body)" stroke="#5a9be7" stroke-opacity=".65"/><path d="M99 209 Q123 245 149 209 M55 250 L121 324 L184 252 M39 296 L113 312 L210 292" fill="none" stroke="#90eeff" stroke-opacity=".45" stroke-width="1.5"/><path d="M66 87 Q52 46 91 17 M170 50 Q204 85 177 169 M54 154 Q29 207 40 270 M181 167 Q218 233 204 288" fill="none" stroke="url(#hair)" stroke-width="3" stroke-linecap="round" opacity=".85"/><path d="M23 335 Q119 310 215 335" stroke="#56ddff" opacity=".7" fill="none"/></svg>';
  const voiceState = { state: 'idle' };
  function avatar() {
    const fig = h('div', { class: 'avatar-figure' });
    fig.innerHTML = AVATAR_SVG; // constant markup, no data
    const bars = h('span', { class: 'audio-bars', 'aria-hidden': 'true' },
      Array.from({ length: 25 }, (_, i) => h('i', { style: { '--h': (3 + ((i * 7) % 16)) + 'px', '--d': '-' + ((i % 9) * 0.1) + 's' } })));
    const label = { idle: '● STANDBY · READY', listening: '● LISTENING', speaking: '● SPEAKING', thinking: '● PROCESSING' };
    return h('div', { class: 'avatar-stage', 'data-state': voiceState.state, id: 'avatarStage' },
      h('div', { class: 'avatar-orbit' }), h('div', { class: 'avatar-orbit two' }), h('div', { class: 'avatar-orbit three' }), fig,
      h('div', { class: 'avatar-status' }, h('span', { id: 'avatarLabel' }, label[voiceState.state] || label.thinking), ' ', bars));
  }
  function setAvatar(state) {
    voiceState.state = state;
    const stage = $('avatarStage');
    if (stage) stage.dataset.state = state;
    const lbl = $('avatarLabel');
    if (lbl) lbl.textContent = { idle: '● STANDBY · READY', listening: '● LISTENING', speaking: '● SPEAKING' }[state] || '● PROCESSING';
  }

  // ------------------------------------------------------------------ command input (chat or agent mission)
  // Minimal, safe formatting for model replies: line breaks, **bold**, `code`. DOM nodes only, never HTML.
  function richText(text) {
    const frag = document.createDocumentFragment();
    String(text).split('\n').forEach((line, i) => {
      if (i) frag.append(h('br'));
      line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach((part) => {
        if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(h('b', {}, part.slice(2, -2)));
        else if (/^`[^`]+`$/.test(part)) frag.append(h('code', {}, part.slice(1, -1)));
        else if (part) frag.append(document.createTextNode(part));
      });
    });
    return frag;
  }
  async function runCommand(text, logEl) {
    text = text.trim();
    if (!text) return;
    if (/^\/mission\s+/i.test(text)) {
      planMissionDialog(text.replace(/^\/mission\s+/i, ''));
      return;
    }
    setAvatar('thinking');
    logEl.textContent = 'Sending to Hood…';
    const r = await api.post('/api/chat', { text });
    setAvatar('idle');
    if (!r.ok) { logEl.textContent = 'Hood could not answer: ' + r.error; return; }
    clear(logEl).append(h('b', {}, (r.data.sender || 'Hood') + ': '), richText(r.data.text));
    app.debouncedOverview();  // the reply was a live model call: refresh the provider badge
    if (r.data.suggested_mission) {
      // HOOD never starts work from chat: it offers a plan the owner approves.
      const objective = r.data.suggested_mission;
      logEl.append(h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button',
        onclick: () => planMissionDialog(objective) }, 'Plan this as a mission')));
    }
    if (voice.autoSpeak) voice.speak(String(r.data.text).replace(/[*`#_]/g, ''), r.data.speaker_id);
  }
  function planMissionDialog(objective) {
    const obj = h('textarea', { class: 'big', 'aria-label': 'Objective', maxlength: '8000' });
    obj.value = objective || '';
    const budget = h('input', { type: 'number', min: '0', max: '100', step: '0.01', value: '1.00', 'aria-label': 'Spend cap in USD' });
    confirmDialog('Plan an agent mission', [
      'Hood sends this objective to the configured AI provider to draft a task plan.',
      'Nothing runs until you approve that exact plan and its spending cap.'],
    'Plan mission', async () => {
      const r = await api.post('/api/agents/missions', { objective: obj.value, budget_usd: Number(budget.value), confirm: true });
      if (!r.ok) return r;
      toast(r.data.state === 'AWAITING_PLAN_APPROVAL' ? 'Plan ready for your review.' : 'Planning stopped: ' + (r.data.error || r.data.state));
      missionsPage.selected = r.data.mission_id;
      app.render('Missions');
      return r;
    }, h('div', { class: 'form' }, h('label', {}, 'Objective', obj), h('label', {}, 'Spend cap (USD)', budget)));
  }

  // ------------------------------------------------------------------ Command page widgets
  const DEFAULT_LAYOUT = [
    { id: 'hero', span: 8 }, { id: 'agents', span: 4 }, { id: 'missions', span: 5 }, { id: 'graph', span: 7 },
    { id: 'safety', span: 4 }, { id: 'cost', span: 4 }, { id: 'engines', span: 4 }, { id: 'feed', span: 6 },
  ];
  const widgets = {
    layout: null, refreshers: {},
    async loadLayout() {
      if (this.layout) return this.layout;
      const r = await api.get('/api/console/layout');
      const saved = r.ok && r.data.layout && Array.isArray(r.data.layout.widgets) ? r.data.layout.widgets : null;
      const known = new Set(DEFAULT_LAYOUT.map((w) => w.id));
      const list = (saved || []).filter((w) => known.has(w.id));
      for (const d of DEFAULT_LAYOUT) if (!list.some((w) => w.id === d.id)) list.push({ ...d });
      this.layout = list;
      return list;
    },
    save() {
      clearTimeout(this.saveTimer);
      this.saveTimer = setTimeout(async () => {
        const r = await api.post('/api/console/layout', { layout: { widgets: this.layout } });
        if (!r.ok) toast('Layout not saved: ' + r.error);
      }, 600);
    },
    refreshAll() { Object.values(this.refreshers).forEach((fn) => fn()); },
  };
  const WIDGETS = {
    hero: { title: 'HOOD / COMMAND', build() {
      const log = h('div', { class: 'command-log', id: 'commandLog' }, 'Ready. Ask anything, or start a line with /mission to plan agent work.');
      const input = h('input', { name: 'prompt', id: 'commandInput', placeholder: 'Ask HOOD, or type /mission followed by an objective…', required: true, autocomplete: 'off', 'aria-label': 'Message Hood' });
      const form = h('form', { class: 'inputbar', id: 'commandForm' },
        h('button', { type: 'button', class: 'icon-btn', id: 'micBtn', 'aria-label': 'Voice input', onclick: () => app.render('Voice') }, '🎙'),
        input, h('button', { type: 'submit', class: 'btn primary' }, 'Send →'));
      form.addEventListener('submit', (e) => { e.preventDefault(); const t = input.value; input.value = ''; runCommand(t, log); });
      const quick = (label, fn) => h('button', { type: 'button', onclick: fn }, '↗ ' + label);
      return h('div', {}, h('div', { class: 'hero-inner' },
        h('div', { class: 'hero-copy' }, h('div', { class: 'eyebrow' }, 'YOUR OPERATING INTELLIGENCE'),
          h('h2', {}, 'Good to see you.', h('br'), h('em', {}, 'What’s next?')),
          h('p', {}, 'One conversation across agents, missions, tools and knowledge. Every real action is checked by the server.'),
          h('div', { class: 'hero-actions' }, quick('Review missions', () => app.render('Missions')),
            quick('Plan a software delivery', () => planMissionDialog('')),
            quick('Explore the system map', () => app.render('Intelligence')))),
        avatar()), form, log);
    } },
    agents: { title: 'Agent activity', async load(body) {
      const r = await api.get('/api/console/agents');
      if (!r.ok) return unavailable(r, 'Agent registry');
      return h('div', {}, r.data.roles.map((a) => h('div', { class: 'list-row' },
        h('div', { class: 'row-icon' }, a.name[0]),
        h('div', { class: 'row-main' }, h('b', {}, a.name, ' ', h('span', { class: 'tag muted' }, a.model ? a.model.primary : (a.id === 'verifier' ? 'deterministic' : 'no model'))),
          h('small', {}, a.duty)),
        h('div', { class: 'row-meta' }, a.observed.running_tasks ? badge('RUNNING', a.observed.running_tasks + ' running')
          : badge(a.observed.calls === null ? 'unknown' : 'idle', a.observed.calls === null ? 'not observed' : (a.observed.calls + ' calls'))))),
      h('button', { class: 'plain', type: 'button', onclick: () => app.render('Agents') }, 'Inspect all agents →'));
    } },
    missions: { title: 'Active missions', async load() {
      const ov = app.overview;
      if (!ov) return loading('missions');
      if (!ov.missions.available) return emptyBox('The agent engine is not configured on this server.');
      const by = ov.missions.by_state;
      const n = (k) => by[k] || 0;
      return h('div', {}, h('div', { class: 'metrics' },
        h('div', { class: 'metric' }, h('strong', {}, String(n('RUNNING') + n('QUEUED') + n('VERIFYING'))), h('small', {}, 'Running / queued')),
        h('div', { class: 'metric' }, h('strong', {}, String(n('AWAITING_PLAN_APPROVAL'))), h('small', {}, 'Awaiting your approval')),
        h('div', { class: 'metric' }, h('strong', {}, String(n('COMPLETED'))), h('small', {}, 'Verified complete'))),
      ov.missions.recent.length ? ov.missions.recent.slice(0, 4).map((m) => h('button', { class: 'list-row plain', type: 'button',
        style: { width: '100%', textAlign: 'left' }, onclick: () => { missionsPage.selected = m.id; app.render('Missions'); } },
        h('div', { class: 'row-main' }, h('b', {}, m.objective.slice(0, 90)), h('small', {}, fmtTime(m.updated))),
        h('div', { class: 'row-meta' }, badge(m.state), h('br'), m.provider_mode === 'SIMULATED' ? badge('simulated') : null)))
        : emptyBox('No missions yet. Type /mission followed by an objective.'),
      h('button', { class: 'plain', type: 'button', onclick: () => app.render('Missions') }, 'Open mission control →'));
    } },
    graph: { title: 'Living system network', async load() {
      const r = await api.get('/api/console/graph?limit=10');
      if (!r.ok) return unavailable(r, 'System map');
      const holder = h('div', { style: { height: '265px' } });
      graphView.draw(holder, r.data, 'root', true);
      return h('div', {}, h('div', { class: 'small-note' }, 'Built from live server data. Select a group to explore it.'), holder,
        h('button', { class: 'plain', type: 'button', onclick: () => app.render('Intelligence') }, 'Expand network workspace →'));
    } },
    safety: { title: 'Safety & approvals', async load() {
      const r = await api.get('/api/console/governance');
      if (!r.ok) return unavailable(r, 'Governance');
      const ov = app.overview || {};
      const stop = r.data.emergency_stop;
      return h('div', {}, h('div', { class: 'metrics' },
        h('div', { class: 'metric' }, h('strong', {}, ov.approvals_pending === null || ov.approvals_pending === undefined ? '?' : String(ov.approvals_pending)), h('small', {}, 'Approvals waiting')),
        h('div', { class: 'metric' }, h('strong', {}, stop ? (stop.engaged ? 'ON' : 'off') : '?'), h('small', {}, 'Emergency stop')),
        h('div', { class: 'metric' }, h('strong', {}, String(r.data.x.state || 'unknown')), h('small', {}, 'X executive'))),
      h('p', { class: 'small-note' }, r.data.x.activation),
      h('button', { class: 'plain', type: 'button', onclick: () => app.render('Sentinel') }, 'Open approvals & safety →'));
    } },
    cost: { title: 'Resources & cost', async load() {
      const ov = app.overview;
      if (!ov) return loading('cost');
      return h('div', {}, h('div', { class: 'metrics' },
        h('div', { class: 'metric' }, h('strong', {}, money(ov.spend_usd)), h('small', {}, 'Your mission spend (ledger)')),
        h('div', { class: 'metric' }, h('strong', {}, String(feed.events.length)), h('small', {}, 'Events this session'))),
      h('p', { class: 'small-note' }, ov.spend_note),
      ov.providers.map((p) => h('div', { class: 'list-row' }, h('div', { class: 'row-main' }, h('b', {}, p.id),
        h('small', {}, p.last_success_at ? 'Last success ' + fmtTime(p.last_success_at) : 'No successful call recorded')),
      badge(p.state))));
    } },
    engines: { title: 'AI engines & capabilities', async load() {
      const r = await api.get('/api/capabilities');
      if (!r.ok) return unavailable(r, 'Capabilities');
      const items = r.data.items.slice().sort((a, b) => (a.live.state === 'unknown') - (b.live.state === 'unknown')).slice(0, 7);
      return h('div', {}, items.map((c) => h('div', { class: 'list-row' }, h('div', { class: 'row-main' }, h('b', {}, c.name),
        h('small', {}, c.live.detail || c.description)), badge(c.live.state))),
      h('button', { class: 'plain', type: 'button', onclick: () => app.render('Integrations') }, 'All capabilities & integrations →'));
    } },
    feed: { title: 'Live execution feed', load() {
      const box = h('div', { class: 'feed', role: 'log', 'aria-live': 'polite' });
      const row = (e) => h('div', {}, h('time', {}, new Date(e.ts).toLocaleTimeString()), h('b', {}, e.kind), e.mission_id.slice(0, 12) + ' · ' + summarizeDetail(e.detail));
      const render = () => { clear(box); if (!feed.events.length) box.append(emptyBox('No events yet.')); feed.events.slice(0, 60).forEach((e) => box.append(row(e))); };
      render();
      app.onCleanup(feed.subscribe(render));
      return h('div', {}, box);
    } },
  };
  function summarizeDetail(detail) {
    try {
      const d = typeof detail === 'string' ? JSON.parse(detail) : detail;
      if (d && d.state) return d.state + (d.error ? ' — ' + String(d.error).slice(0, 120) : '');
      if (d && d.task_id) return 'task ' + d.task_id;
      return JSON.stringify(d).slice(0, 140);
    } catch (e) { return String(detail).slice(0, 140); }
  }
  function widgetEl(cfg) {
    const def = WIDGETS[cfg.id];
    const body = h('div', { class: 'card-body' });
    const tool = (label, title, fn) => h('button', { type: 'button', 'aria-label': title + ' ' + def.title, title, onclick: fn }, label);
    const el = h('section', { class: 'card widget', 'data-widget': cfg.id, style: { '--span': String(cfg.span || 4), '--min': '190px' } },
      h('header', { class: 'card-header drag-handle', draggable: 'true', title: 'Drag to reposition' },
        h('div', {}, h('div', { class: 'card-title' }, def.title), h('div', { class: 'card-sub' }, 'Drag to move · Alt+←/→ to move with keys')),
        h('div', { class: 'widget-tools' },
          tool('−', 'Narrow', () => { cfg.span = Math.max(3, (cfg.span || 4) - 1); el.style.setProperty('--span', cfg.span); widgets.save(); }),
          tool('＋', 'Widen', () => { cfg.span = Math.min(12, (cfg.span || 4) + 1); el.style.setProperty('--span', cfg.span); widgets.save(); }),
          tool('✕', 'Hide', () => { cfg.hidden = true; el.remove(); widgets.save(); toast(def.title + ' hidden. Use “Restore widgets” to bring it back.'); }))),
      body);
    let built = false;
    const refresh = async () => {
      if (def.build) { if (!built) { built = true; clear(body).append(def.build()); } return; }
      try { const out = await def.load(body); clear(body).append(out); } catch (e) { clear(body).append(errorBox(e.message)); }
    };
    widgets.refreshers[cfg.id] = refresh;
    body.append(loading(def.title.toLowerCase()));
    refresh();
    const handle = el.querySelector('.drag-handle');
    handle.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', cfg.id); el.classList.add('dragging'); });
    handle.addEventListener('dragend', () => el.classList.remove('dragging'));
    el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('drop-target'); });
    el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
    el.addEventListener('drop', (e) => {
      e.preventDefault(); el.classList.remove('drop-target');
      moveWidget(e.dataTransfer.getData('text/plain'), cfg.id);
    });
    el.addEventListener('keydown', (e) => {
      if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
      e.preventDefault();
      const list = widgets.layout.filter((w) => !w.hidden);
      const i = list.findIndex((w) => w.id === cfg.id);
      const j = e.key === 'ArrowLeft' ? i - 1 : i + 1;
      if (j >= 0 && j < list.length) moveWidget(cfg.id, list[j].id, e.key === 'ArrowRight');
    });
    return el;
  }
  function moveWidget(srcId, dstId, after) {
    if (!srcId || srcId === dstId) return;
    const list = widgets.layout;
    const from = list.findIndex((w) => w.id === srcId);
    if (from < 0) return;
    const [item] = list.splice(from, 1);
    const to = list.findIndex((w) => w.id === dstId) + (after ? 1 : 0);
    list.splice(to, 0, item);
    widgets.save();
    renderDashboard();
    const moved = document.querySelector('[data-widget="' + CSS.escape(srcId) + '"] .drag-handle button');
    if (moved) moved.focus();
  }
  let dashboardEl = null;
  function renderDashboard() {
    if (!dashboardEl) return;
    clear(dashboardEl);
    widgets.refreshers = {};
    widgets.layout.filter((w) => !w.hidden).forEach((w) => dashboardEl.append(widgetEl(w)));
  }
  function commandView() {
    dashboardEl = h('div', { id: 'dashboard', class: 'dashboard' }, loading('layout'));
    widgets.loadLayout().then(renderDashboard);
    app.onCleanup(() => { dashboardEl = null; widgets.refreshers = {}; });
    return h('div', {}, head('Command centre', 'Live missions, agents, safety and cost from this Hood server.',
      h('button', { class: 'btn', type: 'button', onclick: () => { widgets.layout.forEach((w) => { w.hidden = false; }); widgets.save(); renderDashboard(); } }, 'Restore widgets'),
      h('button', { class: 'btn', type: 'button', onclick: () => { widgets.layout = DEFAULT_LAYOUT.map((w) => ({ ...w })); widgets.save(); renderDashboard(); } }, 'Reset layout')),
    dashboardEl);
  }

  // ------------------------------------------------------------------ Missions
  const missionsPage = { selected: null };
  function missionsView() {
    const list = h('div', {});
    const detail = h('div', { class: 'mission-detail' });
    const loadList = () => fill(list, async () => {
      const r = await api.get('/api/agents/missions');
      if (!r.ok) return unavailable(r, 'Agent missions');
      if (!r.data.length) return emptyBox('No missions yet. Use “Plan mission”.');
      if (!missionsPage.selected) missionsPage.selected = r.data[0].id;
      return h('div', {}, r.data.map((m) => h('button', { type: 'button', class: 'list-row plain' + (m.id === missionsPage.selected ? ' active' : ''),
        style: { width: '100%', textAlign: 'left' }, 'aria-pressed': String(m.id === missionsPage.selected),
        onclick: () => { missionsPage.selected = m.id; loadList(); loadDetail(); } },
        h('div', { class: 'row-main' }, h('b', {}, m.objective.slice(0, 100)), h('small', { class: 'mono' }, m.id)),
        h('div', { class: 'row-meta' }, badge(m.state), ' ', m.provider_mode !== 'LIVE' && m.provider_mode !== 'NONE' ? badge(m.provider_mode) : null))));
    });
    const loadDetail = () => {
      if (!missionsPage.selected) { clear(detail).append(emptyBox('Select a mission.')); return; }
      fill(detail, async () => {
        const r = await api.get('/api/agents/missions/' + encodeURIComponent(missionsPage.selected));
        if (!r.ok) return unavailable(r, 'Mission');
        return missionDetail(r.data, () => { loadList().then(loadDetail); });
      });
    };
    loadList().then(loadDetail);
    app.onCleanup(feed.subscribe((ev) => { if (ev.mission_id === missionsPage.selected) { clearTimeout(missionsPage.t); missionsPage.t = setTimeout(() => { loadList().then(loadDetail); }, 500); } }));
    return h('div', {}, head('Mission control', 'Plan, approve, run and verify agent missions. Only independently checked work is marked complete.',
      h('button', { class: 'btn primary', type: 'button', onclick: () => planMissionDialog('') }, '＋ Plan mission')),
    h('div', { class: 'two-col' }, card('Missions', list), card('Mission detail', detail)));
  }
  function missionDetail(m, reload) {
    const act = (label, path, body, primary) => h('button', { class: 'btn small' + (primary ? ' primary' : ''), type: 'button',
      onclick: async (e) => { e.target.disabled = true; const r = await api.post('/api/agents/missions/' + m.mission_id + path, body); e.target.disabled = false;
        if (!r.ok) toast(r.error); else toast(label + ': done'); reload(); } }, label);
    const actions = [];
    if (m.state === 'AWAITING_PLAN_APPROVAL') actions.push(act('Approve plan & budget', '/approve', { confirm: true, plan_sha256: m.plan_sha256 }, true));
    if (['QUEUED', 'RUNNING', 'VERIFYING'].includes(m.state) && !m.background_run_active) actions.push(act('Run agents', '/run', { confirm: true }, true));
    if (m.state === 'BLOCKED' && m.approved_by) actions.push(act('Retry blocked work', '/retry', { confirm: true }));
    if (!['COMPLETED', 'FAILED', 'UNVERIFIED', 'CANCELLED'].includes(m.state)) actions.push(act('Cancel', '/cancel', {}));
    const v = m.last_verification;
    return h('div', {},
      h('dl', { class: 'kv' },
        h('dt', {}, 'State'), h('dd', {}, badge(m.state), m.background_run_active ? ' (running now)' : ''),
        h('dt', {}, 'Model output'), h('dd', {}, badge(m.provider_mode === 'NONE' ? 'unknown' : m.provider_mode, m.provider_mode === 'LIVE' ? 'live AI' : m.provider_mode === 'NONE' ? 'none yet' : m.provider_mode.toLowerCase())),
        h('dt', {}, 'Objective'), h('dd', {}, m.objective),
        h('dt', {}, 'Spend / cap'), h('dd', {}, money(m.spend) + ' / ' + money(m.budget_usd)),
        h('dt', {}, 'Repairs'), h('dd', {}, String(m.repairs)),
        m.error ? h('dt', {}, 'Reason') : null, m.error ? h('dd', {}, m.error) : null,
        m.plan_sha256 ? h('dt', {}, 'Plan hash') : null, m.plan_sha256 ? h('dd', { class: 'mono' }, m.plan_sha256) : null),
      h('div', { class: 'form-actions' }, actions),
      m.plan ? h('div', {}, h('h3', {}, 'PLAN'), h('p', {}, m.plan.summary),
        m.plan.interface_contract ? h('details', {}, h('summary', {}, 'Interface contract'), h('pre', { class: 'mono' }, m.plan.interface_contract)) : null) : null,
      h('h3', {}, 'TASKS'),
      m.tasks.length ? h('ul', { class: 'task-list' }, m.tasks.map((t) => h('li', {},
        h('span', {}, h('b', {}, t.role), ' · ', t.title, t.depends_on.length ? ' (after ' + t.depends_on.join(', ') + ')' : '',
          t.error ? h('div', { class: 'small-note' }, t.error.slice(0, 300)) : null),
        badge(t.state)))) : emptyBox('No tasks (planning did not produce a plan).'),
      h('h3', {}, 'INDEPENDENT CHECK'),
      v ? h('div', {}, badge(v.verdict), ' ', (v.checks || []).map((c) => badge(c.passed ? 'PASS' : 'FAILED', c.name + (c.tests_collected !== null && c.tests_collected !== undefined ? ' (' + c.tests_collected + ' tests)' : ''))),
        h('p', { class: 'small-note' }, 'Network isolated: ' + (v.network_isolated ? 'yes' : 'no') + ' · checked by ' + v.verifier))
        : emptyBox('Not verified yet.'),
      m.artifact ? h('div', {}, h('h3', {}, 'RESULT'),
        h('a', { class: 'btn small primary', href: '/api/agents/missions/' + encodeURIComponent(m.mission_id) + '/artifact', download: m.artifact.name },
          'Download verified result (' + m.artifact.bytes + ' bytes)'),
        h('p', { class: 'mono' }, 'SHA-256 ' + m.artifact.sha256)) : null);
  }

  // ------------------------------------------------------------------ Agents
  function agentsView() {
    const body = h('div', {});  // the loaded content is the grid; nesting two grids squeezed the cards
    fill(body, async () => {
      const r = await api.get('/api/console/agents');
      if (!r.ok) return unavailable(r, 'Agent registry');
      return h('div', { class: 'dashboard' }, r.data.roles.map((a) => h('section', { class: 'card', style: { gridColumn: 'span 4' } },
        h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, a.name), a.observed.running_tasks ? badge('RUNNING') : badge('idle')),
        h('div', { class: 'card-body' }, h('p', {}, a.duty), h('dl', { class: 'kv' },
          h('dt', {}, 'Model'), h('dd', {}, a.model ? a.model.primary + (a.model.fallbacks.length ? ' (fallback: ' + a.model.fallbacks.join(', ') + ')' : '') : (a.id === 'verifier' ? 'none — deterministic checks' : 'not configured')),
          h('dt', {}, 'May write'), h('dd', {}, a.writes.length ? a.writes.join(', ') : 'nothing'),
          h('dt', {}, 'Tools'), h('dd', {}, a.tools.length ? a.tools.join(', ') : 'none'),
          h('dt', {}, 'Your calls'), h('dd', {}, a.observed.calls === null ? 'not observed' : String(a.observed.calls)))))));
    });
    return h('div', {}, head('Agent constellation', 'Who does what, which model they use and what they are allowed to touch.'), body);
  }

  // ------------------------------------------------------------------ Intelligence graph (zoom, pan, drill-down)
  const graphView = {
    zoom: 1, pan: { x: 0, y: 0 }, focus: 'root',
    groups(data) {
      const by = { capability: [], agent: [], mission: [] };
      data.nodes.forEach((n) => { if (by[n.type]) by[n.type].push(n); });
      return by;
    },
    children(data, id) {
      const g = this.groups(data);
      if (id === 'root') return [
        { id: 'grp:capability', label: 'Capabilities', sub: g.capability.length + ' items', state: 'available' },
        { id: 'grp:agent', label: 'Agents', sub: g.agent.length + ' roles', state: 'available' },
        { id: 'grp:mission', label: 'Missions', sub: g.mission.length + ' yours', state: g.mission.length ? 'available' : 'unknown' }];
      if (id.startsWith('grp:')) return g[id.slice(4)].map((n) => ({ ...n, sub: String(n.state || '') }));
      const kids = data.edges.filter((e) => e.from === id && e.type !== 'assigned_role').map((e) => data.nodes.find((n) => n.id === e.to)).filter(Boolean);
      return kids.map((n) => ({ ...n, sub: String(n.state || '') }));
    },
    node(data, id) {
      if (id === 'root') return { id, label: 'HOOD CORE', sub: 'live server data', state: 'available' };
      if (id.startsWith('grp:')) return this.children(data, 'root').find((n) => n.id === id);
      return data.nodes.find((n) => n.id === id);
    },
    draw(holder, data, focus, mini, onSelect) {
      clear(holder);
      const center = this.node(data, focus) || this.node(data, 'root');
      const kids = this.children(data, center.id).slice(0, 40);
      const NS = 'http://www.w3.org/2000/svg';
      const s = (tag, attrs, ...kids2) => { const el = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v)); kids2.forEach((k) => el.append(k)); return el; };
      const svg = s('svg', { class: 'graph-svg', viewBox: '0 0 820 565', role: 'img', 'aria-label': 'System map around ' + center.label });
      const vp = s('g', { transform: mini ? '' : `translate(${this.pan.x} ${this.pan.y}) scale(${this.zoom})` });
      svg.append(vp);
      const cx = 410, cy = 282;
      const color = (st) => ({ good: '#43e3a3', info: '#6fd3ff', warn: '#f5c542', bad: '#ff5d7a', sim: '#c28bff' }[kindOf(st)] || '#8fa3bf');
      const nodeEl = (n, x, y, r) => {
        const g = s('g', { class: 'graph-node', transform: `translate(${x} ${y})`, tabindex: '0', role: 'button', 'aria-label': n.label + ' ' + (n.state || '') });
        g.append(s('circle', { class: 'graph-halo', r: String(r), fill: '#0d1c31', stroke: color(n.state), 'stroke-width': '1.8' }));
        const t = s('text', { class: 'graph-label', 'text-anchor': 'middle', y: '-2', 'font-size': mini ? '11' : '13' }); t.textContent = String(n.label).slice(0, 18);
        const sub = s('text', { class: 'graph-sub', 'text-anchor': 'middle', y: '14' }); sub.textContent = String(n.sub || '').slice(0, 22).replace(/_/g, ' ');
        g.append(t, sub);
        const go = () => (onSelect ? onSelect(n.id) : app.render('Intelligence'));
        g.addEventListener('click', go);
        g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        return g;
      };
      kids.forEach((n, i) => {
        const a = (i * 2 * Math.PI) / Math.max(1, kids.length) - Math.PI / 2;
        const rx = kids.length > 12 ? 330 : 260, ry = kids.length > 12 ? 230 : 190;
        const x = cx + Math.cos(a) * rx, y = cy + Math.sin(a) * ry;
        vp.append(s('path', { class: 'graph-edge', d: `M${cx} ${cy} Q${(cx + x) / 2 + 18} ${(cy + y) / 2 - 24} ${x} ${y}` }));
        vp.append(nodeEl(n, x, y, mini ? 28 : (kids.length > 12 ? 30 : 38)));
      });
      vp.append(nodeEl(center, cx, cy, mini ? 40 : 50));
      if (!kids.length && !mini) {
        const t = s('text', { 'text-anchor': 'middle', x: String(cx), y: String(cy + 90), class: 'graph-sub' }); t.textContent = 'No deeper relationships recorded.'; vp.append(t);
      }
      holder.append(svg);
      return svg;
    },
  };
  function intelligenceView() {
    const canvas = h('div', { style: { height: '100%' }, id: 'networkCanvas' });
    const inspector = h('div', { class: 'card-body' });
    let data = null;
    const select = (id) => {
      graphView.focus = id;
      const n = graphView.node(data, id);
      graphView.draw(canvas, data, id, false, select);
      clear(inspector).append(h('div', {}, h('div', { class: 'eyebrow' }, 'SELECTED NODE'), h('h2', {}, n ? n.label : id),
        n && n.state ? badge(n.state) : null,
        h('dl', { class: 'kv' }, h('dt', {}, 'Type'), h('dd', {}, (n && n.type) || 'group'), h('dt', {}, 'Data origin'), h('dd', {}, (n && n.provenance) || 'server'),
          n && n.detail ? h('dt', {}, 'Detail') : null, n && n.detail ? h('dd', {}, n.detail) : null,
          n && n.updated ? h('dt', {}, 'Updated') : null, n && n.updated ? h('dd', {}, fmtTime(n.updated)) : null),
        h('div', { class: 'form-actions' }, h('button', { class: 'btn small', type: 'button', onclick: () => select('root') }, '⌂ Back to core'),
          id.startsWith('mission:') ? h('button', { class: 'btn small primary', type: 'button', onclick: () => { missionsPage.selected = id.slice(8); app.render('Missions'); } }, 'Open mission') : null)));
    };
    const load = async () => {
      const r = await api.get('/api/console/graph?limit=50');
      if (!r.ok) { clear(canvas).append(unavailable(r, 'System map')); return; }
      data = r.data;
      select(graphView.focus || 'root');
    };
    const zoomBy = (f) => { graphView.zoom = Math.min(3, Math.max(0.4, graphView.zoom * f)); if (data) graphView.draw(canvas, data, graphView.focus, false, select); };
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.1 : 0.9); }, { passive: false });
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => { if (e.target.closest('.graph-node')) return; drag = { x: e.clientX, y: e.clientY, px: graphView.pan.x, py: graphView.pan.y }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', (e) => { if (!drag || !data) return; graphView.pan = { x: drag.px + (e.clientX - drag.x), y: drag.py + (e.clientY - drag.y) }; graphView.draw(canvas, data, graphView.focus, false, select); });
    canvas.addEventListener('pointerup', () => { drag = null; });
    load();
    app.onCleanup(feed.subscribe(() => { clearTimeout(graphView.t); graphView.t = setTimeout(load, 1500); }));
    return h('div', {}, head('Living intelligence network', 'The real relationships inside this Hood server: capabilities, agents, your missions and their tasks.',
      h('button', { class: 'btn', type: 'button', onclick: () => { graphView.zoom = 1; graphView.pan = { x: 0, y: 0 }; select('root'); } }, 'Reset view')),
    h('div', { class: 'network-shell' },
      h('div', { class: 'card graph-card' }, h('div', { class: 'graph-controls' },
        h('button', { type: 'button', title: 'Zoom in', 'aria-label': 'Zoom in', onclick: () => zoomBy(1.2) }, '＋'),
        h('button', { type: 'button', title: 'Zoom out', 'aria-label': 'Zoom out', onclick: () => zoomBy(1 / 1.2) }, '−'),
        h('button', { type: 'button', title: 'Back to core', 'aria-label': 'Back to core', onclick: () => select('root') }, '⌂')), canvas),
      h('section', { class: 'card graph-inspector' }, h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, 'Node inspector'), badge('available', 'live data')), inspector)));
  }

  // ------------------------------------------------------------------ Integrations / capabilities
  function integrationsView() {
    const body = h('div', {});
    const search = h('input', { placeholder: 'Search capabilities…', 'aria-label': 'Search capabilities' });
    let items = [];
    const render = () => {
      const q = search.value.toLowerCase();
      const shown = items.filter((c) => !q || (c.name + ' ' + c.category + ' ' + c.description).toLowerCase().includes(q));
      const cats = [...new Set(shown.map((c) => c.category))];
      clear(body).append(...cats.map((cat) => h('div', {}, h('h3', { class: 'eyebrow', style: { margin: '18px 0 8px' } }, cat),
        h('div', { class: 'dashboard' }, shown.filter((c) => c.category === cat).map((c) => h('section', { class: 'card', style: { gridColumn: 'span 4' } },
          h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, c.name), badge(c.live.state)),
          h('div', { class: 'card-body' }, h('p', { class: 'small-note' }, c.description), c.live.detail ? h('p', {}, c.live.detail) : null,
            h('p', { class: 'small-note' }, 'Code status: ' + c.status.replace(/_/g, ' ').toLowerCase()))))))));
      if (!shown.length) body.append(emptyBox('Nothing matches.'));
    };
    search.addEventListener('input', render);
    (async () => {
      clear(body).append(loading('capabilities'));
      const r = await api.get('/api/capabilities');
      if (!r.ok) { clear(body).append(unavailable(r, 'Capabilities')); return; }
      items = r.data.items;
      render();
    })();
    return h('div', {}, head('Capabilities & integrations', 'Every capability with its live state from the server. Credentials are never entered here: they are set by the operator in the environment or vault.'),
      h('div', { class: 'toolbar' }, search), body);
  }

  // ------------------------------------------------------------------ Sentinel / approvals / stop
  function sentinelView() {
    const gov = h('div', {});
    const appr = h('div', {});
    const sent = h('div', {});
    fill(gov, async () => {
      const r = await api.get('/api/console/governance');
      if (!r.ok) return unavailable(r, 'Governance');
      const stop = r.data.emergency_stop;
      return h('div', {}, h('dl', { class: 'kv' },
        h('dt', {}, 'Emergency stop'), h('dd', {}, stop ? badge(stop.engaged ? 'engaged' : 'available', stop.engaged ? 'engaged' : 'not engaged') : badge('unknown')),
        stop && stop.engaged ? h('dt', {}, 'Reason') : null, stop && stop.engaged ? h('dd', {}, stop.reason) : null,
        h('dt', {}, 'X executive'), h('dd', {}, badge(r.data.x.state)),
        h('dt', {}, 'X activation'), h('dd', {}, r.data.x.activation),
        h('dt', {}, 'Approvals'), h('dd', {}, r.data.policy.approvals),
        h('dt', {}, 'Simulated output'), h('dd', {}, r.data.policy.simulated_output)),
      stop && stop.engaged && session.role === 'ROOT_OWNER' ? h('button', { class: 'btn primary', type: 'button', onclick: () => confirmDialog('Release emergency stop',
        ['Only release after you have checked why it was engaged.'], 'Release stop', async () => {
          const res = await api.post('/api/emergency_stop/reset', { confirm: true });
          if (res.ok) { toast('Emergency stop released.'); app.refreshOverview(); app.render('Sentinel'); }
          return res;
        }) }, 'Release emergency stop') : null);
    });
    fill(appr, async () => {
      const r = await api.get('/api/approvals');
      if (r.status === 403) return emptyBox('Only the Root Owner sees the approval centre.');
      if (!r.ok) return unavailable(r, 'Approvals');
      if (!r.data.length) return emptyBox('No approvals are waiting.');
      return h('div', {}, r.data.map((a) => h('div', { class: 'list-row' },
        h('div', { class: 'row-main' }, h('b', {}, a.action_type + ' → ' + a.target), h('small', {}, a.reason), h('small', { class: 'mono' }, a.approval_id)),
        h('div', { class: 'row-meta' }, badge(a.risk_level), h('div', { class: 'form-actions' },
          h('button', { class: 'btn small primary', type: 'button', onclick: async () => { const res = await api.post('/api/approvals/resolve', { approval_id: a.approval_id, approved: true }); toast(res.ok ? 'Approved' : res.error); app.render('Sentinel'); } }, 'Approve'),
          h('button', { class: 'btn small', type: 'button', onclick: async () => { const res = await api.post('/api/approvals/resolve', { approval_id: a.approval_id, approved: false }); toast(res.ok ? 'Rejected' : res.error); app.render('Sentinel'); } }, 'Reject'))))));
    });
    fill(sent, async () => {
      const r = await api.get('/api/sentinel/summary');
      if (r.status === 403) return emptyBox('Only the Root Owner sees Sentinel findings.');
      if (!r.ok) return unavailable(r, 'Sentinel');
      const d = r.data;
      const drift = d.integrity ? d.integrity.drift_detected : null;
      return h('dl', { class: 'kv' }, h('dt', {}, 'Posture'), h('dd', {}, String(d.posture || 'unknown')),
        h('dt', {}, 'Last scan'), h('dd', {}, String(d.last_scan || 'not run')),
        h('dt', {}, 'Open findings'), h('dd', {}, String(d.open_findings_count ?? 'unknown')),
        h('dt', {}, 'Integrity'), h('dd', {}, drift === true ? badge('failed', 'drift detected') : drift === false ? badge('available', 'baseline matches') : badge('unknown', 'not inspected')));
    });
    return h('div', {}, head('Sentinel & safety', 'Emergency stop, approvals waiting for you, X state and security posture.',
      h('button', { class: 'btn primary', type: 'button', onclick: stopDialog }, '■ Emergency stop')),
    h('div', { class: 'two-col' }, card('Governance', gov), card('Approvals waiting', appr)), h('div', { style: { marginTop: '14px' } }, card('Security posture', sent)));
  }
  function stopDialog() {
    confirmDialog('Emergency stop', ['Blocks every tool and agent step, kills running sandbox processes and stands down X.',
      'It stays on (even after a restart) until the Root Owner releases it.'], 'Stop everything', async () => {
      const r = await api.post('/api/emergency_stop', {});
      if (!r.ok) return r;
      const incomplete = r.data.incomplete || [];
      toast(incomplete.length ? 'Stop engaged. Not confirmed halted: ' + incomplete.join(', ') : 'Stop engaged: all attached subsystems halted.');
      app.refreshOverview();
      return r;
    });
  }

  // ------------------------------------------------------------------ Memory
  function memoryView() {
    const body = h('div', {});
    fill(body, async () => {
      const r = await api.get('/api/memory/list');
      if (r.status === 403) return emptyBox('Your role does not have access to memory.');
      if (!r.ok) return unavailable(r, 'Memory');
      if (!r.data.memories || !r.data.memories.length) return emptyBox('No memories stored for you yet.');
      return h('div', { class: 'dashboard' }, r.data.memories.map((m) => h('section', { class: 'card', style: { gridColumn: 'span 4' } },
        h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, m.memory_type || m.type || 'memory'), badge(m.learning_status || 'unknown')),
        h('div', { class: 'card-body' }, h('p', {}, String(m.content || '').slice(0, 400)), h('p', { class: 'small-note' }, 'Project: ' + (m.project || 'unknown') + ' · ' + fmtTime(m.created_at))))));
    });
    return h('div', {}, head('Memory', 'What Hood remembers for you. Only your own records are shown.'), body);
  }

  // ------------------------------------------------------------------ Commerce (business module)
  function commerceView() {
    const body = h('div', {});
    fill(body, async () => {
      const [products, orders] = await Promise.all([api.get('/api/commerce/products'), api.get('/api/commerce/orders')]);
      if (!products.ok) return unavailable(products, 'Commerce');
      const rows = (list, fmt) => (list && list.length ? h('div', {}, list.map(fmt)) : emptyBox('Nothing yet.'));
      const p = Array.isArray(products.data) ? products.data : (products.data.products || []);
      const o = orders.ok ? (Array.isArray(orders.data) ? orders.data : (orders.data.orders || [])) : [];
      return h('div', { class: 'two-col' },
        card('Products', rows(p, (x) => h('div', { class: 'list-row' }, h('div', { class: 'row-main' }, h('b', {}, x.name || x.title || x.id), h('small', {}, (x.price_cents !== undefined ? '$' + (x.price_cents / 100).toFixed(2) : '') + ' ' + (x.currency || ''))), x.stock !== undefined ? badge('available', 'stock ' + x.stock) : null))),
        card('Orders', rows(o, (x) => h('div', { class: 'list-row' }, h('div', { class: 'row-main' }, h('b', {}, x.id), h('small', {}, fmtTime(x.created || x.created_at))), badge(x.state || x.status), x.simulated ? badge('simulated') : null))));
    });
    return h('div', {}, head('Commerce', 'Storefront, orders and payments. Sandbox payments are labelled simulated.'), body);
  }

  // ------------------------------------------------------------------ Desktop
  function desktopView() {
    const body = h('div', {});
    fill(body, async () => {
      const r = await api.get('/api/telemetry');
      if (!r.ok) return unavailable(r, 'Desktop telemetry');
      const d = r.data.desktop || {};
      return h('dl', { class: 'kv' }, h('dt', {}, 'Status'), h('dd', {}, String(d.badge || 'unknown')),
        h('dt', {}, 'Active window'), h('dd', {}, String(d.current_app || 'not inspected')),
        h('dt', {}, 'Control method'), h('dd', {}, String(d.control_method || 'unknown')),
        h('dt', {}, 'Note'), h('dd', {}, 'Desktop control works only on the owner’s Windows machine; consequential clicks and typing need an exact approval.'));
    });
    return h('div', {}, head('Desktop', 'Windows desktop observation and control status.'), body);
  }

  // ------------------------------------------------------------------ Voice
  const voice = {
    autoSpeak: false, recorder: null, chunks: [],
    async status() { return api.get('/api/voice/status'); },
    async speak(text, speaker) {
      const r = await fetch('/api/voice/speak', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': api.csrf || '' }, body: JSON.stringify({ text: String(text).slice(0, 2000), speaker: speaker === 'x' ? 'x' : 'hood' }) });
      if (!r.ok) {
        let why = 'HTTP ' + r.status;
        try { why = (await r.json()).error || why; } catch (e) { /* not JSON */ }
        toast('Hood voice unavailable: ' + why); return;
      }
      const url = URL.createObjectURL(await r.blob());
      const audio = new Audio(url);
      setAvatar('speaking');
      audio.addEventListener('ended', () => { setAvatar('idle'); URL.revokeObjectURL(url); });
      audio.play().catch(() => setAvatar('idle'));
    },
  };
  function voiceView() {
    const statusBox = h('div', {});
    const out = h('div', { class: 'command-log' }, 'Transcript appears here.');
    const meter = h('div', { class: 'voice-meter' }, h('i', {}));
    const recBtn = h('button', { class: 'btn primary', type: 'button' }, '● Record');
    const sendBtn = h('button', { class: 'btn', type: 'button', disabled: true }, 'Send transcript to Hood');
    const speakText = h('input', { value: 'Hello, this is Hood.', 'aria-label': 'Text to speak' });
    let transcript = '';
    const refresh = () => fill(statusBox, async () => {
      const r = await voice.status();
      if (!r.ok) return unavailable(r, 'Voice');
      const s = r.data;
      return h('div', {}, h('dl', { class: 'kv' }, h('dt', {}, 'State'), h('dd', {}, badge(s.state || 'unknown')),
        h('dt', {}, 'Provider'), h('dd', {}, String(s.provider || 'unknown')),
        h('dt', {}, 'Cloud audio consent'), h('dd', {}, s.consent ? 'given' : 'not given'),
        s.last_error ? h('dt', {}, 'Last error') : null, s.last_error ? h('dd', {}, String(s.last_error)) : null),
      !s.consent ? h('button', { class: 'btn small', type: 'button', onclick: () => confirmDialog('Allow cloud audio',
        ['Recordings you make here are sent to the configured speech provider (Google Gemini) to be transcribed.', 'Do not record private information.'],
        'I agree', async () => { const res = await api.post('/api/voice/consent', { confirm: true }); refresh(); return res; }) }, 'Give consent for cloud audio') : null);
    });
    recBtn.addEventListener('click', async () => {
      if (voice.recorder && voice.recorder.state === 'recording') { voice.recorder.stop(); return; }
      if (!navigator.mediaDevices || !window.MediaRecorder) { toast('This browser cannot record audio. Use text instead.'); return; }
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { toast('Microphone permission denied.'); return; }
      voice.chunks = [];
      const rec = new MediaRecorder(stream);
      voice.recorder = rec;
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const an = ctx.createAnalyser(); ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.frequencyBinCount);
      const tick = () => { if (rec.state !== 'recording') return; an.getByteFrequencyData(buf); meter.firstChild.style.width = Math.min(100, buf.reduce((a, b) => a + b, 0) / buf.length * 1.5) + '%'; requestAnimationFrame(tick); };
      rec.addEventListener('dataavailable', (e) => voice.chunks.push(e.data));
      rec.addEventListener('stop', async () => {
        stream.getTracks().forEach((t) => t.stop()); ctx.close(); recBtn.textContent = '● Record'; setAvatar('thinking'); meter.firstChild.style.width = '0';
        const blob = new Blob(voice.chunks, { type: rec.mimeType || 'audio/webm' });
        const b64 = await new Promise((resolve) => { const fr = new FileReader(); fr.onload = () => resolve(String(fr.result).split(',')[1]); fr.readAsDataURL(blob); });
        const r = await api.post('/api/voice/transcribe', { audio_b64: b64, mime_type: blob.type.split(';')[0] });
        setAvatar('idle');
        if (!r.ok) { out.textContent = 'Transcription failed: ' + r.error; return; }
        transcript = r.data.transcript || '';
        out.textContent = transcript ? 'You said: ' + transcript : 'No speech recognised.';
        sendBtn.disabled = !transcript;
        refresh();
      });
      rec.start(); setAvatar('listening'); recBtn.textContent = '■ Stop recording'; tick();  // distinct from the emergency STOP button
    });
    sendBtn.addEventListener('click', () => runCommand(transcript, out));
    const auto = h('input', { type: 'checkbox', id: 'autoSpeak' });
    auto.checked = voice.autoSpeak;
    auto.addEventListener('change', () => { voice.autoSpeak = auto.checked; });
    refresh();
    return h('div', {}, head('Voice', 'Talk to Hood. Audio is only sent after you give consent; text always works as a fallback.'),
      h('div', { class: 'two-col' },
        card('Speak to Hood', h('div', {}, avatar(), meter, h('div', { class: 'form-actions' }, recBtn, sendBtn), out)),
        card('Status & playback', h('div', {}, statusBox, h('div', { class: 'form' }, h('label', {}, 'Test Hood’s voice', speakText),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn small', type: 'button', onclick: () => voice.speak(speakText.value) }, '▶ Speak')),
          h('label', { class: 'switchline' }, auto, ' Read Hood’s chat replies aloud'))))));
  }

  // ------------------------------------------------------------------ Settings › Model provider (owner only)
  function modelProviderCard() {
    const box = h('div', {});
    const keyInput = h('input', { type: 'password', autocomplete: 'off', spellcheck: 'false',
      placeholder: 'Paste your Gemini API key', 'aria-label': 'Gemini API key' });
    const testOut = h('p', {});
    const done = async (r, okText) => {
      if (r.ok) { toast(okText); load(); app.refreshOverview(); }
      return r;
    };
    const load = () => fill(box, async () => {
      const r = await api.get('/api/settings/model');
      if (!r.ok) return unavailable(r, 'Model settings');
      const s = r.data, k = s.key, pr = s.pricing;
      const inputs = {};
      const customForm = h('div', { class: 'form hidden' },
        Object.entries(pr.models).map(([m, p]) => {
          const i = h('input', { type: 'number', min: '0', max: '10', step: '0.00001', value: p ? p.input_per_1k_usd : '', 'aria-label': m + ' input price' });
          const o = h('input', { type: 'number', min: '0', max: '10', step: '0.00001', value: p ? p.output_per_1k_usd : '', 'aria-label': m + ' output price' });
          inputs[m] = [i, o];
          return h('div', {}, h('b', {}, m), h('label', {}, 'Input, USD per 1,000 tokens', i), h('label', {}, 'Output, USD per 1,000 tokens', o));
        }),
        h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
          const prices = {};
          for (const [m, [i, o]] of Object.entries(inputs)) prices[m] = { input_per_1k_usd: i.value, output_per_1k_usd: o.value };
          const res = await api.post('/api/settings/model/pricing', { mode: 'custom', prices, confirm: true });
          if (!res.ok) toast('Prices not saved: ' + res.error);
          await done(res, 'Prices saved.');
        } }, 'Save prices')));
      return h('div', {},
        h('dl', { class: 'kv' },
          h('dt', {}, 'Status'), h('dd', {}, badge(s.state || 'unknown')),
          h('dt', {}, 'API key'), h('dd', {}, k.set ? 'Set — from ' + k.source + (k.hint ? ' (' + k.hint + ')' : '') : 'Not set'),
          h('dt', {}, 'Prices'), h('dd', {}, pr.source ? 'From ' + pr.source + (pr.missing.length ? ' — missing: ' + pr.missing.join(', ') : '')
            : 'Not set: every AI call is refused until prices are on file'),
          s.last_error ? h('dt', {}, 'Last error') : null, s.last_error ? h('dd', {}, String(s.last_error).slice(0, 200)) : null),
        h('div', { class: 'form' }, h('label', {}, k.set ? 'Replace API key' : 'Gemini API key', keyInput),
          h('div', { class: 'form-actions' },
            h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
              const v = keyInput.value.trim();
              if (!v) { toast('Paste a key first.'); return; }
              const res = await api.post('/api/settings/model/key', { api_key: v, confirm: true });
              keyInput.value = '';
              if (!res.ok) toast('Key not saved: ' + res.error);
              await done(res, 'API key saved. It is used immediately.');
            } }, 'Save key'),
            k.source === 'settings' ? h('button', { class: 'btn small', type: 'button', onclick: () => confirmDialog('Remove API key',
              ['HOOD will stop using the key saved here. A key in your .env file, if any, is used instead.'], 'Remove key',
              async () => done(await api.post('/api/settings/model/key/remove', { confirm: true }), 'API key removed.')) }, 'Remove key') : null),
          h('small', {}, 'Get a key at aistudio.google.com (“Get API key”). It is stored encrypted on this computer and never shown again.')),
        h('h4', {}, 'Prices'),
        h('p', {}, h('small', {}, 'HOOD never makes an AI call it cannot price. Choose one:')),
        h('div', { class: 'form-actions' },
          h('button', { class: 'btn small', type: 'button', onclick: () => confirmDialog('Free tier: $0 prices',
            ['Only choose this if your Google project has NO billing account. With billing enabled, Google charges you and HOOD would under-count spend.',
              'HOOD will record $0 for every call to: ' + Object.keys(pr.models).join(', ') + '.'], 'My key has no billing account',
            async () => done(await api.post('/api/settings/model/pricing', { mode: 'free', confirm: true }), 'Free-tier prices saved.')) }, 'Free tier (no billing)'),
          h('button', { class: 'btn small', type: 'button', onclick: () => customForm.classList.toggle('hidden') }, 'Enter my prices')),
        customForm,
        h('h4', {}, 'Check'),
        h('div', { class: 'form-actions' }, h('button', { class: 'btn small', type: 'button', onclick: async () => {
          testOut.textContent = 'Testing…';
          const res = await api.post('/api/settings/model/test', {});
          testOut.textContent = !res.ok ? 'Test failed: ' + res.error
            : res.data.ok ? '✓ Connected — ' + res.data.model + ' answered in ' + res.data.latency_ms + ' ms'
              : '✗ Not working: ' + res.data.error;
          load(); app.refreshOverview();
        } }, 'Test connection')), testOut);
    });
    load();
    return card('Model provider (Gemini)', box);
  }

  // ------------------------------------------------------------------ Settings › Voice (owner only)
  function voiceSettingsCard() {
    const box = h('div', {});
    const load = () => fill(box, async () => {
      const r = await api.get('/api/settings/voice');
      if (!r.ok) return unavailable(r, 'Voice settings');
      const v = r.data;
      const provider = h('select', { 'aria-label': 'Voice provider' },
        h('option', { value: 'gemini' }, 'Google Gemini voice'), h('option', { value: 'elevenlabs' }, 'ElevenLabs (my voice)'));
      provider.value = v.tts_provider;
      const key = h('input', { type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: 'Paste your ElevenLabs API key', 'aria-label': 'ElevenLabs API key' });
      const vid = h('input', { value: v.voice_id || '', placeholder: 'e.g. 21m00Tcm4TlvDq8ikWAM', 'aria-label': 'HOOD voice ID' });
      const xvid = h('input', { value: v.x_voice_id || '', placeholder: 'optional: a different voice for X', 'aria-label': 'X voice ID' });
      const model = h('input', { value: v.model_id || v.default_model, 'aria-label': 'ElevenLabs model' });
      const price = h('input', { type: 'number', min: '0', max: '10', step: '0.0001', value: v.price_per_1k_chars === null ? '' : v.price_per_1k_chars, 'aria-label': 'Price per 1,000 characters' });
      return h('div', {},
        h('dl', { class: 'kv' }, h('dt', {}, 'Speaking voice'), h('dd', {}, v.tts_provider === 'elevenlabs' ? 'ElevenLabs' : 'Google Gemini'),
          h('dt', {}, 'ElevenLabs key'), h('dd', {}, v.key.set ? 'Set (' + v.key.hint + ')' : 'Not set')),
        h('div', { class: 'form' },
          h('label', {}, v.key.set ? 'Replace ElevenLabs API key' : 'ElevenLabs API key', key),
          h('div', { class: 'form-actions' },
            h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
              if (!key.value.trim()) { toast('Paste a key first.'); return; }
              const res = await api.post('/api/settings/voice/key', { api_key: key.value.trim(), confirm: true });
              key.value = '';
              toast(res.ok ? 'ElevenLabs key saved; api.elevenlabs.io allowed in the firewall.' : 'Key not saved: ' + res.error);
              if (res.ok) load();
            } }, 'Save key'),
            v.key.set ? h('button', { class: 'btn small', type: 'button', onclick: () => confirmDialog('Remove ElevenLabs key',
              ['HOOD goes back to the Gemini voice.'], 'Remove key',
              async () => { const res = await api.post('/api/settings/voice/key/remove', { confirm: true }); load(); return res; }) }, 'Remove key') : null),
          h('label', {}, 'Speaking voice', provider),
          h('label', {}, 'HOOD voice ID (from ElevenLabs › Voices)', vid),
          h('label', {}, 'X voice ID (optional)', xvid),
          h('label', {}, 'Model (eleven_multilingual_v2 = best quality, eleven_flash_v2_5 = fastest)', model),
          h('label', {}, 'Price, USD per 1,000 characters (0 if your ElevenLabs plan covers it)', price),
          h('div', { class: 'form-actions' },
            h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
              const res = await api.post('/api/settings/voice', { tts_provider: provider.value, voice_id: vid.value.trim(),
                x_voice_id: xvid.value.trim(), model_id: model.value.trim(), price_per_1k_chars: price.value, confirm: true });
              toast(res.ok ? 'Voice settings saved.' : 'Not saved: ' + res.error);
              if (res.ok) load();
            } }, 'Save voice settings'),
            h('button', { class: 'btn small', type: 'button', onclick: () => voice.speak('Hello Zak, this is HOOD. Can you hear me clearly?') }, '▶ Test HOOD voice'),
            v.x_voice_id ? h('button', { class: 'btn small', type: 'button', onclick: () => voice.speak('This is X.', 'x') }, '▶ Test X voice') : null)));
    });
    load();
    return card('Voice (ElevenLabs)', box);
  }

  // ------------------------------------------------------------------ Settings › Security and Users
  function securityCard() {
    const cur = h('input', { type: 'password', autocomplete: 'current-password', 'aria-label': 'Current password' });
    const nw = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'New password' });
    const again = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'Repeat new password' });
    const change = async () => {
      if (nw.value !== again.value) { toast('The new passwords do not match.'); return; }
      const r = await api.post('/api/auth/change_password', { current_password: cur.value, new_password: nw.value });
      toast(r.ok ? 'Password changed.' : 'Password not changed: ' + r.error);
      if (r.ok) { cur.value = ''; nw.value = ''; again.value = ''; }
    };
    const recovery = () => {
      const pw = h('input', { type: 'password', autocomplete: 'current-password', 'aria-label': 'Current password' });
      confirmDialog('New recovery key', ['A new one-time recovery key replaces the old one. It is shown only once: store it offline.'],
        'Generate key', async () => {
          const r = await api.post('/api/auth/rotate_recovery_key', { current_password: pw.value });
          if (!r.ok) return r;
          setTimeout(() => modal('Your new recovery key', h('div', {}, h('p', {}, 'Write this down and keep it offline. It will not be shown again.'),
            h('p', { class: 'mono' }, r.data.one_time_recovery_key),
            h('div', { class: 'form-actions' }, h('button', { class: 'btn', type: 'button', onclick: closeModal }, 'I stored it')))), 0);
          return r;
        }, h('label', {}, 'Current password', pw));
    };
    return card('Security', h('div', { class: 'form' },
      h('label', {}, 'Current password', cur), h('label', {}, 'New password', nw), h('label', {}, 'Repeat new password', again),
      h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: change }, 'Change password'),
        session.role === 'ROOT_OWNER' ? h('button', { class: 'btn small', type: 'button', onclick: recovery }, 'New recovery key') : null)));
  }

  function usersCard() {
    const box = h('div', {});
    const ROLES = ['VIEWER', 'OPERATOR', 'MANAGER', 'ADMINISTRATOR'];
    const load = () => fill(box, async () => {
      const r = await api.get('/api/admin/users');
      if (!r.ok) return unavailable(r, 'Users');
      const u = h('input', { autocomplete: 'off', 'aria-label': 'Username' });
      const dn = h('input', { autocomplete: 'off', 'aria-label': 'Display name' });
      const pw = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'Password' });
      const role = h('select', { 'aria-label': 'Role' }, ROLES.map((x) => h('option', { value: x }, x)));
      return h('div', {},
        r.data.map((x) => h('div', { class: 'list-row' },
          h('div', { class: 'row-main' }, h('b', {}, x.display_name + ' (' + x.username + ')'),
            h('small', {}, x.role + ' · ' + (x.is_active ? 'active' : 'disabled') + (x.last_login ? ' · last login ' + fmtTime(x.last_login) : ''))),
          x.role === 'ROOT_OWNER' ? null : h('button', { class: 'btn small', type: 'button', onclick: async () => {
            const res = await api.post('/api/admin/users/status', { user_id: x.user_id, is_active: !x.is_active });
            toast(res.ok ? 'User ' + (x.is_active ? 'disabled.' : 'enabled.') : 'Not changed: ' + res.error); load();
          } }, x.is_active ? 'Disable' : 'Enable'))),
        h('h4', {}, 'Add a user'),
        h('div', { class: 'form' }, h('label', {}, 'Username', u), h('label', {}, 'Display name', dn),
          h('label', {}, 'Password', pw), h('label', {}, 'Role', role),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
            const res = await api.post('/api/admin/users/create', { username: u.value.trim(), display_name: dn.value.trim(),
              password: pw.value, role: role.value });
            toast(res.ok ? 'User created.' : 'User not created: ' + res.error);
            if (res.ok) load();
          } }, 'Create user'))));
    });
    load();
    return card('Users', box);
  }

  // ------------------------------------------------------------------ Settings
  function settingsView() {
    const sessions = h('div', {});
    fill(sessions, async () => {
      const r = await api.get('/api/auth/sessions');
      if (!r.ok) return unavailable(r, 'Sessions');
      return h('div', {}, r.data.map((s) => h('div', { class: 'list-row' }, h('div', { class: 'row-main' }, h('b', {}, String(s.user_agent || 'client').slice(0, 60)),
        h('small', {}, 'Signed in ' + fmtTime(s.created_at) + ' · expires ' + fmtTime(s.expires_at))))));
    });
    const reduce = h('input', { type: 'checkbox', id: 'reduceMotion' });
    reduce.checked = document.body.classList.contains('reduce-motion');
    reduce.addEventListener('change', () => { document.body.classList.toggle('reduce-motion', reduce.checked); try { localStorage.setItem('hood-next:reduce-motion', reduce.checked ? '1' : '0'); } catch (e) { /* storage blocked */ } });
    return h('div', {}, head('Settings', 'Your account, AI model, sessions and display preferences.'),
      h('div', { class: 'two-col' },
        card('Account', h('div', {}, h('dl', { class: 'kv' }, h('dt', {}, 'User'), h('dd', {}, String(session.user)), h('dt', {}, 'Role'), h('dd', {}, String(session.role))),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn', type: 'button', onclick: () => session.logout() }, 'Sign out'),
            h('a', { class: 'btn', href: '/classic' }, 'Classic console (legacy)')))),
        card('Display', h('div', {}, h('label', { class: 'switchline' }, reduce, ' Reduce motion'),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn', type: 'button', onclick: () => { widgets.layout = DEFAULT_LAYOUT.map((w) => ({ ...w })); widgets.save(); toast('Layout reset.'); } }, 'Reset dashboard layout'))))),
      session.role === 'ROOT_OWNER' ? h('div', { style: { marginTop: '14px' } }, modelProviderCard()) : null,
      session.role === 'ROOT_OWNER' ? h('div', { style: { marginTop: '14px' } }, voiceSettingsCard()) : null,
      h('div', { class: 'two-col', style: { marginTop: '14px' } }, securityCard(),
        session.role === 'ROOT_OWNER' ? usersCard() : null),
      h('div', { style: { marginTop: '14px' } }, card('Active sessions', sessions)));
  }

  const VIEWS = { Command: commandView, Missions: missionsView, Agents: agentsView, Intelligence: intelligenceView,
    Integrations: integrationsView, Sentinel: sentinelView, Memory: memoryView, Commerce: commerceView,
    Desktop: desktopView, Voice: voiceView, Settings: settingsView };

  // ------------------------------------------------------------------ global search (missions, agents, capabilities)
  async function runSearch(q) {
    const box = $('searchResults');
    q = q.trim().toLowerCase();
    if (q.length < 2) { box.classList.add('hidden'); return; }
    const [m, c] = await Promise.all([api.get('/api/agents/missions'), api.get('/api/capabilities')]);
    const results = [];
    if (m.ok) m.data.filter((x) => x.objective.toLowerCase().includes(q)).slice(0, 5).forEach((x) => results.push(['Mission', x.objective.slice(0, 80), () => { missionsPage.selected = x.id; app.render('Missions'); }]));
    if (c.ok) c.data.items.filter((x) => (x.name + ' ' + x.description).toLowerCase().includes(q)).slice(0, 5).forEach((x) => results.push(['Capability', x.name, () => app.render('Integrations')]));
    PAGES.filter(([p]) => p.toLowerCase().includes(q)).forEach(([p]) => results.push(['Page', p, () => app.render(p)]));
    clear(box);
    if (!results.length) box.append(h('div', { class: 'empty-box' }, 'No results.'));
    results.forEach(([kind, label, go]) => box.append(h('button', { type: 'button', role: 'option', onclick: () => { box.classList.add('hidden'); $('globalSearch').value = ''; go(); } }, kind + ' · ' + label)));
    box.classList.remove('hidden');
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    try { if (localStorage.getItem('hood-next:reduce-motion') === '1') document.body.classList.add('reduce-motion'); } catch (e) { /* ignore */ }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) document.body.classList.add('reduce-motion');
    $('authForm').addEventListener('submit', (e) => session.submit(e));
    document.addEventListener('click', (e) => {
      const nav = e.target.closest('[data-page]');
      if (nav && !$('authOverlay').classList.contains('hidden')) return;
      if (nav) { e.preventDefault(); app.render(nav.dataset.page); }
      if (!e.target.closest('.top-search')) $('searchResults').classList.add('hidden');
    });
    $('menuToggle').addEventListener('click', () => $('rail').classList.toggle('open'));
    $('modalClose').addEventListener('click', closeModal);
    $('modalBackdrop').addEventListener('click', (e) => { if (e.target === $('modalBackdrop')) closeModal(); });
    $('alertsBtn').addEventListener('click', () => app.render('Sentinel'));
    $('stopBtn').addEventListener('click', stopDialog);
    $('userBtn').addEventListener('click', () => app.render('Settings'));
    let st;
    $('globalSearch').addEventListener('input', (e) => { clearTimeout(st); st = setTimeout(() => runSearch(e.target.value), 250); });
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('globalSearch').focus(); }
      if (e.key === 'Escape') { closeModal(); $('searchResults').classList.add('hidden'); }
    });
    window.addEventListener('hashchange', () => { const p = decodeURIComponent(location.hash.slice(1)); if (app.booted && p !== app.page) app.render(p); });
    session.start();
  }
  wire();
})();
