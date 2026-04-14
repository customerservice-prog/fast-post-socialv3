/**
 * FastPost Social v3 - Dashboard JavaScript
 * Handles all frontend interactions: queue display, account management,
 * post editing, one-click posting, analytics, and scheduler control.
 */

const API = 'http://localhost:5000/api';

// ── STATE ────────────────────────────────────────────────────────────────────
let currentPage = 'queue';
let editingPostId = null;
let accounts = [];

// ── INIT ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Set today's date
                            document.getElementById('todayDate').textContent = new Date().toLocaleDateString('en-US', {
                                  weekday: 'long', month: 'long', day: 'numeric'
                            });

                            // Navigation
                            document.querySelectorAll('.nav-item').forEach(item => {
                                  item.addEventListener('click', e => {
                                          e.preventDefault();
                                          navigateTo(item.dataset.page);
                                  });
                            });

                            // Header buttons
                            document.getElementById('btnGeneratePosts').addEventListener('click', generatePosts);
    document.getElementById('btnRefresh').addEventListener('click', refreshCurrentPage);

                            // Account form
                            document.getElementById('addAccountForm').addEventListener('submit', addAccount);

                            // Settings buttons
                            document.getElementById('btnStartScheduler').addEventListener('click', () => schedulerAction('start'));
    document.getElementById('btnStopScheduler').addEventListener('click', () => schedulerAction('stop'));
    document.getElementById('btnTriggerNow').addEventListener('click', generatePosts);

                            // Modal buttons
                            document.getElementById('closeModal').addEventListener('click', closeEditModal);
    document.getElementById('cancelEdit').addEventListener('click', closeEditModal);
    document.getElementById('saveEdit').addEventListener('click', savePostEdit);

                            // Close modal on overlay click
                            document.getElementById('editModal').addEventListener('click', e => {
                                  if (e.target === e.currentTarget) closeEditModal();
                            });

                            // Load initial data
                            loadQueue();
    loadAccounts();
    checkScheduler();
});

// ── NAVIGATION ───────────────────────────────────────────────────────────────
function navigateTo(page) {
    currentPage = page;

  // Update nav
  document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
  });

  // Update pages
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('pag' + capitalize(page)).classList.add('active');

  // Update title
  const titles = { queue: 'Daily Queue', accounts: 'Accounts', analytics: 'Analytics', settings: 'Settings' };
    document.getElementById('pageTitle').textContent = titles[page] || page;

  // Load page data
  if (page === 'queue') loadQueue();
    else if (page === 'accounts') loadAccounts();
    else if (page === 'analytics') loadAnalytics();
    else if (page === 'settings') checkScheduler();
                                    }

function refreshCurrentPage() { navigateTo(currentPage); }

// ── QUEUE ─────────────────────────────────────────────────────────────────────
async function loadQueue() {
    try {
          const data = await apiFetch('/queue');
          const posts = data.posts || [];
    renderQueue(posts);
          updateStats(posts);
    } catch (e) {
                 showToast('Could not load queue. Is the backend running?', 'error');
    }
}

function renderQueue(posts) {
    const grid = document.getElementById('queueGrid');

  if (!posts.length) {
        grid.innerHTML = `
              <div class="empty-state">
                      <div class="empty-icon">📭</div>
                              <p>No posts in today's queue.</p>
                                      <p>Click <strong>✨ Generate Today's Posts</strong> to get started!</p>
                                            </div>`;
        return;
  }

  grid.innerHTML = posts.map(post => `
      <div class="post-card ${post.status === 'published' ? 'published' : ''}" data-id="${post.id}">
            <div class="post-card-header">
                    <span class="post-type-badge ${badgeClass(post.post_type)}">${formatType(post.post_type)}</span>
                            <span class="post-account">${escHtml(post.business_name || '')}</span>
                                    <span class="post-time">${formatTime(post.scheduled_time)}</span>
                                          </div>
                                                <div class="post-card-body">
                                                        <div class="post-caption">${escHtml(post.caption)}</div>
                                                              </div>
                                                                    <div class="post-card-footer">
                                                                            ${post.status === 'published'
                                                                                        ? '<button class="btn btn-secondary" disabled>✅ Published</button>'
                                                                                        : `
                                                                                                    <button class="btn btn-secondary" onclick="editPost(${post.id}, \`${escJs(post.caption)}\`, \`${escJs(post.image_prompt || '')}\`)">
                                                                                                                  ✏️ Edit
                                                                                                                              </button>
                                                                                                                                          <button class="btn btn-danger btn-sm" onclick="deletePost(${post.id})">🗑️</button>
                                                                                                                                                      <button class="btn btn-success" onclick="postNow(${post.id}, this)">
                                                                                                                                                                    🚀 Post Now
                                                                                                                                                                                </button>
                                                                                                                                                                                          `
                                                                            }
                                                                                  </div>
                                                                                      </div>
                                                                                        `).join('');
}

function updateStats(posts) {
    const pending = posts.filter(p => p.status === 'pending').length;
    const published = posts.filter(p => p.status === 'published').length;
    document.getElementById('statPending').textContent = pending;
    document.getElementById('statPublished').textContent = published;
    document.getElementById('statAccounts').textContent = accounts.length;
}

// ── POST ACTIONS ──────────────────────────────────────────────────────────────
async function postNow(postId, btn) {
    if (!confirm('Post this to social media now?')) return;

  btn.disabled = true;
    btn.textContent = '⏳ Posting...';

  try {
        const result = await apiFetch(`/post/${postId}`, { method: 'POST' });
        showToast('Posted successfully!', 'success');
        // Refresh queue
      setTimeout(loadQueue, 1000);
  } catch (e) {
        showToast('Posting failed: ' + (e.message || 'Unknown error'), 'error');
        btn.disabled = false;
        btn.textContent = '🚀 Post Now';
  }
}

async function deletePost(postId) {
    if (!confirm('Delete this post?')) return;
    try {
          await apiFetch(`/queue/${postId}`, { method: 'DELETE' });
          showToast('Post deleted', 'info');
          loadQueue();
    } catch (e) {
          showToast('Error deleting post', 'error');
    }
}

function editPost(postId, caption, imagePrompt) {
    editingPostId = postId;
    document.getElementById('editCaption').value = caption;
    document.getElementById('editImagePrompt').textContent =
          imagePrompt ? '🖼️ Image Prompt: ' + imagePrompt : '';
    document.getElementById('editModal').classList.remove('hidden');
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
                body: JSON.stringify({ caption })
        });
        showToast('Post updated!', 'success');
        closeEditModal();
        loadQueue();
  } catch (e) {
        showToast('Error saving changes', 'error');
  }
}

// ── GENERATE POSTS ────────────────────────────────────────────────────────────
async function generatePosts() {
    const btn = document.getElementById('btnGeneratePosts');
    btn.disabled = true;
    btn.textContent = '⏳ Generating...';
    showToast('Generating AI posts... this may take a moment', 'info');

  try {
        const result = await apiFetch('/queue/generate', { method: 'POST', body: JSON.stringify({}) });
        const count = result.generated?.length || 0;
        showToast(`Generated ${count} posts!`, 'success');
        navigateTo('queue');
  } catch (e) {
        showToast('Generation failed: ' + (e.message || 'Check your OpenAI API key'), 'error');
  } finally {
        btn.disabled = false;
        btn.textContent = '✨ Generate Today\'s Posts';
  }
}

// ── ACCOUNTS ──────────────────────────────────────────────────────────────────
async function loadAccounts() {
    try {
          const data = await apiFetch('/accounts');
          accounts = data.accounts || [];
          renderAccounts(accounts);
          document.getElementById('statAccounts').textContent = accounts.length;
    } catch (e) {
          console.error('Could not load accounts', e);
    }
}

function renderAccounts(accs) {
    const list = document.getElementById('accountsList');
    if (!accs.length) {
          list.innerHTML = `
                <div class="empty-state">
                        <div class="empty-icon">🔗</div>
                                <p>No accounts linked yet. Add one above!</p>
                                      </div>`;
          return;
    }

  list.innerHTML = accs.map(acc => `
      <div class="account-item">
            <div class="account-info">
                    <h3>${escHtml(acc.business_name)}</h3>
                            <p>${acc.platform.toUpperCase()} · <a href="${escHtml(acc.page_url)}" target="_blank">${escHtml(acc.page_url)}</a></p>
                                    <p>Website: ${escHtml(acc.business_url)}</p>
                                          </div>
                                                <div class="account-actions">
                                                        <button class="btn btn-secondary" onclick="recrawl(${acc.id})">🔄 Re-crawl</button>
                                                                <button class="btn btn-danger" onclick="deleteAccount(${acc.id})">🗑️ Remove</button>
                                                                      </div>
                                                                          </div>
                                                                            `).join('');
}

async function addAccount(e) {
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = '⏳ Crawling website...';

  const payload = {
        business_name: document.getElementById('bizName').value,
        platform: document.getElementById('platform').value,
        business_url: document.getElementById('bizUrl').value,
        page_url: document.getElementById('pageUrl').value,
  };

  try {
        await apiFetch('/accounts', { method: 'POST', body: JSON.stringify(payload) });
        showToast('Account linked! Website crawled.', 'success');
        e.target.reset();
        loadAccounts();
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
          loadAccounts();
    } catch (e) {
          showToast('Error removing account', 'error');
    }
}

async function recrawl(accountId) {
    showToast('Re-crawling website...', 'info');
    try {
          const result = await apiFetch(`/crawl/${accountId}`, { method: 'POST' });
          showToast(`Crawl complete! ${result.pages_found || 0} pages scanned.`, 'success');
    } catch (e) {
          showToast('Crawl failed', 'error');
    }
}

// ── ANALYTICS ─────────────────────────────────────────────────────────────────
async function loadAnalytics() {
    try {
          const stats = await apiFetch('/analytics');
          document.getElementById('totalPublished').textContent = stats.total_posts_published || 0;
          document.getElementById('totalLikes').textContent = stats.total_likes || 0;
          document.getElementById('totalShares').textContent = stats.total_shares || 0;

      // Load per-account history
      if (accounts.length > 0) {
              const accData = await apiFetch(`/analytics/${accounts[0].id}`);
              const posts = accData.posts || [];
              const historyEl = document.getElementById('postHistory');

            if (!posts.length) {
                      historyEl.innerHTML = '<p class="muted">No published posts yet.</p>';
                      return;
            }

            historyEl.innerHTML = posts.map(p => `
                    <div class="history-item">
                              <span class="history-type">${formatType(p.post_type)}</span>
                                        <span class="history-caption">${escHtml(p.caption)}</span>
                                                  <span class="history-stats">${p.likes || 0} ❤️</span>
                                                          </div>
                                                                `).join('');
      }
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
          // Backend not connected
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

// ── API HELPER ─────────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
    const res = await fetch(API + path, {
          headers: { 'Content-Type': 'application/json' },
          ...options
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
}

// ── TOAST ──────────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.add('hidden'), 4000);
}

// ── UTILITIES ──────────────────────────────────────────────────────────────────
function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escJs(s) { return String(s).replace(/\\/g,'\\\\').replace(/`/g,'\\`').replace(/\$/g,'\\$'); }

function badgeClass(type) {
    if (!type) return 'badge-unknown';
    if (type.includes('morning')) return 'badge-morning';
    if (type.includes('afternoon') || type.includes('tip')) return 'badge-afternoon';
    if (type.includes('evening') || type.includes('proof')) return 'badge-evening';
    return 'badge-unknown';
}

function formatType(type) {
    const labels = {
          morning_promo: '🌅 Morning Promo',
          afternoon_tip: '☀️ Afternoon Tip',
          evening_proof: '🌙 Evening Proof',
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
