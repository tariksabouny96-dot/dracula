// CSRF: every state-changing same-origin request carries the per-session token.
// The token comes from the login response or /api/auth/status (same-origin only).
let hoodCsrfToken = null;
function setCsrfToken(token) { hoodCsrfToken = typeof token === 'string' ? token : null; }
const nativeFetch = window.fetch.bind(window);
window.fetch = function(resource, init = {}) {
  const method = String((init && init.method) || 'GET').toUpperCase();
  const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';
  const sameOrigin = url.startsWith('/') || url.startsWith(window.location.origin);
  if (method !== 'GET' && method !== 'HEAD' && sameOrigin && hoodCsrfToken) {
    const headers = new Headers((init && init.headers) || {});
    headers.set('X-CSRF-Token', hoodCsrfToken);
    init = Object.assign({}, init, { headers, credentials: 'same-origin' });
  }
  return nativeFetch(resource, init);
};

// Escape untrusted API content before inserting into template HTML.
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

// ==========================================================================
// HOOD // X — CINEMATIC COMMAND CENTER CLIENT LOGIC
// Governed by Master System Specification v1.3 & Project Sentinel v1.3
// ==========================================================================

const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const hoodOrb = document.getElementById('hood-orb');
const orbLabel = document.getElementById('orb-state-label');
const freqVal = document.getElementById('freq-val');
const btnPtt = document.getElementById('btn-ptt');
const btnInterrupt = document.getElementById('btn-interrupt');
const btnEmergencyStop = document.getElementById('btn-emergency-stop');
const taskList = document.getElementById('task-list');
const approvalCards = document.getElementById('approval-cards');

// Harmonic Frequencies per system state
const harmonicFrequencies = {
  'IDLE': '432.0 Hz',
  'LISTENING': '528.0 Hz',
  'THINKING': '639.0 Hz',
  'SPEAKING': '741.0 Hz',
  'EXECUTING': '852.0 Hz',
  'EMERGENCY': '963.0 Hz',
  'X_ACTIVE': '174.0 Hz'
};

// State updater
function setUiState(state) {
  if (orbLabel) orbLabel.textContent = state;
  if (hoodOrb) hoodOrb.className = 'hood-orb state-' + state.toLowerCase();
  if (freqVal && harmonicFrequencies[state.toUpperCase()]) {
    freqVal.textContent = harmonicFrequencies[state.toUpperCase()];
  }
}

// Equalizer dynamic wave simulation
function simulateAudioWaves(active) {
  const bars = document.querySelectorAll('.eq-bar');
  if (!active) {
    bars.forEach(b => b.style.height = '4px');
    return;
  }
  bars.forEach(b => {
    const h = Math.floor(Math.random() * 16) + 4;
    b.style.height = `${h}px`;
  });
}

setInterval(() => {
  const curState = orbLabel ? orbLabel.textContent.toUpperCase() : 'IDLE';
  if (curState === 'SPEAKING' || curState === 'LISTENING') {
    simulateAudioWaves(true);
  } else {
    simulateAudioWaves(false);
  }
}, 120);

// Append message to conversation stream
function appendMessage(sender, text, isUser = false, isSystem = false, isX = false) {
  const msgDiv = document.createElement('div');
  const checkX = isX || sender === 'X' || sender.startsWith('X (');
  if (isSystem) {
    msgDiv.className = 'message msg-system';
  } else if (isUser) {
    msgDiv.className = 'message msg-user';
  } else if (checkX) {
    msgDiv.className = 'message msg-x';
  } else {
    msgDiv.className = 'message msg-hood';
  }

  const senderSpan = document.createElement('span');
  if (isUser) {
    senderSpan.className = 'sender-tag user-tag';
  } else if (isSystem) {
    senderSpan.className = 'sender-tag system-tag';
  } else if (checkX) {
    senderSpan.className = 'sender-tag x-tag';
  } else {
    senderSpan.className = 'sender-tag hood-tag';
  }
  senderSpan.textContent = `${sender}: `;

  const bodySpan = document.createElement('span');
  bodySpan.className = 'msg-body';
  bodySpan.textContent = text;

  msgDiv.appendChild(senderSpan);
  msgDiv.appendChild(bodySpan);

  chatMessages.appendChild(msgDiv);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return msgDiv;
}

// Render rich inline actionable approval card directly adjacent to the conversational message
function renderInlineApprovalCard(appr) {
  if (!appr || !chatMessages) return;
  // Prevent duplicate rendering of the same approval in chat
  if (Array.from(document.querySelectorAll('.inline-approval-card')).find(el => el.dataset.approvalId === appr.approval_id)) {
    return;
  }

  const card = document.createElement('div');
  card.className = 'inline-approval-card';
  card.setAttribute('data-approval-id', appr.approval_id);

  const riskClass = appr.risk_level === 'L4' || appr.risk_level === 'L5' ? 'risk-high' : 'risk-moderate';
  const durationMin = (appr.options && appr.options.length > 0 && appr.options[0].duration_minutes) ? appr.options[0].duration_minutes : 5;
  const scope = (appr.options && appr.options.length > 0 && appr.options[0].scope) ? appr.options[0].scope : 'LOCAL_SANDBOX_ENV';

  card.innerHTML = `
    <div class="inline-approval-badge-bar">
      <span class="inline-badge-action">ACTION: ${escapeHtml(appr.action_type)}</span>
      <span class="inline-badge-risk ${riskClass}">RISK: ${escapeHtml(appr.risk_level)}</span>
    </div>
    <div class="inline-approval-meta">
      <div class="meta-row"><strong>Target:</strong> <code>${escapeHtml(appr.target)}</code></div>
      <div class="meta-row"><strong>Scope:</strong> <span>${escapeHtml(scope)}</span></div>
      <div class="meta-row"><strong>Duration:</strong> <span>${escapeHtml(durationMin)} min (Strict Expiry)</span></div>
      <div class="meta-row"><strong>Approval ID:</strong> <code>${escapeHtml(appr.approval_id)}</code></div>
      ${appr.reason ? `<div class="meta-desc">${escapeHtml(appr.reason)}</div>` : ''}
    </div>
    <div class="inline-approval-actions" id="actions-${escapeHtml(appr.approval_id)}">
      <button class="btn-inline-approve" data-approval-action="allow">APPROVE ACTION</button>
      <button class="btn-inline-deny" data-approval-action="deny">DENY ACTION</button>
    </div>
  `;

  card.querySelector('[data-approval-action="allow"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, true));
  card.querySelector('[data-approval-action="deny"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, false));
  chatMessages.appendChild(card);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderInlineApprovalCard = renderInlineApprovalCard;

// Unified Navigation Tabs Handling (Top Bar & Vertical Command Flank)
function switchTab(targetId) {
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.vnav-item').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.hud-tab-content').forEach(c => c.classList.remove('active'));

  const matchingNavTab = document.querySelector(`.nav-tab[data-target="${targetId}"]`);
  if (matchingNavTab) matchingNavTab.classList.add('active');

  const matchingVnavItem = document.querySelector(`.vnav-item[data-target="${targetId}"]`);
  if (matchingVnavItem) matchingVnavItem.classList.add('active');

  const targetEl = document.getElementById(targetId);
  if (targetEl) targetEl.classList.add('active');

  if (targetId === 'tab-missions') novaLoadMissions();
  if (targetId === 'tab-memory') {
    loadMemoryData();
  }
  if (targetId === 'tab-systems') novaLoad();
}
window.switchTab = switchTab;

document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const targetId = tab.getAttribute('data-target');
    if (targetId) switchTab(targetId);
  });
});

document.querySelectorAll('.vnav-item').forEach(vnav => {
  vnav.addEventListener('click', () => {
    const targetId = vnav.getAttribute('data-target');
    if (targetId) switchTab(targetId);
  });
});

// Telemetry Drawer Toggle Handler
const btnToggleTelemetry = document.getElementById('btn-toggle-telemetry');
const btnCollapseTelemetry = document.getElementById('btn-collapse-telemetry');
const mainSurfaceDeck = document.getElementById('main-surface-deck');

function toggleTelemetryDrawer() {
  if (mainSurfaceDeck) {
    mainSurfaceDeck.classList.toggle('telemetry-collapsed');
  }
}

if (btnToggleTelemetry) btnToggleTelemetry.addEventListener('click', toggleTelemetryDrawer);
if (btnCollapseTelemetry) btnCollapseTelemetry.addEventListener('click', toggleTelemetryDrawer);

// Suggestion chips delegated handler (supports dynamic theme chip updates)
document.addEventListener('click', (e) => {
  const chip = e.target.closest('.suggestion-chip');
  if (chip) {
    const prompt = chip.getAttribute('data-prompt');
    if (prompt && chatInput) {
      chatInput.value = prompt;
      if (chatForm) {
        chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      }
    }
  }
});

// Mode pills handler
document.querySelectorAll('.mode-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    if (pill.getAttribute('aria-disabled') === 'true') return;
    document.querySelectorAll('.mode-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
  });
});

// Submit message handler
if (chatForm) {
  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;

    chatInput.value = '';
    appendMessage('Zak', text, true);
    setUiState('THINKING');

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, modality: 'text' })
      });
      const data = await res.json();
      
      const isXSpeaker = data.speaker_id === 'x' || data.sender === 'X';
      const isSysSpeaker = data.speaker_id === 'system' || data.sender === 'System';
      const senderLabel = data.sender || (isXSpeaker ? 'X' : (isSysSpeaker ? 'System' : 'Hood'));
      
      appendMessage(senderLabel, data.text, false, isSysSpeaker, isXSpeaker);

      if (data.approval) {
        renderInlineApprovalCard(data.approval);
      }

      if (data.tasks && data.tasks.length > 0) {
        updateTasks(data.tasks);
        // Do NOT automatically switch tabs. Keep user on current deck per Part 6.2 directive.
        const missionBadge = document.querySelector('.vnav-item[data-target="tab-missions"] .tab-badge, .nav-tab[data-target="tab-missions"] .tab-badge');
        if (missionBadge) {
          missionBadge.textContent = `${data.tasks.length} active`;
          missionBadge.style.display = 'inline-block';
        }
      }

      // Automatically refresh pending approvals and telemetry so any registered activation is immediately visible
      loadApprovals();
      loadTelemetry();
    } catch (err) {
      setUiState('IDLE');
      appendMessage('System', 'Error connecting to Hood Core: ' + err.message, false, true);
    }
  });
}

// Push-to-talk handler (Voice emulation)
if (btnPtt) {
  btnPtt.addEventListener('click', async () => {
    setUiState('LISTENING');
    appendMessage('System', 'Push-to-Talk active (Listening for Zak...)', false, true);

    setTimeout(async () => {
      setUiState('THINKING');
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: 'Hood, inspect the demo app and verify health.', modality: 'voice' })
        });
        const data = await res.json();
        appendMessage('Zak (Voice)', 'Hood, inspect the demo app and verify health.', true);
        setUiState('SPEAKING');
        appendMessage('Hood (Voice)', data.text, false);

        if (data.tasks) {
          updateTasks(data.tasks);
        }

        setTimeout(() => {
          setUiState('IDLE');
        }, 1200);
      } catch (err) {
        setUiState('IDLE');
        appendMessage('System', 'Voice processing failed: ' + err.message, false, true);
      }
    }, 200);
  });
}

// Barge-in Interrupt handler
if (btnInterrupt) {
  btnInterrupt.addEventListener('click', async () => {
    setUiState('LISTENING');
    appendMessage('System', 'Barge-in: Speech output halted immediately.', false, true);
    try {
      await fetch('/api/interrupt', { method: 'POST' });
    } catch (err) {}
  });
}

// Emergency Stop handler
if (btnEmergencyStop) {
  btnEmergencyStop.addEventListener('click', async () => {
    setUiState('EMERGENCY');
    appendMessage('System', 'EMERGENCY STOP TRIGGERED: Halting all executions, revoking capabilities.', false, true);
    try {
      await fetch('/api/emergency_stop', { method: 'POST' });
    } catch (err) {}
  });
}

// Task progress update
function updateTasks(tasks) {
  if (!taskList) return;
  taskList.innerHTML = '';
  if (!tasks || tasks.length === 0) {
    taskList.innerHTML = '<div class="task-empty">System standby. No active tasks executing in background.</div>';
    return;
  }
  tasks.forEach(t => {
    const item = document.createElement('div');
    item.className = 'task-item';
    item.innerHTML = `<span>${escapeHtml(t.title)}</span><span class="task-status">${escapeHtml(t.status)}</span>`;
    taskList.appendChild(item);
  });
}

// Fetch pending approvals
async function loadApprovals() {
  const mainDeck = document.getElementById('approval-cards');
  const sideDeck = document.getElementById('approval-cards-sidebar');
  if (!mainDeck && !sideDeck) return;
  try {
    const res = await fetch('/api/approvals');
    const data = await res.json();
    
    if (mainDeck) mainDeck.innerHTML = '';
    if (sideDeck) sideDeck.innerHTML = '';

    if (!data || data.length === 0) {
      if (mainDeck) {
        mainDeck.innerHTML = '<div class="approval-empty">No actions pending owner approval. Financial and deployment policies enforced.</div>';
      }
      if (sideDeck) {
        sideDeck.innerHTML = '<div class="approval-empty" style="font-size: 0.72rem; padding: 6px;">Zero pending actions. Constitutional guardrails active.</div>';
      }
      return;
    }

    data.forEach(appr => {
      const riskClass = appr.risk_level === 'L4' || appr.risk_level === 'L5' ? 'val-purple' : 'val-accent';
      let extraInfo = '';
      if (appr.options && appr.options.length > 0 && appr.options[0].duration_minutes) {
        extraInfo = `<div><strong>Duration:</strong> ${escapeHtml(appr.options[0].duration_minutes)} min (Strict Expiry) | <strong>Scope:</strong> ${escapeHtml(appr.options[0].scope || 'LOCAL_SANDBOX_ENV')}</div>`;
      }

      // Render full card for main deck
      if (mainDeck) {
        const card = document.createElement('div');
        card.className = 'approval-card';
        card.innerHTML = `
          <div class="approval-header">
            <span style="font-weight:700; color:var(--accent-cyan);">ACTION: ${escapeHtml(appr.action_type)}</span>
            <span class="${riskClass}" style="font-weight:700;">RISK: ${escapeHtml(appr.risk_level)}</span>
          </div>
          <div style="font-size:0.8rem; margin:4px 0;"><strong>ID:</strong> <code>${escapeHtml(appr.approval_id)}</code></div>
          <div style="font-size:0.82rem; margin-bottom:4px;"><strong>Target:</strong> ${escapeHtml(appr.target)}</div>
          <div style="font-size:0.78rem; color:var(--text-muted); margin-bottom:6px;">${escapeHtml(appr.reason || '')}</div>
          ${extraInfo}
          <div class="approval-actions" style="margin-top:8px;">
            <button class="btn-approve" data-approval-action="allow">APPROVE ACTION</button>
            <button class="btn-deny" data-approval-action="deny">DENY ACTION</button>
          </div>
        `;
        card.querySelector('[data-approval-action="allow"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, true));
        card.querySelector('[data-approval-action="deny"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, false));
        mainDeck.appendChild(card);
      }

      // Render compact interactive card for right sidebar
      if (sideDeck) {
        const sideCard = document.createElement('div');
        sideCard.className = 'approval-card';
        sideCard.style.padding = '8px';
        sideCard.style.marginBottom = '6px';
        sideCard.style.border = '1px solid rgba(255, 0, 85, 0.4)';
        sideCard.style.background = 'rgba(255, 0, 85, 0.08)';
        sideCard.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <span style="font-weight:700; font-size:0.75rem; color:#ff0055;">${escapeHtml(appr.action_type)}</span>
            <span class="${riskClass}" style="font-size:0.7rem; font-weight:700;">${escapeHtml(appr.risk_level)}</span>
          </div>
          <div style="font-size:0.7rem; color:var(--text-muted); margin-bottom:4px;">${escapeHtml(appr.target)}</div>
          <div class="approval-actions" style="display:flex; gap:4px; margin-top:6px;">
            <button class="btn-approve" style="font-size:0.68rem; padding:4px 6px; flex:1;" data-approval-action="allow">APPROVE</button>
            <button class="btn-deny" style="font-size:0.68rem; padding:4px 6px; flex:1;" data-approval-action="deny">DENY</button>
          </div>
        `;
        sideCard.querySelector('[data-approval-action="allow"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, true));
        sideCard.querySelector('[data-approval-action="deny"]')?.addEventListener('click', () => resolveApproval(appr.approval_id, false));
        sideDeck.appendChild(sideCard);
      }
    });
  } catch (err) {}
}

async function resolveApproval(approvalId, approved) {
  // Immediately update any inline approval card in the DOM to prevent repeated clicks and reflect pending state
  const inlineActions = document.getElementById(`actions-${approvalId}`);
  if (inlineActions) {
    inlineActions.innerHTML = `<span class="inline-resolving-label">RESOLVING CONSTITUTIONAL GRANT...</span>`;
  }

  try {
    const res = await fetch('/api/approvals/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, approved: approved })
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `Approval request failed (${res.status})`);

    // Update inline card with authoritative resolved badge
    const inlineCard = Array.from(document.querySelectorAll('.inline-approval-card')).find(el => el.dataset.approvalId === approvalId);
    if (inlineCard) {
      const actionsEl = inlineCard.querySelector('.inline-approval-actions');
      if (actionsEl) {
        if (approved) {
          actionsEl.innerHTML = `<span class="inline-badge-resolved approved">✓ GRANTED & AUTHORIZED</span>`;
        } else {
          actionsEl.innerHTML = `<span class="inline-badge-resolved denied">✗ DENIED BY OPERATOR</span>`;
        }
      }
    }

    if (approved && data.x_result && !data.x_result.error) {
      appendMessage('System', `Approval ${approvalId} granted. Executive X is now ACTIVE (${data.x_result.duration_seconds}s remaining).`, false, true);
      updateXThemeState(true);
    } else if (approved) {
      appendMessage('System', `Approval ${approvalId} confirmed. Status: ${data.status}`, false, true);
    } else {
      appendMessage('System', `Approval ${approvalId} denied.`, false, true);
    }
  } catch (err) {
    appendMessage('System', `Error resolving approval: ${err.message}`, false, true);
    if (inlineActions) {
      inlineActions.innerHTML = `
        <button class="btn-inline-approve" data-approval-action="allow">APPROVE ACTION</button>
        <button class="btn-inline-deny" data-approval-action="deny">DENY ACTION</button>
      `;
      inlineActions.querySelector('[data-approval-action="allow"]')?.addEventListener('click', () => resolveApproval(approvalId, true));
      inlineActions.querySelector('[data-approval-action="deny"]')?.addEventListener('click', () => resolveApproval(approvalId, false));
    }
  }
  loadApprovals();
  loadTelemetry();
}

// X Mode Visual State Switcher
let currentXThemeState = false;

function updateXThemeState(isXActive) {
  const xBannerQuick = document.getElementById('x-banner-quick');
  const xBadge = document.getElementById('x-status');
  const avatarImg = document.getElementById('avatar-presence-img');
  const avatarQuote = document.getElementById('avatar-quote-text');
  const brandLogoMain = document.getElementById('brand-logo-main');
  const brandLogoSub = document.getElementById('brand-logo-sub');
  const brandCodename = document.getElementById('brand-system-codename');
  const chipsRow = document.getElementById('suggestion-chips-row');

  // Prevent DOM thrashing if state is unchanged
  if (currentXThemeState === isXActive && document.body.classList.contains(isXActive ? 'theme-x' : 'theme-hood')) {
    return;
  }
  currentXThemeState = isXActive;

  if (isXActive) {
    document.body.classList.remove('theme-hood');
    document.body.classList.add('theme-x');
    if (xBannerQuick) xBannerQuick.style.display = 'flex';
    if (xBadge) {
      xBadge.className = 'indicator-badge x-active-alert';
      xBadge.innerHTML = '<span class="badge-dot"></span><span class="badge-label">X: ACTIVE (RED-TEAM)</span>';
    }
    if (brandLogoMain) brandLogoMain.textContent = 'X';
    if (brandLogoSub) brandLogoSub.textContent = 'EXECUTIVE AI';
    if (brandCodename) brandCodename.textContent = 'SOVEREIGN RED TEAM ENGINE // DISCOVER // EXPLOIT // EVOLVE';

    if (avatarImg) {
      avatarImg.src = '/static/assets/x_avatar_reference.jpg';
      avatarImg.alt = 'X Sovereign Red-Team Avatar';
    }
    if (avatarQuote) {
      avatarQuote.textContent = '"Find weakness. Understand. Exploit. Evolve."';
    }

    if (chipsRow) {
      chipsRow.innerHTML = `
        <button type="button" class="suggestion-chip" data-prompt="What can you do?">What can you do?</button>
        <button type="button" class="suggestion-chip" data-prompt="Simulate an offensive scenario">Simulate a scenario</button>
        <button type="button" class="suggestion-chip" data-prompt="Run security posture assessment">Security assessment</button>
        <button type="button" class="suggestion-chip" data-prompt="Analyze perimeter attack vectors">Strategic analysis</button>
      `;
    }
    setUiState('X_ACTIVE');
  } else {
    document.body.classList.remove('theme-x');
    document.body.classList.add('theme-hood');
    if (xBannerQuick) xBannerQuick.style.display = 'none';
    if (xBadge) {
      xBadge.className = 'indicator-badge x-dormant';
      xBadge.innerHTML = '<span class="badge-dot"></span><span class="badge-label">X: DORMANT</span>';
    }
    if (brandLogoMain) brandLogoMain.textContent = 'HOOD';
    if (brandLogoSub) brandLogoSub.textContent = 'X';
    if (brandCodename) brandCodename.textContent = 'AUTONOMOUS EXECUTIVE & OFFENSIVE SECURITY OPERATING SYSTEM';

    if (avatarImg) {
      avatarImg.src = '/static/assets/hood_avatar_reference.jpg';
      avatarImg.alt = 'HOOD Holographic Avatar';
    }
    if (avatarQuote) {
      avatarQuote.textContent = '"Higher intelligence, a kinder tomorrow"';
    }

    if (chipsRow) {
      chipsRow.innerHTML = `
        <button type="button" class="suggestion-chip" data-prompt="What do you know about me?">What do you know about me?</button>
        <button type="button" class="suggestion-chip" data-prompt="What can you do?">What can you do?</button>
        <button type="button" class="suggestion-chip" data-prompt="Plan a website architecture">Plan a website</button>
        <button type="button" class="suggestion-chip" data-prompt="Inspect system security status">Check security status</button>
      `;
    }
  }
}

// Quick Stand-Down button
const btnQuickStanddownX = document.getElementById('btn-quick-standdown-x');
if (btnQuickStanddownX) {
  btnQuickStanddownX.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/x/stand_down', { method: 'POST' });
      if (res.ok) {
        updateXThemeState(false);
        setUiState('IDLE');
        appendMessage('System', 'X has stood down. Control returned to HOOD.', false, true);
        loadTelemetry();
        loadSentinelData();
      }
    } catch (err) {}
  });
}

// Telemetry updater
async function loadTelemetry() {
  try {
    const res = await fetch('/api/telemetry');
    if (!res.ok) return;
    const data = await res.json();

    // Badges
    const deskBadge = document.getElementById('desktop-badge');
    if (deskBadge && data.desktop && data.desktop.badge) {
      deskBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">${escapeHtml(data.desktop.badge)}</span>`;
    }

    const micBadge = document.getElementById('mic-status');
    if (micBadge && data.mic && data.mic.badge) {
      micBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">${escapeHtml(data.mic.badge)}</span>`;
    }

    const provBadge = document.getElementById('provider-badge');
    if (provBadge && data.provider && data.provider.badge) {
      provBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">${escapeHtml(data.provider.badge)}</span>`;
    }

    const evoBadge = document.getElementById('evolution-badge');
    if (evoBadge && data.evolution && data.evolution.badge) {
      evoBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">${escapeHtml(data.evolution.badge)}</span>`;
    }

    const nodesBadge = document.getElementById('nodes-badge');
    if (nodesBadge && data.nodes && data.nodes.badge) {
      nodesBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">${escapeHtml(data.nodes.badge)}</span>`;
    }

    // Authoritative X Status check & countdown
    const xBadge = document.getElementById('x-status');
    if (data.x_status) {
      const isXActive = data.x_status.is_active === true || data.x_status.state === 'ACTIVE_READ_ONLY';
      updateXThemeState(isXActive);

      if (xBadge) {
        if (isXActive) {
          xBadge.className = 'indicator-badge x-active-alert';
          const rem = data.x_status.remaining_seconds != null ? `${data.x_status.remaining_seconds}s` : 'ACTIVE';
          xBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">X: ACTIVE (${rem})</span>`;
          
          const xBannerText = document.querySelector('.x-banner-text');
          if (xBannerText) {
            xBannerText.textContent = `X OFFENSIVE SECURITY EXECUTIVE ACTIVE (READ-ONLY) — EXPIRES IN ${rem}`;
          }
        } else if (data.x_status.state === 'PENDING_APPROVAL') {
          xBadge.className = 'indicator-badge status-purple';
          xBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">X: PENDING APPROVAL</span>`;
        } else {
          xBadge.className = 'indicator-badge x-dormant';
          xBadge.innerHTML = `<span class="badge-dot"></span><span class="badge-label">X: DORMANT</span>`;
        }
      }
    } else if (data.sentinel && data.sentinel.x_red_team) {
      const isX = data.sentinel.x_red_team.status === 'ACTIVE';
      updateXThemeState(isX);
    }

    // Desktop telemetry panel
    const deskPanel = document.getElementById('desktop-telemetry');
    if (deskPanel && data.desktop) {
      deskPanel.innerHTML = `
        <div>Current App: ${escapeHtml(data.desktop.current_app)}</div>
        <div>Control Method: ${escapeHtml(data.desktop.control_method)}</div>
        <div>Human Verification: ${escapeHtml(data.desktop.verification_state)}</div>
        <div>Financial Spend: ${escapeHtml(data.desktop.financial_spend)}</div>
        <div>Unattended Tasks: ${escapeHtml(data.desktop.unattended_tasks)}</div>
      `;
    }

    const dtActiveWin = document.getElementById('dt-active-window');
    if (dtActiveWin && data.desktop) {
      dtActiveWin.textContent = data.desktop.current_app;
    }

    // Evolution telemetry panel
    const evoPanel = document.getElementById('evolution-telemetry');
    if (evoPanel && data.evolution) {
      evoPanel.innerHTML = `
        <div>Level 1 (Ext): ${escapeHtml(data.evolution.level_1)}</div>
        <div>Level 2 (Self): ${escapeHtml(data.evolution.level_2)}</div>
        <div>Level 3 (HOOD): ${escapeHtml(data.evolution.level_3)}</div>
        <div>GPU Topology: ${escapeHtml(data.evolution.gpu_topology)}</div>
      `;
    }

    // Voice telemetry panel
    const voicePanel = document.getElementById('voice-telemetry');
    if (voicePanel && data.voice) {
      voicePanel.innerHTML = `
        <div>Daily Voice Spend: ${escapeHtml(data.voice.daily_spend)}</div>
        <div>Active Sessions: ${escapeHtml(data.voice.active_sessions)}</div>
        <div>Microphone Privacy: SAFE (No Ambient Cloud Recording)</div>
      `;
    }

    // Project Sentinel telemetry widget
    if (data.sentinel) {
      const sentPosture = document.getElementById('sentinel-posture');
      if (sentPosture) {
        sentPosture.textContent = data.sentinel.posture;
        sentPosture.style.color = data.sentinel.posture === 'OPTIMAL RESILIENCE' ? '#00f2aa' : '#ff0055';
      }

      const tabSentPosture = document.getElementById('tab-sentinel-posture');
      if (tabSentPosture) {
        tabSentPosture.textContent = data.sentinel.posture;
        tabSentPosture.style.color = data.sentinel.posture === 'OPTIMAL RESILIENCE' ? '#00f2aa' : '#ff0055';
      }

      const sentFw = document.getElementById('sentinel-firewall');
      if (sentFw) sentFw.textContent = `${data.sentinel.firewall.active ? 'ACTIVE' : 'INACTIVE'} (${data.sentinel.firewall.mode})`;

      const sentVulns = document.getElementById('sentinel-vulns');
      const tabSentVulns = document.getElementById('tab-sentinel-vulns');
      if (sentVulns) {
        const crit = data.sentinel.findings_by_severity ? data.sentinel.findings_by_severity.CRITICAL || 0 : 0;
        const text = `${data.sentinel.open_findings_count} Open (${crit} Critical)`;
        sentVulns.textContent = text;
        if (tabSentVulns) tabSentVulns.textContent = text;
      }

      const sentInteg = document.getElementById('sentinel-integrity');
      if (sentInteg) {
        // null/undefined means "not inspected" — never render it as verified.
        const drift = data.sentinel.integrity ? data.sentinel.integrity.drift_detected : null;
        sentInteg.textContent = drift === true ? 'DRIFT DETECTED' : (drift === false ? 'BASELINES VERIFIED' : 'NOT INSPECTED');
        sentInteg.style.color = drift === true ? '#ff0055' : (drift === false ? '#00f2aa' : '#f5c542');
      }

      const sentX = document.getElementById('sentinel-x-status');
      const tabSentX = document.getElementById('tab-sentinel-x');
      const isXActive = data.x_status && (data.x_status.is_active || data.x_status.state === 'ACTIVE_READ_ONLY');
      const isXPending = data.x_status && data.x_status.state === 'PENDING_APPROVAL';
      const xDisplay = isXActive ? 'ACTIVE (READ-ONLY)' : (isXPending ? 'PENDING APPROVAL' : 'DORMANT');
      if (sentX) {
        sentX.textContent = xDisplay;
        sentX.style.color = isXActive ? '#ff0055' : (isXPending ? '#b388ff' : '#8892b0');
      }
      if (tabSentX) {
        tabSentX.textContent = xDisplay;
        tabSentX.style.color = isXActive ? '#ff0055' : (isXPending ? '#b388ff' : '#8892b0');
      }
    }
  } catch (err) {}
}

// Economic Engine Data Loader
async function loadEconomicData() {
  try {
    const res = await fetch('/api/economic/summary');
    if (res.ok) {
      const data = await res.json();
      const modeVal = document.getElementById('econ-mode-val');
      if (modeVal) modeVal.textContent = data.mode;
    }
  } catch (err) {}
}

// Impossible List Data Loader
async function loadImpossibleListData() {
  try {
    const res = await fetch('/api/impossible_list');
    if (res.ok) {
      const items = await res.json();
      const container = document.getElementById('impossible-list-container');
      if (container) {
        if (!items || items.length === 0) {
          container.innerHTML = '<div style="color:#00f2aa;">No unresolved frontier barriers recorded. Frontier clear.</div>';
        } else {
          container.innerHTML = items.map(it => `
            <div style="margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid rgba(255,255,255,0.05);">
              <div><strong>[${escapeHtml(it.category)}]</strong> ${escapeHtml(it.objective)}</div>
              <div style="color:var(--text-muted); font-size:0.7rem;">Required: ${escapeHtml(it.required_capability)} | Potential Value: $${escapeHtml(it.estimated_economic_value_usd || 0)}</div>
            </div>
          `).join('');
        }
      }
    }
  } catch (err) {}
}

// Sovereign Memory Data Loader
async function loadMemoryData() {
  try {
    const res = await fetch('/api/memory/list');
    if (res.ok) {
      const data = await res.json();
      const countBadge = document.getElementById('memory-count-badge');
      if (countBadge) countBadge.textContent = `${data.count} MEMORIES STORED`;

      const memStatTotal = document.getElementById('mem-stat-total');
      if (memStatTotal) memStatTotal.textContent = `${data.count} ACTIVE`;

      const tbody = document.getElementById('memory-table-body');
      if (tbody) {
        if (!data.memories || data.memories.length === 0) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No sovereign memory records stored yet. Ask questions or interact to persist memories.</td></tr>';
        } else {
          tbody.innerHTML = data.memories.map(m => `
            <tr>
              <td><code>${escapeHtml(m.project || 'personal')}</code></td>
              <td><strong>${escapeHtml(m.key || 'Fact')}</strong>: ${escapeHtml(m.content)}</td>
              <td><span class="val-green">${escapeHtml(m.confidence || 1.0)}</span></td>
              <td>${escapeHtml(m.source || 'user_explicit')}</td>
              <td><small>${m.created_at ? new Date(m.created_at).toLocaleDateString() : 'Persisted'}</small></td>
            </tr>
          `).join('');
        }
      }
    }
  } catch (err) {
    console.error('Failed to load sovereign memory data:', err);
  }
}

// ----------------------------------------------------
// Authentication & Multi-User Governance
// ----------------------------------------------------
const authModal = document.getElementById('auth-modal');
const loginForm = document.getElementById('login-form');
const setupForm = document.getElementById('setup-form');
const recoverForm = document.getElementById('recover-form');
const recoveryKeyModal = document.getElementById('recovery-key-modal');
const displayedRecoveryKey = document.getElementById('displayed-recovery-key');
const btnDismissRecovery = document.getElementById('btn-dismiss-recovery');

const userBadge = document.getElementById('user-badge');
const userDisplay = document.getElementById('user-display');
const btnLogout = document.getElementById('btn-logout');
const btnAdminPanel = document.getElementById('btn-admin-panel');

const adminModal = document.getElementById('admin-modal');
const btnCloseAdmin = document.getElementById('btn-close-admin');
const createUserForm = document.getElementById('create-user-form');
const userTableBody = document.getElementById('user-table-body');
const createUserStatus = document.getElementById('create-user-status');

const btnSecurityPanel = document.getElementById('btn-security-panel');
const securityModal = document.getElementById('security-modal');
const btnCloseSecurity = document.getElementById('btn-close-security');
const changePasswordForm = document.getElementById('change-password-form');
const changePasswordStatus = document.getElementById('change-password-status');
const rotateKeyForm = document.getElementById('rotate-key-form');
const rotateKeyStatus = document.getElementById('rotate-key-status');
const newKeyDisplayBox = document.getElementById('new-key-display-box');
const newRecoveryKeyText = document.getElementById('new-recovery-key-text');
const btnAckNewKey = document.getElementById('btn-ack-new-key');
const sessionsTableBody = document.getElementById('sessions-table-body');
const btnRevokeOtherSessions = document.getElementById('btn-revoke-other-sessions');
const sessionRevokeStatus = document.getElementById('session-revoke-status');

const linkShowRecover = document.getElementById('link-show-recover');
const linkBackToLogin = document.getElementById('link-back-to-login');

let currentAuthUser = null;

async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();

    if (data.enabled === false) {
      if (authModal) authModal.style.display = 'none';
      return;
    }

    if (!data.initialized) {
      if (authModal) authModal.style.display = 'flex';
      if (setupForm) setupForm.style.display = 'flex';
      if (loginForm) loginForm.style.display = 'none';
      if (recoverForm) recoverForm.style.display = 'none';
      return;
    }

    setCsrfToken(data.authenticated ? data.csrf_token : null);
    if (data.authenticated) {
      if (authModal) authModal.style.display = 'none';
      currentAuthUser = { username: data.username, role: data.role };
      const govName = document.getElementById('gov-pill-name');
      if (govName) govName.textContent = String(data.username || '').toUpperCase();
      const govStatus = document.getElementById('gov-pill-status');
      if (govStatus) govStatus.textContent = '● SIGNED IN · ' + String(data.role || '');
      if (userBadge) userBadge.style.display = 'flex';
      if (userDisplay) userDisplay.textContent = `${data.username.toUpperCase()} (${data.role})`;
      
      const isRoot = data.role === 'ROOT_OWNER';
      if (btnAdminPanel) btnAdminPanel.style.display = isRoot ? 'block' : 'none';
      if (btnSecurityPanel) btnSecurityPanel.style.display = isRoot ? 'block' : 'none';
      const btnSentinel = document.getElementById('btn-sentinel-center');
      if (btnSentinel) btnSentinel.style.display = 'block';
    } else {
      if (authModal) authModal.style.display = 'flex';
      if (setupForm) setupForm.style.display = 'none';
      if (loginForm) loginForm.style.display = 'flex';
      if (recoverForm) recoverForm.style.display = 'none';
      if (userBadge) userBadge.style.display = 'none';
      if (btnAdminPanel) btnAdminPanel.style.display = 'none';
      if (btnSecurityPanel) btnSecurityPanel.style.display = 'none';
    }
  } catch (err) {
    console.error('Failed to check auth status:', err);
  }
}

// Setup Form Submission
if (setupForm) {
  setupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('setup-username').value.trim();
    const displayName = document.getElementById('setup-display-name').value.trim();
    const password = document.getElementById('setup-password').value;
    const errEl = document.getElementById('setup-error');
    errEl.style.display = 'none';

    try {
      const res = await fetch('/api/auth/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, display_name: displayName, password })
      });
      const data = await res.json();
      if (res.ok && data.status === 'INITIALIZED') {
        if (data.one_time_recovery_key && displayedRecoveryKey) {
          displayedRecoveryKey.textContent = data.one_time_recovery_key;
          setupForm.style.display = 'none';
          if (recoveryKeyModal) recoveryKeyModal.style.display = 'block';
        } else {
          checkAuthStatus();
        }
      } else {
        errEl.textContent = data.error || 'Setup failed';
        errEl.style.display = 'block';
      }
    } catch (err) {
      errEl.textContent = 'Network error: ' + err.message;
      errEl.style.display = 'block';
    }
  });
}

// Dismiss Recovery Key
if (btnDismissRecovery) {
  btnDismissRecovery.addEventListener('click', () => {
    if (recoveryKeyModal) recoveryKeyModal.style.display = 'none';
    if (loginForm) loginForm.style.display = 'flex';
    checkAuthStatus();
  });
}

// Login Form Submission
if (loginForm) {
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.style.display = 'none';

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      const data = await res.json();
      if (res.ok && data.status === 'AUTHENTICATED') {
        setCsrfToken(data.csrf_token);
        checkAuthStatus();
      } else {
        errEl.textContent = data.error || 'Authentication failed';
        errEl.style.display = 'block';
      }
    } catch (err) {
      errEl.textContent = 'Network error: ' + err.message;
      errEl.style.display = 'block';
    }
  });
}

// Logout
if (btnLogout) {
  btnLogout.addEventListener('click', async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
      checkAuthStatus();
    } catch (err) {}
  });
}

// Recovery Links
if (linkShowRecover) {
  linkShowRecover.addEventListener('click', (e) => {
    e.preventDefault();
    if (loginForm) loginForm.style.display = 'none';
    if (recoverForm) recoverForm.style.display = 'flex';
  });
}

if (linkBackToLogin) {
  linkBackToLogin.addEventListener('click', (e) => {
    e.preventDefault();
    if (recoverForm) recoverForm.style.display = 'none';
    if (loginForm) loginForm.style.display = 'flex';
  });
}

// Recover Form Submission
if (recoverForm) {
  recoverForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const recoveryKey = document.getElementById('recover-key').value.trim();
    const newPassword = document.getElementById('recover-new-password').value;
    const errEl = document.getElementById('recover-error');
    errEl.style.display = 'none';

    try {
      const res = await fetch('/api/auth/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recovery_key: recoveryKey, new_password: newPassword })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        alert('Master password reset successfully. Please log in with your new credentials.');
        recoverForm.style.display = 'none';
        if (loginForm) loginForm.style.display = 'flex';
      } else {
        errEl.textContent = data.error || 'Recovery failed';
        errEl.style.display = 'block';
      }
    } catch (err) {
      errEl.textContent = 'Network error: ' + err.message;
      errEl.style.display = 'block';
    }
  });
}

// Admin Panel Toggle
if (btnAdminPanel) {
  btnAdminPanel.addEventListener('click', () => {
    adminModal.style.display = 'flex';
    loadUserRegistry();
  });
}

if (btnCloseAdmin) {
  btnCloseAdmin.addEventListener('click', () => {
    adminModal.style.display = 'none';
  });
}

// Create User Identity
if (createUserForm) {
  createUserForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btnSubmit = document.getElementById('btn-create-user-submit');
    const usernameInput = document.getElementById('new-user-username');
    const displayInput = document.getElementById('new-user-display');
    const passInput = document.getElementById('new-user-pass');
    const roleInput = document.getElementById('new-user-role');

    const username = usernameInput ? usernameInput.value.trim().toLowerCase() : '';
    const displayName = displayInput ? displayInput.value.trim() : '';
    const password = passInput ? passInput.value : '';
    const role = roleInput ? roleInput.value : 'OPERATOR';

    createUserStatus.textContent = '';

    // Client-side validations
    if (!username || username.length < 3) {
      createUserStatus.textContent = 'Error: Username must be at least 3 characters.';
      createUserStatus.style.color = '#ff0055';
      return;
    }

    if (!displayName || displayName.length < 2) {
      createUserStatus.textContent = 'Error: Display name must be at least 2 characters.';
      createUserStatus.style.color = '#ff0055';
      return;
    }

    if (!password || password.length < 10) {
      createUserStatus.textContent = 'Error: Password must be at least 10 characters with uppercase, lowercase, and a digit or symbol.';
      createUserStatus.style.color = '#ff0055';
      return;
    }

    // Protect against duplicate submissions and show loading state
    if (btnSubmit) {
      btnSubmit.disabled = true;
      btnSubmit.textContent = 'CREATING IDENTITY...';
    }

    try {
      const res = await fetch('/api/admin/users/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, display_name: displayName, password, role })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        createUserStatus.textContent = `✓ Identity '${username}' successfully created with role [${role}].`;
        createUserStatus.style.color = '#00f2aa';
        createUserForm.reset();
        await loadUserRegistry();
      } else {
        createUserStatus.textContent = `Error: ${data.error || 'Failed to create user'}`;
        createUserStatus.style.color = '#ff0055';
      }
    } catch (err) {
      createUserStatus.textContent = 'Network error: ' + err.message;
      createUserStatus.style.color = '#ff0055';
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.textContent = 'CREATE IDENTITY';
      }
    }
  });
}

async function loadUserRegistry() {
  if (!userTableBody) return;
  try {
    const res = await fetch('/api/admin/users');
    if (!res.ok) return;
    const users = await res.json();
    userTableBody.innerHTML = '';
    users.forEach(u => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${escapeHtml(u.username)}</strong></td>
        <td>${escapeHtml(u.display_name)}</td>
        <td><span class="user-role-badge">${escapeHtml(u.role)}</span></td>
        <td><span class="status-indicator ${u.is_active ? 'active' : 'suspended'}">${u.is_active ? 'ACTIVE' : 'SUSPENDED'}</span></td>
        <td>${escapeHtml(Array.isArray(u.assigned_projects) ? u.assigned_projects.join(', ') : 'GLOBAL')}</td>
        <td>
          ${u.role !== 'ROOT_OWNER' ? `<button class="btn-status-toggle" type="button">${u.is_active ? 'SUSPEND' : 'ACTIVATE'}</button>` : '<em style="color:#64748b;">IMMUTABLE</em>'}
        </td>
      `;
      const toggle = tr.querySelector('button.btn-status-toggle');
      if (toggle) toggle.addEventListener('click', () => window.toggleUserStatus(u.user_id, !u.is_active));
      userTableBody.appendChild(tr);
    });
  } catch (err) {}
}

window.toggleUserStatus = async function(userId, newActive) {
  try {
    const res = await fetch('/api/admin/users/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, is_active: newActive })
    });
    if (res.ok) loadUserRegistry();
  } catch (err) {}
};

// Security Panel Toggle
if (btnSecurityPanel) {
  btnSecurityPanel.addEventListener('click', () => {
    securityModal.style.display = 'flex';
    loadActiveSessions();
  });
}

if (btnCloseSecurity) {
  btnCloseSecurity.addEventListener('click', () => {
    securityModal.style.display = 'none';
  });
}

// Change Master Password
if (changePasswordForm) {
  changePasswordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const currentPassword = document.getElementById('cp-current-pass').value;
    const newPassword = document.getElementById('cp-new-pass').value;
    try {
      const res = await fetch('/api/auth/change_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        changePasswordStatus.textContent = 'Master password updated successfully.';
        changePasswordStatus.style.color = '#00f2aa';
        changePasswordForm.reset();
      } else {
        changePasswordStatus.textContent = data.error || 'Password update failed';
        changePasswordStatus.style.color = '#ff0055';
      }
    } catch (err) {
      changePasswordStatus.textContent = 'Error: ' + err.message;
      changePasswordStatus.style.color = '#ff0055';
    }
  });
}

// Rotate Recovery Key
if (rotateKeyForm) {
  rotateKeyForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const currentPassword = document.getElementById('rk-current-pass').value;
    try {
      const res = await fetch('/api/auth/rotate_recovery_key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        rotateKeyStatus.textContent = 'New recovery key issued.';
        rotateKeyStatus.style.color = '#00f2aa';
        newRecoveryKeyText.textContent = data.one_time_recovery_key;
        newKeyDisplayBox.style.display = 'block';
        rotateKeyForm.reset();
      } else {
        rotateKeyStatus.textContent = data.error || 'Failed to rotate key';
        rotateKeyStatus.style.color = '#ff0055';
      }
    } catch (err) {
      rotateKeyStatus.textContent = 'Error: ' + err.message;
      rotateKeyStatus.style.color = '#ff0055';
    }
  });
}

if (btnAckNewKey) {
  btnAckNewKey.addEventListener('click', () => {
    newKeyDisplayBox.style.display = 'none';
  });
}

// Active Sessions
async function loadActiveSessions() {
  if (!sessionsTableBody) return;
  try {
    const res = await fetch('/api/auth/sessions');
    if (!res.ok) return;
    const sessions = await res.json();
    sessionsTableBody.innerHTML = '';
    sessions.forEach(s => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><code>${escapeHtml(String(s.session_id || '').substring(0, 12))}…</code></td>
        <td>${escapeHtml(s.ip_address)}</td>
        <td><small>${escapeHtml(s.user_agent ? s.user_agent.substring(0, 30) : 'CLI / Surface')}</small></td>
        <td>${escapeHtml(new Date(s.created_at).toLocaleTimeString())}</td>
        <td>${escapeHtml(new Date(s.expires_at).toLocaleTimeString())}</td>
        <td>
          <button class="btn-status-toggle" type="button">REVOKE</button>
        </td>
      `;
      tr.querySelector('button').addEventListener('click', () => window.revokeSession(s.session_id));
      sessionsTableBody.appendChild(tr);
    });
  } catch (err) {}
}

window.revokeSession = async function(sessionId) {
  try {
    const res = await fetch('/api/auth/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId })
    });
    if (res.ok) loadActiveSessions();
  } catch (err) {}
};

if (btnRevokeOtherSessions) {
  btnRevokeOtherSessions.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/auth/sessions/revoke_others', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        sessionRevokeStatus.textContent = `Revoked ${data.revoked_count} other sessions.`;
        sessionRevokeStatus.style.color = '#00f2aa';
        loadActiveSessions();
      }
    } catch (err) {
      sessionRevokeStatus.textContent = 'Error: ' + err.message;
      sessionRevokeStatus.style.color = '#ff0055';
    }
  });
}

// ----------------------------------------------------
// Project Sentinel v1.3 Cybersecurity Center
// ----------------------------------------------------
const btnSentinelCenter = document.getElementById('btn-sentinel-center');
const sentinelModal = document.getElementById('sentinel-modal');
const btnCloseSentinel = document.getElementById('btn-close-sentinel');
const btnRunSentinelScan = document.getElementById('btn-run-sentinel-scan');
const btnActivateXExercise = document.getElementById('btn-activate-x-exercise');
const btnStanddownX = document.getElementById('btn-standdown-x');
const sentinelActionStatus = document.getElementById('sentinel-action-status');
const sentinelFindingsBody = document.getElementById('sentinel-findings-body');
const sentinelFirewallDetails = document.getElementById('sentinel-firewall-details');
const sentinelHealingDetails = document.getElementById('sentinel-healing-details');
const sentinelCenterPostureBadge = document.getElementById('sentinel-center-posture-badge');

if (btnSentinelCenter) {
  btnSentinelCenter.addEventListener('click', () => {
    if (sentinelModal) sentinelModal.style.display = 'flex';
    loadSentinelData();
  });
}

const btnOpenSentinelDirect = document.getElementById('btn-open-sentinel-modal-direct');
if (btnOpenSentinelDirect) {
  btnOpenSentinelDirect.addEventListener('click', () => {
    if (sentinelModal) sentinelModal.style.display = 'flex';
    loadSentinelData();
  });
}

if (btnCloseSentinel) {
  btnCloseSentinel.addEventListener('click', () => {
    if (sentinelModal) sentinelModal.style.display = 'none';
  });
}

async function loadSentinelData() {
  try {
    const [summaryRes, findingsRes] = await Promise.all([
      fetch('/api/sentinel/summary'),
      fetch('/api/sentinel/findings')
    ]);

    if (summaryRes.ok) {
      const summary = await summaryRes.json();
      if (sentinelCenterPostureBadge) {
        sentinelCenterPostureBadge.textContent = summary.posture;
        sentinelCenterPostureBadge.style.color = summary.posture === 'OPTIMAL RESILIENCE' ? '#00f2aa' : '#ff0055';
      }

      if (sentinelFirewallDetails) {
        sentinelFirewallDetails.innerHTML = `
          <div>Windows Firewall State: <strong>${escapeHtml(summary.firewall.active ? 'ACTIVE' : 'INACTIVE')}</strong> (${escapeHtml(summary.firewall.mode)})</div>
          <div>Audited Listening Ports: ${escapeHtml(summary.firewall.listening_ports_count)} TCP sockets</div>
          <div>Unexpected Exposed Ports (0.0.0.0): ${escapeHtml(summary.firewall.unexpected_ports.length > 0 ? summary.firewall.unexpected_ports.join(', ') : 'None (Compliant)')}</div>
          <div>Baseline Deviations: ${escapeHtml(summary.firewall.baseline_deviations.length)}</div>
        `;
      }

      if (sentinelHealingDetails) {
        const lastAction = summary.self_healing.last_action;
        sentinelHealingDetails.innerHTML = `
          <div>Recent Autonomous Repairs: ${escapeHtml(summary.self_healing.recent_actions_count)}</div>
          <div>Last Self-Healing Action: ${escapeHtml(lastAction ? `${lastAction.description} (${lastAction.success ? 'SUCCESS' : 'FAILED'})` : 'None / Standby')}</div>
          <div>Immutable Boundaries: ROOT_OWNER, GOVERNANCE, KEYS, FIREWALL (Protected)</div>
        `;
      }
    }

    if (findingsRes.ok && sentinelFindingsBody) {
      const findings = await findingsRes.json();
      sentinelFindingsBody.innerHTML = '';
      if (findings.length === 0) {
        sentinelFindingsBody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#00f2aa;">No active vulnerabilities detected. System baseline verified.</td></tr>`;
      } else {
        findings.forEach(f => {
          const row = document.createElement('tr');
          const sevColor = f.severity === 'CRITICAL' ? '#ff0055' : (f.severity === 'HIGH' ? '#ff7b00' : '#f6d365');
          row.innerHTML = `
            <td><code>${escapeHtml(f.finding_id)}</code></td>
            <td><strong style="color: ${sevColor}">${escapeHtml(f.severity)}</strong></td>
            <td>${escapeHtml(f.category)}</td>
            <td>${escapeHtml(f.affected_component)}</td>
            <td>${escapeHtml(f.status)}</td>
            <td>${escapeHtml(f.title)}</td>
          `;
          sentinelFindingsBody.appendChild(row);
        });
      }
    }
  } catch (err) {
    console.error('Failed to load sentinel data:', err);
  }
}

if (btnRunSentinelScan) {
  btnRunSentinelScan.addEventListener('click', async () => {
    sentinelActionStatus.textContent = 'Running comprehensive non-destructive assessment...';
    sentinelActionStatus.style.color = '#00f2fe';
    try {
      const res = await fetch('/api/sentinel/scan', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        sentinelActionStatus.textContent = 'Defensive scan completed. Vulnerability registry updated.';
        sentinelActionStatus.style.color = '#00f2aa';
        loadSentinelData();
        loadTelemetry();
      } else {
        sentinelActionStatus.textContent = data.error || 'Scan failed.';
        sentinelActionStatus.style.color = '#ff0055';
      }
    } catch (err) {
      sentinelActionStatus.textContent = 'Error: ' + err.message;
      sentinelActionStatus.style.color = '#ff0055';
    }
  });
}

if (btnActivateXExercise) {
  btnActivateXExercise.addEventListener('click', async () => {
    sentinelActionStatus.textContent = 'Requesting ZACK Root Owner authorization for X exercise...';
    sentinelActionStatus.style.color = '#f6d365';
    try {
      const res = await fetch('/api/sentinel/x/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: 'ISOLATED_HOOD_SANDBOX' })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        sentinelActionStatus.textContent = `X Red-Team ACTIVATED for sandbox exercise (${data.scope.exercise_id}). Boundaries enforced.`;
        sentinelActionStatus.style.color = '#f6d365';
        updateXThemeState(true);
        loadSentinelData();
        loadTelemetry();
      } else {
        sentinelActionStatus.textContent = data.error || 'Activation failed.';
        sentinelActionStatus.style.color = '#ff0055';
      }
    } catch (err) {
      sentinelActionStatus.textContent = 'Error: ' + err.message;
      sentinelActionStatus.style.color = '#ff0055';
    }
  });
}

if (btnStanddownX) {
  btnStanddownX.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/sentinel/x/stand_down', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        sentinelActionStatus.textContent = 'X has stood down. Authority sealed; X is DORMANT.';
        sentinelActionStatus.style.color = '#00f2aa';
        updateXThemeState(false);
        setUiState('IDLE');
        loadSentinelData();
        loadTelemetry();
      }
    } catch (err) {
      sentinelActionStatus.textContent = 'Error: ' + err.message;
      sentinelActionStatus.style.color = '#ff0055';
    }
  });
}

window.resolveApproval = resolveApproval;

// Polling loops
setInterval(loadApprovals, 5000);
setInterval(loadTelemetry, 5000);
setInterval(loadEconomicData, 10000);
setInterval(loadImpossibleListData, 15000);

// Initial bootstrap
loadApprovals();
loadTelemetry();
loadEconomicData();
loadImpossibleListData();
loadMemoryData();
checkAuthStatus();

// HOOD NOVA 2.0 — truthful subsystem inventory and local-only preferences.
const novaGrid = document.getElementById('nova-capability-grid');
const novaSearch = document.getElementById('nova-search');
const novaCategory = document.getElementById('nova-category');
let novaItems = [];
const novaSafeTarget = /^tab-[a-z-]+$/;

function novaRender() {
  if (!novaGrid) return;
  const query = (novaSearch?.value || '').toLowerCase().trim();
  const category = novaCategory?.value || 'all';
  const filtered = novaItems.filter(item => (category === 'all' || item.category === category) &&
    (`${item.name} ${item.category} ${item.description} ${item.status}`).toLowerCase().includes(query));
  novaGrid.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement('p');
    empty.className = 'nova-inventory-note';
    empty.textContent = novaItems.length ? 'No matching capabilities.' : 'No inventory loaded.';
    novaGrid.appendChild(empty);
    return;
  }
  for (const item of filtered) {
    const card = document.createElement('article');
    card.className = 'nova-capability';
    const top = document.createElement('div');
    top.className = 'nova-capability-top';
    const categoryName = document.createElement('span');
    categoryName.className = 'nova-card-category';
    categoryName.textContent = item.category;
    const status = document.createElement('span');
    const recognized = ['ATTACHED_UNVERIFIED', 'MODULE_ONLY', 'UNAVAILABLE', 'PLACEHOLDER'];
    const safeStatus = recognized.includes(item.status) ? item.status : 'UNVERIFIED';
    status.className = `nova-status nova-${safeStatus.toLowerCase().replaceAll('_','-')}`;
    status.textContent = safeStatus.replaceAll('_',' ');
    top.append(categoryName, status);
    const heading = document.createElement('h3');
    heading.textContent = item.name;
    const desc = document.createElement('p');
    desc.textContent = item.description;
    const footer = document.createElement('div');
    footer.className = 'nova-capability-footer';
    const source = document.createElement('span');
    source.textContent = item.source_present ? 'Source module found' : 'Not implemented in source';
    const action = document.createElement('button');
    action.type = 'button';
    action.textContent = item.action === 'OPEN_SECTION' ? 'Open section ↗' : 'Details only';
    action.disabled = item.action !== 'OPEN_SECTION' || !novaSafeTarget.test(item.section);
    if (!action.disabled) action.addEventListener('click', () => switchTab(item.section));
    footer.append(source, action);
    card.append(top, heading, desc, footer);
    novaGrid.appendChild(card);
  }
}

async function novaLoad() {
  if (!novaGrid) return;
  const error = document.getElementById('nova-error');
  const note = document.getElementById('nova-inventory-note');
  if (error) { error.hidden = true; error.textContent = ''; }
  if (note) note.textContent = 'Checking the local capability registry…';
  try {
    const response = await fetch('/api/capabilities', { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in to inspect services.' : `Registry request failed (HTTP ${response.status}).`);
    const data = await response.json();
    if (!Array.isArray(data.items)) throw new Error('Capability registry response is invalid.');
    novaItems = data.items;
    const categories = [...new Set(novaItems.map(x => x.category))].sort();
    const selected = novaCategory?.value || 'all';
    if (novaCategory) {
      novaCategory.replaceChildren(new Option('All systems', 'all'));
      categories.forEach(cat => novaCategory.add(new Option(cat, cat)));
      novaCategory.value = categories.includes(selected) ? selected : 'all';
    }
    const stats = document.getElementById('nova-stats');
    if (stats) {
      stats.replaceChildren();
      for (const [label, value] of [['Subsystems', novaItems.length], ['Attached · unverified', data.counts?.ATTACHED_UNVERIFIED ?? 0], ['Source modules only', data.counts?.MODULE_ONLY ?? 0], ['Placeholders / unavailable', (data.counts?.PLACEHOLDER ?? 0) + (data.counts?.UNAVAILABLE ?? 0)]]) {
        const cell = document.createElement('div');
        const number = document.createElement('strong'); number.textContent = String(value);
        const caption = document.createElement('span'); caption.textContent = label;
        cell.append(number, caption); stats.appendChild(cell);
      }
    }
    if (note) note.textContent = `${data.release || 'Hood'} · ${data.basis || 'Inventory only'}. Last refreshed ${new Date().toLocaleTimeString()}.`;
    const launch = document.getElementById('nova-launch-status');
    if (launch) launch.textContent = `${novaItems.length} configured capabilities · not certified`;
    novaRender();
  } catch (e) {
    novaItems = [];
    novaRender();
    if (note) note.textContent = 'Registry unavailable.';
    const launch = document.getElementById('nova-launch-status');
    if (launch) launch.textContent = 'Sign in to view systems';
    if (error) { error.hidden = false; error.textContent = e.message; }
  }
}

novaSearch?.addEventListener('input', novaRender);
novaCategory?.addEventListener('change', novaRender);
document.getElementById('nova-refresh')?.addEventListener('click', novaLoad);

const novaPreferenceKeys = { 'nova-reduced-motion': 'hood.nova.reduceMotion', 'nova-compact-cards': 'hood.nova.compactCards' };
function novaApplyPreferences() {
  Object.entries(novaPreferenceKeys).forEach(([id, key]) => {
    const value = localStorage.getItem(key) === 'true';
    const checkbox = document.getElementById(id);
    if (checkbox) checkbox.checked = value;
    document.body.classList.toggle(id, value);
  });
}
Object.entries(novaPreferenceKeys).forEach(([id, key]) => document.getElementById(id)?.addEventListener('change', event => {
  localStorage.setItem(key, String(event.target.checked));
  novaApplyPreferences();
}));
document.getElementById('nova-reset-preferences')?.addEventListener('click', () => {
  Object.values(novaPreferenceKeys).forEach(key => localStorage.removeItem(key));
  novaApplyPreferences();
});
novaApplyPreferences();
novaLoad();

// Integrated command-deck launchpad; no unverified execution is implied.
document.querySelectorAll('.nova-launch-action').forEach(button => button.addEventListener('click', () => {
  const section = button.dataset.target;
  if (section === 'tab-systems') switchTab(section);
}));

// NOVA 2.1 — owner-scoped persistent mission planning (not autonomous execution).
async function novaOperation(path, body) {
  const response = await fetch(path, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function novaMissionMessage(message, isError = false) {
  const el = document.getElementById('nova-mission-feedback');
  if (el) { el.textContent = message; el.setAttribute('data-error', String(isError)); }
}
async function novaLoadMissions() {
  const target = document.getElementById('nova-mission-records');
  if (!target) return;
  target.textContent = 'Loading mission records…';
  try {
    const response = await fetch('/api/operations', {credentials: 'same-origin', cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to retrieve missions');
    if (!data.length) { target.textContent = 'No mission records yet.'; return; }
    target.replaceChildren();
    for (const item of data) {
      const card = document.createElement('article');
      card.className = 'nova-mission-record';
      const title = document.createElement('h4'); title.textContent = item.title;
      const status = document.createElement('p'); status.textContent = `${item.status} · ${item.execution_mode} · ${new Date(item.created).toLocaleString()}`;
      const objective = document.createElement('p'); objective.textContent = item.objective;
      card.append(title, status, objective);
      if (Array.isArray(item.steps)) {
        const steps = document.createElement('ol');
        steps.className = 'nova-mission-steps';
        for (const step of item.steps) {
          const line = document.createElement('li');
          line.textContent = `${step.label} — ${step.status}${step.evidence ? ' · ' + step.evidence : ''}`;
          steps.appendChild(line);
        }
        card.appendChild(steps);
      }
      if (item.specialist_review) {
        const review = document.createElement('p');
        review.textContent = 'Local specialist analysis verified; advisory only, no LLM agents or business actions executed.';
        const link = document.createElement('a');
        link.href = `/api/operations/specialist-artifact/${encodeURIComponent(item.id)}`;
        link.textContent = 'Download specialist analysis';
        card.append(review, link);
      } else if (item.status === 'PLAN_CREATED') {
        const analyzeButton = document.createElement('button');
        analyzeButton.className = 'nova-action';
        analyzeButton.textContent = 'Run local specialist review';
        analyzeButton.onclick = async () => {
          if (!window.confirm('Run deterministic local deliverable and boundary reviewers? No LLM, network or external tools. Results are advisory only.')) return;
          analyzeButton.disabled = true;
          try {
            await novaOperation('/api/operations/analyze', {mission_id: item.id, confirm: true});
            novaMissionMessage('Local specialist report independently recomputed and stored.');
            await novaLoadMissions();
          } catch (err) { novaMissionMessage(err.message, true); analyzeButton.disabled = false; }
        };
        card.append(analyzeButton);
      }
      if (item.llm_review) {
        const result = document.createElement('p');
        result.textContent = 'Gemini specialist advice saved. Schema-validated only; not fact checked, no objective executed.';
        const link = document.createElement('a');
        link.href = `/api/operations/llm-artifact/${encodeURIComponent(item.id)}`;
        link.textContent = 'Download AI specialist report';
        card.append(result, link);
      } else if (item.status === 'PLAN_CREATED') {
        const aiButton = document.createElement('button');
        aiButton.className = 'nova-action';
        aiButton.textContent = 'Run Gemini AI specialist review (opt-in)';
        aiButton.onclick = async () => {
          if (!window.confirm('Send this mission objective to the configured Gemini API? Provider usage may consume quota or incur charges. Two analysis calls, no tool execution.')) return;
          aiButton.disabled = true;
          try {
            await novaOperation('/api/operations/llm-analyze', {
              mission_id: item.id, confirm: true, acknowledge_network: true
            });
            novaMissionMessage('Gemini specialist advice saved; outputs have not been independently fact-checked.');
            await novaLoadMissions();
          } catch (err) { novaMissionMessage(err.message, true); aiButton.disabled = false; }
        };
        card.append(aiButton);
      }
      if (item.local_execution) {
        const note = document.createElement('p');
        note.textContent = `${item.local_execution.status} · deterministic local steps; mission objective NOT executed`;
        const manifest = document.createElement('a');
        manifest.href = `/api/operations/execution-artifact/${encodeURIComponent(item.id)}`;
        manifest.textContent = 'Download verified execution manifest';
        card.append(note, manifest);
      }
      if (item.receipt) {
        const evidence = document.createElement('code');
        evidence.textContent = `SHA256 ${item.receipt.sha256} · ${item.receipt.bytes} bytes · verified=${item.receipt.verified}`;
        const link = document.createElement('a');
        link.href = `/api/operations/artifact/${encodeURIComponent(item.id)}`;
        link.textContent = 'Download verified plan';
        card.append(evidence, link);
        if (item.status === 'PLAN_CREATED' && !item.local_execution) {
          const executeButton = document.createElement('button');
          executeButton.className = 'nova-action';
          executeButton.textContent = 'Run bounded local workflow';
          executeButton.onclick = async () => {
            if (!window.confirm('Run three deterministic local steps and create an evidence manifest? This does NOT execute your business objective, launch agents or access the network.')) return;
            executeButton.disabled = true;
            try { await novaOperation('/api/operations/execute', {mission_id: item.id, confirm: true});
              novaMissionMessage('Local task graph executed and manifest verified; business objective not executed.'); await novaLoadMissions();
            } catch (err) { novaMissionMessage(err.message, true); executeButton.disabled = false; }
          };
          card.append(executeButton);
        }
      } else if (item.status === 'AWAITING_APPROVAL') {
        const confirmButton = document.createElement('button');
        confirmButton.className = 'nova-action'; confirmButton.textContent = 'Approve local plan creation';
        confirmButton.onclick = async () => {
          if (!window.confirm('Create a local Markdown plan only? No agents or external tools will run.')) return;
          confirmButton.disabled = true;
          try {
            await novaOperation('/api/operations/approve', {mission_id: item.id, confirm: true});
            novaMissionMessage('Planning artifact created and integrity receipt saved.'); await novaLoadMissions();
          } catch (err) { novaMissionMessage(err.message, true); confirmButton.disabled = false; }
        };
        const cancelButton = document.createElement('button');
        cancelButton.className = 'nova-action'; cancelButton.textContent = 'Cancel pending mission';
        cancelButton.onclick = async () => {
          cancelButton.disabled = true;
          try { await novaOperation('/api/operations/cancel', {mission_id: item.id}); await novaLoadMissions(); }
          catch (err) { novaMissionMessage(err.message, true); cancelButton.disabled = false; }
        };
        card.append(confirmButton, cancelButton);
      }
      if (item.status === 'PLAN_CREATED') {
        const agentSection = document.createElement('section');
        agentSection.className = 'nova-agent-runtime';
        const heading = document.createElement('h5');
        heading.textContent = 'Local agent runtime · bounded preview only';
        const detail = document.createElement('p');
        detail.textContent = 'Retrieving durable task graph…';
        agentSection.append(heading, detail);
        card.append(agentSection);
        const localAction = (label, path, warning) => {
          const button = document.createElement('button');
          button.className = 'nova-action'; button.textContent = label;
          button.addEventListener('click', async () => {
            if (!window.confirm(warning)) return;
            button.disabled = true;
            try {
              await novaOperation(path, {mission_id: item.id, confirm: true});
              novaMissionMessage('Local runtime updated. This does not complete the mission objective.');
              await novaLoadMissions();
            } catch (error) { novaMissionMessage(error.message, true); button.disabled = false; }
          });
          agentSection.append(button);
        };
        fetch(`/api/operations/agent-status/${encodeURIComponent(item.id)}`, {credentials: 'same-origin', cache: 'no-store'})
          .then(async response => { const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Agent status unavailable'); return result; })
          .then(graph => {
            detail.textContent = `State: ${graph.state} · ${graph.tasks.filter(t => t.status === 'COMPLETED').length}/${graph.tasks.length} steps completed. Objective NOT completed.`;
            const sequence = document.createElement('ol'); sequence.className = 'nova-mission-steps';
            for (const task of graph.tasks) {
              const line = document.createElement('li');
              line.textContent = `${task.agent}: ${task.status}${task.error ? ' · ' + task.error : ''}`;
              sequence.append(line);
            }
            agentSection.append(sequence);
            if (graph.state === 'NOT_STARTED') {
              localAction('Start local agent tasks', '/api/operations/agent-start',
                'Authorize creation of a bounded local HTML prototype? No LLM, shell, external network or business actions.');
            } else if (graph.state === 'READY' || graph.state === 'RUNNING') {
              localAction('Run next local agent task', '/api/operations/agent-step',
                'Execute the next bounded local task? It may write one HTML preview inside Hood workspace.');
              localAction('Cancel local runtime', '/api/operations/agent-cancel',
                'Cancel remaining local tasks?');
            } else if (graph.state === 'COMPLETED') {
              const link = document.createElement('a');
              link.href = `/api/operations/agent-preview/${encodeURIComponent(item.id)}`;
              link.textContent = 'Download verified local HTML preview';
              agentSection.append(link);
            } else if (graph.state === 'BLOCKED') {
              detail.textContent += ' Manual investigation required; automatic retry disabled.';
            }
          }).catch(error => { detail.textContent = error.message; });
      }
      target.appendChild(card);
    }
  } catch (err) { target.textContent = ''; novaMissionMessage(err.message, true); }
}
document.getElementById('nova-mission-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    await novaOperation('/api/operations/create', {
      title: document.getElementById('nova-mission-title').value,
      objective: document.getElementById('nova-mission-objective').value
    });
    form.reset(); novaMissionMessage('Mission created. No execution has started.'); await novaLoadMissions();
  } catch (err) { novaMissionMessage(err.message, true); }
  finally { submit.disabled = false; }
});
document.getElementById('nova-refresh-missions')?.addEventListener('click', novaLoadMissions);

// ==========================================================================
// Agent missions (services/agents). Every value from the server is inserted
// with textContent; state badges show exactly what the backend reports.
// ==========================================================================
(function agentMissions() {
  const form = document.getElementById('agent-mission-form');
  const records = document.getElementById('agent-mission-records');
  const feedback = document.getElementById('agent-mission-feedback');
  const refresh = document.getElementById('agent-refresh-missions');
  const badge = document.getElementById('agent-engine-badge');
  if (!form || !records) return;

  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const say = (msg) => { if (feedback) feedback.textContent = msg; };

  async function post(path, body) {
    const res = await fetch(path, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error */ }
    if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
    return data;
  }

  function button(label, handler) {
    const b = el('button', label, 'nova-action');
    b.type = 'button';
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { await handler(); } catch (err) { say(err.message); } finally { b.disabled = false; load(); }
    });
    return b;
  }

  function renderMission(m) {
    const card = el('div', null, 'nova-mission-record');
    const mode = m.simulated ? 'SIMULATED MODEL' : (m.provider_mode === 'LIVE' ? 'LIVE MODEL' : 'NO MODEL OUTPUT');
    card.appendChild(el('h4', m.mission_id));
    card.appendChild(el('p', 'State: ' + m.state + ' · ' + mode + ' · Spent $' + Number(m.spend || 0).toFixed(4) +
      ' of $' + Number(m.budget_usd).toFixed(2)));
    card.appendChild(el('p', 'Objective: ' + m.objective));
    if (m.error) card.appendChild(el('p', 'Reason: ' + m.error, 'nova-error'));
    if (m.plan) {
      card.appendChild(el('p', 'Plan: ' + m.plan.summary));
      if (m.plan.clarifications_needed && m.plan.clarifications_needed.length) {
        card.appendChild(el('p', 'Questions before approving: ' + m.plan.clarifications_needed.join(' | ')));
      }
    }
    const list = el('ol');
    (m.tasks || []).forEach(t => {
      const deps = t.depends_on && t.depends_on.length ? ' (after ' + t.depends_on.join(', ') + ')' : '';
      list.appendChild(el('li', '[' + t.state + '] ' + t.role + ': ' + t.title + deps + (t.error ? ' — ' + t.error : '')));
    });
    card.appendChild(list);
    if (m.last_verification) {
      const v = m.last_verification;
      card.appendChild(el('p', 'Independent check: ' + v.verdict + ' — ' +
        (v.checks || []).map(c => c.name + (c.passed ? ' ✓' : ' ✗')).join(', ')));
    }
    const actions = el('div');
    if (m.state === 'AWAITING_PLAN_APPROVAL') {
      card.appendChild(el('p', 'Plan hash: ' + m.plan_sha256));
      actions.appendChild(button('Approve this plan and budget', () =>
        post('/api/agents/missions/' + m.mission_id + '/approve', {confirm: true, plan_sha256: m.plan_sha256})));
    }
    if (['QUEUED', 'RUNNING', 'VERIFYING'].includes(m.state) && !m.background_run_active) {
      actions.appendChild(button('Run agents', () =>
        post('/api/agents/missions/' + m.mission_id + '/run', {confirm: true})));
    }
    if (!['COMPLETED', 'FAILED', 'UNVERIFIED', 'CANCELLED'].includes(m.state)) {
      actions.appendChild(button('Cancel mission', () => post('/api/agents/missions/' + m.mission_id + '/cancel', {})));
    }
    if (m.artifact) {
      const link = el('a', 'Download verified result (' + m.artifact.bytes + ' bytes)');
      link.href = '/api/agents/missions/' + m.mission_id + '/artifact';
      link.setAttribute('download', m.artifact.name);
      actions.appendChild(link);
      actions.appendChild(el('p', 'SHA-256: ' + m.artifact.sha256));
    }
    card.appendChild(actions);
    return card;
  }

  async function load() {
    try {
      const res = await fetch('/api/agents/missions', {credentials: 'same-origin', cache: 'no-store'});
      if (res.status === 503) {
        if (badge) badge.textContent = 'ENGINE NOT CONFIGURED';
        records.textContent = 'The agent engine is not configured on this server.';
        return;
      }
      if (!res.ok) { records.textContent = 'Agent missions unavailable (' + res.status + ').'; return; }
      if (badge) badge.textContent = 'ENGINE CONNECTED';
      const missions = await res.json();
      records.replaceChildren();
      if (!missions.length) { records.textContent = 'No agent missions yet.'; return; }
      for (const summary of missions.slice(0, 10)) {
        const detail = await fetch('/api/agents/missions/' + summary.id, {credentials: 'same-origin', cache: 'no-store'});
        if (detail.ok) records.appendChild(renderMission(await detail.json()));
      }
    } catch (err) {
      records.textContent = 'Could not load agent missions: ' + err.message;
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const objective = document.getElementById('agent-mission-objective').value.trim();
    const budget = Number(document.getElementById('agent-mission-budget').value);
    say('Planning… this calls the model provider.');
    try {
      const m = await post('/api/agents/missions', {objective, budget_usd: budget, confirm: true});
      say(m.state === 'AWAITING_PLAN_APPROVAL' ? 'Plan ready for your review.' : 'Planning stopped: ' + (m.error || m.state));
      form.reset();
    } catch (err) {
      say(err.message);
    }
    load();
  });
  if (refresh) refresh.addEventListener('click', load);
  load();
  setInterval(load, 5000);
})();
