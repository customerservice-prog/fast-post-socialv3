/**
 * FastPost Social v3 - Dashboard JavaScript
 * Queue, accounts, analytics, scheduler — uses /api/dashboard for Daily Queue UX.
 */

/** API root: same host as the dashboard, with safe fallbacks for odd origins. */
const API = (() => {
  const o = String(window.location.origin || '');
  if (o && o !== 'null' && !o.startsWith('file')) return `${o}/api`;
  const host = window.location.hostname || '127.0.0.1';
  const port = window.location.port || '5000';
  return `http://${host}:${port}/api`;
})();

let currentPage = 'queue';
let editingPostId = null;
let accounts = [];
/** From GET /api/dashboard: true on Railway/cloud (no browser window on your PC). */
let postingHeadless = true;
/** Account id for the Playwright session paste modal. */
let sessionModalAccountId = null;
/** Meta app configured on server — enables Connect Facebook (Graph API). */
let facebookOAuthConfigured = false;
/** Callback URL this site uses for Facebook (copy into Meta → Valid OAuth Redirect URIs). */
let facebookOauthRedirectUri = '';
/** Plan cap for posts/day (1–3 from server). */
let maxPostsCap = 3;
/** Whether the user can add another linked business (plan limit). */
let canAddBusiness = true;
let addBusinessBlockedReason = '';

async function ensureSession() {
  try {
    const r = await fetch(`${API}/auth/me`, { credentials: 'include' });
    const d = await r.json().catch(() => ({}));
    if (!d.user) {
      const next = encodeURIComponent(window.location.pathname || '/dashboard');
      window.location.href = `/login?next=${next}`;
      return false;
    }
    const em = document.getElementById('sidebarUserEmail');
    if (em) em.textContent = d.user.email || '';
    return true;
  } catch {
    window.location.href = '/login?next=' + encodeURIComponent('/dashboard');
    return false;
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const ok = await ensureSession();
  if (!ok) return;
  initFacebookOAuthLandingParams();

  document.getElementById('todayDate').textContent = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  });

  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      navigateTo(item.dataset.page);
    });
  });

  document.getElementById('btnGeneratePosts').addEventListener('click', generatePosts);
  document.getElementById('btnRefresh').addEventListener('click', refreshCurrentPage);
  document.getElementById('addAccountForm').addEventListener('submit', addAccount);
  document.getElementById('btnStartScheduler').addEventListener('click', () => schedulerAction('start'));
  document.getElementById('btnStopScheduler').addEventListener('click', () => schedulerAction('stop'));
  document.getElementById('btnTriggerNow').addEventListener('click', generatePosts);
  document.getElementById('closeModal').addEventListener('click', closeEditModal);
  document.getElementById('cancelEdit').addEventListener('click', closeEditModal);
  document.getElementById('saveEdit').addEventListener('click', savePostEdit);
  document.getElementById('editModal').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeEditModal();
  });

  document.getElementById('closeSessionModal')?.addEventListener('click', closeSessionModal);
  document.getElementById('cancelSessionModal')?.addEventListener('click', closeSessionModal);
  document.getElementById('saveSessionModal')?.addEventListener('click', savePlaywrightSessionFromModal);
  document.getElementById('sessionModal')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) closeSessionModal();
  });

  document.getElementById('btnLogout')?.addEventListener('click', async () => {
    try {
      await apiFetch('/auth/logout', { method: 'POST' });
    } catch { /* ignore */ }
    window.location.href = '/';
  });

  document.getElementById('queueGrid').addEventListener('click', onQueueCardClick);
  document.getElementById('queueGridOlder').addEventListener('click', onQueueCardClick);

  loadDashboard();
  checkScheduler();
});

function initFacebookOAuthLandingParams() {
  const q = new URLSearchParams(window.location.search);
  if (q.get('fb_connected') === '1') {
    showToast(
      'Facebook connected. Post now uses the official API — no session JSON needed for this Page.',
      'success',
    );
    history.replaceState({}, '', '/dashboard' + window.location.hash);
    navigateTo('accounts');
  }
  const fe = q.get('fb_error');
  if (fe) {
    showToast(decodeURIComponent(fe), 'error');
    history.replaceState({}, '', '/dashboard' + window.location.hash);
  }
}

function isFacebookishPlatform(platform) {
  const x = String(platform || '').toLowerCase();
  return x === 'facebook' || x === 'fb' || x === 'both';
}

function syncFacebookOAuthFromPayload(data) {
  if (typeof data.facebook_oauth_configured === 'boolean') {
    facebookOAuthConfigured = data.facebook_oauth_configured;
  }
  if (typeof data.facebook_oauth_redirect_uri === 'string') {
    facebookOauthRedirectUri = data.facebook_oauth_redirect_uri;
  }
  renderFacebookConnectHint();
}

function renderFacebookConnectHint() {
  const el = document.getElementById('facebookConnectHint');
  if (!el) return;
  const show = facebookOAuthConfigured && facebookOauthRedirectUri;
  if (!show) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML = `
    <h2>Facebook — one-time Meta setup</h2>
    <p class="muted">In <a href="https://developers.facebook.com/apps/" target="_blank" rel="noopener">Meta for Developers</a> → your app → Facebook Login → Settings: add this under <strong>Valid OAuth Redirect URIs</strong> (exact copy):</p>
    <code class="callback-url" id="fbCallbackUrlDisplay">${escHtml(facebookOauthRedirectUri)}</code>
    <div class="hint-actions">
      <button type="button" class="btn btn-secondary" id="btnCopyFbCallback">Copy URL</button>
    </div>
  `;
  el.querySelector('#btnCopyFbCallback')?.addEventListener('click', () => {
    navigator.clipboard?.writeText(facebookOauthRedirectUri).then(
      () => showToast('Copied — paste into Meta', 'success'),
      () => showToast('Copy failed — select the URL above', 'error'),
    );
  });
}

function cloudPostingStatusPill(acc) {
  if (!postingHeadless) return '';
  if (isFacebookishPlatform(acc.platform)) {
    if (acc.fb_graph_connected) {
      return '<span class="status-pill status-pill--ok">Ready (Facebook API)</span>';
    }
    if (facebookOAuthConfigured) {
      return '<span class="status-pill status-pill--warn">→ Connect Facebook</span>';
    }
    return '<span class="status-pill status-pill--warn">Set Facebook app env (DEPLOY.md)</span>';
  }
  if (acc.has_playwright_session) {
    return '<span class="status-pill status-pill--ok">Browser session saved</span>';
  }
  return '<span class="status-pill status-pill--warn">Paste session JSON (browser)</span>';
}

function connectFacebook(accountId) {
  window.location.href = `${API}/facebook/oauth/start?account_id=${accountId}`;
}

async function disconnectFacebook(accountId) {
  if (!confirm('Disconnect Facebook? Post now will stop using the API until you connect again.')) return;
  try {
    await apiFetch(`/accounts/${accountId}/facebook/disconnect`, { method: 'POST' });
    showToast('Facebook disconnected', 'info');
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast(e.message || 'Disconnect failed', 'error');
  }
}

/** Avoid inline onclick + caption escaping breaking sibling buttons; read id from the card. */
function onQueueCardClick(ev) {
  const btn = ev.target.closest('button[data-action]');
  if (!btn) return;
  const card = btn.closest('.post-card');
  if (!card) return;
  const postId = Number(card.dataset.id);
  if (!Number.isFinite(postId) || postId <= 0) {
    showToast('Invalid draft id — refresh the Daily Queue.', 'error');
    return;
  }
  const action = btn.dataset.action;
  if (action === 'edit') {
    ev.preventDefault();
    openEditPost(postId);
  } else if (action === 'delete') {
    deletePost(postId);
  } else if (action === 'post') {
    postNow(postId, btn);
  }
}

function navigateTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.page === page);
  });
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('pag' + capitalize(page)).classList.add('active');
  const titles = { queue: 'Daily Queue', accounts: 'Accounts', analytics: 'Analytics', settings: 'Settings' };
  document.getElementById('pageTitle').textContent = titles[page] || page;
  if (page === 'queue') loadDashboard();
  else if (page === 'accounts') loadAccounts();
  else if (page === 'analytics') loadAnalytics();
  else if (page === 'settings') checkScheduler();
}

function refreshCurrentPage() {
  if (currentPage === 'queue') loadDashboard();
  else navigateTo(currentPage);
}

// ── DASHBOARD (Daily Queue) ───────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const data = await apiFetch('/dashboard');
    accounts = data.accounts || [];
    if (typeof data.max_posts_per_day_cap === 'number') maxPostsCap = data.max_posts_per_day_cap;
    postingHeadless = data.posting_headless !== false;
    syncAccountPlanLimit(data);
    syncFacebookOAuthFromPayload(data);
    document.getElementById('statAccounts').textContent = accounts.length;
    document.getElementById('statPending').textContent = (data.pending_today || []).length;
    document.getElementById('statPublished').textContent = data.published_today_count ?? 0;

    renderQueueOnboarding(data);
    renderAccountsStrip(accounts);
    document.getElementById('queueGrid').innerHTML = renderPostCards(data.pending_today || [], { emptyLabel: 'today' });

    const older = data.pending_other_days || [];
    const olderSec = document.getElementById('olderDraftsSection');
    if (older.length) {
      olderSec.classList.remove('hidden');
      document.getElementById('queueGridOlder').innerHTML = renderPostCards(older, { emptyLabel: 'older' });
    } else {
      olderSec.classList.add('hidden');
      document.getElementById('queueGridOlder').innerHTML = '';
    }

    renderRecentPublished(data.recent_published || []);
  } catch (e) {
    showToast('Could not load dashboard. Is the backend running?', 'error');
  }
}

function renderQueueOnboarding(d) {
  const el = document.getElementById('queueOnboarding');
  const accs = d.accounts || [];
  const pendingToday = d.pending_today || [];
  const recent = d.recent_published || [];
  const older = d.pending_other_days || [];

  if (!accs.length) {
    el.classList.remove('hidden');
    el.innerHTML = `
      <div class="onboarding-inner">
        <strong>Get started</strong>
        <p>Link a Facebook or Instagram Page under <a href="#" class="inline-link" data-go="accounts">Accounts</a>, then come back here to build today's drafts.</p>
      </div>`;
    el.querySelector('[data-go="accounts"]')?.addEventListener('click', ev => {
      ev.preventDefault();
      navigateTo('accounts');
    });
    return;
  }

  if (!pendingToday.length) {
    el.classList.remove('hidden');
    const extra = older.length
      ? `<p class="onboarding-note">You have <strong>${older.length}</strong> older draft(s) below — or create fresh ones for today.</p>`
      : '';
    const hadPosts = recent.length > 0;
    el.innerHTML = `
      <div class="onboarding-inner">
        <strong>No drafts for today's date yet</strong>
        <p>Linking an account only saves your Page and crawls your website. Press <strong>Build today's posts</strong> (top right) to create three drafts per account for <em>today</em>.</p>
        ${extra}
        ${hadPosts ? '<p class="muted onboarding-foot">Published history appears in the section below.</p>' : ''}
      </div>`;
    return;
  }

  el.classList.add('hidden');
  el.innerHTML = '';
}

function renderAccountsStrip(accs) {
  const wrap = document.getElementById('accountsStrip');
  if (!accs.length) {
    wrap.classList.add('hidden');
    wrap.innerHTML = '';
    return;
  }
  wrap.classList.remove('hidden');
  wrap.innerHTML = accs.map(a => `
    <div class="account-chip ${a.crawl_ready ? 'account-chip--ok' : 'account-chip--warn'}">
      <span class="account-chip-icon" aria-hidden="true">${platformEmoji(a.platform)}</span>
      <span class="account-chip-name">${escHtml(a.business_name)}</span>
      <span class="account-chip-meta">${escHtml(platformLabel(a.platform))}${a.crawl_pages != null ? ' · ' + a.crawl_pages + ' pages' : ''}${a.week_approval_active ? ' · <strong>Week approved ✓</strong>' : ' · Pending approval'}</span>
    </div>
  `).join('');
}

function renderPostCards(posts, opts) {
  const emptyLabel = opts && opts.emptyLabel;
  if (!posts.length) {
    if (emptyLabel === 'today') {
      return `<div class="empty-state">
        <div class="empty-icon" aria-hidden="true">&#128203;</div>
        <p><strong>No drafts dated for today.</strong></p>
        <p>Use <strong>Build today's posts</strong> in the header (one click per refresh day).</p>
      </div>`;
    }
    return `<div class="empty-state muted"><p>No items.</p></div>`;
  }
  return posts.map(p => postCardHtml(p)).join('');
}

function postCardHtml(post) {
  const pub = post.status === 'published';
  const acc = accounts.find((a) => Number(a.id) === Number(post.account_id));
  const blockFbPost =
    postingHeadless &&
    acc &&
    isFacebookishPlatform(acc.platform) &&
    facebookOAuthConfigured &&
    !acc.fb_graph_connected &&
    !acc.has_playwright_session;
  const postBtn = blockFbPost
    ? `<button type="button" class="btn btn-success" disabled title="Use Accounts → Connect Facebook first (or paste Session JSON)">Post now</button>`
    : `<button type="button" class="btn btn-success" data-action="post">Post now</button>`;
  const footer = pub
    ? '<button type="button" class="btn btn-secondary" disabled>Published</button>'
    : `<button type="button" class="btn btn-secondary" data-action="edit">Edit</button>
          <button type="button" class="btn btn-danger btn-sm" data-action="delete">Delete</button>
          ${postBtn}`;
  return `
    <div class="post-card ${pub ? 'published' : ''}" data-id="${post.id}">
      <div class="post-card-header">
        <span class="post-type-badge ${badgeClass(post.post_type)}">${formatType(post.post_type)}</span>
        <span class="post-account">${platformEmoji(post.platform)} ${escHtml(post.business_name || '')}</span>
        <span class="post-time">${formatTime(post.scheduled_time)}</span>
      </div>
      <div class="post-card-body">
        <div class="post-caption">${escHtml(post.caption)}</div>
      </div>
      <div class="post-card-footer">${footer}</div>
    </div>`;
}

function renderRecentPublished(items) {
  const el = document.getElementById('recentPublished');
  if (!items.length) {
    el.innerHTML = `<div class="empty-state muted recent-empty">
      <p><strong>No posts published through FastPost yet.</strong></p>
      <p>When you use <strong>Post now</strong> on a draft and it succeeds, it will show up here and in Analytics.</p>
    </div>`;
    return;
  }
  el.innerHTML = items.map(p => `
    <div class="recent-item">
      <div class="recent-item-top">
        <span class="recent-platform">${platformEmoji(p.platform)} ${escHtml(platformLabel(p.platform))}</span>
        <span class="recent-name">${escHtml(p.business_name || '')}</span>
        <span class="recent-date">${formatDateTime(p.published_at)}</span>
      </div>
      <div class="recent-type">${formatType(p.post_type)}</div>
      <div class="recent-caption">${escHtml(truncate(p.caption, 220))}</div>
    </div>
  `).join('');
}

// ── POST ACTIONS ──────────────────────────────────────────────────────────────
function postNowConfirmText() {
  if (postingHeadless) {
    return (
      'Post this draft now?\n\n' +
      'On this server, posting runs in a hidden (headless) browser — no window will open on your computer. ' +
      'It can take 1–3 minutes. Keep this tab open and wait for success or an error message.'
    );
  }
  return (
    'Post this draft now? A Chromium window should open on this computer. It may take 1–3 minutes. Keep this tab open.'
  );
}

async function postNow(postId, btn) {
  if (!confirm(postNowConfirmText())) return;

  const resetBtn = () => {
    btn.disabled = false;
    btn.textContent = 'Post now';
  };

  btn.disabled = true;
  btn.textContent = 'Posting...';
  if (postingHeadless) {
    showToast(
      'Posting in headless mode on the server (no popup). This can take a few minutes — do not close this tab.',
      'info',
    );
  } else {
    showToast('Posting… Complete any login in the Chromium window if it appears.', 'info');
  }
  try {
    await apiFetch(`/queue/${postId}`, { method: 'GET' });
  } catch (e) {
    showToast('This draft is not on the server anymore. Refreshing the queue…', 'error');
    resetBtn();
    loadDashboard();
    return;
  }

  const postTimeoutMs = postingHeadless ? 900_000 : 1_300_000;
  try {
    const posted = await apiFetch(`/post/${postId}`, { method: 'POST', timeoutMs: postTimeoutMs });
    const ph = posted.posting_headless !== false;
    showToast(
      ph
        ? 'Posted successfully! Confirm on Facebook (post ran headless on the server).'
        : 'Posted successfully!',
      'success',
    );
    setTimeout(loadDashboard, 800);
  } catch (e) {
    showToast('Posting failed: ' + (e.message || 'Unknown error'), 'error');
  } finally {
    resetBtn();
  }
}

async function deletePost(postId) {
  if (!confirm('Delete this post?')) return;
  try {
    await apiFetch(`/queue/${postId}`, { method: 'DELETE' });
    showToast('Post deleted', 'info');
    loadDashboard();
  } catch (e) {
    showToast('Error deleting post', 'error');
  }
}

async function openEditPost(postId) {
  try {
    const post = await apiFetch(`/queue/${postId}`);
    editingPostId = postId;
    document.getElementById('editCaption').value = post.caption || '';
    const ip = post.image_prompt || '';
    document.getElementById('editImagePrompt').textContent = ip ? 'Image note: ' + ip : '';
    document.getElementById('editModal').classList.remove('hidden');
  } catch (e) {
    showToast('Could not load that draft: ' + (e.message || 'Unknown'), 'error');
  }
}

function closeEditModal() {
  document.getElementById('editModal').classList.add('hidden');
  editingPostId = null;
}

async function savePostEdit() {
  const caption = document.getElementById('editCaption').value.trim();
  if (!caption) { showToast('Caption cannot be empty', 'error'); return; }
  try {
    await apiFetch(`/queue/${editingPostId}`, {
      method: 'PUT',
      body: JSON.stringify({ caption }),
    });
    showToast('Post updated!', 'success');
    closeEditModal();
    loadDashboard();
  } catch (e) {
    showToast('Error saving changes', 'error');
  }
}

async function generatePosts() {
  const btn = document.getElementById('btnGeneratePosts');
  btn.disabled = true;
  btn.textContent = 'Building...';
  showToast('Building drafts from your website content...', 'info');
  try {
    const result = await apiFetch('/queue/generate', { method: 'POST', body: JSON.stringify({}) });
    const count = result.generated?.length || 0;
    showToast(count ? `Created ${count} draft(s).` : 'No new drafts (check accounts).', 'success');
    navigateTo('queue');
  } catch (e) {
    showToast('Could not build posts: ' + (e.message || 'Unknown'), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Build today\'s posts';
  }
}

// ── ACCOUNTS ──────────────────────────────────────────────────────────────────
async function loadAccounts() {
  try {
    const data = await apiFetch('/accounts');
    accounts = data.accounts || [];
    if (typeof data.max_posts_per_day_cap === 'number') maxPostsCap = data.max_posts_per_day_cap;
    if (typeof data.posting_headless === 'boolean') postingHeadless = data.posting_headless;
    syncAccountPlanLimit(data);
    syncFacebookOAuthFromPayload(data);
    renderAccounts(accounts);
    document.getElementById('statAccounts').textContent = accounts.length;
  } catch (e) {
    console.error('Could not load accounts', e);
  }
}

function postsPerDayOptions(selected, cap) {
  const c = Math.min(3, Math.max(1, cap || 3));
  const opts = [];
  for (let n = 1; n <= c; n += 1) {
    opts.push(`<option value="${n}"${n === selected ? ' selected' : ''}>${n} post${n === 1 ? '' : 's'}/day</option>`);
  }
  return opts.join('');
}

async function onPostsPerDayChange(accountId, value) {
  try {
    await apiFetch(`/accounts/${accountId}/posts-per-day`, {
      method: 'PATCH',
      body: JSON.stringify({ posts_per_day: Number(value) }),
    });
    showToast('Posts per day updated', 'success');
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast(e.message || 'Could not update', 'error');
  }
}

async function setWeeklyApproval(accountId, approved) {
  try {
    await apiFetch(`/accounts/${accountId}/weekly-approval`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    });
    showToast(approved ? 'Week approved — scheduler will auto-publish.' : 'Weekly approval revoked.', 'info');
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast(e.message || 'Could not update approval', 'error');
  }
}

function renderAccounts(accs) {
  const list = document.getElementById('accountsList');
  const cap = maxPostsCap;
  if (!accs.length) {
    list.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">&#128279;</div>
        <p>No accounts linked yet. Add one above!</p>
      </div>`;
    return;
  }
  list.innerHTML = accs.map(acc => `
    <div class="account-item">
      <div class="account-info">
        <div class="account-title-row">
          <span class="platform-tag platform-${escAttr(acc.platform)}">${platformEmoji(acc.platform)} ${escHtml(platformLabel(acc.platform))}</span>
          <h3>${escHtml(acc.business_name)}</h3>
        </div>
        <p class="account-links">
          <a href="${escAttr(acc.page_url)}" target="_blank" rel="noopener">Page</a>
          · <a href="${escAttr(acc.business_url)}" target="_blank" rel="noopener">Website</a>
        </p>
        <div class="account-meta-row">
          <span class="status-pill ${acc.crawl_ready ? 'status-pill--ok' : 'status-pill--warn'}">${escHtml(acc.status_label || '')}</span>
          ${cloudPostingStatusPill(acc)}
          <span class="meta-text posts-sent-badge" title="Successful publishes tracked in history">${Number(acc.posts_sent_count) || 0} posts sent</span>
          ${acc.crawl_pages != null ? `<span class="meta-text">${acc.crawl_pages} page(s) indexed</span>` : ''}
          ${acc.updated_at ? `<span class="meta-text">Updated ${escHtml(formatDateTime(acc.updated_at))}</span>` : ''}
        </div>
        ${acc.crawl_summary_preview ? `<p class="crawl-preview">${escHtml(acc.crawl_summary_preview)}</p>` : ''}
        <p class="next-step-hint">${escHtml(acc.next_step_hint || '')}</p>
        <div class="posts-per-day-select">
          <label for="ppd-${acc.id}">Posts / day</label>
          <select id="ppd-${acc.id}" data-account-id="${acc.id}">
            ${postsPerDayOptions(Number(acc.posts_per_day) || 3, cap)}
          </select>
        </div>
        <div class="weekly-approval-row">
          <span class="muted">${acc.week_approval_active ? 'Week approved ✓' : 'Pending approval'}</span>
          ${acc.week_approval_active
    ? `<button type="button" class="btn btn-secondary btn-sm" data-revoke-weekly="${acc.id}">Revoke week</button>`
    : `<button type="button" class="btn btn-primary btn-sm" data-approve-weekly="${acc.id}">Approve for the week</button>`}
        </div>
      </div>
      <div class="account-actions">
        ${facebookOAuthConfigured && isFacebookishPlatform(acc.platform) && !acc.fb_graph_connected ? `<button type="button" class="btn btn-primary" onclick="connectFacebook(${acc.id})" title="Official Facebook Login — no JSON files">Connect Facebook</button>` : ''}
        ${acc.fb_graph_connected ? `<button type="button" class="btn btn-secondary" onclick="disconnectFacebook(${acc.id})">Disconnect Facebook</button>` : ''}
        <button type="button" class="btn btn-secondary" onclick="openPlaywrightSessionModal(${acc.id})" title="Advanced: Playwright storage (Instagram or fallback)">Session JSON…</button>
        <button type="button" class="btn btn-secondary" onclick="clearPlaywrightSession(${acc.id})">Clear session</button>
        <button type="button" class="btn btn-secondary" onclick="recrawl(${acc.id})">Re-crawl</button>
        <button type="button" class="btn btn-danger" onclick="deleteAccount(${acc.id})">Remove</button>
      </div>
    </div>
  `).join('');
  list.querySelectorAll('select[data-account-id]').forEach(sel => {
    sel.addEventListener('change', () => {
      onPostsPerDayChange(Number(sel.dataset.accountId), sel.value);
    });
  });
  list.querySelectorAll('[data-approve-weekly]').forEach(btn => {
    btn.addEventListener('click', () => setWeeklyApproval(Number(btn.dataset.approveWeekly), true));
  });
  list.querySelectorAll('[data-revoke-weekly]').forEach(btn => {
    btn.addEventListener('click', () => setWeeklyApproval(Number(btn.dataset.revokeWeekly), false));
  });
}

async function addAccount(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = 'Crawling website...';
  const payload = {
    business_name: document.getElementById('bizName').value,
    platform: document.getElementById('platform').value,
    business_url: document.getElementById('bizUrl').value,
    page_url: document.getElementById('pageUrl').value,
  };
  try {
    const res = await apiFetch('/accounts', { method: 'POST', body: JSON.stringify(payload) });
    if (res.crawl_ok === false) {
      showToast(
        (res.message || 'Account saved.') + (res.crawl_error ? ' — ' + res.crawl_error : ''),
        'info',
      );
    } else {
      showToast(res.message || 'Account linked and site crawled.', 'success');
    }
    e.target.reset();
    await loadAccounts();
    loadDashboard();
  } catch (err) {
    showToast('Error adding account: ' + (err.message || 'Unknown'), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '+ Link Account & Crawl Website';
  }
}

async function deleteAccount(accountId) {
  if (!confirm('Remove this account? All its posts will also be deleted.')) return;
  try {
    await apiFetch(`/accounts/${accountId}`, { method: 'DELETE' });
    showToast('Account removed', 'info');
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast('Error removing account', 'error');
  }
}

async function recrawl(accountId) {
  showToast('Re-crawling website...', 'info');
  try {
    const result = await apiFetch(`/crawl/${accountId}`, { method: 'POST' });
    showToast(`Crawl complete. ${result.pages_found || 0} page(s).`, 'success');
    loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast('Crawl failed', 'error');
  }
}

function openPlaywrightSessionModal(accountId) {
  sessionModalAccountId = accountId;
  const ta = document.getElementById('sessionJsonInput');
  if (ta) ta.value = '';
  const el = document.getElementById('sessionModal');
  if (el) {
    el.classList.remove('hidden');
    ta?.focus();
  }
}

function closeSessionModal() {
  sessionModalAccountId = null;
  document.getElementById('sessionModal')?.classList.add('hidden');
}

/**
 * Normalize pasted Playwright storage: BOM, markdown fences, accidental extra text.
 */
function parsePlaywrightStoragePaste(text) {
  let s = String(text).replace(/^\uFEFF/, '').trim();
  const fence = s.match(/^```(?:json)?\s*\r?\n?([\s\S]*?)\r?\n?```\s*$/im);
  if (fence) s = fence[1].trim();
  try {
    return JSON.parse(s);
  } catch (e1) {
    const i = s.indexOf('{');
    const j = s.lastIndexOf('}');
    if (i >= 0 && j > i) {
      try {
        return JSON.parse(s.slice(i, j + 1));
      } catch {
        /* fall through */
      }
    }
    throw e1;
  }
}

async function savePlaywrightSessionFromModal() {
  const id = sessionModalAccountId;
  const raw = document.getElementById('sessionJsonInput')?.value || '';
  if (!id) {
    showToast('No account selected.', 'error');
    return;
  }
  if (!String(raw).trim()) {
    showToast('Paste the JSON first.', 'error');
    return;
  }
  let parsed;
  try {
    parsed = parsePlaywrightStoragePaste(raw);
  } catch {
    showToast(
      'Invalid JSON. Use the full file from export (starts with {"cookies"). On Windows, run the script from the project folder — see Session JSON help text.',
      'error',
    );
    return;
  }
  try {
    await apiFetch(`/accounts/${id}/playwright-storage`, {
      method: 'POST',
      body: JSON.stringify(parsed),
    });
    showToast('Session saved. You can try Post now.', 'success');
    closeSessionModal();
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast(e.message || 'Save failed', 'error');
  }
}

async function clearPlaywrightSession(accountId) {
  if (!confirm('Remove the saved browser session for this account? Posting on the server will need a new JSON.')) return;
  try {
    await apiFetch(`/accounts/${accountId}/playwright-storage`, { method: 'DELETE' });
    showToast('Session cleared', 'info');
    await loadAccounts();
    loadDashboard();
  } catch (e) {
    showToast(e.message || 'Clear failed', 'error');
  }
}

// ── ANALYTICS ─────────────────────────────────────────────────────────────────
async function loadAnalytics() {
  try {
    const stats = await apiFetch('/analytics');
    document.getElementById('totalPublished').textContent = stats.total_posts_published || 0;
    document.getElementById('totalLikes').textContent = stats.total_likes || 0;
    document.getElementById('totalShares').textContent = stats.total_shares || 0;
    const historyEl = document.getElementById('postHistory');
    const posts = stats.recent_posts || [];
    if (!posts.length) {
      historyEl.innerHTML = '<p class="muted">No published posts yet. Publish a draft from Daily Queue.</p>';
      return;
    }
    historyEl.innerHTML = posts.map(p => `
      <div class="history-item history-item--wide">
        <span class="history-type">${formatType(p.post_type)}<br><span class="history-platform">${platformEmoji(p.platform)} ${escHtml(p.business_name || '')}</span></span>
        <span class="history-caption" title="${escAttr(p.caption)}">${escHtml(truncate(p.caption, 120))}</span>
        <span class="history-stats">${formatDateTime(p.published_at)}</span>
      </div>
    `).join('');
  } catch (e) {
    showToast('Could not load analytics', 'error');
  }
}

// ── SCHEDULER ─────────────────────────────────────────────────────────────────
async function checkScheduler() {
  try {
    const status = await apiFetch('/scheduler/status');
    const dot = document.getElementById('schedulerDot');
    const label = document.getElementById('schedulerStatus');
    dot.className = 'status-dot ' + (status.running ? 'running' : 'stopped');
    label.textContent = status.running ? 'Scheduler ON' : 'Scheduler OFF';
    const nextEl = document.getElementById('nextRunTime');
    if (nextEl) nextEl.textContent = 'Next run: ' + (status.next_run || 'Not scheduled');
  } catch (e) {
    document.getElementById('schedulerStatus').textContent = 'Backend offline';
  }
}

async function schedulerAction(action) {
  try {
    await apiFetch(`/scheduler/${action}`, { method: 'POST' });
    showToast(`Scheduler ${action}ed`, 'success');
    checkScheduler();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function apiFetch(path, options = {}) {
  const { timeoutMs, headers: optHeaders, ...fetchOpts } = options;
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = timeoutMs && ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  let res;
  try {
    res = await fetch(API + path, {
      ...fetchOpts,
      credentials: 'include',
      signal: ctrl ? ctrl.signal : fetchOpts.signal,
      headers: { 'Content-Type': 'application/json', ...optHeaders },
    });
  } catch (err) {
    const name = err && err.name;
    const msg = (err && err.message) || String(err);
    if (name === 'AbortError') {
      const hint = postingHeadless
        ? 'Timed out waiting for the server (~15 min). Refresh the Daily Queue, check Facebook for the post, then try again if needed.'
        : 'Request timed out. Posting can take several minutes — try again or check the server terminal.';
      throw new Error(hint);
    }
    if (/failed to fetch|networkerror|load failed/i.test(msg)) {
      throw new Error(
        `Could not reach the API at ${API.replace(/\/api$/, '')}. ` +
          'Open the app from that same address, keep the server running, and leave this tab open while Chromium finishes posting.'
      );
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
  const raw = await res.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = { error: raw.slice(0, 280) };
    }
  }
  if (!res.ok) throw new Error(data.error || res.statusText || 'Request failed');
  return data;
}

function showToast(msg, type = 'info') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), 4500);
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function escHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function escAttr(s) { return escHtml(s).replace(/'/g, '&#39;'); }
function escJs(s) { return String(s).replace(/\\/g, '\\\\').replace(/`/g, '\\`').replace(/\$/g, '\\$'); }
function truncate(s, n) {
  const t = String(s || '');
  if (t.length <= n) return t;
  return t.slice(0, n - 1) + '\u2026';
}

function platformLabel(p) {
  const x = (p || '').toLowerCase();
  if (x === 'facebook' || x === 'fb') return 'Facebook';
  if (x === 'instagram' || x === 'ig') return 'Instagram';
  if (x === 'both') return 'Facebook + Instagram';
  return p || 'Social';
}

function platformEmoji(p) {
  const x = (p || '').toLowerCase();
  if (x === 'instagram' || x === 'ig') return '\u{1F4F7}';
  if (x === 'both') return '\u{1F4F1}';
  return '\u{1F4F2}';
}

function badgeClass(type) {
  if (!type) return 'badge-unknown';
  if (type.includes('morning')) return 'badge-morning';
  if (type.includes('afternoon') || type.includes('tip')) return 'badge-afternoon';
  if (type.includes('evening') || type.includes('proof')) return 'badge-evening';
  return 'badge-unknown';
}

function formatType(type) {
  const labels = {
    morning_promo: 'Morning promo',
    afternoon_tip: 'Afternoon tip',
    evening_proof: 'Evening proof',
  };
  return labels[type] || (type || 'Post');
}

function formatTime(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  } catch { return dateStr; }
}

function formatDateTime(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return String(dateStr); }
}
