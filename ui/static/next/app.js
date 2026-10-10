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
    ['Sentinel', '◇'], ['Memory', '▤'], ['Commerce', '◈'], ['Desktop', '▣'], ['Voice', '◉'], ['Repair', '✚'], ['Settings', '⚙'],
  ];
  const PAGE_LABEL = { Intelligence: 'System map', Repair: 'Self-repair' };   // page keys stay stable in links; labels are for people
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
        const r = await api.post('/api/auth/init', { username, password, display_name: $('authDisplay').value || username,
          setup_code: $('authSetupCode').value.trim() });
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
      const fromHash = decodeURIComponent((location.hash || '').slice(1)).split('/')[0];
      this.render(PAGES.some(([p]) => p === fromHash) ? fromHash : 'Command', { replace: true });
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
      $('envDetail').textContent = gem.state === 'degraded' && gem.last_error ? 'Last call failed: ' + clip(gem.last_error, 140)
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
    render(p, opts) {
      this.cleanups.forEach((fn) => { try { fn(); } catch (e) { /* ignore */ } });
      this.cleanups = [];
      this.subroute = null;
      this.page = PAGES.some(([n]) => n === p) ? p : 'Command';
      // Each page is a browser history entry, so the browser's (or mouse's) Back button works inside HOOD.
      if (decodeURIComponent(location.hash.slice(1)).split('/')[0] !== this.page) {
        if (opts && opts.replace) history.replaceState(null, '', '#' + this.page); else history.pushState(null, '', '#' + this.page);
      }
      $('crumb').textContent = (PAGE_LABEL[this.page] || this.page).toUpperCase() + ' / ' + (this.page === 'Command' ? 'OVERVIEW' : 'WORKSPACE');
      const nav = clear($('nav'));
      for (const [name, icon] of PAGES) {
        nav.append(h('button', { class: 'nav-link' + (name === this.page ? ' active' : ''), 'data-page': name, type: 'button',
          'aria-current': name === this.page ? 'page' : null }, h('span', { class: 'nav-icon', 'aria-hidden': 'true' }, icon), PAGE_LABEL[name] || name));
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
      h('div', {}, h('div', { class: 'eyebrow' }, 'HOOD / ' + (PAGE_LABEL[app.page] || app.page).toUpperCase()), h('h1', {}, title), h('p', {}, sub)),
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
    if (runCommand.busy) { toast('HOOD is still answering your previous message.'); return; }
    runCommand.busy = true;
    setAvatar('thinking');
    const started = Date.now();
    const tick = () => {
      const secs = Math.round((Date.now() - started) / 1000);
      logEl.textContent = 'HOOD is thinking… ' + secs + 's' +
        (secs >= 20 ? ' (the AI model is slow right now; it may be busy or rate-limited)' : '');
    };
    tick();
    const timer = setInterval(tick, 1000);
    let r;
    try {
      r = await Promise.race([api.post('/api/chat', { text }),
        new Promise((resolve) => setTimeout(() => resolve({ ok: false, status: 0,
          error: 'no answer after 2 minutes; the AI model may be overloaded or out of free quota. Try again in a moment.' }), 120000))]);
    } finally {
      clearInterval(timer);
      runCommand.busy = false;
    }
    setAvatar('idle');
    if (!r.ok) { logEl.textContent = 'Hood could not answer: ' + r.error; return; }
    clear(logEl).append(h('b', {}, (r.data.sender || 'Hood') + ': '), richText(r.data.text));
    app.debouncedOverview();  // the reply was a live model call: refresh the provider badge
    if (r.data.suggested_mission) {
      // HOOD never starts work from chat: it opens a plan the owner reviews and approves.
      const objective = r.data.suggested_mission;
      const notes = r.data.scope_notes || [];
      const btn = h('button', { class: 'btn primary plan-btn', type: 'button' }, '▶ Plan this as a mission');
      const openPlan = async () => {
        btn.disabled = true; btn.textContent = 'Writing the brief from our conversation…';
        const d = await api.post('/api/chat/mission_draft', { fallback: objective });
        btn.disabled = false; btn.textContent = '▶ Plan this as a mission';
        if (d.ok && d.data.note) toast(d.data.note);
        planMissionDialog(d.ok ? d.data.objective : objective);
      };
      btn.addEventListener('click', openPlan);
      if (r.data.needs_tools) logEl.append(toolsPanel(r.data.needs_tools, () => toast('Ready: you can plan it now.')));
      else if (notes.length) logEl.append(h('div', { class: 'callout warn' }, h('b', {}, 'Good to know'), h('ul', {}, notes.map((n) => h('li', {}, n)))));
      logEl.append(h('div', { class: 'form-actions' }, btn, h('small', { class: 'small-note' }, 'Nothing runs until you approve the plan.')));
      if (r.data.open_mission_draft) openPlan();   // the owner asked to plan it: open it for review
    }
    if (r.data.offer_self_repair && session.role === 'ROOT_OWNER') {
      // A problem with HOOD itself: offer self-repair. Only an offer; the dialog asks before sending anything.
      logEl.append(h('div', { class: 'callout' }, h('b', {}, 'Is something wrong with HOOD itself?'),
        h('p', {}, 'HOOD can look for the cause in its own code, test a fix in its sandbox, and show you the exact change before anything is applied.'),
        h('div', { class: 'form-actions' }, h('button', { class: 'btn primary', type: 'button', onclick: () => reportProblemDialog(text) }, '✚ Investigate & fix'))));
    }
    if (voice.autoSpeak) voice.speak(String(r.data.text).replace(/[*`#_]/g, ''), r.data.speaker_id);
  }
  // "Allow & install": HOOD asks instead of saying no (owner's rule: once per tool, inside WSL2).
  // HOOD's own Linux sandbox on Windows: the owner approves once; HOOD does every step (and says
  // up front what only Windows can ask of them: its administrator prompt and, maybe, a restart).
  const SANDBOX_PHASE = {
    not_set_up: 'Not set up', setting_up: 'Setting up…', installing_wsl: 'Installing WSL (Windows asks for administrator permission)',
    restart_needed: 'Windows needs a restart', downloading: 'Downloading Ubuntu’s official image', importing: 'Creating HOOD’s Linux system',
    configuring: 'Locking it down', packages: 'Installing Python and pytest', ready: 'Ready', failed: 'Stopped',
  };
  function sandboxPanel(onReady, opts) {
    const o = opts || {};
    const box = h('div', { class: 'callout warn sandbox-panel' });
    let timer = null;
    const actions = (sb) => {
      const row = h('div', { class: 'form-actions' });
      if (session.role !== 'ROOT_OWNER') return h('p', { class: 'small-note' }, 'Only the Root Owner can approve this.');
      if (!sb.approved_by || sb.phase === 'failed') {
        row.append(h('button', { class: 'btn small primary', type: 'button', onclick: () => confirmDialog(o.title || 'Set up HOOD’s sandbox',
          sb.approval_text.concat(o.extra || []), o.button || 'Allow & set up', async () => {
            const r = o.approve ? await o.approve() : await api.post('/api/sandbox/setup', { confirm: true });
            if (r.ok) setTimeout(load, 500);
            return r;
          }) }, sb.phase === 'failed' ? 'Try again' : (o.button || 'Allow & set up')));
      }
      if (sb.phase === 'restart_needed') {
        row.append(h('button', { class: 'btn small primary', type: 'button', onclick: () => confirmDialog('Restart Windows now?',
          ['Windows restarts in 60 seconds: save your work first.', 'When Windows is back, start HOOD again: it finishes the setup by itself and continues waiting missions.'],
          'Restart in 60 s', async () => { const r = await api.post('/api/sandbox/restart', { confirm: true }); if (r.ok) toast(r.data.message); return r; }) }, 'Restart now'));
      }
      return row;
    };
    const load = () => api.get('/api/sandbox').then((r) => {
      clearTimeout(timer);
      if (!box.isConnected && box.dataset.started) return;
      box.dataset.started = '1';
      clear(box);
      if (!r.ok) { box.append(h('p', {}, r.error)); return; }
      const sb = r.data;
      if (!sb.managed) { box.className = 'callout ' + (sb.ready ? 'good' : 'warn') + ' sandbox-panel';
        box.append(h('b', {}, sb.ready ? 'Sandbox ready' : 'No sandbox here'), h('p', {}, sb.problem || 'Agent code runs isolated: no internet, only the mission folder, limited CPU and memory.')); return; }
      if (sb.ready) {
        box.className = 'callout good sandbox-panel';
        box.append(h('b', {}, 'HOOD’s Linux sandbox is ready'), h('p', {}, 'Agent code runs there: no internet, no access to your Windows files, limited CPU and memory.'));
        if (onReady) { const f = onReady; onReady = null; f(); }
        return;
      }
      box.className = 'callout warn sandbox-panel';
      const running = sb.job && sb.job.state === 'running';
      box.append(...[h('b', {}, o.heading || 'HOOD’s Linux sandbox (WSL2)'),
        h('p', {}, sb.approved_by ? (SANDBOX_PHASE[sb.phase] || sb.phase) + (sb.phase === 'restart_needed'
          ? ': HOOD continues by itself after the restart (start HOOD again when Windows is back).' : '')
          : (o.intro || 'Approve once: HOOD sets up its own Linux system on this PC and runs agent code there. You don’t type anything.')),
        !sb.approved_by ? h('ul', {}, sb.approval_text.map((t) => h('li', {}, t))) : null,
        sb.phase === 'failed' && sb.last_error ? reasonBlock(sb.last_error) : null,
        sb.job && sb.job.log.length ? h('details', { open: running ? '' : null }, h('summary', {}, 'What HOOD is doing'),
          h('pre', { class: 'file-view wrap' }, sb.job.log.join('\n'))) : null,
        running ? null : actions(sb)].filter(Boolean));   // DOM append() would print "null"
      if (running || sb.phase === 'restart_needed') timer = setTimeout(load, running ? 2000 : 15000);
    });
    load();
    return box;
  }
  function toolsPanel(need, onReady) {
    const box = h('div', { class: 'callout warn tools-panel' });
    const render = (n) => {
      clear(box);
      if (!n || n.ready) { box.className = 'callout good tools-panel'; box.append(h('b', {}, 'Tools ready'), h('p', {}, 'Everything this needs is installed.')); return; }
      const names = n.missing.map((t) => n.names[t] || t);
      const sb = n.sandbox;                     // Windows: the tools go into HOOD's Linux sandbox
      box.append(h('b', {}, 'Needs: ' + names.join(', ')));
      if (n.running) { box.append(h('p', {}, 'Installing…')); follow(n.running); return; }
      if (sb && !sb.ready && (!sb.approved || sb.phase === 'failed') && n.unapproved.length) {
        box.append(h('p', {}, 'One approval covers everything: HOOD sets up its own Linux sandbox on this PC, then installs these inside it from their official sources (checked before use). You don’t do anything else.'));
        box.append(sandboxPanel(null, { heading: 'What HOOD will do', button: 'Allow & set up', title: 'Allow & set up',
          extra: ['Then installs: ' + names.join(', ') + '. You allow each tool once; later updates and reuse don’t ask again.'],
          approve: async () => {
            const r = await api.post('/api/tools/install', { tools: n.missing, confirm: true, with_sandbox: true });
            if (r.ok) follow(r.data.id);
            return r;
          } }));
        return;
      }
      if (sb && !sb.ready && sb.approved) box.append(sandboxPanel(null));
      else if (n.problem) { box.append(h('p', {}, n.problem)); return; }
      if (!n.unapproved.length) { box.append(h('p', {}, 'You allowed these; HOOD installs them by itself' + (sb && !sb.ready ? ' as soon as its sandbox is ready.' : ' when the mission needs them.'))); return; }
      box.append(h('p', {}, 'HOOD can install these inside WSL2 from their official sources (checked before use). You allow each tool once; after that HOOD reuses and updates it without asking.'));
      if (session.role !== 'ROOT_OWNER') { box.append(h('p', { class: 'small-note' }, 'Only the Root Owner can allow installs.')); return; }
      box.append(h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: () => confirmDialog('Allow & install',
        ['HOOD will install: ' + names.join(', ') + ' (inside WSL2, from their official sources).',
          'You allow each tool once; later updates and reuse don’t ask again. You can remove them in Settings › Tools.'], 'Allow & install', async () => {
          const r = await api.post('/api/tools/install', { tools: n.missing, confirm: true });
          if (!r.ok) return r;
          follow(r.data.id);
          return r;
        }) }, 'Allow & install')));
    };
    const follow = async (jobId) => {
      const log = h('pre', { class: 'file-view wrap' }, 'Starting…');
      const sandboxBox = h('div', {});
      clear(box).append(h('b', {}, 'Installing…'), sandboxBox, log);
      let sandboxShown = false;
      for (;;) {
        const j = await api.get('/api/tools/jobs/' + jobId);
        if (!j.ok) { log.textContent = j.error; return; }
        log.textContent = j.data.log.join('\n') || '…';
        if (!sandboxShown && /sandbox/i.test(log.textContent)) { sandboxShown = true; sandboxBox.append(sandboxPanel(null)); }
        if (j.data.state !== 'running') {
          if (j.data.state === 'done') {
            render({ ready: true });
            box.append(h('details', {}, h('summary', {}, 'What was installed'), h('pre', { class: 'file-view wrap' }, j.data.log.join('\n'))));
            toast('Tools installed.');
            if (onReady) onReady();
          } else if (j.data.state === 'waiting') {
            box.firstChild.textContent = 'Waiting for HOOD’s sandbox';
            box.append(h('p', {}, 'HOOD installs these by itself as soon as its sandbox is ready, and waiting missions continue on their own.'));
          } else box.append(h('p', {}, 'Install stopped: ' + (j.data.error || 'unknown error')));
          return;
        }
        await new Promise((res) => setTimeout(res, 1500));
      }
    };
    render(need);
    return box;
  }
  const WEBSITE_RE = /\b(web ?site|site ?web|landing ?page|web ?page|page ?web|home ?page|html|site|catalog(ue)?|boutique|shop|store|portfolio|blog)\b/i;
  function planMissionDialog(objective, opts) {
    const obj = h('textarea', { class: 'big', 'aria-label': 'Objective', maxlength: '8000', rows: '12' });
    obj.value = objective || '';
    const limits = h('div', {});
    const showLimits = async () => {
      const r = await api.post('/api/agents/scope', { objective: obj.value.slice(0, 8000), profile: kind.value });
      clear(limits);
      if (kind.value === 'wordpress_site') {
        const t = await api.get('/api/tools/needs?profile=wordpress_site');
        if (t.ok && !t.data.ready) { limits.append(toolsPanel(t.data, showLimits)); return; }
      }
      if (r.ok && r.data.notes.length) limits.append(h('div', { class: 'callout warn' }, h('b', {}, 'Good to know before planning'),
        h('ul', {}, r.data.notes.map((n) => h('li', {}, n)))));
    };
    const budget = h('input', { type: 'number', min: '0', max: '100', step: '0.01', value: '1.00', 'aria-label': 'Spend cap in USD' });
    const kind = h('select', { 'aria-label': 'Kind of work' },
      h('option', { value: 'static_web' }, 'Website (HTML, CSS, JavaScript): checked by reading the files, works on this PC'),
      h('option', { value: 'wordpress_site' }, 'WordPress site: a real WordPress theme and pages, run and checked in the sandbox (needs PHP and WordPress, installed with your OK inside WSL2)'),
      h('option', { value: 'python_app' }, 'Python program: its tests must run (Linux/WSL2, or "Run on my PC" with your approval)'));
    const guessKind = (t) => (/\bword ?press\b/i.test(t) ? 'wordpress_site' : WEBSITE_RE.test(t) ? 'static_web' : 'python_app');
    kind.value = (opts && opts.kind) || guessKind(obj.value);
    let touched = !!(opts && opts.kind), limitTimer = null;
    kind.addEventListener('change', () => { touched = true; showLimits(); });
    obj.addEventListener('input', () => {
      if (!touched) kind.value = guessKind(obj.value);
      clearTimeout(limitTimer); limitTimer = setTimeout(showLimits, 500);
    });
    showLimits();
    confirmDialog('Plan an agent mission', [
      'Review and edit the brief below: the agents only see this text, not our conversation.',
      'Hood sends this objective to the configured AI provider to draft a task plan.',
      'Nothing runs until you approve that exact plan and its spending cap.'],
    'Plan mission', async () => {
      const r = await api.post('/api/agents/missions', { objective: obj.value, budget_usd: Number(budget.value), profile: kind.value, confirm: true });
      if (!r.ok) return r;
      toast(r.data.state === 'AWAITING_PLAN_APPROVAL' ? 'Plan ready for your review.' : 'Planning stopped: ' + (r.data.error || r.data.state));
      missionsPage.selected = r.data.mission_id;
      app.render('Missions');
      return r;
    }, h('div', { class: 'form' }, h('label', {}, 'Objective', obj), limits, h('label', {}, 'Kind of work', kind), h('label', {}, 'Spend cap (USD)', budget)));
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
        h('div', { class: 'row-main' }, h('b', {}, clip(missionTitle(m.objective), 90)), h('small', {}, fmtTime(m.updated))),
        h('div', { class: 'row-meta' }, badge(m.state), h('br'), m.provider_mode === 'SIMULATED' ? badge('simulated') : null)))
        : emptyBox('No missions yet. Type /mission followed by an objective.'),
      h('button', { class: 'plain', type: 'button', onclick: () => app.render('Missions') }, 'Open mission control →'));
    } },
    graph: { title: 'Living system network', async load() {
      const r = await api.get('/api/console/graph?limit=10');
      if (!r.ok) return unavailable(r, 'System map');
      const holder = h('div', { style: { height: '265px' } });
      graphView.draw(holder, r.data, 'root', true);
      return h('div', {}, h('div', { class: 'small-note' }, 'Live: green works, amber needs you, red is failing. Click an area to look inside.'), holder,
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
      if (d && d.state) return d.state + (d.error ? ' — ' + clip(d.error, 120) : '');
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

  // ------------------------------------------------------------------ Missions (live view)
  const ROLE_LABEL = { planner: 'Planner', engineer: 'Engineer', qa: 'QA', reviewer: 'Reviewer', verifier: 'Verifier' };
  const PROFILE_LABEL = {
    static_web: 'Website: checked by reading its files (nothing is run), works on this PC',
    wordpress_site: 'WordPress site: theme and pages, run by a real WordPress in the sandbox and checked',
    python_app: 'Python program: checked by running its tests (needs a sandbox, WSL2, or your "Run on my PC" approval)',
  };
  const CHECK_LABEL = { site_structure: 'Page structure', links_and_assets: 'Links and files', css_syntax: 'CSS',
    javascript_structure: 'JavaScript structure', independent_acceptance_checks: 'QA acceptance checks',
    php_syntax: 'PHP syntax', wordpress_setup: 'WordPress set up', pages_render: 'Pages render without errors',
    compile: 'Code compiles', engineer_unit_tests: "Engineer's unit tests", independent_acceptance_tests: 'QA acceptance tests' };
  const STEP_LABEL = { CREATED: 'waiting', QUEUED: 'waiting', RUNNING: 'working', COMPLETED: 'done', FAILED: 'failed',
    BLOCKED: 'needs you', CANCELLED: 'cancelled', UNVERIFIED: 'not verified' };
  const missionsPage = { selected: null, open: new Set(['progress', 'outputs', 'checks']), cache: {}, poll: null };
  function clip(text, n) {
    const s = String(text || '');
    if (s.length <= n) return s;
    const cut = s.slice(0, n);
    const at = cut.lastIndexOf(' ');
    return (at > n * 0.6 ? cut.slice(0, at) : cut).replace(/[\s,.;:]+$/, '') + '…';
  }
  function elapsed(iso) {
    const ms = Date.now() - new Date(iso).getTime();
    if (!(ms >= 0)) return '';
    const sec = Math.floor(ms / 1000);
    return sec < 60 ? sec + 's' : Math.floor(sec / 60) + 'm ' + (sec % 60) + 's';
  }
  // A brief may open with "Not possible here: ..."; its "Goal:" line names the mission better.
  const KIND_SHORT = { static_web: 'Website', wordpress_site: 'WordPress site', python_app: 'Python program' };
  function missionTitle(objective) {
    const text = String(objective || '');
    const goal = text.match(/^\s*(?:goal|objectif)\s*:\s*(.+)$/im);
    if (goal) return goal[1].trim();
    return (text.split('\n').find((l) => l.trim() && !/^\s*not possible here/i.test(l)) || text).trim();
  }
  const missionUrl = (mid, rest) => '/api/agents/missions/' + encodeURIComponent(mid) + (rest || '');
  // Plain words for known technical reasons; the original stays under "Technical details".
  function friendlyReason(raw) {
    const t = String(raw || '');
    let m;
    if (/only implemented for POSIX hosts|Windows has no agent sandbox/.test(t)) {
      return 'This computer had no sandbox to run the tests. Approve HOOD’s Linux sandbox once (Settings › Agents): HOOD sets it up itself. Or turn on “Run on my PC”.';
    }
    if ((m = t.match(/^(The checks need a sandbox[^\[]*|Waiting for HOOD's Linux sandbox[^\[]*)/))) return m[1].trim();
    if (/unshare -rn|Network namespace isolation/.test(t)) return 'Linux network isolation isn’t available here, so the tests could not run in the sandbox.';
    if ((m = t.match(/AgentOutputRejected: (\w+) output rejected: (.*?)(?: \[\d+ chars|$)/s))) {
      return 'The ' + (ROLE_LABEL[m[1]] || m[1]) + ' agent’s answer was rejected: ' + clip(m[2], 300);
    }
    if ((m = t.match(/Planner provider unavailable: (.*)/s))) return 'The AI model couldn’t be reached while planning: ' + clip(m[1], 240);
    if ((m = t.match(/Planning failed: (.*)/s))) return 'Planning failed: ' + clip(m[1], 300);
    return t;
  }
  function reasonBlock(raw, cls) {
    const plain = friendlyReason(raw);
    const technical = plain !== raw || String(raw).length > 320;
    return h('div', { class: cls || '' }, clip(plain, 420),
      technical ? h('details', { class: 'tech' }, h('summary', {}, 'Technical details'), h('pre', { class: 'file-view wrap' }, String(raw))) : null);
  }

  function missionsView() {
    const list = h('div', {});
    const detail = h('div', { class: 'mission-detail' });
    const loadList = () => fill(list, async () => {
      const r = await api.get('/api/agents/missions');
      if (!r.ok) return unavailable(r, 'Agent missions');
      if (!r.data.length) return emptyBox('No missions yet. Use “Plan mission”, or ask HOOD in the chat to build something.');
      if (!missionsPage.selected) missionsPage.selected = r.data[0].id;
      return h('div', {}, r.data.map((m) => h('button', { type: 'button', class: 'list-row plain' + (m.id === missionsPage.selected ? ' active' : ''),
        style: { width: '100%', textAlign: 'left' }, 'aria-pressed': String(m.id === missionsPage.selected),
        onclick: () => { missionsPage.selected = m.id; loadList(); loadDetail(true); } },
        h('div', { class: 'row-main' }, h('b', {}, clip(missionTitle(m.objective), 110)),
          h('small', {}, (KIND_SHORT[m.profile] || 'Python program') + ' · ' + fmtTime(m.updated))),
        h('div', { class: 'row-meta' }, badge(m.state), ' ', m.provider_mode !== 'LIVE' && m.provider_mode !== 'NONE' ? badge(m.provider_mode) : null))));
    });
    // Live re-render without flashing a loading box (keeps scroll position and open sections).
    const loadDetail = async (fresh) => {
      clearTimeout(missionsPage.poll);
      if (!missionsPage.selected) { clear(detail).append(emptyBox('Select a mission.')); return; }
      if (fresh) clear(detail).append(loading('mission'));
      const mid = missionsPage.selected;
      const r = await api.get(missionUrl(mid));
      if (mid !== missionsPage.selected) return;
      clear(detail).append(r.ok ? missionDetail(r.data, () => { loadList().then(() => loadDetail()); }) : unavailable(r, 'Mission'));
      if (r.ok && (r.data.background_run_active || ['PLANNING', 'RUNNING', 'VERIFYING'].includes(r.data.state))) {
        missionsPage.poll = setTimeout(() => loadDetail(), 4000);   // safety net if a live event is missed
      }
    };
    loadList().then(() => loadDetail(true));
    const tick = setInterval(() => document.querySelectorAll('.tick[data-since]').forEach((el) => { el.textContent = ' ' + elapsed(el.dataset.since); }), 1000);
    app.onCleanup(() => { clearInterval(tick); clearTimeout(missionsPage.poll); });
    app.onCleanup(feed.subscribe((ev) => { if (ev.mission_id === missionsPage.selected) { clearTimeout(missionsPage.t); missionsPage.t = setTimeout(() => { loadList().then(() => loadDetail()); }, 500); } }));
    return h('div', {}, head('Mission control', 'Watch the agents work: what each one is doing, what it wrote, and what the independent check found.',
      h('button', { class: 'btn primary', type: 'button', onclick: () => planMissionDialog('') }, '＋ Plan mission')),
    h('div', { class: 'two-col missions-layout' }, card('Missions', list), card('Mission detail', detail)));
  }

  function section(id, title, body, extra, forceOpen) {
    const d = h('details', { class: 'mission-section', open: forceOpen || missionsPage.open.has(id) || null },
      h('summary', {}, title, extra ? h('span', { class: 'section-extra' }, extra) : null));
    const holder = h('div', { class: 'section-body' });
    d.append(holder);
    let loaded = false;
    const load = () => { if (loaded) return; loaded = true; holder.append(typeof body === 'function' ? body() : body); };
    if (d.open) load();
    d.addEventListener('toggle', () => { if (d.open) { missionsPage.open.add(id); load(); } else missionsPage.open.delete(id); });
    return d;
  }

  function pipeline(m) {
    const steps = [{ role: 'planner', title: 'Plan the work',
      state: m.plan ? 'COMPLETED' : m.state === 'PLANNING' ? 'RUNNING' : m.state === 'BLOCKED' ? 'BLOCKED' : 'CREATED',
      since: m.state === 'PLANNING' ? m.created : null }];
    m.tasks.forEach((t) => steps.push({ role: t.role, title: t.title, state: t.state, since: t.updated, attempts: t.attempts, error: t.error }));
    const v = m.last_verification;
    let vs = 'CREATED';
    if (m.state === 'VERIFYING') vs = 'RUNNING';
    else if (m.state === 'COMPLETED') vs = 'COMPLETED';
    else if (m.local_run && m.local_run.awaiting_approval) vs = 'BLOCKED';
    else if (v && ['FAILED', 'UNVERIFIED'].includes(m.state)) vs = v.verdict === 'FAIL' ? 'FAILED' : 'UNVERIFIED';
    else if (v && m.state === 'RUNNING') vs = 'CREATED';   // repair round under way; checked again after it
    steps.push({ role: 'verifier', title: m.profile === 'static_web' ? 'Check pages, links, code structure and QA checks'
      : m.profile === 'wordpress_site' ? 'Run WordPress, render the pages, apply QA checks' : 'Run the tests and QA checks',
      state: vs, since: m.state === 'VERIFYING' ? m.updated : null });
    const look = (state) => (['CREATED', 'QUEUED'].includes(state) ? 'muted' : kindOf(state));
    return h('ol', { class: 'pipeline' }, steps.map((st) => h('li', { class: 'pipe-step ' + look(st.state) },
      h('div', { class: 'pipe-role' }, ROLE_LABEL[st.role] || st.role),
      h('div', { class: 'pipe-title' }, st.title),
      h('div', { class: 'pipe-meta' }, h('span', { class: 'state ' + look(st.state) }, STEP_LABEL[st.state] || st.state),
        st.state === 'RUNNING' && st.since ? h('span', { class: 'tick', 'data-since': st.since }, ' ' + elapsed(st.since)) : null,
        st.attempts > 1 ? h('small', {}, ' · attempt ' + st.attempts) : null),
      st.error && st.state !== 'COMPLETED' ? reasonBlock(st.error, 'small-note') : null)));
  }

  async function viewFile(mid, path) {
    const r = await api.get(missionUrl(mid, '/file?path=' + encodeURIComponent(path)));
    if (!r.ok) { toast('Cannot open ' + path + ': ' + r.error); return; }
    modal(path, h('div', {}, h('p', { class: 'small-note' }, r.data.bytes + ' bytes' + (r.data.truncated ? ' (first 200 KB shown)' : '') + ' · read-only'),
      h('pre', { class: 'file-view' }, r.data.text)));
  }
  const fileLink = (mid, path) => h('button', { class: 'linkish mono', type: 'button', onclick: () => viewFile(mid, path) }, path);
  const SEVERITY = { critical: 'FAILED', high: 'FAILED', medium: 'PENDING', low: 'muted', info: 'muted' };

  function agentOutputs(m) {
    const done = m.tasks.filter((t) => t.result);
    if (!done.length) return emptyBox('No agent has finished a task yet.');
    return h('div', {}, done.map((t) => h('div', { class: 'output-block' },
      h('div', {}, h('b', {}, ROLE_LABEL[t.role] || t.role), ' · ', t.title),
      t.result.files ? h('ul', { class: 'file-list' }, t.result.files.map((f) => h('li', {}, fileLink(m.mission_id, f.path), h('small', {}, ' ' + f.bytes + ' bytes')))) : null,
      t.result.findings ? (t.result.findings.length ? h('ul', { class: 'file-list' }, t.result.findings.map((f) => h('li', {},
        badge(SEVERITY[f.severity] || 'muted', f.severity), ' ', f.path ? h('span', { class: 'mono' }, f.path + ': ') : null, f.message)))
        : h('p', { class: 'small-note' }, 'No defects reported.')) : null,
      t.result.notes ? h('p', { class: 'small-note' }, 'Notes: ' + t.result.notes) : null,
      t.result.uncertainty && !/^none\.?$/i.test(t.result.uncertainty.trim()) ? h('p', { class: 'small-note' }, 'Unsure about: ' + t.result.uncertainty) : null)));
  }

  function checksSection(m) {
    const v = m.last_verification;
    if (!v) return emptyBox(m.profile === 'static_web'
      ? "Not checked yet. HOOD will read the pages, links, CSS and JavaScript, then apply the QA agent's acceptance checks. The site's code is never run."
      : m.profile === 'wordpress_site'
        ? "Not checked yet. HOOD will set up a real WordPress with this theme and these pages in the sandbox, render every page, and apply the QA agent's checks to what WordPress shows."
        : 'Not checked yet. HOOD will compile the code and run the unit tests and the QA acceptance tests.');
    return h('div', {},
      h('p', {}, badge(v.verdict), ' ', h('small', {}, v.execution ? 'How: ' + v.execution : (v.network_isolated ? 'In the sandbox, no network' : ''))),
      (v.checks || []).map((c) => h('details', { class: 'check-row' },
        h('summary', {}, badge(c.passed ? 'PASS' : 'FAILED', c.passed ? 'passed' : 'failed'), ' ', CHECK_LABEL[c.name] || c.name,
          c.tests_collected !== null && c.tests_collected !== undefined ? h('small', {}, ' · ' + c.tests_collected + (m.profile === 'static_web' ? ' checks' : ' tests')) : null),
        c.output_tail ? h('pre', { class: 'file-view' }, c.output_tail) : h('p', { class: 'small-note' }, 'No details recorded.'))),
      m.state === 'RUNNING' && v.verdict === 'FAIL' ? h('p', { class: 'small-note' }, 'The agents are repairing these failures (round ' + m.repairs + ' of 2); HOOD checks again afterwards.') : null);
  }

  function filesSection(m) {
    const box = h('div', {}, missionsPage.cache['files:' + m.mission_id] || loading('files'));
    api.get(missionUrl(m.mission_id, '/files')).then((r) => {
      const out = !r.ok ? unavailable(r, 'Mission files') : h('div', {},
        h('div', { class: 'form-actions' },
          m.preview_path ? h('a', { class: 'btn small primary', href: m.preview_path, target: '_blank', rel: 'noopener noreferrer' },
            m.state === 'COMPLETED' ? 'Open website preview' : 'Preview (not verified yet)') : null,
          session.role === 'ROOT_OWNER' ? h('button', { class: 'btn small', type: 'button', onclick: async () => {
            const x = await api.post(missionUrl(m.mission_id, '/open_folder'), { confirm: true });
            toast(x.ok ? 'Folder opened (' + x.data.opened_with + ').' : x.error);
          } }, 'Open folder') : null,
          h('button', { class: 'btn small', type: 'button', onclick: () => {
            const done = () => toast('Folder path copied.');
            if (navigator.clipboard) navigator.clipboard.writeText(r.data.folder).then(done, () => toast(r.data.folder)); else toast(r.data.folder);
          } }, 'Copy folder path')),
        h('p', { class: 'small-note mono' }, r.data.folder),
        m.preview_path ? h('p', { class: 'small-note' }, 'The preview runs sandboxed: no internet and nothing saved in the browser, so a cart that remembers items may reset. Open site/index.html from the folder for the full experience.') : null,
        r.data.files.length ? h('ul', { class: 'file-list' }, r.data.files.map((f) => h('li', {}, fileLink(m.mission_id, f.path),
          h('small', {}, ' ' + f.bytes + ' bytes' + (f.written_by ? ' · by ' + (ROLE_LABEL[f.written_by.role] || f.written_by.role) : '')))))
          : emptyBox('No files written yet.'));
      missionsPage.cache['files:' + m.mission_id] = out;
      clear(box).append(out);
    });
    return box;
  }

  function describeEvent(e) {
    let d = {};
    try { d = typeof e.detail === 'string' ? JSON.parse(e.detail) : (e.detail || {}); } catch (err) { d = {}; }
    d = d || {};
    switch (e.kind) {
      case 'CREATED': return 'Mission created' + (d.profile ? ' (' + (KIND_SHORT[d.profile] || d.profile).toLowerCase() + ')' : '');
      case 'STATE': return 'Now ' + String(d.state || '').replace(/_/g, ' ').toLowerCase() + (d.error ? ': ' + clip(d.error, 220) : '');
      case 'TASK_STARTED': return (ROLE_LABEL[d.role] || 'Agent') + ' started “' + d.title + '”' + (d.attempt > 1 ? ' (attempt ' + d.attempt + ')' : '');
      case 'TASK_COMPLETED': return 'Task ' + d.task_id + ' finished';
      case 'TASK_FAILED': return 'Task ' + d.task_id + ' failed';
      case 'TASK_BLOCKED': return 'Task ' + d.task_id + ' is blocked';
      case 'TASK_RETRY': return 'Output for ' + d.task_id + ' was rejected; retrying (' + clip(d.error, 160) + ')';
      case 'VERIFIED': return 'Independent check: ' + d.verdict + (d.reason ? ' — ' + clip(d.reason, 220) : '');
      case 'LOCAL_RUN_REQUESTED': return 'Waiting for the sandbox (or your OK to run the checks on this PC)';
      case 'PROCESSES_KILLED': return 'Stopped ' + d.count + ' running process(es)';
      case 'TASK_REQUEUED': return 'Task ' + d.task_id + ' re-queued after a restart';
      default: return e.kind.replace(/_/g, ' ').toLowerCase() + ' ' + summarizeDetail(e.detail);
    }
  }
  function timelineSection(m) {
    const box = h('div', {}, missionsPage.cache['events:' + m.mission_id] || loading('activity'));
    api.get(missionUrl(m.mission_id, '/events')).then((r) => {
      const out = !r.ok ? unavailable(r, 'Activity') : h('ol', { class: 'timeline' }, r.data.slice().reverse().slice(0, 100).map((e) =>
        h('li', {}, h('time', {}, new Date(e.ts).toLocaleTimeString()), h('span', {}, describeEvent(e)))));
      missionsPage.cache['events:' + m.mission_id] = out;
      clear(box).append(out);
    });
    return box;
  }

  function localRunPanel(m, reload) {
    const lr = m.local_run;
    const wrap = h('div', {});
    api.get('/api/sandbox').then((r) => {
      if (!r.ok || !r.data.managed) return;
      // Windows: the recommended way is HOOD's own sandbox; the mission continues by itself once it's ready.
      wrap.prepend(h('div', {}, h('h4', {}, 'Recommended: HOOD’s Linux sandbox'),
        sandboxPanel(() => setTimeout(reload, 3000), { intro: 'Approve once: HOOD sets up its own Linux system on this PC, runs these checks there (isolated), and this mission continues by itself. You don’t do anything else.' }),
        h('h4', {}, 'Or: run the checks directly on this PC')));
    });
    const actions = h('div', { class: 'form-actions' });
    const box = h('div', { class: 'callout warn' },
      h('b', {}, 'Run the checks on this PC?'),
      h('p', {}, 'The sandbox isn’t ready, so HOOD stopped before running anything. To verify this program it would run these fixed commands in the mission folder:'),
      h('ul', {}, lr.commands.map((c) => h('li', { class: 'mono' }, c))),
      h('p', {}, 'They execute the code the agents wrote directly on this PC, with no network block and no file isolation. API keys are removed from their environment and each step has a time limit. Look at the files below first.'),
      h('p', { class: 'small-note mono' }, 'Files fingerprint ' + String(lr.workspace_sha256).slice(0, 16) + '… (the approval covers these exact files)'), actions);
    if (session.role !== 'ROOT_OWNER') { actions.append(h('p', { class: 'small-note' }, 'Only the Root Owner can approve this.')); wrap.append(box); return wrap; }
    api.get('/api/settings/agents').then((st) => {
      if (st.ok && !st.data.allow_local_run) {
        actions.append(h('p', { class: 'small-note' }, '"Run on my PC" is turned off (Settings › Agents).'),
          h('button', { class: 'btn small', type: 'button', onclick: () => app.render('Settings') }, 'Open Settings'));
        return;
      }
      actions.append(h('button', { class: 'btn small primary', type: 'button', onclick: () => confirmDialog('Run the checks on this PC',
        ['The agent-written code runs directly on this computer, without isolation, for these exact files.',
          'You can stop it at any time with the emergency STOP button.'], 'Run on this PC', async () => {
          const r = await api.post(missionUrl(m.mission_id, '/approve_local_run'), { confirm: true, workspace_sha256: lr.workspace_sha256 });
          if (!r.ok) return r;
          const run = await api.post(missionUrl(m.mission_id, '/run'), { confirm: true });
          toast(run.ok ? 'Running the checks on this PC…' : run.error);
          reload();
          return run;
        }) }, 'Approve and run on this PC'));
    });
    wrap.append(box);
    return wrap;
  }

  function missionDetail(m, reload) {
    const act = (label, path, body, primary) => h('button', { class: 'btn small' + (primary ? ' primary' : ''), type: 'button',
      onclick: async (e) => { e.target.disabled = true; const r = await api.post(missionUrl(m.mission_id, path), body); e.target.disabled = false;
        if (!r.ok) toast(r.error); else toast(label + ': done'); reload(); } }, label);
    const actions = [];
    const toolsMissing = m.needs_tools && !m.needs_tools.ready;
    // Tools the owner already allowed install while the agents work; only unapproved ones hold the plan.
    const toolsUnapproved = toolsMissing && m.needs_tools.unapproved.length > 0;
    if (m.state === 'AWAITING_PLAN_APPROVAL') {
      // One approval starts the work: approve this exact plan and budget, then the agents run.
      actions.push(h('button', { class: 'btn small primary', type: 'button', disabled: toolsUnapproved || null,
        title: toolsUnapproved ? 'Allow the tools above first' : null, onclick: async (e) => {
        e.target.disabled = true;
        const a = await api.post(missionUrl(m.mission_id, '/approve'), { confirm: true, plan_sha256: m.plan_sha256 });
        if (!a.ok) { e.target.disabled = false; toast(a.error); return; }
        const run = await api.post(missionUrl(m.mission_id, '/run'), { confirm: true });
        toast(run.ok ? 'Plan approved; the agents are starting.' : 'Plan approved, but the agents did not start: ' + run.error);
        reload();
      } }, 'Approve plan & start agents'));
    }
    if (['QUEUED', 'RUNNING', 'VERIFYING'].includes(m.state) && !m.background_run_active) actions.push(act('Run agents', '/run', { confirm: true }, true));
    if (m.state === 'BLOCKED' && m.approved_by && !m.waiting_env && !(m.local_run && m.local_run.awaiting_approval)) actions.push(act('Retry blocked work', '/retry', { confirm: true }));
    if (!['COMPLETED', 'FAILED', 'UNVERIFIED', 'CANCELLED'].includes(m.state)) actions.push(act('Cancel', '/cancel', {}));
    if (['FAILED', 'UNVERIFIED', 'CANCELLED'].includes(m.state)) {
      // e.g. a website request from before website missions existed: plan it again as the right kind.
      actions.push(h('button', { class: 'btn small', type: 'button', onclick: () => planMissionDialog(m.objective) },
        WEBSITE_RE.test(m.objective) && m.profile !== 'static_web' ? 'Plan again as a website…' : 'Plan again…'));
    }
    const beforeApprove = m.state === 'AWAITING_PLAN_APPROVAL' && ((m.scope_notes || []).length || (m.plan && (m.plan.clarifications_needed || []).length))
      ? h('div', { class: 'callout warn' }, h('b', {}, 'Before you approve'),
        h('ul', {}, (m.scope_notes || []).map((n) => h('li', {}, n)),
          ((m.plan && m.plan.clarifications_needed) || []).map((q) => h('li', {}, 'The planner assumed or asks: ' + q))))
      : null;
    const running = m.tasks.find((t) => t.state === 'RUNNING');
    const now = m.state === 'PLANNING' ? 'The planner is drafting the task plan.'
      : running ? (ROLE_LABEL[running.role] || running.role) + ' is working on “' + running.title + '”.'
        : m.state === 'VERIFYING' ? 'The independent check is running.'
          : m.state === 'AWAITING_PLAN_APPROVAL' ? 'Waiting for you: read the plan below, then approve it to start the agents.'
            : m.waiting_env && /Waiting for HOOD|Installing/.test(m.error || '') ? 'Waiting for the sandbox/tools you approved: the mission continues by itself when they’re ready.'
              : m.local_run && m.local_run.awaiting_approval ? 'Waiting for your decision: approve HOOD’s sandbox (recommended) or running the checks on this PC.'
              : m.state === 'QUEUED' || (m.state === 'RUNNING' && !m.background_run_active) ? 'Ready: press “Run agents”.' : null;
    return h('div', {},
      now ? h('div', { class: 'now-line' }, m.background_run_active || m.state === 'PLANNING' ? h('span', { class: 'pulse', 'aria-hidden': 'true' }) : null, now) : null,
      h('dl', { class: 'kv' },
        h('dt', {}, 'State'), h('dd', {}, badge(m.state), m.background_run_active ? ' (running now)' : ''),
        h('dt', {}, 'Kind'), h('dd', {}, PROFILE_LABEL[m.profile] || m.profile),
        h('dt', {}, 'Model output'), h('dd', {}, badge(m.provider_mode === 'NONE' ? 'unknown' : m.provider_mode, m.provider_mode === 'LIVE' ? 'live AI' : m.provider_mode === 'NONE' ? 'none yet' : m.provider_mode.toLowerCase())),
        h('dt', {}, 'Objective'), h('dd', {}, m.objective.length > 400 ? h('details', {}, h('summary', {}, clip(m.objective, 300)), h('p', { class: 'pre-wrap' }, m.objective)) : h('span', { class: 'pre-wrap' }, m.objective)),
        h('dt', {}, 'Spend / cap'), h('dd', {}, money(m.spend) + ' / ' + money(m.budget_usd)),
        h('dt', {}, 'Repairs'), h('dd', {}, String(m.repairs)),
        m.error ? h('dt', {}, 'Reason') : null, m.error ? h('dd', {}, reasonBlock(m.error)) : null),
      toolsMissing && !['COMPLETED', 'CANCELLED'].includes(m.state) ? toolsPanel(m.needs_tools, reload) : null,
      beforeApprove,
      h('div', { class: 'form-actions' }, actions),
      m.local_run && m.local_run.awaiting_approval ? localRunPanel(m, reload) : null,
      m.artifact ? h('div', { class: 'callout good' }, h('b', {}, 'Verified result ready'),
        h('div', { class: 'form-actions' },
          h('a', { class: 'btn small primary', href: missionUrl(m.mission_id, '/artifact'), download: m.artifact.name }, 'Download verified result (' + m.artifact.bytes + ' bytes)'),
          m.preview_path ? h('a', { class: 'btn small', href: m.preview_path, target: '_blank', rel: 'noopener noreferrer' }, 'Open website preview') : null),
        h('p', { class: 'mono small-note' }, 'SHA-256 ' + m.artifact.sha256)) : null,
      section('progress', 'Progress', () => pipeline(m)),
      m.plan ? section('plan', 'Plan', () => h('div', {}, h('p', { class: 'pre-wrap' }, m.plan.summary), h('p', { class: 'small-note' }, 'Deliverable: ' + m.plan.deliverable),
        m.plan.interface_contract ? h('pre', { class: 'file-view wrap' }, m.plan.interface_contract) : null,
        m.plan_sha256 ? h('p', { class: 'mono small-note' }, 'Plan hash ' + m.plan_sha256) : null), null, m.state === 'AWAITING_PLAN_APPROVAL') : null,
      section('outputs', 'What the agents produced', () => agentOutputs(m)),
      section('checks', 'Independent check', () => checksSection(m), m.last_verification ? m.last_verification.verdict : null),
      section('files', 'Files and preview', () => filesSection(m)),
      section('activity', 'Activity', () => timelineSection(m)));
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

  // ------------------------------------------------------------------ System map (areas → parts → missions → tasks)
  // Health buckets for the map: green works, amber needs you (setup, approval, degraded), red failing,
  // grey not built / not connected / unknown.
  const HEALTH = {
    working: ['verified_online', 'available', 'active', 'online', 'COMPLETED'],
    attention: ['not_configured', 'needs_pricing', 'awaiting_approval', 'blocked', 'checking', 'degraded',
      'AWAITING_PLAN_APPROVAL', 'BLOCKED', 'UNVERIFIED'],
    failing: ['failed', 'offline', 'FAILED'],
    busy: ['RUNNING', 'VERIFYING', 'PLANNING', 'QUEUED'],
  };
  const HEALTH_COLOR = { working: '#43e3a3', partial: '#2f9b78', attention: '#f5c542', failing: '#ff7f92', busy: '#6fd3ff', other: '#8fa3bf' };
  const HEALTH_WORD = { working: 'working', partial: 'partly working', attention: 'needs you', failing: 'failing', busy: 'in progress',
    other: 'not built / unknown' };
  const HEALTH_GLYPH = { working: '✓', partial: '◐', attention: '!', failing: '✕', busy: '…', other: '?' };
  const healthOf = (st) => Object.keys(HEALTH).find((k) => HEALTH[k].includes(st)) || 'other';
  const NEXT_STEP = {
    gemini: ['Settings', 'Set the Gemini key and prices in Settings › Model provider, then press Test connection.'],
    conversation: ['Settings', 'Chat needs a working AI model: Settings › Model provider.'],
    voice: ['Settings', 'Pick a voice in Settings › Voice (this computer’s voice works offline).'],
    orchestrator: ['Settings', 'Website missions work here. For Python and WordPress missions, approve HOOD’s Linux sandbox once in Settings › Agents: HOOD sets it up itself.'],
  };
  const graphView = {
    zoom: 1, pan: { x: 0, y: 0 }, path: ['root'],
    get focus() { return this.path[this.path.length - 1]; },
    byId(data, id) { return data.nodes.find((n) => n.id === id); },
    caps(data, area) { return data.nodes.filter((n) => n.type === 'capability' && n.area === area); },
    summary(nodes) {
      const c = { working: 0, attention: 0, failing: 0, busy: 0, other: 0 };
      nodes.forEach((n) => { c[healthOf(n.state)] += 1; });
      const health = c.failing ? 'failing' : c.attention ? 'attention' : c.busy ? 'busy'
        : c.working && c.working === nodes.length ? 'working' : c.working ? 'partial' : 'other';
      return { counts: c, health, sub: c.working + '/' + nodes.length + ' working' + (c.attention ? ' · ' + c.attention + ' need you' : '') };
    },
    missions(data) { return data.nodes.filter((n) => n.type === 'mission'); },
    node(data, id) {
      if (id === 'root') {
        const sum = this.summary(data.nodes.filter((n) => n.type === 'capability'));
        return { id, type: 'core', label: 'HOOD', sub: sum.sub, health: sum.health, counts: sum.counts };
      }
      if (id === 'grp:agent') {
        const agents = data.nodes.filter((n) => n.type === 'agent');
        const busy = data.nodes.filter((n) => n.type === 'task' && n.state === 'RUNNING').length;
        return { id, type: 'group', label: 'Agents', sub: busy ? busy + ' working now' : agents.length + ' roles, idle',
          health: busy ? 'busy' : 'working', description: 'The specialist agents that do mission work.' };
      }
      if (id === 'grp:mission') {
        const ms = this.missions(data);
        const sum = this.summary(ms);
        return { id, type: 'group', label: 'Missions', sub: ms.length + ' yours', health: ms.length ? sum.health : 'other',
          counts: sum.counts, description: 'Your agent missions, newest first.' };
      }
      const n = this.byId(data, id);
      if (!n) return null;
      if (n.type === 'area') {
        const sum = this.summary(this.caps(data, id.slice(5)));
        return { ...n, sub: sum.sub, health: sum.health, counts: sum.counts };
      }
      if (n.type === 'agent') {
        const tasks = data.nodes.filter((t) => t.type === 'task' && t.role === id.slice(6));
        const busy = tasks.filter((t) => t.state === 'RUNNING').length;
        return { ...n, sub: busy ? busy + ' task running' : tasks.length + ' tasks done/queued', health: busy ? 'busy' : 'working' };
      }
      if (n.type === 'mission') return { ...n, label: missionTitle(n.description || n.label), sub: String(n.state).replace(/_/g, ' ').toLowerCase(), health: healthOf(n.state) };
      if (n.type === 'task') return { ...n, sub: (ROLE_LABEL[n.role] || n.role) + ' · ' + String(n.state).toLowerCase(), health: healthOf(n.state) };
      return { ...n, sub: String(n.state || '').replace(/_/g, ' '), health: healthOf(n.state) };
    },
    children(data, id) {
      let ids = [];
      if (id === 'root') ids = data.nodes.filter((n) => n.type === 'area').map((n) => n.id);
      else if (id === 'area:build') ids = this.caps(data, 'build').map((n) => n.id).concat(['grp:agent', 'grp:mission']);
      else if (id.startsWith('area:')) ids = this.caps(data, id.slice(5)).map((n) => n.id);
      else if (id === 'cap:orchestrator') ids = ['grp:agent', 'grp:mission'];
      else if (id === 'grp:agent') ids = data.nodes.filter((n) => n.type === 'agent').map((n) => n.id);
      else if (id === 'grp:mission') ids = this.missions(data).map((n) => n.id);
      else if (id.startsWith('mission:')) ids = data.nodes.filter((n) => n.type === 'task' && n.mission === id).map((n) => n.id);
      else if (id.startsWith('agent:')) {
        ids = data.nodes.filter((n) => n.type === 'task' && n.role === id.slice(6))
          .sort((a, b) => (b.state === 'RUNNING') - (a.state === 'RUNNING')).slice(0, 16).map((n) => n.id);
      }
      return ids.map((x) => this.node(data, x)).filter(Boolean);
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
      const nodeEl = (n, x, y, r, isCenter) => {
        const color = HEALTH_COLOR[n.health] || HEALTH_COLOR.other;
        const g = s('g', { class: 'graph-node', transform: `translate(${x} ${y})`, tabindex: '0', role: 'button',
          'aria-label': n.label + ': ' + (n.sub || '') + (isCenter ? ' (current)' : '') });
        g.append(s('circle', { class: 'graph-halo', r: String(r), fill: '#0d1c31', stroke: color, 'stroke-width': isCenter ? '2.6' : '1.8' }));
        const kidsCount = isCenter ? 0 : this.children(data, n.id).length;
        // Inside the circle: a count (areas, groups) or a status sign; the name and status go underneath.
        const total = n.counts ? Object.values(n.counts).reduce((a, b) => a + b, 0) : 0;
        const mark = s('text', { class: 'graph-mark', 'text-anchor': 'middle', y: String(isCenter ? 8 : 6), fill: color,
          'font-size': String(isCenter ? 22 : mini ? 13 : 16) });
        mark.textContent = total ? n.counts.working + '/' + total : HEALTH_GLYPH[n.health] || '?';
        const t = s('text', { class: 'graph-label', 'text-anchor': 'middle', y: String(r + 17), 'font-size': mini ? '11' : '13' });
        t.textContent = clip(n.label, isCenter ? 34 : 22);
        g.append(mark, t);
        if (!mini) {
          const sub = s('text', { class: 'graph-sub', 'text-anchor': 'middle', y: String(r + 31) }); sub.textContent = clip(n.sub || '', 30);
          g.append(sub);
        }
        if (kidsCount && !mini) { const more = s('text', { class: 'graph-sub', 'text-anchor': 'middle', y: String(r + 45) }); more.textContent = kidsCount + ' inside ›'; g.append(more); }
        const go = () => {
          if (onSelect) { onSelect(n.id); return; }
          graphView.path = n.id === 'root' ? ['root'] : ['root', n.id];
          app.render('Intelligence');
        };
        g.addEventListener('click', go);
        g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        return g;
      };
      kids.forEach((n, i) => {
        const a = (i * 2 * Math.PI) / Math.max(1, kids.length) - Math.PI / 2;
        const rx = kids.length > 12 ? 330 : 260, ry = kids.length > 12 ? 230 : 190;
        const x = cx + Math.cos(a) * rx, y = cy + Math.sin(a) * ry;
        vp.append(s('path', { class: 'graph-edge', d: `M${cx} ${cy} Q${(cx + x) / 2 + 18} ${(cy + y) / 2 - 24} ${x} ${y}`,
          stroke: HEALTH_COLOR[n.health] || HEALTH_COLOR.other, 'stroke-opacity': '.35' }));
        vp.append(nodeEl(n, x, y, mini ? 22 : (kids.length > 12 ? 24 : 32), false));
      });
      vp.append(nodeEl(center, cx, mini ? cy - 10 : cy - 16, mini ? 34 : 46, true));
      if (!kids.length && !mini) {
        const t = s('text', { 'text-anchor': 'middle', x: String(cx), y: String(cy + 90), class: 'graph-sub' }); t.textContent = 'Nothing inside. Press Esc or ← Back to go up.'; vp.append(t);
      }
      holder.append(svg);
      return svg;
    },
  };

  function intelligenceView() {
    const canvas = h('div', { style: { height: '100%' }, id: 'networkCanvas' });
    const crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Where you are on the map' });
    const backBtn = h('button', { class: 'btn small', type: 'button', title: 'Back one step (Esc)', onclick: () => back() }, '← Back');
    const inspector = h('div', { class: 'card-body' });
    let data = null;
    const hashFor = (path) => '#Intelligence' + (path.length > 1 ? '/' + path.slice(1).map(encodeURIComponent).join('/') : '');
    const fromHash = decodeURIComponent(location.hash.slice(1)).split('/').slice(1).filter(Boolean);
    if (fromHash.length) graphView.path = ['root', ...fromHash];
    const label = (id) => { const n = data && graphView.node(data, id); return n ? clip(n.label, 34) : id; };
    const renderCrumbs = () => {
      clear(crumbs).append(...graphView.path.map((id, i) => [i ? h('span', { class: 'crumb-sep', 'aria-hidden': 'true' }, '›') : null,
        i === graphView.path.length - 1 ? h('b', { 'aria-current': 'location' }, label(id))
          : h('button', { class: 'linkish', type: 'button', onclick: () => go(id) }, label(id))]).flat().filter(Boolean));
      backBtn.disabled = graphView.path.length <= 1;
    };
    const renderInspector = (id) => {
      const n = graphView.node(data, id);
      if (!n) { clear(inspector).append(emptyBox('This item no longer exists.')); return; }
      const kids = graphView.children(data, id);
      const counts = n.counts ? h('div', { class: 'health-counts' }, Object.entries(n.counts).filter(([, v]) => v).map(([k, v]) =>
        h('span', { class: 'hc', style: { '--c': HEALTH_COLOR[k] } }, v + ' ' + HEALTH_WORD[k]))) : null;
      const next = n.type === 'capability' && n.health !== 'working' ? NEXT_STEP[id.slice(4)] : null;
      const attention = kids.filter((k) => ['attention', 'failing'].includes(k.health));
      clear(inspector).append(h('div', {},
        h('div', { class: 'eyebrow' }, { core: 'WHOLE SYSTEM', area: 'AREA', group: 'GROUP', capability: 'PART OF HOOD', agent: 'AGENT', mission: 'MISSION', task: 'AGENT TASK' }[n.type] || 'ITEM'),
        h('h2', {}, n.type === 'mission' ? clip(n.description || n.label, 140) : n.label),
        ['mission', 'task'].includes(n.type) ? h('p', {}, badge(n.state), n.type === 'task' ? h('small', {}, '  ' + (ROLE_LABEL[n.role] || n.role)) : null)
          : h('p', {}, h('span', { class: 'state', style: { color: HEALTH_COLOR[n.health] } }, HEALTH_WORD[n.health] || 'unknown'),
            n.state && n.type === 'capability' ? h('small', {}, '  (' + String(n.state).replace(/_/g, ' ') + ')') : null),
        n.description && n.type !== 'mission' ? h('p', {}, n.description) : null,
        n.type === 'core' ? h('p', {}, 'Everything HOOD has, grouped by what you use it for. Click an area to look inside; Esc or ← Back goes up one step.') : null,
        n.detail ? h('p', { class: 'small-note' }, 'Live: ' + n.detail) : null,
        counts,
        n.type === 'mission' ? h('dl', { class: 'kv' }, h('dt', {}, 'Kind'), h('dd', {}, KIND_SHORT[n.profile] || 'Python program'),
          h('dt', {}, 'Updated'), h('dd', {}, fmtTime(n.updated))) : null,
        next ? h('div', { class: 'callout warn' }, h('b', {}, 'Next step'), h('p', {}, next[1]),
          h('button', { class: 'btn small primary', type: 'button', onclick: () => app.render(next[0]) }, 'Open ' + next[0])) : null,
        attention.length && n.type !== 'capability' ? h('div', {}, h('div', { class: 'eyebrow' }, 'NEEDS YOU'),
          h('ul', { class: 'file-list' }, attention.slice(0, 8).map((k) => h('li', {}, h('button', { class: 'linkish', type: 'button', onclick: () => go(k.id) }, k.label),
            h('small', {}, ' · ' + (k.sub || '')))))) : null,
        h('div', { class: 'form-actions' },
          id.startsWith('mission:') ? h('button', { class: 'btn small primary', type: 'button', onclick: () => { missionsPage.selected = id.slice(8); app.render('Missions'); } }, 'Open mission') : null,
          id.startsWith('task:') ? h('button', { class: 'btn small primary', type: 'button', onclick: () => { missionsPage.selected = id.split(':')[1]; app.render('Missions'); } }, 'Open its mission') : null,
          n.type === 'capability' && n.page && n.page !== 'Intelligence' ? h('button', { class: 'btn small', type: 'button', onclick: () => app.render(n.page) }, 'Open ' + n.page + ' page') : null,
          graphView.path.length > 1 ? h('button', { class: 'btn small', type: 'button', onclick: () => back() }, '← Back') : null)));
    };
    const show = () => {
      if (!data) return;
      if (!graphView.node(data, graphView.focus)) graphView.path = ['root'];   // item vanished (e.g. mission deleted)
      graphView.draw(canvas, data, graphView.focus, false, (id) => go(id));
      renderCrumbs();
      renderInspector(graphView.focus);
    };
    // Drill down / jump: a new browser history entry, so the browser's Back button also goes back one step.
    const go = (id) => {
      if (id === graphView.focus) return;
      const at = graphView.path.indexOf(id);
      graphView.path = id === 'root' ? ['root'] : at >= 0 ? graphView.path.slice(0, at + 1) : [...graphView.path, id];
      graphView.zoom = 1; graphView.pan = { x: 0, y: 0 };
      history.pushState({ hoodMap: true }, '', hashFor(graphView.path));
      show();
    };
    // Back one step: undo the last move when it was ours, otherwise go up to the parent.
    const back = () => {
      if (graphView.path.length <= 1) return;
      if (history.state && history.state.hoodMap) { history.back(); return; }   // hashchange → subroute
      graphView.path = graphView.path.slice(0, -1);
      history.replaceState(null, '', hashFor(graphView.path));
      show();
    };
    app.subroute = (rest) => { graphView.path = ['root', ...rest]; graphView.zoom = 1; graphView.pan = { x: 0, y: 0 }; show(); };
    const onKey = (e) => {
      if (e.key !== 'Escape' || !$('modalBackdrop').classList.contains('hidden')) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '')) return;
      back();
    };
    document.addEventListener('keydown', onKey);
    app.onCleanup(() => document.removeEventListener('keydown', onKey));
    const load = async () => {
      const r = await api.get('/api/console/graph?limit=50');
      if (!r.ok) { clear(canvas).append(unavailable(r, 'System map')); return; }
      data = r.data;
      if (location.hash.split('/')[0] === '#Intelligence') history.replaceState(history.state, '', hashFor(graphView.path));
      show();
    };
    const zoomBy = (f) => { graphView.zoom = Math.min(3, Math.max(0.4, graphView.zoom * f)); if (data) graphView.draw(canvas, data, graphView.focus, false, go); };
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.1 : 0.9); }, { passive: false });
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => { if (e.target.closest('.graph-node')) return; drag = { x: e.clientX, y: e.clientY, px: graphView.pan.x, py: graphView.pan.y }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', (e) => { if (!drag || !data) return; graphView.pan = { x: drag.px + (e.clientX - drag.x), y: drag.py + (e.clientY - drag.y) }; graphView.draw(canvas, data, graphView.focus, false, go); });
    canvas.addEventListener('pointerup', () => { drag = null; });
    load();
    app.onCleanup(feed.subscribe(() => { clearTimeout(graphView.t); graphView.t = setTimeout(load, 1500); }));
    const legend = h('div', { class: 'map-legend' }, ['working', 'attention', 'failing', 'busy', 'other'].map((k) =>
      h('span', { class: 'hc', style: { '--c': HEALTH_COLOR[k] } }, HEALTH_WORD[k])));
    return h('div', {}, head('System map', 'What HOOD has, what works right now, and what needs you. Click to look inside; Esc or ← Back goes up one step.',
      h('button', { class: 'btn', type: 'button', onclick: () => go('root') }, '⌂ Whole system')),
    h('div', { class: 'map-bar' }, backBtn, crumbs),
    h('div', { class: 'network-shell' },
      h('div', { class: 'card graph-card' }, h('div', { class: 'graph-controls' },
        h('button', { type: 'button', title: 'Back one step (Esc)', 'aria-label': 'Back one step', onclick: () => back() }, '←'),
        h('button', { type: 'button', title: 'Zoom in', 'aria-label': 'Zoom in', onclick: () => zoomBy(1.2) }, '＋'),
        h('button', { type: 'button', title: 'Zoom out', 'aria-label': 'Zoom out', onclick: () => zoomBy(1 / 1.2) }, '−'),
        h('button', { type: 'button', title: 'Whole system', 'aria-label': 'Whole system', onclick: () => go('root') }, '⌂')), canvas, legend),
      h('section', { class: 'card graph-inspector' }, h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, 'Details'), badge('available', 'live data')), inspector)),
    h('div', { style: { marginTop: '14px' } }, liveDataCard()));
  }

  // ------------------------------------------------------------------ Intelligence: live data (Phase 4)
  // What HOOD actually uses, spends and achieves, from its own stores. Nothing estimated or invented.
  const perMillion = (p) => (p === null || p === undefined) ? 'no price set' : (p.input_per_1k_usd === 0 && p.output_per_1k_usd === 0)
    ? 'free tier' : '$' + (p.input_per_1k_usd * 1000).toFixed(2) + ' in / $' + (p.output_per_1k_usd * 1000).toFixed(2) + ' out per 1M tokens';
  function liveDataCard() {
    const box = h('div', {});
    fill(box, async () => {
      const r = await api.get('/api/intelligence/live');
      if (!r.ok) return unavailable(r, 'Live data');
      const d = r.data, m = d.models || {}, s = d.spend || {}, ms = d.missions || {}, env = d.environment || {}, sr = d.self_repair || {};
      const classes = Object.entries(m.classes || {});
      const seen = m.observed || {};
      return h('div', {},
        h('p', { class: 'small-note' }, 'Measured ' + fmtTime(d.measured_at) + ' from ' + d.sources + '.'),
        h('div', { class: 'two-col' },
          h('div', {}, h('h3', { class: 'eyebrow' }, 'AI MODELS IN USE'),
            classes.length ? h('dl', { class: 'kv' }, classes.map(([, c]) => [h('dt', {}, c.role),
              h('dd', {}, h('b', {}, c.model), ' · ', perMillion((m.prices || {})[c.model]),
                c.fallbacks.length ? h('small', { class: 'small-note' }, ' (if busy: ' + c.fallbacks.join(', ') + ')') : null)]))
              : emptyBox('No AI provider configured. Set it in Settings › Model provider.'),
            seen.last_success_at || seen.last_error ? h('p', { class: 'small-note' }, seen.last_success_at ? 'Last answer ' + fmtTime(seen.last_success_at) + '. ' : '',
              seen.last_error ? 'Last problem: ' + clip(seen.last_error, 160) : '') : null),
          h('div', {}, h('h3', { class: 'eyebrow' }, 'SPEND (DURABLE LEDGER)'),
            !s.available ? emptyBox('Spend tracking is not attached.') : h('div', {},
              h('dl', { class: 'kv' }, h('dt', {}, 'Today'), h('dd', {}, money(s.daily_spend_usd) + ' of ' + money(s.max_daily_limit_usd)),
                h('dt', {}, 'This month'), h('dd', {}, money(s.monthly_spend_usd) + ' of ' + money(s.max_monthly_limit_usd)),
                s.reserved_usd ? [h('dt', {}, 'Reserved now'), h('dd', {}, money(s.reserved_usd))] : null),
              s.ledger_error ? h('div', { class: 'callout warn' }, 'The spend ledger could not be written: ' + s.ledger_error) : null,
              s.note ? h('p', { class: 'small-note' }, s.note) : null,
              (s.by_model || []).length ? h('table', { class: 'mini-table' }, h('thead', {}, h('tr', {}, h('th', {}, 'Model (30 days)'), h('th', {}, 'Calls'), h('th', {}, 'Tokens in / out'), h('th', {}, 'Cost'))),
                h('tbody', {}, s.by_model.map((x) => h('tr', {}, h('td', {}, x.model), h('td', {}, String(x.calls)),
                  h('td', {}, x.prompt_tokens.toLocaleString() + ' / ' + x.completion_tokens.toLocaleString()), h('td', {}, money(x.cost_usd)))))) : null,
              (s.recent || []).length ? h('details', {}, h('summary', {}, 'Last ' + s.recent.length + ' AI calls'), h('ol', { class: 'call-list' }, s.recent.map((x) =>
                h('li', {}, h('time', {}, fmtTime(x.ts)), h('span', {}, x.kind + ' · ' + x.model + ' · ' + x.tokens.toLocaleString() + ' tokens · ' + money(x.cost_usd) + (x.measured ? '' : ' (not reported by the provider: full reservation charged)')))))) : null))),
        h('div', { class: 'two-col' },
          h('div', {}, h('h3', { class: 'eyebrow' }, 'MISSIONS'),
            !ms.available ? emptyBox('The agent engine is not attached.') : !ms.total ? emptyBox('No missions yet.') : h('div', {},
              h('dl', { class: 'kv' }, h('dt', {}, 'Total'), h('dd', {}, String(ms.total)),
                h('dt', {}, 'Success rate'), h('dd', {}, ms.success_rate === null ? 'none finished yet' : Math.round(ms.success_rate * 100) + '% of finished missions'),
                h('dt', {}, 'Typical duration'), h('dd', {}, ms.median_completed_seconds === null ? 'none completed yet' : Math.round(ms.median_completed_seconds / 60) + ' min (median)')),
              h('p', {}, Object.entries(ms.by_state || {}).map(([k, v]) => [badge(k, v + ' ' + k.replace(/_/g, ' ').toLowerCase()), ' '])))),
          h('div', {}, h('h3', { class: 'eyebrow' }, 'ENVIRONMENT'),
            h('dl', { class: 'kv' }, h('dt', {}, 'Sandbox'), h('dd', {}, env.sandbox_ready ? badge('available', 'ready') : h('span', {}, badge('UNVERIFIED', 'not ready'), ' ', h('small', {}, clip(env.sandbox_problem || '', 160)))),
              env.tools ? [h('dt', {}, 'Tools'), h('dd', {}, env.tools.error ? 'unknown (' + clip(env.tools.error, 80) + ')' : env.tools.installed + ' installed, ' + env.tools.approved + ' allowed, of ' + env.tools.known)] : null,
              h('dt', {}, 'Self-repair'), h('dd', {}, !sr.available ? 'not attached' : Object.keys(sr.by_state || {}).length
                ? Object.entries(sr.by_state).map(([k, v]) => v + ' ' + (REPAIR_STATE[k] ? REPAIR_STATE[k][1] : k).toLowerCase()).join(', ') : 'no reports yet')))));
    }, (x) => x);
    return card('Live data', box, { right: h('button', { class: 'btn small', type: 'button', onclick: () => app.render('Intelligence') }, '↻ Refresh') });
  }

  // ------------------------------------------------------------------ Self-repair (Phase 4)
  // The owner reports a problem with HOOD; HOOD looks for the cause in its own code, proves a fix in
  // its sandbox, and changes nothing until the Root Owner approves that exact fix (undo available).
  const REPAIR_STATE = {
    INVESTIGATING: ['RUNNING', 'Looking for the cause'], VERIFYING: ['VERIFYING', 'Testing the fix'],
    NEEDS_DECISION: ['awaiting_approval', 'Fix ready: your decision'], AWAITING_LOCAL_RUN: ['PENDING', 'Waiting: where to test'],
    NO_RELIABLE_FIX: ['UNVERIFIED', 'No reliable fix found'], APPLIED: ['COMPLETED', 'Applied'], UNDONE: ['muted', 'Undone'],
    DISCARDED: ['muted', 'Discarded'], FAILED: ['FAILED', 'Stopped'],
  };
  const repairBadge = (s) => { const [k, label] = REPAIR_STATE[s] || [s, s]; return badge(k, label); };
  const REPAIR_CHECK = { reproduces_the_problem: 'The new test fails on today’s code (it reproduces the problem)',
    fix_makes_it_pass: 'With the fix, the new test passes', whole_suite_still_passes: 'All of HOOD’s other tests still pass' };
  const repairPage = { selected: null };
  const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

  function reportProblemDialog(prefill) {
    if (session.role !== 'ROOT_OWNER') { toast('Only the Root Owner can ask HOOD to repair itself.'); return; }
    const text = h('textarea', { class: 'big', rows: '6', maxlength: '8000', 'aria-label': 'What is wrong',
      placeholder: 'Where (page, button), what you did, what you expected, what happened. Copy any error message exactly.' });
    text.value = prefill || '';
    const file = h('input', { type: 'file', accept: IMAGE_TYPES.join(','), 'aria-label': 'Screenshot (optional)' });
    const preview = h('div', {});
    const consent = h('input', { type: 'checkbox', 'aria-label': 'Send this screenshot to the AI provider' });
    const consentLine = h('label', { class: 'switchline hidden' },
      h('span', {}, h('b', {}, 'Send this screenshot to the AI provider'), h('small', {}, 'Check it shows nothing private (passwords, keys, personal data).')), consent);
    let shot = null;
    file.addEventListener('change', () => {
      shot = null; clear(preview); consentLine.classList.add('hidden'); consent.checked = false;
      const f = file.files && file.files[0];
      if (!f) return;
      if (!IMAGE_TYPES.includes(f.type)) { preview.append(errorBox('Use a PNG, JPEG or WebP image.')); return; }
      if (f.size > 4 * 1024 * 1024) { preview.append(errorBox('The screenshot must be under 4 MB.')); return; }
      const reader = new FileReader();
      reader.onload = () => {
        const url = String(reader.result);
        shot = { mime: f.type, b64: url.slice(url.indexOf(',') + 1) };
        preview.append(h('img', { src: url, alt: 'Your screenshot', class: 'repair-shot' }));
        consentLine.classList.remove('hidden');
      };
      reader.readAsDataURL(f);
    });
    confirmDialog('Report a problem with HOOD', [
      'HOOD looks for the cause in its own code: it sends your description and short excerpts of its code (secrets removed) to the AI model.',
      'If it finds a fix it is sure about, it proves it in its sandbox first: a new test must fail today and pass with the fix, and all of HOOD’s other tests must still pass.',
      'Nothing changes until you approve the exact change. If HOOD isn’t sure, it says so instead of guessing.'],
    'Investigate', async () => {
      const body = { text: text.value.trim(), confirm: true };
      if (body.text.length < 10) return { error: 'Describe the problem in a sentence or two.' };
      if (shot) {
        if (!consent.checked) return { error: 'Tick “Send this screenshot to the AI provider”, or remove the screenshot.' };
        Object.assign(body, { screenshot_b64: shot.b64, screenshot_mime: shot.mime, screenshot_consent: true });
      }
      const r = await api.post('/api/selfrepair/report', body);
      if (!r.ok) return r;
      repairPage.selected = r.data.id;
      toast('HOOD is looking into it.');
      app.render('Repair');
      return r;
    }, h('div', { class: 'form' }, h('label', {}, 'What is wrong', text), h('label', {}, 'Screenshot (optional)', file), preview, consentLine));
  }

  // A unified diff as coloured lines (text nodes only).
  const diffView = (diff) => h('pre', { class: 'file-view diff-view' }, String(diff).split('\n').map((l) =>
    h('span', { class: /^(\+\+\+|---)/.test(l) ? 'd-file' : l.startsWith('@@') ? 'd-hunk' : l.startsWith('+') ? 'd-add' : l.startsWith('-') ? 'd-del' : null }, l + '\n')));

  function repairDetail(x, reload) {
    const d = x.diagnosis || {};
    const root = session.role === 'ROOT_OWNER';
    const post = (path, body, okText) => async () => {
      const r = await api.post('/api/selfrepair/' + x.id + path, body);
      if (!r.ok) { toast(r.error); return r; }
      if (okText) toast(okText);
      reload();
      return r;
    };
    const running = ['INVESTIGATING', 'VERIFYING'].includes(x.state);
    const files = x.diff ? (x.diff.match(/^\+\+\+ (?!b\/tests\/selfrepair\/).*/gm) || []).length : 0;   // the new test is extra
    const actions = h('div', { class: 'form-actions' });
    if (root && x.state === 'NEEDS_DECISION') actions.append(h('button', { class: 'btn primary', type: 'button', onclick: () => confirmDialog('Apply this fix?', [
      'HOOD changes ' + files + ' file(s) exactly as shown under “The change”, and adds the new test. A restore point is kept; you can undo it.',
      'Python changes take effect after HOOD restarts; console changes after you reload the page.'],
    'Apply fix', post('/apply', { proposal_sha: x.proposal_sha, confirm: true }, 'Fix applied.')) }, '✓ Apply fix'));
    if (root && x.state === 'AWAITING_LOCAL_RUN') actions.append(h('button', { class: 'btn primary', type: 'button', onclick: () => confirmDialog('Run the checks on this computer?', [
      'HOOD’s sandbox isn’t available here, so the fix could not be tested in isolation.',
      'HOOD can run its tests with the fix on this computer, in a temporary copy of its code (your HOOD files are not changed). The tests run without the sandbox’s isolation.',
      'This needs “Run on my PC” to be on (Settings › Agents).'],
    'Run checks here', post('/run_checks_here', { confirm: true }, 'Running the checks…')) }, '▶ Run checks on this PC'));
    if (root && ['APPLIED', 'UNDONE'].includes(x.state) && x.needs_restart) actions.append(h('button', { class: 'btn primary', type: 'button', onclick: () => confirmDialog('Restart HOOD now?', [
      'HOOD stops for a few seconds and starts again with its code as it is now. Anything running right now (missions, installs) is interrupted.',
      'Reload this page after about 10 seconds.'],
    'Restart HOOD', async () => { const r = await api.post('/api/selfrepair/restart', { confirm: true }); if (r.ok) toast(r.data.message); return r; }) }, '↻ Restart HOOD'));
    if (root && x.state === 'APPLIED') actions.append(h('button', { class: 'btn', type: 'button', onclick: () => confirmDialog('Undo this fix?', [
      'HOOD puts back its code exactly as it was before this fix and removes the test it added.'], 'Undo fix', post('/undo', { confirm: true }, 'Fix undone.')) }, '↶ Undo'));
    if (root && ['NEEDS_DECISION', 'AWAITING_LOCAL_RUN', 'NO_RELIABLE_FIX', 'FAILED'].includes(x.state)) actions.append(
      h('button', { class: 'btn', type: 'button', onclick: post('/discard', {}, 'Report discarded.') }, 'Discard'));
    return h('div', {},
      h('p', {}, repairBadge(x.state), ' ', h('small', {}, 'Reported ' + fmtTime(x.created) + ' by ' + x.owner)),
      h('h3', { class: 'eyebrow' }, 'YOUR REPORT'), h('p', { class: 'pre-wrap' }, x.report),
      x.has_screenshot ? h('details', {}, h('summary', {}, 'Your screenshot' + (x.screenshot_note ? ' and what HOOD saw in it' : '')),
        h('img', { src: '/api/selfrepair/' + x.id + '/screenshot', alt: 'The screenshot you sent', class: 'repair-shot' }),
        x.screenshot_note ? h('p', { class: 'pre-wrap small-note' }, x.screenshot_note) : null) : null,
      running ? h('div', { class: 'callout' }, h('b', {}, x.state === 'VERIFYING' ? 'Proving the fix in the sandbox…' : 'Looking for the cause…'),
        h('p', { class: 'small-note' }, 'This takes one to a few minutes (the whole test suite runs). You can leave this page.')) : null,
      x.state === 'NO_RELIABLE_FIX' ? h('div', { class: 'callout warn' }, h('b', {}, 'HOOD couldn’t find a reliable fix'), h('p', {}, d.why_not || 'No reason recorded.'),
        h('p', { class: 'small-note' }, 'Nothing was changed. The exact error text or a screenshot can help; deeper problems need a stronger AI model or a developer.')) : null,
      x.state === 'FAILED' ? h('div', { class: 'callout warn' }, h('b', {}, 'The investigation stopped'), h('p', {}, x.error || 'Unknown error.'), h('p', { class: 'small-note' }, 'Nothing was changed.')) : null,
      x.state === 'AWAITING_LOCAL_RUN' ? h('div', { class: 'callout warn' }, h('b', {}, 'HOOD has a fix but couldn’t prove it in its sandbox'), h('p', {}, x.error || 'The sandbox isn’t available here.'),
        h('p', { class: 'small-note' }, 'You choose: run the checks on this PC, or discard. Nothing was changed.')) : null,
      x.state === 'APPLIED' ? h('div', { class: 'callout good' }, h('b', {}, 'Applied'), h('p', {}, x.needs_restart ? 'Restart HOOD so the fix takes effect.' : 'Reload the page to see the change.')) : null,
      x.state === 'UNDONE' && x.needs_restart ? h('div', { class: 'callout' }, 'Undone. Restart HOOD so its previous code is loaded again.') : null,
      d.summary_for_owner || d.understood_problem ? h('div', {}, h('h3', { class: 'eyebrow' }, 'WHAT HOOD FOUND'),
        d.summary_for_owner ? h('p', {}, d.summary_for_owner) : null,
        h('dl', { class: 'kv' }, d.understood_problem ? [h('dt', {}, 'The problem'), h('dd', {}, d.understood_problem)] : null,
          d.root_cause ? [h('dt', {}, 'Cause'), h('dd', {}, d.root_cause)] : null,
          d.confidence ? [h('dt', {}, 'Confidence'), h('dd', {}, d.confidence)] : null)) : null,
      (x.checks || []).length ? h('div', {}, h('h3', { class: 'eyebrow' }, 'PROOF'), x.checks.map((c) => h('details', { class: 'check-row' },
        h('summary', {}, badge(c.passed ? 'PASS' : 'FAILED', c.passed ? 'passed' : 'failed'), ' ', REPAIR_CHECK[c.name] || c.name,
          c.duration_s ? h('small', {}, ' · ' + c.duration_s + ' s') : null),
        c.output ? h('pre', { class: 'file-view' }, c.output) : h('p', { class: 'small-note' }, c.why || 'No details recorded.')))) : null,
      x.diff ? h('details', { open: x.state === 'NEEDS_DECISION' || null }, h('summary', {}, 'The change (' + files + ' file(s) and a new test)'), diffView(x.diff)) : null,
      x.applied ? h('p', { class: 'small-note' }, 'Applied ' + fmtTime(x.applied.at) + ' by ' + x.applied.by + '. Restore point and patch file kept on this computer.') : null,
      actions,
      (x.log || []).length ? h('details', { open: running || null }, h('summary', {}, 'What HOOD did'), h('pre', { class: 'file-view wrap' }, x.log.join('\n'))) : null);
  }

  // Self-development proposals (changes to HOOD's code proposed through the API): the owner sees the
  // exact change, approves it in Sentinel › Approvals (bound to its hash), then applies it here.
  const SELFDEV_STATE = { AWAITING_APPROVAL: ['awaiting_approval', 'Waiting for approval'], APPLIED: ['COMPLETED', 'Applied'],
    ROLLED_BACK: ['FAILED', 'Rolled back (checks failed)'] };
  function selfDevCard() {
    const box = h('div', {});
    const load = () => fill(box, async () => {
      const r = await api.get('/api/selfdev/proposals');
      if (!r.ok) return unavailable(r, 'Self-development');
      const items = r.data.proposals;
      if (!items.length) return emptyBox('No other proposed changes. Fixes found by self-repair appear above.');
      return h('div', {}, items.slice(0, 20).map((p) => {
        const [k, label] = SELFDEV_STATE[p.state] || [p.state, p.state];
        const diffBox = h('div', {});
        const det = h('details', { class: 'check-row' }, h('summary', {}, badge(k, label), ' ', h('b', {}, p.target_path),
          h('small', {}, ' · ' + clip(p.rationale || '', 120) + ' · by ' + p.proposed_by + ' · ' + fmtTime(new Date(p.created_at * 1000).toISOString()))), diffBox);
        det.addEventListener('toggle', async () => {
          if (!det.open || diffBox.childNodes.length) return;
          const d = await api.get('/api/selfdev/proposals/' + p.proposal_id);
          clear(diffBox).append(!d.ok ? unavailable(d, 'Proposal') : h('div', {},
            d.data.diff ? diffView(d.data.diff) : emptyBox('No change against the current file.'),
            d.data.detail ? h('p', { class: 'small-note' }, d.data.detail) : null,
            p.state === 'AWAITING_APPROVAL' && session.role === 'ROOT_OWNER' ? h('div', { class: 'form-actions' },
              h('button', { class: 'btn small', type: 'button', onclick: () => app.render('Sentinel') }, 'Approve in Sentinel › Approvals'),
              h('button', { class: 'btn small primary', type: 'button', onclick: () => confirmDialog('Apply this change?', [
                'HOOD changes ' + p.target_path + ' exactly as shown. It must be approved first (Sentinel › Approvals); the approval is bound to this exact content.',
                'A checkpoint is kept; if HOOD’s checks fail, the change is rolled back automatically.'], 'Apply change', async () => {
                const x = await api.post('/api/selfdev/proposals/' + p.proposal_id + '/apply', { confirm: true });
                if (x.ok) { toast(x.data.state === 'APPLIED' ? 'Change applied.' : 'Rolled back: ' + (x.data.detail || '')); load(); }
                return x;
              }) }, 'Apply change')) : null));
        });
        return det;
      }));
    }, (x) => x);
    load();
    return card('Other proposed changes (self-development)', box);
  }

  function repairView() {
    const list = h('div', { class: 'repair-list' });
    const detail = h('div', {}, loading('reports'));
    let timer = null;
    const load = async () => {
      clearTimeout(timer);
      const r = await api.get('/api/selfrepair');
      if (!r.ok) { clear(list).append(unavailable(r, 'Self-repair')); clear(detail); return; }
      const items = r.data.reports;
      if (!items.some((x) => x.id === repairPage.selected)) repairPage.selected = items.length ? items[0].id : null;
      clear(list).append(...(items.length ? items.map((x) => h('button', { class: 'list-row repair-item' + (x.id === repairPage.selected ? ' active' : ''), type: 'button',
        'aria-current': x.id === repairPage.selected ? 'true' : null, onclick: () => { repairPage.selected = x.id; load(); } },
        h('div', { class: 'row-main' }, h('b', {}, clip(x.report, 90)), h('small', {}, fmtTime(x.created))),
        h('div', { class: 'row-meta' }, repairBadge(x.state)))) : [emptyBox('No problems reported yet. Use “Report a problem”, or tell HOOD in chat what is wrong.')]));
      const cur = items.find((x) => x.id === repairPage.selected);
      clear(detail).append(cur ? repairDetail(cur, load) : emptyBox('Select a report.'));
      if (items.some((x) => ['INVESTIGATING', 'VERIFYING'].includes(x.state))) timer = setTimeout(load, 3000);
    };
    app.onCleanup(() => clearTimeout(timer));
    load();
    return h('div', {}, head('Self-repair', 'Tell HOOD what is wrong with it. HOOD finds the cause in its own code, proves a fix in its sandbox, and changes nothing until you approve. If it isn’t sure, it says so.',
      session.role === 'ROOT_OWNER' ? h('button', { class: 'btn primary', type: 'button', onclick: () => reportProblemDialog('') }, '✚ Report a problem') : null),
    h('div', { class: 'repair-layout' }, card('Reports', list), card('Details', detail)),
    h('div', { style: { marginTop: '14px' } }, selfDevCard()));
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
    const text = (content) => {
      const full = String(content || '');
      if (full.length <= 320) return h('p', { class: 'pre-wrap' }, full);
      const p = h('p', { class: 'pre-wrap' }, clip(full, 300));
      const more = h('button', { class: 'linkish', type: 'button', onclick: () => {
        const open = more.dataset.open === '1';
        p.textContent = open ? clip(full, 300) : full;
        more.textContent = open ? 'Show all' : 'Show less';
        more.dataset.open = open ? '0' : '1';
      } }, 'Show all');
      return h('div', {}, p, more);
    };
    const memCard = (m, kind) => h('section', { class: 'card', style: { gridColumn: 'span 4' } },
      h('header', { class: 'card-header' }, h('span', { class: 'card-title' }, kind === 'fact' ? 'You told HOOD' : 'Old chat log'),
        kind === 'fact' ? badge('available', 'your words') : badge('UNVERIFIED', 'not a verified fact')),
      h('div', { class: 'card-body' }, text(m.content), h('p', { class: 'small-note' }, fmtTime(m.created_at))));
    fill(body, async () => {
      const r = await api.get('/api/memory/list');
      if (r.status === 403) return emptyBox('Your role does not have access to memory.');
      if (!r.ok) return unavailable(r, 'Memory');
      const all = r.data.memories || [];
      const facts = all.filter((m) => m.project === 'personal');
      const logs = all.filter((m) => m.project !== 'personal');
      return h('div', {},
        h('h3', { class: 'section-title' }, 'Facts you told HOOD (' + facts.length + ')'),
        facts.length ? h('div', { class: 'dashboard' }, facts.map((m) => memCard(m, 'fact')))
          : emptyBox('Nothing yet. Tell HOOD in the chat: “Remember that …” (or “Souviens-toi que …”).'),
        logs.length ? h('div', {},
          h('h3', { class: 'section-title' }, 'Old chat logs (' + logs.length + ')'),
          h('p', { class: 'small-note' }, 'Saved by earlier versions of HOOD. They include HOOD’s own replies, which are not verified facts, so HOOD no longer uses them as memory. New chats are kept in the conversation history instead.'),
          h('details', {}, h('summary', {}, 'Show old chat logs'), h('div', { class: 'dashboard' }, logs.map((m) => memCard(m, 'log'))))) : null);
    });
    return h('div', {}, head('Memory', 'What HOOD remembers for you: only facts you told it. Only your own records are shown.'), body);
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
    autoSpeak: false, recorder: null, chunks: [], provider: null,
    async status() { return api.get('/api/voice/status'); },
    // This computer's built-in speech engine: free, offline, no quota. Voice choice is per browser.
    localAvailable() { return 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'; },
    localVoices() { return this.localAvailable() ? window.speechSynthesis.getVoices() : []; },
    localVoiceName(speaker) {
      try { return localStorage.getItem(speaker === 'x' ? 'hood-next:browser-voice-x' : 'hood-next:browser-voice') || ''; } catch (e) { return ''; }
    },
    speakLocally(text, speaker) {
      if (!this.localAvailable()) { toast('This browser has no built-in voice.'); return; }
      const u = new SpeechSynthesisUtterance(String(text).slice(0, 4000));
      const voices = this.localVoices();
      const wanted = this.localVoiceName(speaker);
      u.voice = voices.find((v) => v.name === wanted)
        || (speaker === 'x' && voices.length > 1 ? voices[1] : null) || null;
      u.onstart = () => setAvatar('speaking');
      u.onend = u.onerror = () => setAvatar('idle');
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    },
    async speak(text, speaker) {
      if (this.provider === null) {
        const st = await this.status();
        this.provider = st.ok ? st.data.tts_provider : 'gemini';
      }
      if (this.provider === 'browser') { this.speakLocally(text, speaker); return; }
      const r = await fetch('/api/voice/speak', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': api.csrf || '' }, body: JSON.stringify({ text: String(text).slice(0, 2000), speaker: speaker === 'x' ? 'x' : 'hood' }) });
      if (!r.ok) {
        let why = 'HTTP ' + r.status;
        try { why = (await r.json()).error || why; } catch (e) { /* not JSON */ }
        if (this.localAvailable()) {
          // The cloud voice failed (quota, plan, network): keep talking with this computer's voice.
          toast('Cloud voice unavailable (' + why + '). Using this computer\'s voice instead.');
          this.speakLocally(text, speaker);
        } else {
          toast('Hood voice unavailable: ' + why);
        }
        return;
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

  // API-key field: masked, but not a password field, so the browser does not offer to "save a password".
  function secretInput(label, placeholder) {
    const masked = window.CSS && CSS.supports && CSS.supports('-webkit-text-security', 'disc');
    return h('input', { type: masked ? 'text' : 'password', autocomplete: 'off', spellcheck: 'false',
      'data-lpignore': 'true', 'data-1p-ignore': 'true', placeholder, 'aria-label': label,
      style: masked ? { webkitTextSecurity: 'disc' } : null });
  }

  // ------------------------------------------------------------------ Settings › Model provider (owner only)
  function modelProviderCard() {
    const box = h('div', {});
    const keyInput = secretInput('Gemini API key', 'Paste your Gemini API key');
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
          const a = h('input', { type: 'number', min: '0', max: '10', step: '0.00001', value: p && p.audio_input_per_1k_usd !== null && p.audio_input_per_1k_usd !== undefined ? p.audio_input_per_1k_usd : '', 'aria-label': m + ' audio input price' });
          inputs[m] = [i, o, a];
          return h('div', {}, h('b', {}, m), h('label', {}, 'Input, USD per 1,000 tokens', i), h('label', {}, 'Output, USD per 1,000 tokens', o),
            h('label', {}, 'Audio input (voice transcription), USD per 1,000 tokens — needed for paid voice; blank if unused', a));
        }),
        h('div', { class: 'form-actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
          const prices = {};
          for (const [m, [i, o, a]] of Object.entries(inputs)) prices[m] = { input_per_1k_usd: i.value, output_per_1k_usd: o.value, audio_input_per_1k_usd: a.value };
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
          s.last_error ? h('dt', {}, 'Last error') : null, s.last_error ? h('dd', {}, clip(s.last_error, 200)) : null),
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
  function toolsSettingsCard() {
    const box = h('div', {});
    const load = () => fill(box, async () => {
      const r = await api.get('/api/tools');
      if (!r.ok) return unavailable(r, 'Tools');
      const t = r.data;
      const act = (label, path, body, okText, confirmLines) => h('button', { class: 'btn small', type: 'button', onclick: () => {
        const go = async () => {
          const x = await api.post(path, body);
          if (!x.ok) { toast(x.error); return x; }
          toast(okText);
          if (x.data && x.data.id) follow(x.data.id); else load();
          return x;
        };
        if (confirmLines) confirmDialog(label, confirmLines, label, go); else go();
      } }, label);
      const follow = async (jobId) => {
        for (;;) {
          const j = await api.get('/api/tools/jobs/' + jobId);
          if (!j.ok || j.data.state !== 'running') { load(); if (j.ok && j.data.state === 'failed') toast('Install stopped: ' + j.data.error); return; }
          await new Promise((res) => setTimeout(res, 1500));
        }
      };
      return h('div', {},
        h('p', {}, 'Tools HOOD installs when a mission needs them, only inside WSL2 and only with your OK (once per tool; after that HOOD reuses and updates them without asking).'),
        t.platform_problem ? h('div', { class: 'callout warn' }, t.platform_problem)
          : t.helper_problem ? h('div', { class: 'callout warn' }, h('b', {}, 'System packages: not switched on yet'), h('p', {}, t.helper_problem),
            h('p', { class: 'small-note' }, 'Downloads such as WordPress itself still work; PHP needs this step.')) : null,
        h('div', { class: 'tool-rows' }, t.tools.map((tool) => h('div', { class: 'list-row' },
          h('div', { class: 'row-main' }, h('b', {}, tool.name), h('small', {}, tool.purpose + ' · ' + tool.size + ' · from ' + tool.source),
            tool.detail ? h('small', {}, tool.detail) : null, tool.last_error ? h('small', { class: 'small-note' }, 'Last problem: ' + clip(tool.last_error, 160)) : null),
          h('div', { class: 'row-meta' },
            tool.installed ? badge('available', 'installed' + (tool.version ? ' · ' + clip(tool.version, 24) : '')) : badge('muted', 'not installed'), ' ',
            tool.approved ? badge('active', 'allowed') : null, ' ',
            !tool.installed ? act('Allow & install', '/api/tools/install', { tools: [tool.id], confirm: true }, 'Installing ' + tool.name + '…',
              ['HOOD will install ' + tool.name + (tool.requires.length ? ' (and what it needs: ' + tool.requires.join(', ') + ')' : '') + ' from ' + tool.source + '.',
                'You allow it once; later updates and reuse don’t ask again.']) : null,
            tool.installed && tool.approved ? act('Update', '/api/tools/update', { tool: tool.id }, 'Updating ' + tool.name + '…') : null,
            tool.installed && (tool.installed_by_hood || tool.kind !== 'apt') && tool.approved ? act('Remove', '/api/tools/remove', { tool: tool.id, confirm: true }, tool.name + ' removed.',
              ['Remove ' + tool.name + '? HOOD will ask again before reinstalling it.']) : null)))),
        t.history.length ? h('details', {}, h('summary', {}, 'History'), h('ol', { class: 'timeline' }, t.history.slice().reverse().map((e) =>
          h('li', {}, h('time', {}, fmtTime(e.at)), h('span', {}, e.actor + ' · ' + e.action + ' · ' + e.tool + ' · ' + e.result))))) : null);
    });
    load();
    return card('Tools (installs)', box);
  }
  function agentSettingsCard() {
    const box = h('div', {});
    const load = () => fill(box, async () => {
      const r = await api.get('/api/settings/agents');
      if (!r.ok) return unavailable(r, 'Agent settings');
      const a = r.data;
      const toggle = (on) => confirmDialog(on ? 'Turn on "Run on my PC"' : 'Turn off "Run on my PC"', on ? [
        'When a Python mission is ready to be checked and this computer has no sandbox, HOOD will ask you, mission by mission, to run its fixed check commands directly on this PC.',
        'Those commands execute code the agents wrote, without network or file isolation. API keys are removed from their environment and each step has a time limit.',
        'Nothing runs until you approve the exact files of that mission. Website missions never need this: they are checked without running anything.']
        : ['Python missions will wait for HOOD’s sandbox instead of asking to run directly on this PC.'],
      on ? 'Turn on' : 'Turn off', async () => { const x = await api.post('/api/settings/agents', { allow_local_run: on, confirm: true }); if (x.ok) load(); return x; });
      return h('div', {},
        h('dl', { class: 'kv' },
          h('dt', {}, 'Sandbox on this computer'), h('dd', {}, a.sandbox_available ? badge('available', 'available') : badge('unavailable', 'not ready'),
            a.sandbox_problem ? h('div', { class: 'small-note' }, a.sandbox_problem) : null),
          h('dt', {}, 'Website missions'), h('dd', {}, badge('available', 'work here'), h('div', { class: 'small-note' }, 'Checked by reading the files; nothing is run.')),
          h('dt', {}, 'Python missions'), h('dd', {}, a.sandbox_available ? 'Tests run in the sandbox.'
            : a.allow_local_run ? 'Wait for the sandbox, or ask you before running their checks on this PC.' : 'Wait for the sandbox (approve it below).'),
          h('dt', {}, '"Run on my PC"'), h('dd', {}, a.allow_local_run ? badge('active', 'on') : badge('muted', 'off (default)'),
            a.changed_by ? h('div', { class: 'small-note' }, 'Changed by ' + a.changed_by + ' · ' + fmtTime(a.changed_at)) : null)),
        h('div', { class: 'form-actions' },
          h('button', { class: 'btn small' + (a.allow_local_run ? '' : ' primary'), type: 'button', onclick: () => toggle(!a.allow_local_run) },
            a.allow_local_run ? 'Turn off "Run on my PC"' : 'Turn on "Run on my PC"')),
        sandboxPanel(null));
    });
    load();
    return card('Agents', box);
  }
  function voiceSettingsCard() {
    const box = h('div', {});
    const load = () => fill(box, async () => {
      const r = await api.get('/api/settings/voice');
      if (!r.ok) return unavailable(r, 'Voice settings');
      const v = r.data;
      const provider = h('select', { 'aria-label': 'Voice provider' },
        h('option', { value: 'gemini' }, 'Google Gemini voice'), h('option', { value: 'elevenlabs' }, 'ElevenLabs (my voice)'),
        h('option', { value: 'browser' }, "This computer's voice (free, offline, no quota)"));
      const localPick = (speaker) => {
        const sel = h('select', { 'aria-label': speaker === 'x' ? 'X computer voice' : 'HOOD computer voice' },
          h('option', { value: '' }, 'System default'),
          voice.localVoices().map((lv) => h('option', { value: lv.name }, lv.name + ' (' + lv.lang + ')')));
        sel.value = voice.localVoiceName(speaker);
        sel.addEventListener('change', () => {
          try { localStorage.setItem(speaker === 'x' ? 'hood-next:browser-voice-x' : 'hood-next:browser-voice', sel.value); } catch (e) { /* storage blocked */ }
        });
        return sel;
      };
      provider.value = v.tts_provider;
      const key = secretInput('ElevenLabs API key', 'Paste your ElevenLabs API key (starts with sk_)');
      const vid = h('input', { value: v.voice_id || '', placeholder: 'e.g. 21m00Tcm4TlvDq8ikWAM', 'aria-label': 'HOOD voice ID' });
      const xvid = h('input', { value: v.x_voice_id || '', placeholder: 'optional: a different voice for X', 'aria-label': 'X voice ID' });
      const model = h('input', { value: v.model_id || v.default_model, 'aria-label': 'ElevenLabs model' });
      const price = h('input', { type: 'number', min: '0', max: '10', step: '0.0001', value: v.price_per_1k_chars === null ? '' : v.price_per_1k_chars, 'aria-label': 'Price per 1,000 characters' });
      return h('div', {},
        h('dl', { class: 'kv' }, h('dt', {}, 'Speaking voice'), h('dd', {}, { elevenlabs: 'ElevenLabs', browser: "This computer's voice" }[v.tts_provider] || 'Google Gemini'),
          h('dt', {}, 'ElevenLabs key'), h('dd', {}, v.key.set ? 'Set (' + v.key.hint + ')' : 'Not set')),
        h('div', { class: 'form' },
          h('label', {}, (v.key.set ? 'Replace ElevenLabs API key' : 'ElevenLabs API key') + ' (the secret value starting with sk_, not the key ID)', key),
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
          voice.localAvailable() ? h('label', {}, "This computer's voice for HOOD (also used automatically if a cloud voice fails)", localPick('hood')) : null,
          voice.localAvailable() ? h('label', {}, "This computer's voice for X", localPick('x')) : null,
          h('label', {}, 'HOOD voice ID (from ElevenLabs › Voices)', vid),
          h('label', {}, 'X voice ID (optional)', xvid),
          h('label', {}, 'Model (eleven_multilingual_v2 = best quality, eleven_flash_v2_5 = fastest)', model),
          h('label', {}, 'Price, USD per 1,000 characters (0 if your ElevenLabs plan covers it)', price),
          h('div', { class: 'form-actions' },
            h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
              const res = await api.post('/api/settings/voice', { tts_provider: provider.value, voice_id: vid.value.trim(),
                x_voice_id: xvid.value.trim(), model_id: model.value.trim(), price_per_1k_chars: price.value, confirm: true });
              toast(res.ok ? 'Voice settings saved.' : 'Not saved: ' + res.error);
              if (res.ok) { voice.provider = provider.value; load(); }
            } }, 'Save voice settings'),
            h('button', { class: 'btn small', type: 'button', onclick: () => voice.speak('Hello Zak, this is HOOD. Can you hear me clearly?') }, '▶ Test HOOD voice'),
            h('button', { class: 'btn small', type: 'button', onclick: () => voice.speak('This is X.', 'x') }, '▶ Test X voice'))));
    });
    if (voice.localAvailable() && !voice.localVoices().length) {
      // Chrome loads its voice list asynchronously; redraw once it is ready.
      window.speechSynthesis.addEventListener('voiceschanged', () => load(), { once: true });
    }
    load();
    return card('Voice', box);
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
      session.role === 'ROOT_OWNER' ? h('div', { style: { marginTop: '14px' } }, agentSettingsCard()) : null,
      session.role === 'ROOT_OWNER' ? h('div', { style: { marginTop: '14px' } }, toolsSettingsCard()) : null,
      h('div', { class: 'two-col', style: { marginTop: '14px' } }, securityCard(),
        session.role === 'ROOT_OWNER' ? usersCard() : null),
      h('div', { style: { marginTop: '14px' } }, card('Active sessions', sessions)));
  }

  const VIEWS = { Command: commandView, Missions: missionsView, Agents: agentsView, Intelligence: intelligenceView,
    Integrations: integrationsView, Sentinel: sentinelView, Memory: memoryView, Commerce: commerceView,
    Desktop: desktopView, Voice: voiceView, Repair: repairView, Settings: settingsView };

  // ------------------------------------------------------------------ global search (missions, agents, capabilities)
  async function runSearch(q) {
    const box = $('searchResults');
    q = q.trim().toLowerCase();
    if (q.length < 2) { box.classList.add('hidden'); return; }
    const [m, c] = await Promise.all([api.get('/api/agents/missions'), api.get('/api/capabilities')]);
    const results = [];
    if (m.ok) m.data.filter((x) => x.objective.toLowerCase().includes(q)).slice(0, 5).forEach((x) => results.push(['Mission', clip(missionTitle(x.objective), 80), () => { missionsPage.selected = x.id; app.render('Missions'); }]));
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
    window.addEventListener('hashchange', () => {
      if (!app.booted) return;
      const [p, ...rest] = decodeURIComponent(location.hash.slice(1)).split('/');
      if (p !== app.page) app.render(p);
      else if (app.subroute) app.subroute(rest.filter(Boolean));
    });
    session.start();
  }
  wire();
})();
