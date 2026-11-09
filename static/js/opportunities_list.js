(() => {
  function showFeedbackSuccessToast(msg) {
    let toast = document.getElementById('global-feedback-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'global-feedback-toast';
      toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#047857;color:#fff;padding:10px 20px;border-radius:8px;font-size:0.86rem;font-weight:700;box-shadow:0 10px 25px rgba(0,0,0,0.25);z-index:9999;transition:all 0.3s ease;display:flex;align-items:center;gap:8px;pointer-events:none;';
      document.body.appendChild(toast);
    }
    toast.innerHTML = `<span>✓</span> <span>${msg}</span>`;
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(-50%) translateY(0)';
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(10px)';
    }, 4000);
  }

  document.querySelectorAll('.list-feedback-button').forEach(button => {
    button.addEventListener('click', () => {
      const row = button.closest('article, .opportunity-card');
      const container = button.closest('.feedback-actions') || row;
      const reason = row?.querySelector('.feedback-reason')?.value || '';
      row?.querySelectorAll('.list-feedback-button').forEach(item => { item.disabled = true; });
      fetch(button.dataset.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': button.dataset.csrf },
        body: 'feedback=' + encodeURIComponent(button.dataset.feedback) + '&reason=' + encodeURIComponent(reason)
      }).then(response => response.json()).then(data => {
        if (data.status !== 'success') throw new Error(data.message || 'ذخیره بازخورد ناموفق بود.');
        const fbLabel = data.feedback === 'relevant' ? 'مرتبط' : 'نامرتبط';
        const colorBg = data.feedback === 'relevant' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)';
        const colorText = data.feedback === 'relevant' ? '#047857' : '#DC2626';
        if (container) {
          container.innerHTML = `<span class="p-badge" style="background: ${colorBg}; color: ${colorText}; font-size: 0.8rem; font-weight: 700; padding: 5px 12px; border-radius: 6px;">✓ بازخورد ثبت شد: ${fbLabel}</span>`;
        }
        showFeedbackSuccessToast(data.message || 'بازخورد با موفقیت ثبت شد و در یادگیری سیستم اعمال گردید.');
      }).catch(err => {
        row?.querySelectorAll('.list-feedback-button').forEach(item => { item.disabled = false; });
        alert(err.message || 'خطا در ذخیره بازخورد');
      });
    });
  });
  // Check for new opportunities every 20 seconds
  const bar = document.getElementById('new-opps-bar');
  const barText = document.getElementById('new-opps-text');
  const faDigits = n => String(n).replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]);
  if (bar) {
    const check = () => {
      if (document.hidden) return;
      const scope = new URLSearchParams(bar.dataset.query || '');
      scope.set('after', bar.dataset.after);
      fetch(`${bar.dataset.endpoint}?${scope.toString()}`, { headers: { 'Accept': 'application/json' } })
        .then(r => r.ok ? r.json() : null)
        .then(res => {
          if (res && res.count > 0) {
            barText.textContent = `${faDigits(res.count)} فرصت جدید`;
            bar.style.display = 'block';
          }
        })
        .catch(() => {});
    };
    setInterval(check, 20000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  }

  // Long texts: clamp to N lines with a «بیشتر / کمتر» toggle (only when the text really overflows)
  document.querySelectorAll('.js-clamp').forEach(el => {
    el.style.setProperty('--clamp', el.dataset.lines || 3);
    el.classList.add('is-clamped');
    requestAnimationFrame(() => {
      if (el.scrollHeight - el.clientHeight < 4) { el.classList.remove('is-clamped'); return; }
      const t = document.createElement('button');
      t.type = 'button'; t.className = 'clamp-toggle'; t.textContent = 'بیشتر';
      t.addEventListener('click', () => {
        const open = el.classList.toggle('is-clamped') === false;
        t.textContent = open ? 'کمتر' : 'بیشتر';
      });
      el.insertAdjacentElement('afterend', t);
    });
  });

  // 1-Click Copy to Clipboard for Suggested Replies
  document.querySelectorAll('.copy-reply-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      const el = document.getElementById(targetId);
      if (!el) return;

      const text = el.textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        const originalHTML = btn.innerHTML;
        btn.innerHTML = '<span> کپی شد!</span>';
        btn.style.color = '#10B981';
        btn.style.borderColor = '#10B981';
        setTimeout(() => {
          btn.innerHTML = originalHTML;
          btn.style.color = '';
          btn.style.borderColor = '';
        }, 2000);
      }).catch(() => {
        alert('خطا در کپی متن.');
      });
    });
  });

  // When a seller chooses a specific catalog product, automatically set sort to highest intent if not already changed
  const prodSelect = document.getElementById('product-filter-select');
  const sortSelect = document.getElementById('sort-select');
  if (prodSelect && sortSelect) {
    prodSelect.addEventListener('change', () => {
      if (prodSelect.value && sortSelect.value === 'newest') {
        sortSelect.value = 'intent_desc';
      }
    });
  }
})();
