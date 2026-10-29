// Peyda UI: turn every data table into stacked cards on phones (labels come from <thead>).
(() => {
  const label = () => document.querySelectorAll('table').forEach(table => {
    const heads = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    if (!heads.length) {   // header-less tables just scroll sideways inside their own box
      if (!table.parentElement.classList.contains('scroll-x')) { const w = document.createElement('div'); w.className = 'scroll-x'; table.parentNode.insertBefore(w, table); w.appendChild(table); }
      return;
    }
    table.classList.add('stack');
    table.querySelectorAll('tbody tr').forEach(tr => Array.from(tr.children).forEach((td, i) => {
      if (td.tagName === 'TD' && !td.hasAttribute('data-label') && heads[i]) td.setAttribute('data-label', heads[i]);
    }));
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', label); else label();

})();
