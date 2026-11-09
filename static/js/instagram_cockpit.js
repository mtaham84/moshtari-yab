    const COCKPIT_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
    const ACCOUNT_ID = Number(COCKPIT_CFG.accountId);
    const CSRF_TOKEN = COCKPIT_CFG.csrfToken;

    function switchCockpitTab(tabKey, btn) {
      document.querySelectorAll('.cockpit-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      const p = document.getElementById('pane-' + tabKey);
      if (p) p.classList.add('active');
      if (btn) btn.classList.add('active');

      if (tabKey === 'direct' && !window.threadsLoaded) {
        loadThreads();
      } else if (tabKey === 'feed' && !window.feedLoaded) {
        loadTimelineFeed();
      }
    }

    // Load Direct Threads
    function loadThreads() {
      const ul = document.getElementById('threads-ul');
      ul.innerHTML = '<li class="empty-state" style="padding:40px 10px;">در حال بروزرسانی چت‌ها...</li>';
      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/threads/`)
        .then(r => r.json())
        .then(data => {
          window.threadsLoaded = true;
          if (!data.success) {
            ul.innerHTML = `<li class="empty-state" style="color:#ef4444;">خطا: ${data.error || 'ناشناخته'}</li>`;
            return;
          }
          if (!data.threads || data.threads.length === 0) {
            ul.innerHTML = '<li class="empty-state">هیچ گفتگوی فعالی در دایرکت یافت نشد.</li>';
            return;
          }
          let html = '';
          data.threads.forEach(t => {
            const initial = (t.title || 'U')[0].toUpperCase();
            html += `
              <li class="thread-item" onclick="openThread('${t.id}', '${escapeHtml(t.title)}')">
                <div class="thread-avatar">${initial}</div>
                <div class="thread-content">
                  <div class="thread-title">${escapeHtml(t.title)}</div>
                  <div class="thread-snippet">${escapeHtml(t.last_message || '...')}</div>
                </div>
                <div class="thread-time">${t.last_message_time || ''}</div>
              </li>
            `;
          });
          ul.innerHTML = html;
        })
        .catch(err => {
          ul.innerHTML = `<li class="empty-state" style="color:#ef4444;">خطای شبکه: ${err}</li>`;
        });
    }

    function openThread(threadId, title) {
      document.querySelectorAll('.thread-item').forEach(el => el.classList.remove('active'));
      if (event && event.currentTarget) event.currentTarget.classList.add('active');

      document.getElementById('active-chat-title').innerText = '@' + title;
      document.getElementById('active-thread-id').value = threadId;
      document.getElementById('chat-send-form').style.display = 'flex';

      const box = document.getElementById('chat-messages-box');
      box.innerHTML = '<div class="empty-state">در حال فراخوانی پیام‌های چت...</div>';

      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/threads/${threadId}/`)
        .then(r => r.json())
        .then(data => {
          if (!data.success) {
            box.innerHTML = `<div class="empty-state" style="color:#ef4444;">خطا: ${data.error}</div>`;
            return;
          }
          if (!data.messages || data.messages.length === 0) {
            box.innerHTML = '<div class="empty-state">هیچ پیامی در این گفتگو ثبت نشده است.</div>';
            return;
          }
          let html = '';
          data.messages.forEach(m => {
            const cls = m.is_outgoing ? 'msg-outgoing' : 'msg-incoming';
            html += `
              <div class="msg-bubble ${cls}">
                <div>${escapeHtml(m.text)}</div>
                <div class="msg-time">${m.timestamp || ''}</div>
              </div>
            `;
          });
          box.innerHTML = html;
          box.scrollTop = box.scrollHeight;
        })
        .catch(err => {
          box.innerHTML = `<div class="empty-state" style="color:#ef4444;">خطا در دریافت پیام‌ها: ${err}</div>`;
        });
    }

    function sendDirectMessage(e) {
      e.preventDefault();
      const input = document.getElementById('chat-msg-input');
      const text = input.value.trim();
      const threadId = document.getElementById('active-thread-id').value;
      if (!text || !threadId) return;

      const btn = document.getElementById('btn-send-msg');
      btn.disabled = true;
      btn.innerText = '⏳';

      const fd = new FormData();
      fd.append('thread_id', threadId);
      fd.append('text', text);

      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/direct/send/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF_TOKEN },
        body: fd
      })
      .then(r => r.json())
      .then(data => {
        btn.disabled = false;
        btn.innerText = 'ارسال ✈️';
        if (data.success) {
          input.value = '';
          // append bubble
          const box = document.getElementById('chat-messages-box');
          box.innerHTML += `
            <div class="msg-bubble msg-outgoing">
              <div>${escapeHtml(text)}</div>
              <div class="msg-time">هم‌اکنون</div>
            </div>
          `;
          box.scrollTop = box.scrollHeight;
        } else {
          alert('خطا در ارسال: ' + (data.error || 'ناشناخته'));
        }
      })
      .catch(err => {
        btn.disabled = false;
        btn.innerText = 'ارسال ✈️';
        alert('خطای ارتباط با سرور: ' + err);
      });
    }

    // Load Timeline Feed
    function loadTimelineFeed() {
      const grid = document.getElementById('feed-grid-box');
      grid.innerHTML = '<div class="empty-state" style="grid-column: 1 / -1;">در حال بارگذاری پست‌ها...</div>';
      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/feed/`)
        .then(r => r.json())
        .then(data => {
          window.feedLoaded = true;
          if (!data.success) {
            grid.innerHTML = `<div class="empty-state" style="grid-column:1 / -1; color:#ef4444;">خطا: ${data.error}</div>`;
            return;
          }
          if (!data.feed || data.feed.length === 0) {
            grid.innerHTML = '<div class="empty-state" style="grid-column:1 / -1;">هیچ پستی در فید این اکانت یافت نشد.</div>';
            return;
          }
          let html = '';
          data.feed.forEach(item => {
            const u = item.user || {};
            const img = item.image_url ? `<img src="${item.image_url}" class="feed-card-img" alt="Post">` : `<div class="feed-card-img" style="display:flex;align-items:center;justify-content:center;color:#64748b;">بدون تصویر</div>`;
            html += `
              <div class="feed-card">
                ${img}
                <div class="feed-card-body">
                  <div>
                    <div class="feed-card-author">👤 @${escapeHtml(u.username || 'unknown')}</div>
                    <div class="feed-card-caption">${escapeHtml(item.caption || '')}</div>
                  </div>
                  <div style="font-size:0.78rem; color:var(--muted); display:flex; justify-content:space-between; align-items:center;">
                    <span>❤️ ${item.like_count || 0} لایک</span>
                    <span style="color:#10b981;">مشاهده‌شده</span>
                  </div>
                </div>
              </div>
            `;
          });
          grid.innerHTML = html;
        })
        .catch(err => {
          grid.innerHTML = `<div class="empty-state" style="grid-column:1 / -1; color:#ef4444;">خطای شبکه: ${err}</div>`;
        });
    }

    function triggerSimulatedScroll(btn) {
      btn.disabled = true;
      btn.innerText = '⏳ در حال شبیه‌سازی اسکرول انسان...';
      const alertBox = document.getElementById('feed-status-alert');

      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/scroll-feed/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF_TOKEN }
      })
      .then(r => r.json())
      .then(data => {
        btn.disabled = false;
        btn.innerText = '📜 اجرای اسکرول و گرم‌سازی ارگانیک (Warm-up)';
        alertBox.style.display = 'block';
        if (data.success) {
          alertBox.style.background = 'rgba(16, 185, 129, 0.2)';
          alertBox.style.color = '#34d399';
          alertBox.innerText = `✅ ${data.message} ${data.liked_post ? '❤️ (یک پست تصادفی در فید لایک شد)' : ''}`;
        } else {
          alertBox.style.background = 'rgba(239, 68, 68, 0.2)';
          alertBox.style.color = '#f87171';
          alertBox.innerText = `❌ خطا: ${data.error}`;
        }
      })
      .catch(err => {
        btn.disabled = false;
        btn.innerText = '📜 اجرای اسکرول و گرم‌سازی ارگانیک (Warm-up)';
        alertBox.style.display = 'block';
        alertBox.style.background = 'rgba(239, 68, 68, 0.2)';
        alertBox.style.color = '#f87171';
        alertBox.innerText = 'خطای ارتباط: ' + err;
      });
    }

    // Search Users
    function searchInstagramUser(e) {
      e.preventDefault();
      const q = document.getElementById('search-username-input').value.trim();
      if (!q) return;

      const box = document.getElementById('search-results-box');
      box.innerHTML = '<div style="color:var(--muted); padding:10px;">در حال جستجو در اینستاگرام...</div>';

      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/search/?q=${encodeURIComponent(q)}`)
        .then(r => r.json())
        .then(data => {
          if (!data.success) {
            box.innerHTML = `<div style="color:#ef4444; padding:10px;">خطا: ${data.error}</div>`;
            return;
          }
          if (!data.results || data.results.length === 0) {
            box.innerHTML = '<div style="color:var(--muted); padding:10px;">هیچ کاربری با این نام یافت نشد.</div>';
            return;
          }
          let html = '';
          data.results.forEach(u => {
            html += `
              <div style="background:#0f172a; border:1px solid var(--border); border-radius:8px; padding:12px; display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; align-items:center; gap:12px;">
                  <div class="thread-avatar">${(u.username || 'U')[0].toUpperCase()}</div>
                  <div>
                    <div style="font-weight:700;">@${escapeHtml(u.username)} ${u.is_verified ? '☑️' : ''}</div>
                    <div style="font-size:0.8rem; color:var(--muted);">${escapeHtml(u.full_name || '')} ${u.is_private ? '🔒 خصوصی' : '🌍 عمومی'}</div>
                  </div>
                </div>
                <div>
                  <button type="button" class="btn-action" style="background:#9333ea; font-size:0.8rem;" onclick="openOrganicDmModal('${escapeHtml(u.username)}')">
                    🚀 ارسال پیام ارگانیک
                  </button>
                </div>
              </div>
            `;
          });
          box.innerHTML = html;
        })
        .catch(err => {
          box.innerHTML = `<div style="color:#ef4444; padding:10px;">خطا: ${err}</div>`;
        });
    }

    function openOrganicDmModal(username) {
      document.getElementById('target-user-label').innerText = '@' + username;
      document.getElementById('organic-dm-box').style.display = 'block';
      document.getElementById('organic-dm-box').dataset.targetUsername = username;
      document.getElementById('organic-log-box').style.display = 'none';
      document.getElementById('organic-dm-box').scrollIntoView({ behavior: 'smooth' });
    }

    function executeOrganicDm(btn) {
      const username = document.getElementById('organic-dm-box').dataset.targetUsername;
      const text = document.getElementById('organic-dm-text').value.trim();
      if (!username || !text) {
        alert('لطفاً متن پیام را وارد کنید.');
        return;
      }

      btn.disabled = true;
      btn.innerText = '⏳ در حال اجرای سفر ارگانیک...';
      const logBox = document.getElementById('organic-log-box');
      logBox.style.display = 'block';
      logBox.innerHTML = '<span style="color:#38bdf8;">[۱/۵] باز کردن فید و شبیه‌سازی اسکرول انسان...</span><br>';

      const fd = new FormData();
      fd.append('target_username', username);
      fd.append('message_text', text);

      fetch(`/manage/instagram/accounts/${ACCOUNT_ID}/api/organic-dm/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF_TOKEN },
        body: fd
      })
      .then(r => r.json())
      .then(data => {
        btn.disabled = false;
        btn.innerText = 'ارسال پیام ارگانیک';
        if (data.success) {
          const j = data.journey || {};
          logBox.innerHTML += `
            <span style="color:#34d399;">[۲/۵] سرچ نام کاربری @${escapeHtml(username)} در سرچ‌بار اینستاگرام انجام شد.</span><br>
            <span style="color:#34d399;">[۳/۵] پروفایل و پست‌های کاربر بررسی شد.</span><br>
            <span style="color:#34d399;">[۴/۵] مکث طبیعی تایپ اعمال شد.</span><br>
            <span style="color:#10b981; font-weight:700;">[۵/۵] 🎉 پیام با موفقیت به دایرکت کاربر ارسال گردید!</span>
          `;
        } else {
          logBox.innerHTML += `<span style="color:#ef4444;">❌ خطا: ${data.error}</span>`;
        }
      })
      .catch(err => {
        btn.disabled = false;
        btn.innerText = 'ارسال پیام ارگانیک';
        logBox.innerHTML += `<span style="color:#ef4444;">❌ خطای ارتباط: ${err}</span>`;
      });
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    // Auto load on open
    window.addEventListener('DOMContentLoaded', () => {
      loadThreads();
    });
