  const MANAGE_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
  document.addEventListener('DOMContentLoaded', () => {
    // If URL contains ?tab=... or hash, switch to it directly
    const urlParams = new URLSearchParams(window.location.search);
    const tabParam = urlParams.get('tab') || window.location.hash.replace('#', '').replace('tab-', '');
    if (tabParam) {
      const tabBtn = document.querySelector(`.tab-btn[onclick*="${tabParam}"]`);
      if (tabBtn) openTab(tabParam, tabBtn);
    }

    document.querySelectorAll('.table-responsive table').forEach(table => {
      const heads = Array.from(table.querySelectorAll('thead th')).map(th => th.innerText.trim());
      table.querySelectorAll('tbody tr').forEach(tr => Array.from(tr.children).forEach((td, i) => {
        if (td.tagName === 'TD' && !td.hasAttribute('data-label') && heads[i]) td.setAttribute('data-label', heads[i]);
      }));
    });
  function updateAdmPitch(sel) {
    const opt = sel.options[sel.selectedIndex];
    const c = opt.getAttribute('data-consultant') || '';
    const t = opt.getAttribute('data-template') || '';
    if (c) document.getElementById('adm-consultant-input').value = c;
    if (t) document.getElementById('adm-pitch-text').value = t;
  }

  function generateAdminAiPitches() {
    const btn = document.getElementById('adm-ai-btn');
    const consultant = document.getElementById('adm-consultant-input').value || 'مشاور';
    const tone = document.getElementById('adm-ai-tone').value;
    const focus = document.getElementById('adm-ai-focus').value;
    const box = document.getElementById('adm-ai-pitches-box');

    btn.disabled = true;
    btn.innerText = '⏳ در حال نگارش ۳ متن با AI...';

    const formData = new FormData();
    formData.append('consultant_name', consultant);
    formData.append('tone_style', tone);
    formData.append('focus_area', focus);

    const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    fetch(MANAGE_CFG.generatePitchesUrl, {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
      body: formData,
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerText = '🤖 نگارش مجدد پیام‌ها با AI';
      if (data.success && data.pitches) {
        box.style.display = 'flex';
        box.innerHTML = '';
        data.pitches.forEach((p, i) => {
          const d = document.createElement('div');
          d.style.background = '#fff';
          d.style.border = '1px solid #d8b4fe';
          d.style.borderRadius = '6px';
          d.style.padding = '8px 10px';
          d.style.fontSize = '0.8rem';
          d.innerHTML = `
            <div style="font-weight:700; color:#6b21a8; margin-bottom:4px;">${p.title}</div>
            <div style="color:#334155; line-height:1.5; margin-bottom:6px;">${p.text}</div>
            <button type="button" class="btn" style="padding:3px 8px; font-size:0.75rem; background:#7c3aed; color:#fff;" onclick="document.getElementById('adm-pitch-text').value = \`${p.text.replace(/`/g, '\\`')}\`">
              ✍️ قرار دادن در کادر ویرایش
            </button>
          `;
          box.appendChild(d);
        });
      }
    })
    .catch(() => {
      btn.disabled = false;
      btn.innerText = '🤖 نگارش ۳ متن هوشمند با هوش مصنوعی';
    });
  }

  window.openSenderEditModal = function(id, username, tenantId, tier, dailyLimit, proxy, status, consultant) {
    document.getElementById('editSenderId').value = id;
    document.getElementById('senderModalUsername').innerText = '@' + username;
    document.getElementById('editSenderSessionid').value = '';
    document.getElementById('editSenderPassword').value = '';
    document.getElementById('editSenderConsultant').value = consultant || '';
    document.getElementById('editSenderTenant').value = tenantId || 'tenant_4';
    document.getElementById('editSenderTier').value = tier || 'NEW';
    document.getElementById('editSenderDailyLimit').value = dailyLimit || 9;
    document.getElementById('editSenderProxy').value = proxy || '';
    document.getElementById('editSenderStatus').value = status || 'ACTIVE';
    document.getElementById('editSenderClearCooldown').checked = true;
    document.getElementById('editSenderError').style.display = 'none';
    document.getElementById('editSenderError').innerText = '';

    const modal = document.getElementById('senderEditModal');
    modal.style.display = 'flex';
    document.getElementById('editSenderSessionid').focus();
  };

  window.closeSenderEditModal = function() {
    document.getElementById('senderEditModal').style.display = 'none';
  };

  window.submitSenderEdit = function(e) {
    e.preventDefault();
    const id = document.getElementById('editSenderId').value;
    const btn = document.getElementById('editSenderSubmitBtn');
    const errEl = document.getElementById('editSenderError');
    const form = document.getElementById('senderEditForm');

    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = 'در حال ذخیره و تست سشن...';
    errEl.style.display = 'none';

    const formData = new FormData(form);

    fetch(`/manage/senders/${id}/edit/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerText = origText;
      if (data.success) {
        closeSenderEditModal();
        alert('✓ ' + data.message);
        window.location.reload();
      } else {
        errEl.innerText = data.message || 'خطا در ویرایش اکانت.';
        errEl.style.display = 'block';
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerText = origText;
      errEl.innerText = 'خطای ارتباط: ' + err;
      errEl.style.display = 'block';
    });
  };

  window.clearSenderCooldown = function(id, username) {
    if (!confirm(`آیا مایلید وضعیت استراحت اکانت @${username} فوراً لغو شده و اکانت آماده ارسال شود؟`)) return;

    fetch(`/manage/senders/${id}/clear-cooldown/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        alert('✓ ' + data.message);
        window.location.reload();
      } else {
        alert('خطا: ' + (data.message || 'عملیات ناموفق بود'));
      }
    })
    .catch(err => alert('خطای ارتباط: ' + err));
  };

  window.syncChannelConfigs = function(btn) {
    const textSpan = document.getElementById('btn-sync-ar14n24b-text') || btn;
    const alertBox = document.getElementById('proxy-live-alert');
    const origHtml = btn.innerHTML;

    btn.disabled = true;
    btn.style.opacity = '0.75';
    if (textSpan) textSpan.innerHTML = '⏳ در حال دریافت و تست کانفیگ‌ها...';

    if (alertBox) {
      alertBox.style.display = 'block';
      alertBox.style.background = '#f0f9ff';
      alertBox.style.color = '#0369a1';
      alertBox.style.border = '1px solid #bae6fd';
      alertBox.innerHTML = '📥 در حال اتصال به کانال @AR14N24B، استخراج کانفیگ‌های تازه و سنجش پینگ بلادرنگ...';
    }

    const formData = new FormData();
    formData.append('channel', 'AR14N24B');

    fetch('/manage/proxies/channel-sync/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;

      if (!data.success) {
        if (alertBox) {
          alertBox.style.background = '#fef2f2';
          alertBox.style.color = '#991b1b';
          alertBox.style.border = '1px solid #fecaca';
          alertBox.innerHTML = '❌ ' + (data.error || data.message || 'خطا در ارتباط با کانال');
        }
        return;
      }

      if (alertBox) {
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#065f46';
        alertBox.style.border = '1px solid #a7f3d0';
        alertBox.innerHTML = `✓ ${data.message}`;
      }

      if (data.added_count > 0) {
        setTimeout(() => window.location.reload(), 1500);
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;
      if (alertBox) {
        alertBox.style.background = '#fef2f2';
        alertBox.style.color = '#991b1b';
        alertBox.style.border = '1px solid #fecaca';
        alertBox.innerHTML = '❌ خطای شبکه در ارتباط با سرور: ' + err;
      }
    });
  };

  // ==========================================
  // TELEGRAM PROXY CHANNELS DYNAMIC CRUD & SYNC
  // ==========================================
  window.addProxyChannel = function(e) {
    e.preventDefault();
    const inputAddr = document.getElementById('input-channel-address');
    const inputTitle = document.getElementById('input-channel-title');
    const btnSubmit = document.getElementById('btn-submit-channel');
    const alertBox = document.getElementById('proxy-live-alert');

    const address = inputAddr ? inputAddr.value.trim() : '';
    const title = inputTitle ? inputTitle.value.trim() : '';
    if (!address) return;

    btnSubmit.disabled = true;
    const origHtml = btnSubmit.innerHTML;
    btnSubmit.innerHTML = '⏳ در حال ثبت...';

    const fd = new FormData();
    fd.append('channel_username', address);
    fd.append('title', title);

    fetch('/manage/proxies/channels/add/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: fd
    })
    .then(r => r.json())
    .then(data => {
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = origHtml;

      if (!data.success) {
        alert('خطا در افزودن کانال: ' + (data.message || 'خطای سرور'));
        return;
      }

      inputAddr.value = '';
      if (inputTitle) inputTitle.value = '';

      if (alertBox) {
        alertBox.style.display = 'block';
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#065f46';
        alertBox.style.border = '1px solid #a7f3d0';
        alertBox.innerHTML = `✓ ${data.message}`;
      }

      // Reload to refresh table cleanly
      setTimeout(() => window.location.reload(), 1200);
    })
    .catch(err => {
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = origHtml;
      alert('خطای ارتباط با سرور: ' + err);
    });
  };

  window.deleteProxyChannel = function(id, username) {
    if (!confirm(`آیا از حذف کانال @${username} از لیست پایش کانفیگ اطمینان دارید؟`)) {
      return;
    }
    const alertBox = document.getElementById('proxy-live-alert');

    fetch(`/manage/proxies/channels/${id}/delete/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        const row = document.getElementById(`channel-row-${id}`);
        if (row) {
          row.style.transition = 'all 0.3s';
          row.style.opacity = '0';
          setTimeout(() => row.remove(), 300);
        }
        if (alertBox) {
          alertBox.style.display = 'block';
          alertBox.style.background = '#ecfdf5';
          alertBox.style.color = '#065f46';
          alertBox.style.border = '1px solid #a7f3d0';
          alertBox.innerHTML = `✓ ${data.message}`;
        }
      } else {
        alert(data.message || 'خطا در حذف کانال');
      }
    })
    .catch(err => alert('خطای شبکه: ' + err));
  };

  window.toggleChannelActive = function(id, btn) {
    fetch(`/manage/proxies/channels/${id}/toggle/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        const statusEl = document.getElementById(`channel-status-${id}`);
        if (statusEl) {
          if (data.is_active) {
            statusEl.innerHTML = '<span class="badge-status active"><i class="dot dot-ok" aria-hidden="true"></i> فعال برای پایش</span>';
            btn.innerText = 'توقف';
          } else {
            statusEl.innerHTML = '<span class="badge-status cooldown"><i class="dot dot-warn" aria-hidden="true"></i> متوقف</span>';
            btn.innerText = 'فعال';
          }
        }
      } else {
        alert(data.message || 'خطا در تغییر وضعیت کانال');
      }
    })
    .catch(err => alert('خطای شبکه: ' + err));
  };

  window.syncSingleChannel = function(id, btn) {
    const alertBox = document.getElementById('proxy-live-alert');
    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '⏳ بررسی...';

    if (alertBox) {
      alertBox.style.display = 'block';
      alertBox.style.background = '#f0f9ff';
      alertBox.style.color = '#0369a1';
      alertBox.style.border = '1px solid #bae6fd';
      alertBox.innerHTML = '📥 در حال دریافت پیام‌های تازه کانال، استخراج کانفیگ‌های جدید و سنجش پینگ...';
    }

    fetch(`/manage/proxies/channels/${id}/sync/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerHTML = origText;

      if (!data.success) {
        if (alertBox) {
          alertBox.style.background = '#fef2f2';
          alertBox.style.color = '#991b1b';
          alertBox.style.border = '1px solid #fecaca';
          alertBox.innerHTML = '❌ ' + (data.error || data.message || 'خطا در بررسی کانال');
        }
        return;
      }

      if (alertBox) {
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#065f46';
        alertBox.style.border = '1px solid #a7f3d0';
        alertBox.innerHTML = `✓ ${data.message}`;
      }

      if (data.added_count > 0) {
        setTimeout(() => window.location.reload(), 1500);
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = origText;
      alert('خطای ارتباط با سرور: ' + err);
    });
  };

  window.syncAllChannels = function(btn) {
    const alertBox = document.getElementById('proxy-live-alert');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.style.opacity = '0.75';
    btn.innerHTML = '⏳ در حال بررسی تمام کانال‌ها...';

    if (alertBox) {
      alertBox.style.display = 'block';
      alertBox.style.background = '#f0f9ff';
      alertBox.style.color = '#0369a1';
      alertBox.style.border = '1px solid #bae6fd';
      alertBox.innerHTML = '📥 در حال استخراج و پینگ‌سنجی کانفیگ‌های جدید از تمام کانال‌های فعال...';
    }

    fetch('/manage/proxies/channels/sync-all/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;

      if (alertBox) {
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#065f46';
        alertBox.style.border = '1px solid #a7f3d0';
        alertBox.innerHTML = `✓ ${data.message}`;
      }

      if (data.total_added > 0) {
        setTimeout(() => window.location.reload(), 1500);
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;
      alert('خطای ارتباط با سرور: ' + err);
    });
  };

  window.testAllProxiesLive = function(btn) {
    const textSpan = document.getElementById('btn-live-test-text') || btn;
    const alertBox = document.getElementById('proxy-live-alert');
    const origHtml = btn.innerHTML;

    btn.disabled = true;
    btn.style.opacity = '0.75';
    if (textSpan) textSpan.innerHTML = '⏳ در حال تست زنده...';

    // Update table rows to indicate probing in progress
    document.querySelectorAll('[id^="proxy-ping-"]').forEach(el => {
      el.innerHTML = '<span style="color:#2563eb; font-weight:600;">در حال پینگ... ⚡</span>';
    });
    document.querySelectorAll('[id^="proxy-status-"]').forEach(el => {
      el.innerHTML = '<span class="badge-status cooldown"><i class="dot dot-warn" aria-hidden="true"></i> در حال تست</span>';
    });

    if (alertBox) {
      alertBox.style.display = 'block';
      alertBox.style.background = '#eff6ff';
      alertBox.style.color = '#1e40af';
      alertBox.style.border = '1px solid #bfdbfe';
      alertBox.innerHTML = '⚡ در حال برقراری ارتباط با پورت‌های پروکسی و سنجش تاخیر میلی‌ثانیه‌ای سرورها...';
    }

    fetch('/manage/proxies/test/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: 'all=1'
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;

      if (!data.success) {
        if (alertBox) {
          alertBox.style.background = '#fef2f2';
          alertBox.style.color = '#991b1b';
          alertBox.style.border = '1px solid #fecaca';
          alertBox.innerHTML = '❌ خطا در تست پروکسی‌ها: ' + (data.message || 'خطای سرور');
        }
        return;
      }

      if (data.results && data.results.length > 0) {
        data.results.forEach(px => {
          const pingEl = document.getElementById(`proxy-ping-${px.id}`);
          const statusEl = document.getElementById(`proxy-status-${px.id}`);
          const timeEl = document.getElementById(`proxy-last-checked-${px.id}`);

          if (pingEl) {
            if (px.status === 'ACTIVE' && px.ping_ms >= 0) {
              pingEl.style.color = '#16a34a';
              pingEl.innerHTML = `${Math.round(px.ping_ms)} ms`;
            } else {
              pingEl.style.color = '#dc2626';
              pingEl.innerHTML = '<span style="color:#dc2626;">قطع</span>';
            }
          }

          if (statusEl) {
            if (px.status === 'ACTIVE') {
              statusEl.innerHTML = '<span class="badge-status active"><i class="dot dot-ok" aria-hidden="true"></i> فعال</span>';
            } else {
              statusEl.innerHTML = '<span class="badge-status banned"><i class="dot dot-bad" aria-hidden="true"></i> قطع</span>';
            }
          }

          if (timeEl) {
            timeEl.innerText = px.last_checked_fa || 'چند لحظه پیش';
          }
        });
      }

      if (alertBox) {
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#065f46';
        alertBox.style.border = '1px solid #a7f3d0';
        alertBox.innerHTML = `✓ ${data.message}`;
      }

      const tabBtn = document.getElementById('tab-btn-proxies');
      if (tabBtn && data.total !== undefined) {
        tabBtn.innerHTML = `<svg class="ico" aria-hidden="true"><use href="#i-globe"/></svg> پروکسی‌ها (${data.active_count}/${data.total})`;
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHtml;
      if (alertBox) {
        alertBox.style.background = '#fef2f2';
        alertBox.style.color = '#991b1b';
        alertBox.style.border = '1px solid #fecaca';
        alertBox.innerHTML = '❌ خطای شبکه در ارتباط با سرور: ' + err;
      }
    });
  };

  window.testSingleProxyLive = function(id, btn) {
    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '⏳...';

    const pingEl = document.getElementById(`proxy-ping-${id}`);
    const statusEl = document.getElementById(`proxy-status-${id}`);
    const timeEl = document.getElementById(`proxy-last-checked-${id}`);

    if (pingEl) pingEl.innerHTML = '<span style="color:#2563eb; font-size:0.75rem;">تست...</span>';
    if (statusEl) statusEl.innerHTML = '<span class="badge-status cooldown"><i class="dot dot-warn" aria-hidden="true"></i> در حال تست</span>';

    const formData = new FormData();
    formData.append('proxy_id', id);

    fetch(`/manage/proxies/${id}/test/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: formData
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerHTML = origText;

      if (data.success && data.proxy) {
        const px = data.proxy;
        if (pingEl) {
          if (px.status === 'ACTIVE' && px.ping_ms >= 0) {
            pingEl.style.color = '#16a34a';
            pingEl.innerHTML = `${Math.round(px.ping_ms)} ms`;
          } else {
            pingEl.style.color = '#dc2626';
            pingEl.innerHTML = '<span style="color:#dc2626;">قطع</span>';
          }
        }
        if (statusEl) {
          if (px.status === 'ACTIVE') {
            statusEl.innerHTML = '<span class="badge-status active"><i class="dot dot-ok" aria-hidden="true"></i> فعال</span>';
          } else {
            statusEl.innerHTML = '<span class="badge-status banned"><i class="dot dot-bad" aria-hidden="true"></i> قطع</span>';
          }
        }
        if (timeEl) {
          timeEl.innerText = px.last_checked_fa || 'چند لحظه پیش';
        }
      } else {
        alert(data.message || 'خطا در تست پروکسی');
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = origText;
      alert('خطای ارتباط: ' + err);
    });
  };
  });
