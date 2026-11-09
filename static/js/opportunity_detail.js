  const DETAIL_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
document.addEventListener("DOMContentLoaded", function() {
  document.querySelectorAll(".x-reply-text").forEach(function(area) {
    const counter = area.nextElementSibling;
    const update = function() {
      const value = area.value.replace(/https?:\/\/\S+|www\.\S+/g, "x".repeat(23));
      let length = 0;
      for (const ch of value) length += (ch.codePointAt(0) > 0xffff ? 2 : 1);
      counter.textContent = length + " / 280";
      counter.style.color = length > 280 ? "#DC2626" : "";
    };
    area.addEventListener("input", update); update();
    area.parentElement.querySelectorAll(".x-copy-reply").forEach(function(button) {
      button.addEventListener("click", function() { navigator.clipboard.writeText(area.value); });
    });
  });
  document.querySelectorAll(".x-intent-link").forEach(function(link) {
    const area = link.closest(".x-reply-wrap")?.querySelector(".x-reply-text");
    if (!area || !link.dataset.tweetId) return;
    link.addEventListener("click", function(event) {
      event.preventDefault();
      const params = new URLSearchParams({in_reply_to: link.dataset.tweetId, text: area.value});
      window.open(link.dataset.base + "?" + params.toString(), "_blank", "noopener");
    });
  });
  document.querySelectorAll(".feedback-button").forEach(function(button) {
    button.addEventListener("click", function() {
      document.querySelectorAll(".feedback-button").forEach(function(item) { item.disabled = true; });
      const reasonEl = document.getElementById("feedback-reason");
      const reasonVal = reasonEl ? reasonEl.value : "";
      fetch(DETAIL_CFG.feedbackUrl, {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": DETAIL_CFG.csrfToken},
        body: "feedback=" + encodeURIComponent(button.dataset.feedback) +
          "&reason=" + encodeURIComponent(reasonVal)
      }).then(function(response) { return response.json(); }).then(function(data) {
        if (data.status === "success") {
          const toast = document.getElementById("feedback-toast");
          if (toast) {
            toast.textContent = "✓ " + (data.message || "بازخورد با موفقیت ثبت شد و در یادگیری سیستم اعمال گردید.");
            toast.style.display = "block";
          }
          const container = document.getElementById("feedback-input-container");
          if (container) {
            const fbLabel = data.feedback === "relevant" ? "مرتبط" : "نامرتبط";
            const colorBg = data.feedback === "relevant" ? "rgba(16, 185, 129, 0.12)" : "rgba(239, 68, 68, 0.12)";
            const colorBorder = data.feedback === "relevant" ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)";
            const colorText = data.feedback === "relevant" ? "#047857" : "#DC2626";
            container.innerHTML = `<div style="background:${colorBg};border:1px solid ${colorBorder};border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:0.82rem;color:${colorText};font-weight:700;">✓ بازخورد ثبت شد: ${fbLabel}</div>`;
          }
          document.querySelectorAll(".feedback-button").forEach(function(item) { item.disabled = true; });
        } else {
          document.querySelectorAll(".feedback-button").forEach(function(item) { item.disabled = false; });
          alert(data.message || "ذخیره بازخورد ناموفق بود.");
        }
      }).catch(function(err) {
        document.querySelectorAll(".feedback-button").forEach(function(item) { item.disabled = false; });
        alert(err.message || "ذخیره بازخورد ناموفق بود.");
      });
    });
  });

  // 1. Copy reply to clipboard
  const copyBtn = document.getElementById("btn-copy-reply");
  const copyBtnText = document.getElementById("copy-btn-text");
  const replyBox = document.getElementById("suggested-reply-box");
  const editBtn = document.getElementById("btn-edit-reply");
  const editBtnText = document.getElementById("edit-reply-text");

  copyBtn.addEventListener("click", function() {
    navigator.clipboard.writeText(replyBox.value).then(function() {
      const orig = copyBtnText.textContent;
      copyBtnText.textContent = " کپی شد!";
      copyBtn.style.background = "#10B981";
      setTimeout(function() {
        copyBtnText.textContent = orig;
        copyBtn.style.background = "";
      }, 2000);
    }).catch(function(err) {
      alert("خطا در دسترسی به کلیپ‌بورد");
    });
  });

  // 1b. Drafts per matched product + «دوباره بنویس» (uses the saved message style)
  const drafts = JSON.parse(document.getElementById("drafts-data").textContent || "{}");
  const draftProduct = document.getElementById("draft-product");
  const rewriteBtn = document.getElementById("btn-rewrite-reply");
  const rewriteText = document.getElementById("rewrite-reply-text");
  const rewriteError = document.getElementById("rewrite-error");
  const currentProduct = () => draftProduct ? draftProduct.value : (rewriteBtn ? rewriteBtn.dataset.product : "");
  draftProduct?.addEventListener("change", () => {
    replyBox.value = drafts[draftProduct.value] || "";
    rewriteError.style.display = "none";
  });
  rewriteBtn?.addEventListener("click", () => {
    const pid = currentProduct();
    rewriteBtn.disabled = true;
    rewriteText.textContent = "در حال نوشتن...";
    rewriteError.style.display = "none";
    fetch(rewriteBtn.dataset.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": DETAIL_CFG.csrfToken },
      body: "product_id=" + encodeURIComponent(pid),
    })
      .then(r => r.json().catch(() => ({ status: "error", message: "پاسخ نامعتبر از سرور." })))
      .then(res => {
        if (res.status === "success") {
          drafts[String(res.product_id)] = res.reply;
          if (currentProduct() === String(res.product_id)) replyBox.value = res.reply;
        } else {
          rewriteError.textContent = res.message || "نوشتن دوباره ممکن نشد.";
          rewriteError.style.display = "block";
        }
      })
      .catch(() => { rewriteError.textContent = "ارتباط با سرور برقرار نشد."; rewriteError.style.display = "block"; })
      .finally(() => { rewriteBtn.disabled = false; rewriteText.textContent = "دوباره بنویس"; });
  });

  // 2. Inline edit reply
  editBtn.addEventListener("click", function() {
    if (replyBox.hasAttribute("readonly")) {
      replyBox.removeAttribute("readonly");
      replyBox.focus();
      editBtnText.textContent = "قفل مجدد";
      editBtn.classList.add("p-btn-primary");
      editBtn.classList.remove("p-btn-secondary");
    } else {
      replyBox.setAttribute("readonly", "readonly");
      editBtnText.textContent = "ویرایش متن";
      editBtn.classList.remove("p-btn-primary");
      editBtn.classList.add("p-btn-secondary");
    }
  });

  // 3. AJAX Status Update
  const saveStatusBtn = document.getElementById("btn-save-status");
  const statusSelect = document.getElementById("select-status-changer");
  const statusBadge = document.getElementById("status-current-badge");
  const statusToast = document.getElementById("status-toast");

  saveStatusBtn.addEventListener("click", function() {
    const newStatus = statusSelect.value;
    saveStatusBtn.disabled = true;
    saveStatusBtn.textContent = "در حال ذخیره...";

    fetch(DETAIL_CFG.statusUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": DETAIL_CFG.csrfToken
      },
      body: "status=" + encodeURIComponent(newStatus)
    })
    .then(res => res.json())
    .then(data => {
      saveStatusBtn.disabled = false;
      saveStatusBtn.textContent = "ثبت وضعیت جدید";
      if (data.status === "success") {
        statusBadge.innerHTML = '<span class="p-badge" style="background: rgba(16, 185, 129, 0.15); color: #10B981; font-size: 0.85rem; padding: 6px 14px;">' + data.status_display + '</span>';
        statusToast.style.display = "block";
        setTimeout(() => { statusToast.style.display = "none"; }, 3000);
      } else {
        alert(data.message || "خطا در تغییر وضعیت");
      }
    })
    .catch(err => {
      saveStatusBtn.disabled = false;
      saveStatusBtn.textContent = "ثبت وضعیت جدید";
      alert("خطا در برقراری ارتباط با سرور");
    });
  });
});
