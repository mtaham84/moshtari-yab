/**
 * Responsive Table on Desktop, Cards on Mobile Helper
 * Handles Mobile More Menu, Bulk Selection, and Copy Actions
 */

document.addEventListener('DOMContentLoaded', function () {
  // 1. Close dropdown menus when clicking outside
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.card-more-menu-wrapper')) {
      document.querySelectorAll('.card-more-menu-wrapper.is-open').forEach(function (el) {
        el.classList.remove('is-open');
      });
    }
  });

  // 2. Initialize Bulk Selection Bar
  const checkboxes = document.querySelectorAll('.card-select-checkbox, .item-select-checkbox');
  const bulkBar = document.getElementById('sticky-bulk-bar');
  const countEl = document.getElementById('bulk-count');

  function updateBulkBar() {
    if (!bulkBar) return;
    const checked = document.querySelectorAll('.card-select-checkbox:checked, .item-select-checkbox:checked');
    const total = checked.length;

    if (countEl) {
      countEl.textContent = total;
    }

    if (total > 0) {
      bulkBar.classList.add('is-visible');
    } else {
      bulkBar.classList.remove('is-visible');
    }

    // Toggle card selection highlight
    document.querySelectorAll('.responsive-card').forEach(function (card) {
      const cb = card.querySelector('.card-select-checkbox');
      if (cb && cb.checked) {
        card.classList.add('is-selected');
      } else {
        card.classList.remove('is-selected');
      }
    });
  }

  checkboxes.forEach(function (cb) {
    cb.addEventListener('change', updateBulkBar);
  });

  window.clearSelectedItems = function () {
    document.querySelectorAll('.card-select-checkbox:checked, .item-select-checkbox:checked').forEach(function (cb) {
      cb.checked = false;
    });
    updateBulkBar();
  };

  window.bulkCopySelectedLinks = function () {
    const checked = Array.from(document.querySelectorAll('.card-select-checkbox:checked, .item-select-checkbox:checked'));
    if (!checked.length) return;

    const urls = checked.map(function (cb) {
      return cb.getAttribute('data-url') || window.location.origin + '/discovery/opportunities/' + cb.getAttribute('data-id') + '/';
    });

    const textToCopy = urls.join('\n');
    navigator.clipboard.writeText(textToCopy).then(function () {
      alert(checked.length + ' لینک فرصت با موفقیت کپی شد.');
    }).catch(function (err) {
      prompt('لینک‌های انتخابی:', textToCopy);
    });
  };
});

window.toggleCardMoreMenu = function (btn) {
  const wrapper = btn.closest('.card-more-menu-wrapper');
  if (!wrapper) return;

  const isOpen = wrapper.classList.contains('is-open');
  // Close others first
  document.querySelectorAll('.card-more-menu-wrapper.is-open').forEach(function (el) {
    el.classList.remove('is-open');
  });

  if (!isOpen) {
    wrapper.classList.add('is-open');
  }
};

window.copyCardDirectLink = function (path) {
  const fullUrl = window.location.origin + path;
  navigator.clipboard.writeText(fullUrl).then(function () {
    alert('لینک مستقیم کپی شد: ' + fullUrl);
  }).catch(function () {
    prompt('لینک مستقیم:', fullUrl);
  });
};
