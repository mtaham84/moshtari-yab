  const OUTREACH_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
  function onTierChange(val) {
    const dailyInput = document.getElementById('daily-limit-input');
    if (!dailyInput) return;
    if (val === 'NEW') {
      dailyInput.value = '15';
    } else if (val === 'MEDIUM') {
      dailyInput.value = '40';
    } else if (val === 'AGED') {
      dailyInput.value = '75';
    }
  }

  function updatePitchEditor(username) {
    const select = document.getElementById('sender-select');
    if (!select || select.selectedIndex < 0) return;   // one pitch per customer: no per-sender picker any more
    const selectedOption = select.options[select.selectedIndex];
    const consultant = selectedOption.getAttribute('data-consultant') || '';
    const template = selectedOption.getAttribute('data-template') || '';

    if (consultant) {
      document.getElementById('consultant-input').value = consultant;
      document.getElementById('preview-sender-name').innerText = consultant;
    }
    if (template) {
      document.getElementById('pitch-textarea').value = template;
      document.getElementById('preview-bubble').innerText = template;
    }
  }

  function updateLivePreview(text) {
    document.getElementById('preview-bubble').innerText = text || '(متن پیام خالی است)';
  }

  // AI Pitch Generation AJAX Caller
  function generateAiPitches() {
    const btn = document.getElementById('ai-generate-btn');
    const consultant = document.getElementById('consultant-input').value || 'مشاور';
    const tone = document.getElementById('ai-tone-select').value;
    const focus = document.getElementById('ai-focus-input').value;
    const container = document.getElementById('ai-pitches-container');

    btn.disabled = true;
    btn.innerHTML = '<span>در حال نگارش ۳ متن هوشمند با هوش مصنوعی...</span>';

    const formData = new FormData();
    formData.append('consultant_name', consultant);
    formData.append('tone_style', tone);
    formData.append('focus_area', focus);

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

    fetch(OUTREACH_CFG.generatePitchesUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: formData,
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerHTML = '<span>تولید مجدد پیام‌ها با AI</span>';

      if (data.success && data.pitches && data.pitches.length > 0) {
        container.style.display = 'flex';
        container.innerHTML = '';

        data.pitches.forEach((p, idx) => {
          const card = document.createElement('div');
          card.style.background = '#fff';
          card.style.border = '1px solid #d8b4fe';
          card.style.borderRadius = '8px';
          card.style.padding = '10px 12px';
          card.style.fontSize = '0.82rem';

          card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <strong style="color:#6b21a8;">گزینه ${idx + 1}: ${p.title || 'پیشنهاد هوش مصنوعی'}</strong>
              <span style="background:#f3e8ff; color:#7e22ce; font-size:0.72rem; padding:2px 6px; border-radius:4px; font-weight:700;">
                ${p.badge || 'کیفیت بالا'}
              </span>
            </div>
            <div style="color:#334155; line-height:1.6; white-space:pre-wrap; margin-bottom:8px;" id="pitch-text-${idx}">
              ${escapeHtml(p.text)}
            </div>
            <button type="button" onclick="useAiPitch(${idx})"
                    style="padding:5px 12px; background:#7c3aed; color:#fff; border:none; border-radius:6px; font-size:0.76rem; font-weight:700; cursor:pointer;">
              انتخاب و انتقال به کادر ویرایش
            </button>
          `;
          container.appendChild(card);
        });

        window._lastAiPitches = data.pitches;
      } else {
        alert('خطا در تولید متن هوش مصنوعی: ' + (data.error || 'پاسخی دریافت نشد.'));
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = '<span>تولید ۳ نمونه پیام هوشمند با AI</span>';
      alert('خطا در ارتباط با سرور: ' + err);
    });
  }

  function useAiPitch(index) {
    if (window._lastAiPitches && window._lastAiPitches[index]) {
      const selected = window._lastAiPitches[index].text;
      const textarea = document.getElementById('pitch-textarea');
      textarea.value = selected;
      updateLivePreview(selected);
      textarea.scrollIntoView({ behavior: 'smooth', block: 'center' });
      textarea.focus();
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.innerText = text;
    return div.innerHTML;
  }

  // Sender Account Edit Modal & Cooldown Reset
  window.openOutreachEditModal = function(id, username, tier, dailyLimit, consultant, status) {
    document.getElementById('editOutreachId').value = id;
    document.getElementById('outreachModalUsername').innerText = '@' + username;
    document.getElementById('editOutreachSessionid').value = '';
    document.getElementById('editOutreachPassword').value = '';
    document.getElementById('editOutreachConsultant').value = consultant || '';
    document.getElementById('editOutreachTier').value = tier || 'NEW';
    document.getElementById('editOutreachDailyLimit').value = dailyLimit || 18;
    document.getElementById('editOutreachClearCooldown').checked = true;
    document.getElementById('editOutreachError').style.display = 'none';
    document.getElementById('editOutreachError').innerText = '';

    const modal = document.getElementById('outreachEditModal');
    modal.style.display = 'flex';
    document.getElementById('editOutreachSessionid').focus();
  };

  window.closeOutreachEditModal = function() {
    document.getElementById('outreachEditModal').style.display = 'none';
  };

  window.submitOutreachEdit = function(e) {
    e.preventDefault();
    const id = document.getElementById('editOutreachId').value;
    const btn = document.getElementById('editOutreachSubmitBtn');
    const errEl = document.getElementById('editOutreachError');
    const form = document.getElementById('outreachEditForm');

    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = 'در حال ذخیره و احراز سشن...';
    errEl.style.display = 'none';

    const formData = new FormData(form);

    fetch(`/accounts/outreach/senders/${id}/edit/`, {
      method: 'POST',
      body: formData,
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerText = origText;
      if (data.success) {
        closeOutreachEditModal();
        alert(data.message || 'اطلاعات با موفقیت ذخیره شد.');
        window.location.reload();
      } else {
        errEl.innerText = data.message || 'خطا در ویرایش اکانت.';
        errEl.style.display = 'block';
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerText = origText;
      errEl.innerText = 'خطای ارتباط با سرور: ' + err;
      errEl.style.display = 'block';
    });
  };

  window.clearOutreachCooldown = function(id, username) {
    if (!confirm(`آیا می‌خواهید استراحت و محدودیت اکانت @${username} را لغو کرده و آن را فوراً فعال کنید؟`)) {
      return;
    }
    const formData = new FormData();
    formData.append('csrfmiddlewaretoken', OUTREACH_CFG.csrfToken);

    fetch(`/accounts/outreach/senders/${id}/clear-cooldown/`, {
      method: 'POST',
      body: formData,
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        alert(data.message || 'محدودیت با موفقیت برداشته شد.');
        window.location.reload();
      } else {
        alert('خطا: ' + (data.message || 'عملیات ناموفق بود.'));
      }
    })
    .catch(err => alert('خطای ارتباط با سرور: ' + err));
  };

  // Chat Room Logic
  window._currentChatLeadId = null;

  window.selectChatLead = function(leadId) {
    if (!leadId) return;
    window._currentChatLeadId = leadId;

    document.querySelectorAll('.chat-lead-item').forEach(el => {
      el.style.background = 'transparent';
      el.style.borderRight = '4px solid transparent';
      el.classList.remove('active');
    });
    const activeEl = document.getElementById(`lead-item-${leadId}`);
    if (activeEl) {
      activeEl.style.background = '#eff6ff';
      activeEl.style.borderRight = '4px solid #2563eb';
      activeEl.classList.add('active');
    }

    const threadEl = document.getElementById('chatMessagesThread');
    threadEl.innerHTML = '<div style="text-align:center; padding:40px 20px; color:#94a3b8; font-size:0.86rem;">در حال دریافت سوابق گفتگو...</div>';

    fetch(`/accounts/outreach/lead-chat/${leadId}/`, {
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      if (!data.success || !data.lead) {
        threadEl.innerHTML = `<div style="text-align:center; padding:30px; color:#ef4444; font-size:0.86rem;">${data.error || 'خطا در دریافت گفتگو'}</div>`;
        return;
      }

      const lead = data.lead;
      document.getElementById('chatLeadUsername').innerText = '@' + lead.username;
      document.getElementById('chatLeadAvatar').innerText = lead.username.slice(0, 2).toUpperCase();
      document.getElementById('chatLeadProfileUrl').href = lead.profile_url || `https://instagram.com/${lead.username}`;
      document.getElementById('chatLeadIntentBadge').innerText = `احتمال خرید: ${lead.intent_score}`;

      let statusText = 'در حال گفتگو';
      if (lead.status === 'QUEUED') statusText = 'پیام در صف ارسال امن';
      else if (lead.has_replied) statusText = 'پاسخ مشتری ثبت شده';
      else if (lead.status === 'FOLLOWUP_SENT') statusText = 'پیگیری ارسال شد';
      else if (lead.status === 'DM_SENT') statusText = 'پیام اول تحویل شد';
      document.getElementById('chatLeadStatusBadge').innerText = `وضعیت: ${statusText}`;
      const phoneBadge = document.getElementById('chatLeadPhoneBadge');
      if (lead.captured_phone) {
        phoneBadge.innerText = '📞 شماره تماس: ' + lead.captured_phone;
        phoneBadge.style.display = 'inline-block';
      } else {
        phoneBadge.style.display = 'none';
      }

      // Sender Account Info
      const senderAcc = lead.sender_account || lead.second_sender_username || '';
      window._currentLeadSenderAccount = senderAcc;

      const senderBadge = document.getElementById('chatLeadSenderAccountBadge');
      if (senderAcc) {
        senderBadge.innerText = 'ارسال شده با اکانت فرستنده: @' + senderAcc;
        senderBadge.style.display = 'inline-block';
      } else {
        senderBadge.style.display = 'none';
      }

      // Populate Lead & Comment Source Drawer
      document.getElementById('leadSourceCommentText').innerText = lead.comment_text || '(متن کامنت ثبت نشده است)';
      document.getElementById('leadSourceCommentTime').innerText = lead.comment_created_at ? ('زمان ثبت کامنت: ' + lead.comment_created_at) : 'زمان کامنت: نامشخص';

      const postLink = document.getElementById('leadSourcePostLink');
      if (lead.source_post_url) {
        postLink.href = lead.source_post_url;
        postLink.innerText = 'مشاهده پست در اینستاگرام ↗';
        postLink.style.display = 'inline';
      } else {
        postLink.removeAttribute('href');
        postLink.innerText = 'لینک پست اختصاصی ثبت نشده (مراجعه مستقیم در دایرکت)';
      }

      const pageLink = document.getElementById('leadSourcePageLink');
      if (lead.source_page_username) {
        const cleanPage = lead.source_page_username.replace('@', '');
        pageLink.href = 'https://instagram.com/' + cleanPage;
        pageLink.innerText = '@' + cleanPage;
      } else {
        pageLink.removeAttribute('href');
        pageLink.innerText = '(پیج هدف نامشخص)';
      }

      document.getElementById('leadSourcePostSnippet').innerText = lead.post_caption_snippet ? ('موضوع پست: ' + lead.post_caption_snippet) : '';
      document.getElementById('leadSourceSenderName').innerText = senderAcc ? ('@' + senderAcc) : 'مشاور کلینیک';

      // Auto-set sender select in composer and update badge
      const select = document.getElementById('chatSenderSelect');
      let foundInOptions = false;
      if (senderAcc) {
        for (let i = 0; i < select.options.length; i++) {
          if (select.options[i].value.toLowerCase() === senderAcc.toLowerCase()) {
            select.selectedIndex = i;
            foundInOptions = true;
            break;
          }
        }
      }

      const sameBadge = document.getElementById('chatSameSenderBadge');
      const diffNotice = document.getElementById('chatDifferentSenderNotice');
      if (foundInOptions) {
        sameBadge.innerText = `پاسخ با همان اکانت فرستنده قبلی (@${senderAcc})`;
        sameBadge.style.display = 'inline-block';
        diffNotice.style.display = 'none';
      } else if (senderAcc) {
        sameBadge.style.display = 'none';
        diffNotice.innerText = `پیام اولیه با @${senderAcc} بود — پاسخ با اکانت انتخابی ارسال می‌شود`;
        diffNotice.style.display = 'inline-block';
      } else {
        sameBadge.style.display = 'none';
        diffNotice.style.display = 'none';
      }

      threadEl.innerHTML = '';
      const history = lead.conversation_history || [];

      if (history.length > 0) {
        history.forEach(msg => {
          const isOut = (msg.direction === 'out' || msg.sender !== lead.username);
          renderChatBubble(threadEl, {
            isOut: isOut,
            sender: msg.sender || (isOut ? 'مشاور' : lead.username),
            text: msg.text,
            time: msg.time,
            queued: msg.delivery_status === 'QUEUED'
          });
        });
      } else {
        let hasAnyMsg = false;
        if (lead.initial_dm_text) {
          hasAnyMsg = true;
          renderChatBubble(threadEl, {
            isOut: true,
            sender: lead.sender_account || 'مشاور کلینیک',
            text: lead.initial_dm_text,
            time: lead.initial_dm_at
          });
        }
        if (lead.reply_text) {
          hasAnyMsg = true;
          renderChatBubble(threadEl, {
            isOut: false,
            sender: lead.username,
            text: lead.reply_text,
            time: lead.reply_at
          });
        }
        if (lead.second_message_text) {
          hasAnyMsg = true;
          renderChatBubble(threadEl, {
            isOut: true,
            sender: lead.second_sender_username || 'مشاور کلینیک',
            text: lead.second_message_text,
            time: lead.second_message_at
          });
        }
        if (!hasAnyMsg) {
          threadEl.innerHTML = `
            <div style="text-align:center; padding:40px 20px; color:#94a3b8; font-size:0.86rem;">
              هنوز پیامی رد و بدل نشده است. می‌توانید با نوشتن متن در کادر زیر پیام ارسال فرمایید.
            </div>
          `;
        }
      }

      threadEl.scrollTop = threadEl.scrollHeight;
    })
    .catch(err => {
      threadEl.innerHTML = `<div style="text-align:center; padding:30px; color:#ef4444; font-size:0.86rem;">خطای ارتباط با سرور: ${err}</div>`;
    });
  };

  function renderChatBubble(container, { isOut, sender, text, time, queued }) {
    const bubble = document.createElement('div');
    bubble.style.maxWidth = '82%';
    bubble.style.borderRadius = '12px';
    bubble.style.padding = '10px 14px';
    bubble.style.fontSize = '0.86rem';
    bubble.style.lineHeight = '1.6';
    bubble.style.marginBottom = '10px';
    bubble.style.boxShadow = '0 1px 2px rgba(0,0,0,0.04)';

    if (isOut) {
      bubble.style.alignSelf = 'flex-start';
      bubble.style.background = '#eff6ff';
      bubble.style.border = '1px solid #bfdbfe';
      bubble.style.color = '#1e3a8a';
      bubble.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px; font-size:0.73rem; color:#2563eb; font-weight:700;">
          <span>${queued ? 'در صف ارسال توسط' : 'ارسال شده توسط'} @${escapeHtml(sender)}:</span>
          <span style="font-size:0.68rem; font-weight:400; color:#64748b;">${queued ? '<span style="background:#fef3c7; color:#92400e; padding:1px 6px; border-radius:4px; font-weight:700;">⏳ در صف</span> ' : ''}${time ? escapeHtml(time).slice(0, 16) : ''}</span>
        </div>
        <div style="white-space:pre-wrap; word-break:break-word;">${escapeHtml(text)}</div>
      `;
    } else {
      bubble.style.alignSelf = 'flex-end';
      bubble.style.background = '#f0fdf4';
      bubble.style.border = '1px solid #bbf7d0';
      bubble.style.color = '#064e3b';
      bubble.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px; font-size:0.73rem; color:#15803d; font-weight:700;">
          <span>پاسخ مشتری @${escapeHtml(sender)}:</span>
          <span style="font-size:0.68rem; font-weight:400; color:#64748b;">${time ? escapeHtml(time).slice(0, 16) : ''}</span>
        </div>
        <div style="white-space:pre-wrap; word-break:break-word; font-weight:600;">${escapeHtml(text)}</div>
      `;
    }
    container.appendChild(bubble);
  }

  window.refreshCurrentChatLead = function() {
    if (window._currentChatLeadId) {
      window.selectChatLead(window._currentChatLeadId);
    }
  };

  window.applyChatPreset = function(text) {
    const input = document.getElementById('chatComposerInput');
    input.value = text;
    input.focus();
  };

  window.toggleLeadSourceInfo = function() {
    const box = document.getElementById('leadSourceInfoBox');
    const btn = document.getElementById('toggleLeadSourceBtn');
    if (box.style.display === 'none' || !box.style.display) {
      box.style.display = 'block';
      btn.innerText = 'بستن اطلاعات مشتری';
      btn.style.background = '#e2e8f0';
      btn.style.color = '#334155';
    } else {
      box.style.display = 'none';
      btn.innerText = 'نمایش اطلاعات مشتری و منبع کامنت';
      btn.style.background = '#e0f2fe';
      btn.style.color = '#0369a1';
    }
  };

  window.onChatSenderChanged = function() {
    const select = document.getElementById('chatSenderSelect');
    const chosen = select.value.toLowerCase();
    const prev = (window._currentLeadSenderAccount || '').toLowerCase();
    const sameBadge = document.getElementById('chatSameSenderBadge');
    const diffNotice = document.getElementById('chatDifferentSenderNotice');

    if (prev && chosen === prev) {
      sameBadge.innerText = `پاسخ با همان اکانت فرستنده قبلی (@${select.value})`;
      sameBadge.style.display = 'inline-block';
      diffNotice.style.display = 'none';
    } else if (prev) {
      sameBadge.style.display = 'none';
      diffNotice.innerText = `اکانت تغییر کرد: پاسخ توسط @${select.value} ارسال خواهد شد`;
      diffNotice.style.display = 'inline-block';
    } else {
      sameBadge.style.display = 'none';
      diffNotice.style.display = 'none';
    }
  };

  window.submitChatMessage = function() {
    if (!window._currentChatLeadId) {
      alert('لطفاً ابتدا یک خریدار را از لیست انتخاب کنید.');
      return;
    }

    const textInput = document.getElementById('chatComposerInput');
    const messageText = textInput.value.trim();
    if (!messageText) {
      alert('لطفاً متن پیام را وارد فرمایید.');
      textInput.focus();
      return;
    }

    const senderSelect = document.getElementById('chatSenderSelect');
    const senderUsername = senderSelect.value;
    const sendBtn = document.getElementById('chatSendButton');
    const alertBox = document.getElementById('chatSendingAlert');

    sendBtn.disabled = true;
    sendBtn.innerText = 'در حال ارسال پیام...';
    alertBox.style.display = 'inline-block';
    alertBox.style.background = '#eff6ff';
    alertBox.style.color = '#1d4ed8';
    alertBox.innerText = 'در حال ثبت پیام در صف ارسال امن...';

    const formData = new FormData();
    formData.append('lead_id', window._currentChatLeadId);
    formData.append('sender_username', senderUsername);
    formData.append('message_text', messageText);

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

    fetch(OUTREACH_CFG.sendFollowupUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: formData,
    })
    .then(r => r.json())
    .then(data => {
      sendBtn.disabled = false;
      sendBtn.innerText = 'ارسال دایرکت';

      if (data.success) {
        textInput.value = '';
        alertBox.style.background = '#ecfdf5';
        alertBox.style.color = '#047857';
        alertBox.innerText = data.message || 'پیام در صف ارسال امن قرار گرفت.';
        setTimeout(() => { alertBox.style.display = 'none'; }, 6000);

        const threadEl = document.getElementById('chatMessagesThread');
        renderChatBubble(threadEl, {
          isOut: true,
          sender: data.sender || senderUsername,
          text: messageText,
          time: data.sent_at || '',
          queued: !!data.queued
        });
        threadEl.scrollTop = threadEl.scrollHeight;

        const activeItem = document.getElementById(`lead-item-${window._currentChatLeadId}`);
        if (activeItem) {
          const snippetEl = activeItem.querySelector('div:last-child');
          if (snippetEl) snippetEl.innerText = messageText.slice(0, 42);
        }
      } else {
        alertBox.style.background = '#fef2f2';
        alertBox.style.color = '#b91c1c';
        alertBox.innerText = 'خطا در ثبت پیام: ' + (data.error || 'نامشخص');
      }
    })
    .catch(err => {
      sendBtn.disabled = false;
      sendBtn.innerText = 'ارسال دایرکت';
      alertBox.style.background = '#fef2f2';
      alertBox.style.color = '#b91c1c';
      alertBox.innerText = 'خطای ارتباطی: ' + err;
    });
  };

  window.syncChatReplies = function() {
    const btn = document.getElementById('syncRepliesBtn');
    btn.disabled = true;
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<span>در حال پایش دایرکت‌ها...</span>';

    fetch(OUTREACH_CFG.syncRepliesUrl, {
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
      const count = data.replies_found || 0;
      if (count > 0) {
        alert(`پایش انجام شد: ${count} پاسخ جدید از خریداران دریافت و ثبت شد.`);
        window.location.reload();
      } else {
        alert('صندوق دایرکت‌ها بررسی شد؛ پاسخ جدیدی تا این لحظه ثبت نشده است.');
        refreshCurrentChatLead();
      }
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
      alert('خطا در پایش صندوق: ' + err);
    });
  };

  window.filterChatLeads = function(query) {
    const q = (query || '').toLowerCase().trim();
    document.querySelectorAll('.chat-lead-item').forEach(el => {
      const uname = el.getAttribute('data-username') || '';
      if (!q || uname.includes(q)) {
        el.style.display = 'block';
      } else {
        el.style.display = 'none';
      }
    });
  };

  window.setChatFilter = function(type) {
    const btnAll = document.getElementById('chatFilterAll');
    const btnReplied = document.getElementById('chatFilterReplied');

    if (type === 'all') {
      btnAll.style.background = '#2563eb';
      btnAll.style.color = '#fff';
      btnAll.style.borderColor = '#2563eb';
      btnReplied.style.background = '#fff';
      btnReplied.style.color = '#475569';
      btnReplied.style.borderColor = '#cbd5e1';

      document.querySelectorAll('.chat-lead-item').forEach(el => {
        el.style.display = 'block';
      });
    } else {
      btnReplied.style.background = '#2563eb';
      btnReplied.style.color = '#fff';
      btnReplied.style.borderColor = '#2563eb';
      btnAll.style.background = '#fff';
      btnAll.style.color = '#475569';
      btnAll.style.borderColor = '#cbd5e1';

      document.querySelectorAll('.chat-lead-item').forEach(el => {
        const hasReplied = el.getAttribute('data-has-replied') === '1';
        el.style.display = hasReplied ? 'block' : 'none';
      });
    }
  };

  document.addEventListener('DOMContentLoaded', function() {
    const firstLead = document.querySelector('.chat-lead-item');
    if (firstLead) {
      const leadId = firstLead.getAttribute('data-lead-id');
      if (leadId) selectChatLead(parseInt(leadId));
    }
  });
