  const PRODUCT_FORM_CFG = document.currentScript.dataset;  // values from the template: data-* on the <script> tag
(() => {
  const nameInput = document.getElementById('prod_name');
  const descInput = document.getElementById('prod_desc');
  const categorySelect = document.getElementById('category_select');
  const selectedCatText = document.getElementById('selected-category-text');
  const suggestionBox = document.getElementById('ai-suggestion-box');
  const aiPath = document.getElementById('ai-path-display');
  const aiReason = document.getElementById('ai-reason-text');
  const aiConfidence = document.getElementById('ai-confidence');
  const btnAcceptAi = document.getElementById('btn-accept-ai');
  const btnManualAi = document.getElementById('btn-manual-ai');
  const dynamicContainer = document.getElementById('dynamic-attributes-container');

  let currentSuggestedCatId = null;

  // Manual Trigger for AI Auto-Categorization (Never fires while typing)
  function triggerAutoCategorize(silent = false) {
    const name = nameInput?.value.trim() || '';
    const desc = descInput?.value.trim() || '';
    if (name.length < 2) {
      if (!silent) {
        alert('لطفاً ابتدا نام محصول را به صورت کامل وارد کنید.');
        if (nameInput) nameInput.focus();
      }
      return;
    }

    const btn1 = document.getElementById('btn-trigger-ai-category');
    const btnLabel = document.getElementById('btn-ai-label');
    const btn2 = document.getElementById('btn-trigger-ai-category-2');
    const origText = btnLabel ? btnLabel.textContent : 'دسته‌بندی هوشمند خودکار';

    if (btn1) btn1.disabled = true;
    if (btn2) btn2.disabled = true;
    if (btnLabel) btnLabel.textContent = 'در حال تحلیل...';

    const typeRadio = document.querySelector('input[name="product_type"]:checked');
    const prodType = typeRadio ? typeRadio.value : '';

    fetch(`/products/api/suggest-category/?name=${encodeURIComponent(name)}&description=${encodeURIComponent(desc)}&product_type=${prodType}`)
      .then(res => res.json())
      .then(data => {
        if (data.status === 'success' && data.suggestion) {
          const s = data.suggestion;
          currentSuggestedCatId = s.category_id;

          // 1. Update category select dropdown
          if (categorySelect) {
            let opt = Array.from(categorySelect.options).find(o => o.value === String(s.category_id));
            if (!opt) {
              opt = document.createElement('option');
              opt.value = String(s.category_id);
              opt.textContent = s.full_path;
              opt.dataset.fullpath = s.full_path;
              categorySelect.appendChild(opt);
            }
            categorySelect.value = String(s.category_id);
            categorySelect.dispatchEvent(new Event('change', { bubbles: true }));
          }

          // 2. Update active category text display
          if (selectedCatText) {
            selectedCatText.textContent = s.full_path;
          }

          // 3. Update suggestion card widget
          if (aiPath) aiPath.textContent = s.full_path;
          if (aiReason) aiReason.textContent = s.reason;
          if (aiConfidence) aiConfidence.textContent = `${s.confidence}٪ دقت ایجنت`;
          if (suggestionBox) suggestionBox.style.display = 'block';

          // 4. Highlight category in tree explorer
          if (typeof fetchAndRenderTree === 'function') {
            fetchAndRenderTree();
          }
        } else {
          if (!silent) {
            alert('دسته‌بندی دقیقی به‌صورت خودکار پیدا نشد. لطفاً از درخت‌واره زیر دسته مورد نظر را انتخاب نمایید.');
          }
        }
      })
      .catch(err => {
        console.error('Auto categorization error:', err);
        if (!silent) {
          alert('خطا در ارتباط با سرور هنگام تحلیل دسته‌بندی.');
        }
      })
      .finally(() => {
        if (btn1) btn1.disabled = false;
        if (btn2) btn2.disabled = false;
        if (btnLabel) btnLabel.textContent = origText;
      });
  }

  // Bind click on dedicated auto-categorize buttons
  document.getElementById('btn-trigger-ai-category')?.addEventListener('click', () => triggerAutoCategorize(false));
  document.getElementById('btn-trigger-ai-category-2')?.addEventListener('click', () => triggerAutoCategorize(false));

  // Accept AI suggestion button
  btnAcceptAi?.addEventListener('click', () => {
    if (currentSuggestedCatId && categorySelect) {
      categorySelect.value = String(currentSuggestedCatId);
      categorySelect.dispatchEvent(new Event('change', { bubbles: true }));
      suggestionBox.style.display = 'none';
      categorySelect.style.borderColor = 'var(--brand-purple)';
      setTimeout(() => { categorySelect.style.borderColor = ''; }, 1500);
    }
  });

  btnManualAi?.addEventListener('click', () => {
    if (suggestionBox) suggestionBox.style.display = 'none';
    if (categorySelect) categorySelect.focus();
  });

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Dynamic Custom Attributes List Manager
  const customAttrsList = document.getElementById('custom-attributes-list');
  const noAttrsPlaceholder = document.getElementById('no-attrs-placeholder');
  const btnAddCustomAttr = document.getElementById('btn-add-custom-attr');
  const presetChips = document.querySelectorAll('.preset-chip');

  function addCustomAttrRow(key = '', val = '', placeholder = '') {
    if (noAttrsPlaceholder) noAttrsPlaceholder.style.display = 'none';
    const row = document.createElement('div');
    row.className = 'custom-attr-row';
    row.style = 'display: flex; gap: 10px; align-items: center; background: var(--panel-surface); padding: 8px 12px; border-radius: 10px; border: 1px solid var(--panel-border-subtle);';
    row.innerHTML = `
      <div style="flex: 1;">
        <input type="text" name="custom_attr_key" class="form-input" placeholder="ویژگی (مثلاً جنس)" value="${escapeHtml(key)}" style="height: 38px; font-size: 0.84rem; background: var(--panel-surface-elevated);">
      </div>
      <div style="flex: 1.6;">
        <input type="text" name="custom_attr_value" class="form-input" placeholder="${placeholder || 'مقدار (مثلاً کتان)'}" value="${escapeHtml(val)}" style="height: 38px; font-size: 0.84rem; background: var(--panel-surface-elevated);">
      </div>
      <button type="button" class="p-btn p-btn-ghost btn-delete-attr-row" title="حذف این ویژگی" style="padding: 6px 8px; color: var(--p-danger); border-radius: 8px;">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
      </button>
    `;
    const btnDelete = row.querySelector('.btn-delete-attr-row');
    btnDelete.addEventListener('click', () => {
      row.remove();
      if (customAttrsList && customAttrsList.querySelectorAll('.custom-attr-row').length === 0) {
        if (noAttrsPlaceholder) noAttrsPlaceholder.style.display = 'block';
      }
    });
    if (customAttrsList) customAttrsList.appendChild(row);
    const valInput = row.querySelector('input[name="custom_attr_value"]');
    const keyInput = row.querySelector('input[name="custom_attr_key"]');
    if (key && valInput) {
      valInput.focus();
    } else if (keyInput) {
      keyInput.focus();
    }
    return row;
  }

  // Bind existing delete buttons
  document.querySelectorAll('.btn-delete-attr-row').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.custom-attr-row');
      if (row) row.remove();
      if (customAttrsList && customAttrsList.querySelectorAll('.custom-attr-row').length === 0) {
        if (noAttrsPlaceholder) noAttrsPlaceholder.style.display = 'block';
      }
    });
  });

  // Preset chips click handling
  presetChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const key = chip.dataset.key || '';
      const sample = chip.dataset.sample || '';
      const existingInp = Array.from(customAttrsList ? customAttrsList.querySelectorAll('input[name="custom_attr_key"]') : []).find(inp => inp.value.trim() === key);
      if (existingInp) {
        const row = existingInp.closest('.custom-attr-row');
        const valInp = row ? row.querySelector('input[name="custom_attr_value"]') : null;
        if (valInp) valInp.focus();
      } else {
        addCustomAttrRow(key, '', sample);
      }
    });
  });

  btnAddCustomAttr?.addEventListener('click', () => {
    addCustomAttrRow('', '', '');
  });

  // Autofill from link: only empty fields are filled; the link goes into the product's url field
  const btnAutofill = document.getElementById('btn-autofill');
  const autofillUrl = document.getElementById('autofill_url');
  const autofillStatus = document.getElementById('autofill-status');
  const autofillLabel = document.getElementById('btn-autofill-label');
  function autofillMsg(text, ok) {
    autofillStatus.style.display = 'block';
    autofillStatus.style.color = ok ? '#10B981' : 'var(--p-danger, #ef4444)';
    autofillStatus.textContent = text;
  }
  btnAutofill?.addEventListener('click', () => {
    const link = (autofillUrl.value || '').trim();
    if (!link) { autofillMsg('لینک صفحه‌ی محصول را وارد کنید.', false); return; }
    btnAutofill.disabled = true;
    autofillLabel.textContent = 'در حال خواندن صفحه...';
    const body = new URLSearchParams({ url: link });
    fetch(btnAutofill.dataset.endpoint, {
      method: 'POST',
      headers: { 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
      .then(r => r.json().catch(() => ({ status: 'error', message: 'پاسخ نامعتبر از سرور.' })))
      .then(res => {
        if (res.status !== 'success') { autofillMsg(res.message || 'خواندن صفحه ممکن نشد.', false); return; }
        const d = res.data || {};
        const filled = [];
        const fill = (el, value, label) => {
          if (el && value && !el.value.trim()) { el.value = value; filled.push(label); }
        };
        fill(nameInput, d.name, 'نام');
        fill(document.getElementById('prod_price'), d.price ? String(d.price) : '', 'قیمت');
        fill(descInput, d.description, 'توضیح');
        const urlInput = document.getElementById('prod_url');
        if (urlInput && res.url) { urlInput.value = res.url; }
        const existing = new Set(Array.from(customAttrsList ? customAttrsList.querySelectorAll('input[name="custom_attr_key"]') : [])
          .map(i => i.value.trim()).filter(Boolean));
        let added = 0;
        (d.features || []).forEach(f => {
          if (f && f.name && f.value && !existing.has(f.name)) { addCustomAttrRow(f.name, f.value, ''); existing.add(f.name); added++; }
        });
        if (added) filled.push(`${added} ویژگی`);
        if (nameInput && filled.includes('نام')) nameInput.dispatchEvent(new Event('input', { bubbles: true }));
        autofillMsg(filled.length ? `پر شد: ${filled.join('، ')}. لطفاً پیش از ذخیره بررسی کنید.`
                                  : 'فیلدها از قبل پر بودند؛ فقط لینک محصول ثبت شد.', true);
      })
      .catch(() => autofillMsg('ارتباط با سرور برقرار نشد.', false))
      .finally(() => { btnAutofill.disabled = false; autofillLabel.textContent = 'خواندن از لینک'; });
  });

  // Category attributes loader: smoothly integrates suggested attributes into custom rows
  function loadAttributesForCategory(catId) {
    if (!catId) return;
    fetch(`/products/api/category-attributes/${catId}/`)
      .then(res => res.json())
      .then(data => {
        const attrs = data.attributes || [];
        attrs.forEach(a => {
          const key = a.label || a.key;
          const existingInp = Array.from(customAttrsList ? customAttrsList.querySelectorAll('input[name="custom_attr_key"]') : []).find(inp => inp.value.trim() === key);
          if (!existingInp) {
            addCustomAttrRow(key, '', a.placeholder || '');
          }
        });
      })
      .catch(err => console.log('Category attributes loading error:', err));
  }

  categorySelect?.addEventListener('change', () => {
    const val = categorySelect.value;
    if (selectedCatText) {
      const opt = categorySelect.selectedOptions ? categorySelect.selectedOptions[0] : null;
      if (opt && val) {
        selectedCatText.textContent = opt.dataset.fullpath || opt.text.trim();
      } else if (!val) {
        selectedCatText.textContent = 'هنوز شاخه‌ای انتخاب نشده است';
      }
    }
    loadAttributesForCategory(val);
  });

  // Dynamic Category Tree Builder Logic (Max 2-3 Levels Enforced)
  const btnToggleTree = document.getElementById('btn-toggle-tree');
  const btnTreeText = document.getElementById('btn-tree-text');
  const treeExplorer = document.getElementById('category-tree-explorer');
  const treeContainer = document.getElementById('tree-container');
  const inlineCreator = document.getElementById('inline-category-creator');
  const creatorLabel = document.getElementById('creator-parent-label');
  const newCatInput = document.getElementById('new-cat-name-input');
  const btnSaveInlineCat = document.getElementById('btn-save-inline-cat');
  const btnCancelCreator = document.getElementById('btn-cancel-creator');
  const btnAddRootCat = document.getElementById('btn-add-root-cat');
  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;

  let currentParentId = null;

  function fetchAndRenderTree() {
    if (!treeContainer) return;
    treeContainer.innerHTML = '<div style="font-size: 0.8rem; color: var(--stone-text-muted); padding: 10px;">در حال بارگذاری درخت دسته‌بندی‌ها...</div>';
    fetch('/products/categories/api/tree/')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'success' && data.tree) {
          treeContainer.innerHTML = '';
          if (data.tree.length === 0) {
            treeContainer.innerHTML = '<div style="font-size: 0.8rem; color: var(--stone-text-muted); padding: 10px;">هنوز دسته‌ای ایجاد نشده است. با دکمه بالا سرشاخه اصلی بسازید.</div>';
            return;
          }
          renderTreeNodes(data.tree, treeContainer, 0);
        }
      })
      .catch(err => {
        treeContainer.innerHTML = '<div style="font-size: 0.8rem; color: var(--p-danger); padding: 10px;">خطا در دریافت درخت‌واره.</div>';
      });
  }

  function renderTreeNodes(nodes, container, depth) {
    nodes.forEach(node => {
      const nodeEl = document.createElement('div');
      const nodeDepth = (typeof node.depth === 'number') ? node.depth : depth;
      nodeEl.style = `margin-right: ${nodeDepth * 20}px; margin-bottom: 6px; padding: 7px 12px; border-radius: 8px; background: var(--panel-surface-elevated); border: 1px solid var(--panel-border-subtle); display: flex; align-items: center; justify-content: space-between;`;
      
      const isSelected = categorySelect && categorySelect.value === String(node.id);
      if (isSelected) {
        nodeEl.style.borderColor = 'var(--brand-purple)';
        nodeEl.style.background = 'rgba(0, 242, 254, 0.08)';
      }

      // Max 5 levels: nodeDepth = 0 (root), 1 (sub), 2 (sub-sub), 3 (level 4), 4 (level 5).
      // Only allow adding subcategories if nodeDepth < 4 (levels 1 through 4).
      const canHaveChildren = nodeDepth < 4;

      nodeEl.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
          <span style="font-size: 0.8rem; color: var(--obsidian-cyan); display: inline-flex; align-items: center;">${nodeDepth > 0 ? '↳' : '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>'}</span>
          <span style="font-size: 0.84rem; font-weight: 750; color: var(--panel-text-main);">${escapeHtml(node.name)}</span>
          ${node.is_custom ? '<span style="font-size: 0.65rem; padding: 1px 5px; border-radius: 4px; background: rgba(168,85,247,0.15); color: #c084fc;">سفارشی</span>' : ''}
          ${!canHaveChildren ? '<span style="font-size: 0.68rem; color: var(--stone-text-muted); background: var(--panel-surface); padding: 1px 6px; border-radius: 4px; border: 1px solid var(--panel-border-subtle);">سقف ۵ سطح</span>' : ''}
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
          <button type="button" class="p-btn p-btn-sm p-btn-primary btn-select-node" style="font-size: 0.72rem; padding: 4px 10px;">
            ${isSelected ? ' انتخاب شده' : 'انتخاب'}
          </button>
          ${canHaveChildren ? `
            <button type="button" class="p-btn p-btn-sm p-btn-secondary btn-add-sub" title="افزودن زیرشاخه به این دسته (سطح ${nodeDepth + 2} از ۵)" style="font-size: 0.72rem; padding: 4px 8px;">
              + زیرشاخه
            </button>
          ` : ''}
        </div>
      `;

      nodeEl.querySelector('.btn-select-node').addEventListener('click', () => {
        if (categorySelect) {
          categorySelect.value = String(node.id);
          if (selectedCatText) selectedCatText.textContent = node.full_path;
          loadAttributesForCategory(node.id);
        }
        fetchAndRenderTree();
      });

      const btnAddSub = nodeEl.querySelector('.btn-add-sub');
      if (btnAddSub) {
        btnAddSub.addEventListener('click', () => {
          currentParentId = node.id;
          if (creatorLabel) creatorLabel.textContent = `افزودن زیرشاخه جدید به «${node.name}» (سطح ${nodeDepth + 2} از ۵):`;
          if (inlineCreator) inlineCreator.style.display = 'block';
          if (newCatInput) {
            newCatInput.value = '';
            newCatInput.focus();
          }
        });
      }

      container.appendChild(nodeEl);

      if (node.children && node.children.length > 0) {
        renderTreeNodes(node.children, container, depth + 1);
      }
    });
  }

  btnToggleTree?.addEventListener('click', () => {
    if (treeExplorer.style.display === 'none') {
      treeExplorer.style.display = 'block';
      if (btnTreeText) btnTreeText.textContent = 'بستن درخت‌واره';
      fetchAndRenderTree();
    } else {
      treeExplorer.style.display = 'none';
      if (btnTreeText) btnTreeText.textContent = 'مشاهده و مدیریت درخت‌واره';
    }
  });

  btnAddRootCat?.addEventListener('click', () => {
    currentParentId = null;
    if (creatorLabel) creatorLabel.textContent = 'افزودن سرشاخه اصلی جدید (سطح ۱):';
    if (inlineCreator) inlineCreator.style.display = 'block';
    if (newCatInput) {
      newCatInput.value = '';
      newCatInput.focus();
    }
  });

  btnCancelCreator?.addEventListener('click', () => {
    if (inlineCreator) inlineCreator.style.display = 'none';
  });

  btnSaveInlineCat?.addEventListener('click', () => {
    const name = newCatInput?.value.trim() || '';
    if (!name) {
      alert('لطفاً نام دسته‌بندی را وارد نمایید.');
      return;
    }

    const typeRadio = document.querySelector('input[name="product_type"]:checked');
    const prodType = typeRadio ? typeRadio.value : 'PHYSICAL';

    btnSaveInlineCat.disabled = true;
    btnSaveInlineCat.textContent = 'در حال ثبت...';

    fetch('/products/categories/api/add/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({
        name: name,
        parent_id: currentParentId,
        product_type: prodType,
      })
    })
      .then(res => res.json())
      .then(data => {
        btnSaveInlineCat.disabled = false;
        btnSaveInlineCat.textContent = 'ثبت و انتخاب این شاخه';
        if (data.status === 'success' && data.category) {
          const cat = data.category;
          let opt = categorySelect.querySelector(`option[value="${cat.id}"]`);
          if (!opt) {
            opt = document.createElement('option');
            opt.value = cat.id;
            opt.textContent = `↳ ${cat.full_path}`;
            opt.setAttribute('data-fullpath', cat.full_path);
            categorySelect.appendChild(opt);
          }
          categorySelect.value = String(cat.id);
          if (selectedCatText) selectedCatText.textContent = cat.full_path;
          inlineCreator.style.display = 'none';
          fetchAndRenderTree();
          loadAttributesForCategory(cat.id);
        } else {
          alert(data.message || 'خطا در ثبت دسته.');
        }
      })
      .catch(err => {
        btnSaveInlineCat.disabled = false;
        btnSaveInlineCat.textContent = 'ثبت و انتخاب این شاخه';
        alert('خطای اتصال به سرور.');
      });
  });

  categorySelect?.addEventListener('change', () => {
    const selectedOpt = categorySelect.options[categorySelect.selectedIndex];
    if (selectedOpt && selectedCatText) {
      selectedCatText.textContent = selectedOpt.getAttribute('data-fullpath') || selectedOpt.text;
    }
  });

  // Render tree immediately on load
  fetchAndRenderTree();

  // Multi-image client preview & main photo selector
  const imagesInput = document.getElementById('product_images');
  const previewGrid = document.getElementById('new-images-preview-grid');
  const selectedMainIndexInput = document.getElementById('selected_main_index');
  const maxAllowedSlots = Number(PRODUCT_FORM_CFG.maxImageSlots);

  if (imagesInput && previewGrid) {
    imagesInput.addEventListener('change', () => {
      previewGrid.innerHTML = '';
      const files = Array.from(imagesInput.files);
      if (files.length === 0) {
        previewGrid.style.display = 'none';
        return;
      }

      if (files.length > maxAllowedSlots) {
        alert(`حداکثر می‌توانید ${maxAllowedSlots} تصویر انتخاب فرمایید.`);
        imagesInput.value = '';
        previewGrid.style.display = 'none';
        return;
      }

      previewGrid.style.display = 'grid';
      files.forEach((file, index) => {
        if (!file.type.startsWith('image/')) return;
        const reader = new FileReader();
        reader.onload = (e) => {
          const item = document.createElement('div');
          item.style = 'position: relative; border-radius: 12px; overflow: hidden; border: 2px solid ' + (index === 0 ? 'var(--brand-purple)' : 'var(--panel-border)') + '; background: var(--panel-surface); text-align: center; cursor: pointer; transition: all 0.2s;';
          item.innerHTML = `
            <img src="${e.target.result}" style="width: 100%; height: 95px; object-fit: cover; display: block;">
            <div style="padding: 5px; font-size: 0.72rem; color: var(--panel-text-muted); background: var(--panel-surface-elevated);">
              <span class="preview-badge" style="font-weight: 700; color: ${index === 0 ? 'var(--brand-purple)' : 'inherit'};">
                ${index === 0 ? 'عکس اصلی انتخابی' : 'انتخاب به عنوان اصلی'}
              </span>
            </div>
          `;
          item.addEventListener('click', () => {
            if (selectedMainIndexInput) selectedMainIndexInput.value = index;
            previewGrid.querySelectorAll('div').forEach((d, i) => {
              const badge = d.querySelector('.preview-badge');
              if (i === index) {
                d.style.borderColor = 'var(--brand-purple)';
                if (badge) {
                  badge.textContent = 'عکس اصلی انتخابی';
                  badge.style.color = 'var(--brand-purple)';
                }
              } else {
                d.style.borderColor = 'var(--panel-border)';
                if (badge) {
                  badge.textContent = 'انتخاب به عنوان اصلی';
                  badge.style.color = 'inherit';
                }
              }
            });
          });
          previewGrid.appendChild(item);
        };
        reader.readAsDataURL(file);
      });
    });
  }
})();
