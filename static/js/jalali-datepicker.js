/**
 * Peyda Interactive Jalali (Persian) Datepicker
 * Zero-dependency, lightweight, neumorphic dark/light UI.
 */
(function (window, document) {
  'use strict';

  const PERSIAN_MONTHS = [
    'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
    'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'
  ];

  const PERSIAN_WEEKDAYS = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];

  function toFaDigits(val) {
    const pDigits = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
    return String(val).replace(/[0-9]/g, w => pDigits[+w]);
  }

  function toEnDigits(val) {
    const pDigits = [/۰/g, /۱/g, /۲/g, /۳/g, /۴/g, /۵/g, /۶/g, /۷/g, /۸/g, /۹/g];
    let str = String(val);
    for (let i = 0; i < 10; i++) {
      str = str.replace(pDigits[i], String(i));
    }
    return str;
  }

  function gregorianToJalali(gy, gm, gd) {
    const g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    let jy = (gy > 1600) ? 979 : 0;
    gy -= (gy > 1600) ? 1600 : 621;
    const gy2 = (gm > 2) ? gy : (gy - 1);
    let days = (365 * gy) + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400) - 80 + gd + g_d_m[gm - 1];
    jy += 33 * Math.floor(days / 12053);
    days %= 12053;
    jy += 4 * Math.floor(days / 1461);
    days %= 1461;
    if (days > 365) {
      jy += Math.floor((days - 1) / 365);
      days = (days - 1) % 365;
    }
    let jm, jd;
    if (days < 186) {
      jm = 1 + Math.floor(days / 31);
      jd = 1 + (days % 31);
    } else {
      jm = 7 + Math.floor((days - 186) / 30);
      jd = 1 + ((days - 186) % 30);
    }
    return [jy, jm, jd];
  }

  function jalaliToGregorian(jy, jm, jd) {
    let gy = (jy > 979) ? 1600 : 621;
    jy -= (jy > 979) ? 979 : 0;
    let days = (365 * jy) + (Math.floor(jy / 33) * 8) + Math.floor(((jy % 33) + 3) / 4) + 78 + jd + ((jm < 7) ? (jm - 1) * 31 : ((jm - 7) * 30) + 186);
    gy += 400 * Math.floor(days / 146097);
    days %= 146097;
    if (days > 36524) {
      days--;
      gy += 100 * Math.floor(days / 36524);
      days %= 36524;
      if (days >= 365) days++;
    }
    gy += 4 * Math.floor(days / 1461);
    days %= 1461;
    if (days > 365) {
      gy += Math.floor((days - 1) / 365);
      days = (days - 1) % 365;
    }
    let gd = days + 1;
    const isLeap = ((gy % 4 === 0 && gy % 100 !== 0) || (gy % 400 === 0));
    const sal_a = [0, 31, isLeap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let gm = 0;
    while (gm < 13 && gd > sal_a[gm]) {
      gd -= sal_a[gm];
      gm++;
    }
    return [gy, gm, gd];
  }

  function getDaysInJalaliMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    // Month 12: leap year check
    const [gy, gm, gd] = jalaliToGregorian(jy, 12, 30);
    const [jy2, jm2, jd2] = gregorianToJalali(gy, gm, gd);
    return (jy2 === jy && jm2 === 12 && jd2 === 30) ? 30 : 29;
  }

  function getFirstDayOfWeek(jy, jm) {
    const [gy, gm, gd] = jalaliToGregorian(jy, jm, 1);
    const d = new Date(gy, gm - 1, gd);
    return (d.getDay() + 1) % 7; // 0 = Sat, 1 = Sun, ..., 6 = Fri
  }

  function getTodayJalali() {
    const now = new Date();
    return gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
  }

  function parseJalaliString(str) {
    if (!str) return null;
    const cleaned = toEnDigits(String(str).trim());
    const parts = cleaned.split(/[\/\-\\]/);
    if (parts.length !== 3) return null;
    const jy = parseInt(parts[0], 10);
    const jm = parseInt(parts[1], 10);
    const jd = parseInt(parts[2], 10);
    if (isNaN(jy) || isNaN(jm) || isNaN(jd)) return null;
    if (jm < 1 || jm > 12 || jd < 1 || jd > 31) return null;
    return [jy, jm, jd];
  }

  function formatJalaliString(jy, jm, jd) {
    const mm = String(jm).padStart(2, '0');
    const dd = String(jd).padStart(2, '0');
    return `${jy}/${mm}/${dd}`;
  }

  // Active popover instance
  let activeInstance = null;

  class JalaliDatepicker {
    constructor(inputElement, options = {}) {
      this.input = inputElement;
      this.options = Object.assign({
        autoClose: true,
        onSelect: null
      }, options);

      const [tY, tM, tD] = getTodayJalali();
      this.today = [tY, tM, tD];
      this.currentYear = tY;
      this.currentMonth = tM;

      this.init();
    }

    init() {
      // Click or focus on input
      this.input.addEventListener('click', (e) => {
        e.stopPropagation();
        this.open();
      });

      // Find toggle button if adjacent
      const container = this.input.parentElement;
      if (container) {
        const toggleBtn = container.querySelector('.jalali-calendar-toggle');
        if (toggleBtn) {
          toggleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.toggle();
          });
        }
      }
    }

    toggle() {
      if (activeInstance === this) {
        this.close();
      } else {
        this.open();
      }
    }

    open() {
      if (activeInstance && activeInstance !== this) {
        activeInstance.close();
      }

      // Check current input value
      const existing = parseJalaliString(this.input.value);
      if (existing) {
        this.currentYear = existing[0];
        this.currentMonth = existing[1];
        this.selectedDate = existing;
      } else {
        this.selectedDate = null;
      }

      this.render();
      this.positionPopover();
      activeInstance = this;
    }

    close() {
      if (this.popover && this.popover.parentNode) {
        this.popover.parentNode.removeChild(this.popover);
      }
      this.popover = null;
      if (activeInstance === this) {
        activeInstance = null;
      }
    }

    positionPopover() {
      if (!this.popover) return;
      const rect = this.input.getBoundingClientRect();
      const scrollY = window.scrollY || document.documentElement.scrollTop;
      const scrollX = window.scrollX || document.documentElement.scrollLeft;

      let top = rect.bottom + scrollY + 6;
      let left = rect.left + scrollX;

      // Ensure it does not overflow screen width
      const popoverWidth = 290;
      if (left + popoverWidth > window.innerWidth - 16) {
        left = window.innerWidth - popoverWidth - 16;
      }
      if (left < 16) left = 16;

      this.popover.style.top = `${top}px`;
      this.popover.style.left = `${left}px`;
    }

    render() {
      if (!this.popover) {
        this.popover = document.createElement('div');
        this.popover.className = 'jdp-popover';
        this.popover.addEventListener('click', (e) => e.stopPropagation());
        document.body.appendChild(this.popover);
      }

      const totalDays = getDaysInJalaliMonth(this.currentYear, this.currentMonth);
      const startDayOfWeek = getFirstDayOfWeek(this.currentYear, this.currentMonth);

      let html = `
        <div class="jdp-header">
          <button type="button" class="jdp-nav-btn jdp-prev-btn" title="ماه قبلی">&#10095;</button>
          <div class="jdp-selectors">
            <select class="jdp-select jdp-month-select">
              ${PERSIAN_MONTHS.map((m, idx) => `
                <option value="${idx + 1}" ${this.currentMonth === (idx + 1) ? 'selected' : ''}>${m}</option>
              `).join('')}
            </select>
            <select class="jdp-select jdp-year-select">
              ${Array.from({ length: 15 }, (_, i) => this.today[0] - 8 + i).map(y => `
                <option value="${y}" ${this.currentYear === y ? 'selected' : ''}>${toFaDigits(y)}</option>
              `).join('')}
            </select>
          </div>
          <button type="button" class="jdp-nav-btn jdp-next-btn" title="ماه بعدی">&#10094;</button>
        </div>

        <div class="jdp-weekdays">
          ${PERSIAN_WEEKDAYS.map((w, idx) => `
            <div class="jdp-weekday ${idx === 6 ? 'is-friday' : ''}">${w}</div>
          `).join('')}
        </div>

        <div class="jdp-days-grid">
      `;

      // Empty slots before month start
      for (let i = 0; i < startDayOfWeek; i++) {
        html += `<div class="jdp-day is-empty"></div>`;
      }

      // Month days
      for (let d = 1; d <= totalDays; d++) {
        const dow = (startDayOfWeek + d - 1) % 7;
        const isFriday = (dow === 6);
        const isToday = (this.currentYear === this.today[0] && this.currentMonth === this.today[1] && d === this.today[2]);
        const isSelected = (this.selectedDate && this.currentYear === this.selectedDate[0] && this.currentMonth === this.selectedDate[1] && d === this.selectedDate[2]);

        const classes = [
          'jdp-day',
          isFriday ? 'is-friday' : '',
          isToday ? 'is-today' : '',
          isSelected ? 'is-selected' : ''
        ].filter(Boolean).join(' ');

        html += `<button type="button" class="${classes}" data-day="${d}">${toFaDigits(d)}</button>`;
      }

      html += `
        </div>
        <div class="jdp-footer">
          <button type="button" class="jdp-footer-btn btn-today">امروز</button>
          <button type="button" class="jdp-footer-btn btn-clear">پاک کردن</button>
          <button type="button" class="jdp-footer-btn btn-close">بستن</button>
        </div>
      `;

      this.popover.innerHTML = html;
      this.attachEvents();
    }

    attachEvents() {
      // Prev month
      this.popover.querySelector('.jdp-prev-btn').addEventListener('click', () => {
        this.currentMonth--;
        if (this.currentMonth < 1) {
          this.currentMonth = 12;
          this.currentYear--;
        }
        this.render();
      });

      // Next month
      this.popover.querySelector('.jdp-next-btn').addEventListener('click', () => {
        this.currentMonth++;
        if (this.currentMonth > 12) {
          this.currentMonth = 1;
          this.currentYear++;
        }
        this.render();
      });

      // Month select
      this.popover.querySelector('.jdp-month-select').addEventListener('change', (e) => {
        this.currentMonth = parseInt(e.target.value, 10);
        this.render();
      });

      // Year select
      this.popover.querySelector('.jdp-year-select').addEventListener('change', (e) => {
        this.currentYear = parseInt(e.target.value, 10);
        this.render();
      });

      // Day buttons
      this.popover.querySelectorAll('.jdp-day[data-day]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          const day = parseInt(btn.getAttribute('data-day'), 10);
          this.selectDate(this.currentYear, this.currentMonth, day);
        });
      });

      // Today button
      this.popover.querySelector('.btn-today').addEventListener('click', () => {
        this.selectDate(this.today[0], this.today[1], this.today[2]);
      });

      // Clear button
      this.popover.querySelector('.btn-clear').addEventListener('click', () => {
        this.input.value = '';
        this.selectedDate = null;
        if (this.options.onSelect) this.options.onSelect('');
        this.close();
      });

      // Close button
      this.popover.querySelector('.btn-close').addEventListener('click', () => {
        this.close();
      });
    }

    selectDate(jy, jm, jd) {
      const val = formatJalaliString(jy, jm, jd);
      this.input.value = val;
      this.selectedDate = [jy, jm, jd];
      if (this.options.onSelect) {
        this.options.onSelect(val);
      }
      if (this.options.autoClose) {
        this.close();
      }
    }
  }

  // Global close on click outside
  document.addEventListener('click', (e) => {
    if (activeInstance && activeInstance.popover) {
      if (!activeInstance.popover.contains(e.target) && e.target !== activeInstance.input) {
        activeInstance.close();
      }
    }
  });

  // Global close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeInstance) {
      activeInstance.close();
    }
  });

  // Utility to set relative preset dates for a start and end input
  function applyDatePreset(fromInputId, toInputId, daysAgo) {
    const fromInput = document.getElementById(fromInputId);
    const toInput = document.getElementById(toInputId);
    if (!toInput) return;

    const now = new Date();
    const [tY, tM, tD] = gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
    toInput.value = formatJalaliString(tY, tM, tD);

    if (daysAgo === 0) {
      // Today only
      if (fromInput) fromInput.value = formatJalaliString(tY, tM, tD);
    } else if (daysAgo > 0) {
      const past = new Date(now.getTime() - (daysAgo * 24 * 60 * 60 * 1000));
      const [pY, pM, pD] = gregorianToJalali(past.getFullYear(), past.getMonth() + 1, past.getDate());
      if (fromInput) fromInput.value = formatJalaliString(pY, pM, pD);
    } else {
      // Clear
      if (fromInput) fromInput.value = '';
      toInput.value = '';
    }
  }

  // Auto initialize on inputs with class 'jalali-datepicker-input'
  function autoInit() {
    document.querySelectorAll('.jalali-datepicker-input').forEach(input => {
      if (!input._jdpInstance) {
        input._jdpInstance = new JalaliDatepicker(input);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
  } else {
    autoInit();
  }

  // Export to window
  window.JalaliDatepicker = JalaliDatepicker;
  window.applyDatePreset = applyDatePreset;
  window.gregorianToJalali = gregorianToJalali;
  window.jalaliToGregorian = jalaliToGregorian;

})(window, document);
