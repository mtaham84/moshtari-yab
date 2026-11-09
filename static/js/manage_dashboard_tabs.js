  const TABS_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
  function openTab(tabName, btn) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    const targetPane = document.getElementById('tab-' + tabName);
    if (targetPane) targetPane.classList.add('active');
    if (btn) btn.classList.add('active');
    if (tabName === 'instagram-live') {
      fetchLiveLogs();
    }
  }

  let livePollingTimer = null;
  let isLivePollingActive = true;

  function toggleLivePolling() {
    isLivePollingActive = !isLivePollingActive;
    const btn = document.getElementById('btn-toggle-live-polling');
    if (isLivePollingActive) {
      if (btn) {
        btn.style.background = '#10b981';
        btn.innerHTML = '<span class="live-dot-pulse" style="background:#fff; margin-left:6px;"></span> پایش زنده (هر ۲ ثانیه)';
      }
      fetchLiveLogs();
      if (!livePollingTimer) livePollingTimer = setInterval(fetchLiveLogs, 2000);
    } else {
      if (btn) {
        btn.style.background = '#64748b';
        btn.innerHTML = '⏸️ پایش زنده متوقف شد';
      }
      if (livePollingTimer) {
        clearInterval(livePollingTimer);
        livePollingTimer = null;
      }
    }
  }

  async function fetchLiveLogs() {
    try {
      const res = await fetch(TABS_CFG.liveLogsUrl);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.success) return;

      const timeEl = document.getElementById('live-status-time');
      if (timeEl && data.server_time) timeEl.textContent = 'آخرین بررسی: ' + data.server_time;

      // 1. Scraper State
      const scBadge = document.getElementById('live-scraper-badge');
      const scText = document.getElementById('live-scraper-text');
      const scSub = document.getElementById('live-scraper-sub');
      const sc = data.scraper_state || {};

      if (sc.is_active) {
        if (scBadge) {
          scBadge.style.background = '#dcfce7';
          scBadge.style.color = '#15803d';
          scBadge.textContent = '🟢 ' + (sc.action_label || 'در حال کار');
        }
        if (scText) scText.innerHTML = `<b>@${sc.account}</b> — ${sc.details || 'در حال انجام عملیات'}`;
        if (scSub) scSub.textContent = (sc.seconds_ago !== null ? `${sc.seconds_ago} ثانیه پیش` : '');
      } else {
        if (scBadge) {
          scBadge.style.background = '#f1f5f9';
          scBadge.style.color = '#64748b';
          scBadge.textContent = '⚪ آماده‌باش / وقفه';
        }
        if (scText) scText.textContent = sc.details || 'در انتظار چرخه بعدی...';
        if (scSub) scSub.textContent = sc.account ? `آخرین اکانت: @${sc.account}` : 'اکانت: —';
      }

      // 2. Video / Reel Watching State
      const vidBadge = document.getElementById('live-video-badge');
      const vidText = document.getElementById('live-video-text');
      const vidSub = document.getElementById('live-video-sub');

      const isWatching = sc.is_active && (sc.action_type === 'WATCH_VIDEO' || sc.media_type === 'REEL' || sc.media_type === 'VIDEO');
      if (isWatching) {
        const isReel = sc.media_type === 'REEL';
        if (vidBadge) {
          vidBadge.style.background = '#f3e8ff';
          vidBadge.style.color = '#7e22ce';
          vidBadge.textContent = `🎬 در حال تماشای ${isReel ? 'ریلز' : 'ویدیو'} (${sc.duration_seconds || 0}s)`;
        }
        if (vidText) {
          const link = sc.media_url ? `<a href="${sc.media_url}" target="_blank" style="color:#c084fc; text-decoration:underline; font-weight:700;">پست ${sc.media_code || ''} ↗</a>` : `پست ${sc.media_code || ''}`;
          vidText.innerHTML = `${link} توسط @${sc.account}`;
        }
        if (vidSub) vidSub.textContent = sc.details || `مشاهده و پیمایش انسان‌گونه`;
      } else {
        if (vidBadge) {
          vidBadge.style.background = '#f1f5f9';
          vidBadge.style.color = '#64748b';
          vidBadge.textContent = 'بدون ویدیو';
        }
        if (vidText) vidText.textContent = 'در این لحظه ویدیویی باز نیست.';
        if (vidSub) vidSub.textContent = 'مدت تماشا: ۰ ثانیه';
      }

      // 3. Outreach State
      const outBadge = document.getElementById('live-outreach-badge');
      const outText = document.getElementById('live-outreach-text');
      const outSub = document.getElementById('live-outreach-sub');
      const out = data.outreach_state || {};

      if (out.is_active) {
        if (outBadge) {
          outBadge.style.background = '#dcfce7';
          outBadge.style.color = '#15803d';
          outBadge.textContent = '🟢 ' + (out.action_label || 'در حال ارسال');
        }
        if (outText) outText.innerHTML = `<b>@${out.account}</b> — ${out.details || 'در حال انجام'}`;
        if (outSub) outSub.textContent = (out.seconds_ago !== null ? `${out.seconds_ago} ثانیه پیش` : '');
      } else {
        if (outBadge) {
          outBadge.style.background = '#f1f5f9';
          outBadge.style.color = '#64748b';
          outBadge.textContent = '⚪ آماده‌باش';
        }
        if (outText) outText.textContent = out.details || 'در انتظار دیسپچ بعدی...';
        if (outSub) outSub.textContent = out.account ? `فرستنده: @${out.account}` : 'فرستنده: —';
      }

      // 4. Render Terminal Logs
      const term = document.getElementById('live-terminal-stream');
      const countEl = document.getElementById('live-log-count');
      if (countEl && data.logs) countEl.textContent = data.logs.length;

      if (term) {
        if (!data.logs || data.logs.length === 0) {
          term.innerHTML = '<div style="color:#64748b; text-align:center; padding:20px;">هنوز لاگی ثبت نشده است. با دکمه «تست زنده» می‌توانید یک رویداد نمونه ثبت کنید.</div>';
          return;
        }

        let html = '';
        data.logs.forEach(item => {
          const b = item.badge || {};
          const link = item.media_url ? `<a href="${item.media_url}" target="_blank" style="color:#c084fc; font-size:0.76rem; text-decoration:none; margin-right:4px;">[نمایش در اینستاگرام ↗]</a>` : '';
          html += `
            <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.06); display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;">
              <span style="color:#64748b; font-size:0.75rem; font-family:monospace;">[${item.time}]</span>
              <span style="padding:1px 6px; border-radius:4px; font-size:0.75rem; font-weight:700; background:${b.bg || 'rgba(255,255,255,0.1)'}; color:${b.color || '#fff'};">${b.icon || '📌'} ${b.label || item.action_type}</span>
              <span style="color:#38bdf8; font-weight:600; font-size:0.8rem;">@${item.account}</span>
              <span style="color:#e2e8f0; font-size:0.83rem;">${item.title}</span>
              ${item.description ? `<span style="color:#94a3b8; font-size:0.79rem;">(${item.description})</span>` : ''}
              ${link}
            </div>
          `;
        });
        term.innerHTML = html;
      }
    } catch (err) {
      console.warn("fetchLiveLogs error:", err);
    }
  }

  async function triggerTestLivePulse(btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ در حال شبیه‌سازی...';
    }
    try {
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
      await fetch(TABS_CFG.livePulseUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf, "Content-Type": "application/x-www-form-urlencoded" },
      });
      await fetchLiveLogs();
    } catch (err) {
      alert("خطا در ارسال پالس تستی: " + err);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '⚡ تست زنده (تماشای ریلز)';
      }
    }
  }

  async function clearLiveLogs(btn) {
    if (!confirm('آیا از پاک‌سازی لاگ‌های زنده اطمینان دارید؟')) return;
    try {
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
      await fetch(TABS_CFG.liveClearUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
      });
      await fetchLiveLogs();
    } catch (err) {
      alert("خطا: " + err);
    }
  }

  document.addEventListener('DOMContentLoaded', function() {
    fetchLiveLogs();
    livePollingTimer = setInterval(fetchLiveLogs, 2000);
  });

  function filterIgAccounts(role, btn) {
    document.querySelectorAll('#btn-ig-filter-all, #btn-ig-filter-senders, #btn-ig-filter-scrapers').forEach(b => {
      b.style.background = 'transparent';
      b.style.color = 'var(--muted)';
    });
    if (btn) {
      btn.style.background = 'var(--accent)';
      btn.style.color = '#fff';
    }
    document.querySelectorAll('.ig-account-row').forEach(row => {
      if (role === 'ALL' || row.dataset.role === role) {
        row.style.display = '';
      } else {
        row.style.display = 'none';
      }
    });
  }

  function switchTab(tabName) {
    openTab(tabName, document.querySelector('.tab-btn[onclick*="' + tabName + '"]'));
  }

  function switchQuerySubTab(subName, btn) {
    document.querySelectorAll('.query-subpane').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.query-subtab-btn').forEach(el => {
      el.classList.remove('primary');
      el.classList.remove('active');
    });
    const pane = document.getElementById('query-subtab-' + subName);
    if (pane) pane.style.display = 'block';
    if (btn) {
      btn.classList.add('primary');
      btn.classList.add('active');
    }
  }

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === (name + '=')) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function handleAccountAddForm(e, form) {
    e.preventDefault();
    const submitBtn = form.querySelector('button[type="submit"]');
    const origText = submitBtn.innerText;
    submitBtn.innerText = ' در حال ورود و احراز هویت در اینستاگرام...';
    submitBtn.disabled = true;

    const formData = new FormData(form);

    fetch(form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      if (data.account_id && ['running', 'needs_code', 'verifying'].includes(data.state)) {
        // the login continues on the account page: Instagram's code is typed there, into the same login
        window.location.href = '/manage/instagram/accounts/' + data.account_id + '/settings/#login';
      } else if (data.success) {
        alert(' ' + data.message);
        window.location.reload();
      } else {
        alert(' ' + (data.message || 'خطا در ثبت اکانت'));
      }
    })
    .catch(err => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      alert('خطای برقراری ارتباط با سرور: ' + err);
    });
  }

  function openChallengeModal(accId, username) {
    document.getElementById('modalAccountId').value = accId;
    document.getElementById('modalAccountHint').innerText = `اینستاگرام برای اکانت @${username} کد تایید امنیتی (ایمیل یا پیامک) درخواست کرده است. لطفاً کد ۶ رقمی دریافتی را وارد کنید:`;
    document.getElementById('modalCodeInput').value = '';
    document.getElementById('modalError').style.display = 'none';
    document.getElementById('modalError').innerText = '';
    const modal = document.getElementById('challengeModal');
    modal.style.display = 'flex';
    setTimeout(() => {
      document.getElementById('modalCodeInput').focus();
    }, 100);
  }

  function closeChallengeModal() {
    document.getElementById('challengeModal').style.display = 'none';
  }

  function submitChallengeCode() {
    const accId = document.getElementById('modalAccountId').value;
    let code = document.getElementById('modalCodeInput').value.trim();
    const errorEl = document.getElementById('modalError');
    const submitBtn = document.getElementById('btnSubmitCode');

    // Normalize Persian and Arabic digits
    code = code.replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d))
               .replace(/[٠-٩]/g, d => '٠١٢٣٤٥٦٧٨٩'.indexOf(d))
               .replace(/[\s\-]/g, '');

    if (!code) {
      errorEl.innerText = 'لطفاً کد تایید را وارد نمایید.';
      errorEl.style.display = 'block';
      return;
    }

    errorEl.style.display = 'none';
    const origText = submitBtn.innerText;
    submitBtn.innerText = ' در حال تایید و ثبت سشن...';
    submitBtn.disabled = true;

    fetch(`/manage/accounts/${accId}/code/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ code: code })
    })
    .then(res => res.json())
    .then(data => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      if (data.success) {
        closeChallengeModal();
        alert(' ' + data.message);
        window.location.reload();
      } else {
        errorEl.innerText = data.message || 'خطا در ثبت کد تایید.';
        errorEl.style.display = 'block';
      }
    })
    .catch(err => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      errorEl.innerText = 'خطای ارتباط با سرور: ' + err;
      errorEl.style.display = 'block';
    });
  }

  function openSessionModal(accId, username) {
    document.getElementById('modalSessionAccountId').value = accId;
    document.getElementById('modalSessionAccountHint').innerText = `کوکی sessionid مربوط به اکانت @${username} را از مرورگر کپی و وارد نمایید تا سشن فعال شود (بدون نیاز به پسورد و بدون چالش امنیتی):`;
    document.getElementById('modalSessionInput').value = '';
    document.getElementById('modalSessionError').style.display = 'none';
    document.getElementById('modalSessionError').innerText = '';
    document.getElementById('sessionModal').style.display = 'flex';
    setTimeout(() => {
      document.getElementById('modalSessionInput').focus();
    }, 100);
  }

  function closeSessionModal() {
    document.getElementById('sessionModal').style.display = 'none';
  }

  function submitSessionId() {
    const accId = document.getElementById('modalSessionAccountId').value;
    const sessionid = document.getElementById('modalSessionInput').value.trim();
    const errorEl = document.getElementById('modalSessionError');
    const submitBtn = document.getElementById('btnSubmitSession');

    if (!sessionid) {
      errorEl.innerText = 'لطفاً مقدار کوکی sessionid را وارد نمایید.';
      errorEl.style.display = 'block';
      return;
    }

    errorEl.style.display = 'none';
    const origText = submitBtn.innerText;
    submitBtn.innerText = ' در حال اتصال و ثبت سشن...';
    submitBtn.disabled = true;

    const formData = new FormData();
    formData.append('sessionid', sessionid);

    fetch(`/manage/accounts/${accId}/session/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      if (data.success) {
        closeSessionModal();
        alert(' ' + data.message);
        window.location.reload();
      } else if (data.deleted) {
        alert(' ' + data.message);
        window.location.reload();
      } else {
        errorEl.innerText = data.message || 'خطا در ثبت سشن.';
        errorEl.style.display = 'block';
      }
    })
    .catch(err => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      errorEl.innerText = 'خطای ارتباط با سرور: ' + err;
      errorEl.style.display = 'block';
    });
  }

  function testAccountLive(accId, btn) {
    const originalText = btn.innerText;
    btn.innerText = " تست...";
    btn.disabled = true;

    const feedback = document.getElementById('feedback-' + accId);
    if (feedback) {
      feedback.innerText = "در حال اتصال به پروکسی و اینستاگرام...";
      feedback.style.color = "var(--muted)";
    }

    fetch(`/manage/accounts/${accId}/test/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json'
      }
    })
    .then(res => res.json())
    .then(data => {
      btn.innerText = originalText;
      btn.disabled = false;
      const badge = document.getElementById('badge-' + accId);
      const challengeBtn = document.getElementById('btn-challenge-' + accId);

      if (badge && data.status) {
        badge.className = 'badge-status ' + data.status.toLowerCase();
        if (data.status === 'ACTIVE') {
          badge.innerText = ' متصل و فعال';
          if (challengeBtn) challengeBtn.style.display = 'none';
        } else if (data.status === 'COOLDOWN') {
          badge.innerText = ' استراحت';
          if (challengeBtn) challengeBtn.style.display = 'none';
        } else if (data.status === 'CHALLENGED') {
          badge.innerText = ' چالش امنیتی (کد تایید)';
          if (challengeBtn) challengeBtn.style.display = 'inline-block';
        } else {
          badge.innerText = ' مسدود شده';
          if (challengeBtn) challengeBtn.style.display = 'none';
        }
      }
      if (feedback) {
        feedback.innerText = data.message || '';
        feedback.style.color = data.success ? 'var(--ok)' : 'var(--down)';
      }
    })
    .catch(err => {
      btn.innerText = originalText;
      btn.disabled = false;
      if (feedback) {
        feedback.innerText = "خطا در تست: " + err;
        feedback.style.color = 'var(--down)';
      }
    });
  }

  function handleTwitterAccountAddForm(e, form) {
    e.preventDefault();
    const submitBtn = form.querySelector('button[type="submit"]');
    const origText = submitBtn.innerText;
    submitBtn.innerText = ' در حال تست و ثبت اکانت...';
    submitBtn.disabled = true;

    const formData = new FormData(form);

    fetch(form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      if (data.success) {
        alert(' ' + data.message);
        window.location.reload();
      } else {
        alert((data.success === false && data.account_id ? ' ' : ' ') + (data.message || 'خطا در ثبت اکانت'));
        window.location.reload();
      }
    })
    .catch(err => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      alert('خطای برقراری ارتباط با سرور: ' + err);
    });
  }

  function openTwitterSessionModal(accId, username, authToken, ct0) {
    document.getElementById('modalTwitterSessionAccountId').value = accId;
    document.getElementById('modalTwitterSessionAccountHint').innerText = `کوکی‌های سشن اکانت @${username} را وارد یا ویرایش نمایید:`;
    document.getElementById('modalTwitterAuthTokenInput').value = authToken || '';
    document.getElementById('modalTwitterCt0Input').value = (ct0 && ct0 !== 'None') ? ct0 : '';
    document.getElementById('modalTwitterPasswordInput').value = '';
    document.getElementById('modalTwitterSessionError').style.display = 'none';
    document.getElementById('modalTwitterSessionError').innerText = '';
    document.getElementById('twitterSessionModal').style.display = 'flex';
    setTimeout(() => {
      document.getElementById('modalTwitterAuthTokenInput').focus();
    }, 100);
  }

  function closeTwitterSessionModal() {
    document.getElementById('twitterSessionModal').style.display = 'none';
  }

  function submitTwitterSession() {
    const accId = document.getElementById('modalTwitterSessionAccountId').value;
    const authToken = document.getElementById('modalTwitterAuthTokenInput').value.trim();
    const ct0 = document.getElementById('modalTwitterCt0Input').value.trim();
    const password = document.getElementById('modalTwitterPasswordInput').value.trim();
    const errorEl = document.getElementById('modalTwitterSessionError');
    const submitBtn = document.getElementById('btnSubmitTwitterSession');

    if (!authToken) {
      errorEl.innerText = 'لطفاً مقدار کوکی auth_token را وارد نمایید.';
      errorEl.style.display = 'block';
      return;
    }

    errorEl.style.display = 'none';
    const origText = submitBtn.innerText;
    submitBtn.innerText = ' در حال اتصال و اعتبارسنجی سشن...';
    submitBtn.disabled = true;

    const formData = new FormData();
    formData.append('auth_token', authToken);
    if (ct0) formData.append('ct0', ct0);
    if (password) formData.append('password', password);

    fetch(`/manage/twitter-accounts/${accId}/session/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      if (data.success) {
        closeTwitterSessionModal();
        alert(' ' + data.message);
        window.location.reload();
      } else if (data.deleted) {
        alert(' ' + data.message);
        window.location.reload();
      } else {
        errorEl.innerText = data.message || 'خطا در ثبت سشن.';
        errorEl.style.display = 'block';
      }
    })
    .catch(err => {
      submitBtn.innerText = origText;
      submitBtn.disabled = false;
      errorEl.innerText = 'خطای ارتباط با سرور: ' + err;
      errorEl.style.display = 'block';
    });
  }

  function testTwitterAccount(accId, btn) {
    const originalText = btn.innerText;
    btn.innerText = " تست...";
    btn.disabled = true;

    const msgEl = document.getElementById('tw-status-msg-' + accId);
    if (msgEl) {
      msgEl.innerText = "در حال تست اتصال به توییتر / X...";
      msgEl.style.color = "var(--muted)";
    }

    fetch(`/manage/twitter-accounts/${accId}/test/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json'
      }
    })
    .then(res => res.json())
    .then(data => {
      btn.innerText = originalText;
      btn.disabled = false;
      const badge = document.getElementById('tw-status-badge-' + accId);

      if (badge && data.status) {
        if (data.status === 'ACTIVE') {
          badge.innerHTML = '<span class="badge-status active"> فعال و متصل</span>';
        } else if (data.status === 'COOLDOWN') {
          badge.innerHTML = '<span class="badge-status cooldown"> استراحت موقت (Rate Limit)</span>';
        } else if (data.status === 'CHALLENGED') {
          badge.innerHTML = '<span class="badge-status challenged"> نیازمند سشن معتبر</span>';
        } else if (data.status === 'BANNED') {
          badge.innerHTML = '<span class="badge-status banned"> مسدود شده</span>';
        } else {
          badge.innerHTML = `<span class="badge-status" style="background:#f1f5f9; color:#64748b;"> ${data.status_display || data.status}</span>`;
        }
      }
      if (msgEl) {
        msgEl.innerText = data.message || '';
        msgEl.style.color = data.success ? 'var(--ok)' : 'var(--down)';
      }
    })
    .catch(err => {
      btn.innerText = originalText;
      btn.disabled = false;
      if (msgEl) {
        msgEl.innerText = "خطا در تست: " + err;
        msgEl.style.color = 'var(--down)';
      }
    });
  }

  function toggleTwitterAccount(accId, btn) {
    const origText = btn.innerText;
    btn.innerText = '...';
    btn.disabled = true;

    fetch(`/manage/twitter-accounts/${accId}/toggle/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json'
      }
    })
    .then(res => res.json())
    .then(data => {
      btn.disabled = false;
      if (data.success) {
        btn.innerText = data.is_active ? 'غیرفعال' : 'فعال';
        window.location.reload();
      } else {
        btn.innerText = origText;
        alert('خطا در تغییر وضعیت اکانت');
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerText = origText;
      alert('خطای ارتباط با سرور: ' + err);
    });
  }
