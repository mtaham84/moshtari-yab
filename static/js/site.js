(() => {
  // Theme Management
  const root = document.documentElement;
  const themeButton = document.querySelector('[data-theme-toggle]');
  const themeLabel = document.querySelector('[data-theme-label]');
  const themeIcon = document.querySelector('[data-theme-icon]');
  const themes = ['system', 'light', 'dark'];
  const labels = { system: 'خودکار', light: 'روشن', dark: 'تیره' };
  
  const icons = {
    system: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/>',
    light: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    dark: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
  };

  const readTheme = () => {
    try {
      const saved = localStorage.getItem('moshtari-yab-theme');
      return themes.includes(saved) ? saved : 'system';
    } catch (_) {
      return 'system';
    }
  };

  let currentTheme = readTheme();
  root.dataset.theme = currentTheme;

  const updateThemeUI = () => {
    if (themeLabel) themeLabel.textContent = labels[currentTheme];
    if (themeIcon) themeIcon.innerHTML = icons[currentTheme];
    if (themeButton) {
      themeButton.setAttribute('aria-label', `پوسته: ${labels[currentTheme]}؛ کلیک برای تغییر`);
    }
  };

  updateThemeUI();

  themeButton?.addEventListener('click', () => {
    currentTheme = themes[(themes.indexOf(currentTheme) + 1) % themes.length];
    root.dataset.theme = currentTheme;
    updateThemeUI();
    try {
      localStorage.setItem('moshtari-yab-theme', currentTheme);
    } catch (_) {}
  });

  // Mobile Navigation Menu Toggle
  const mobileToggle = document.querySelector('.mobile-menu-btn');
  const navMenu = document.querySelector('#main-nav-menu');
  const landingBackdrop = document.querySelector('#landing-menu-backdrop');
  const drawerCloseBtn = document.querySelector('#mobile-drawer-close-btn');

  const closeLandingMenu = () => {
    mobileToggle?.setAttribute('aria-expanded', 'false');
    navMenu?.classList.remove('is-open');
    landingBackdrop?.classList.remove('is-active');
    document.body.style.overflow = '';
  };

  const openLandingMenu = () => {
    mobileToggle?.setAttribute('aria-expanded', 'true');
    navMenu?.classList.add('is-open');
    landingBackdrop?.classList.add('is-active');
    document.body.style.overflow = 'hidden';
  };

  mobileToggle?.addEventListener('click', (e) => {
    e.stopPropagation();
    const isExpanded = mobileToggle.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      closeLandingMenu();
    } else {
      openLandingMenu();
    }
  });

  drawerCloseBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    closeLandingMenu();
  });

  landingBackdrop?.addEventListener('click', (e) => {
    e.stopPropagation();
    closeLandingMenu();
  });

  // Stop clicks inside nav-menu from propagating to backdrop
  navMenu?.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  navMenu?.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href');
      closeLandingMenu();
      if (href && href.startsWith('#')) {
        e.preventDefault();
        try {
          const rawId = href.substring(1);
          const decodedId = decodeURIComponent(rawId);
          const targetEl = document.getElementById(decodedId) || document.getElementById(rawId) || document.querySelector(href);
          if (targetEl) {
            setTimeout(() => {
              targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 60);
          }
        } catch (_) {}
      }
    });
  });

  // Auto-open drawer if requested via URL (?menu=open or #menu)
  if (window.location.search.includes('menu=open') || window.location.hash === '#menu') {
    setTimeout(openLandingMenu, 120);
  }

  // Scroll Reveal Animations via IntersectionObserver
  const revealElements = document.querySelectorAll('.reveal-on-scroll');
  if (revealElements.length > 0) {
    if ('IntersectionObserver' in window) {
      const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      }, {
        root: null,
        rootMargin: '0px 0px -50px 0px',
        threshold: 0.08
      });

      revealElements.forEach((el) => revealObserver.observe(el));
    } else {
      revealElements.forEach((el) => el.classList.add('is-visible'));
    }
  }

  // Interactive Hero Search Pill Simulation
  const heroForm = document.querySelector('#hero-search-form');
  const heroInput = document.querySelector('#hero-product-input');
  const matchedProductTitle = document.querySelector('#demo-matched-product');

  heroForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    const query = heroInput?.value.trim();
    if (query && matchedProductTitle) {
      matchedProductTitle.textContent = query;
    }
    
    // Smooth scroll to opportunity demo section
    const opportunitySection = document.querySelector('#فرصت-ها');
    if (opportunitySection) {
      opportunitySection.scrollIntoView({ behavior: 'smooth' });
    }
  });

  // Outreach Draft Copy Button
  const copyBtn = document.querySelector('#btn-copy-outreach');
  const outreachText = document.querySelector('#outreach-draft-text');
  copyBtn?.addEventListener('click', () => {
    if (outreachText) {
      navigator.clipboard?.writeText(outreachText.textContent.trim()).then(() => {
        const originalText = copyBtn.innerHTML;
        copyBtn.innerHTML = '<span> کپی شد</span>';
        setTimeout(() => {
          copyBtn.innerHTML = originalText;
        }, 2000);
      });
    }
  });
})();

// Hero video: start after the page is interactive, only on a decent connection, and fall back to the aurora
// if it cannot buffer quickly or stalls. The page never waits for it.
(() => {
  const video = document.querySelector('.hero-video');
  if (!video || !video.dataset.src) return;
  const conn = navigator.connection || {};
  const weak = conn.saveData || /(^|-)2g$|3g/.test(conn.effectiveType || '') ||
    (typeof conn.downlink === 'number' && conn.downlink > 0 && conn.downlink < 1.5) ||
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (weak) return;

  let done = false;
  const giveUp = () => {
    if (done) return;
    done = true;
    video.classList.remove('is-ready');
    video.pause();
    video.removeAttribute('src');
    video.load();
  };
  const start = () => {
    video.src = video.dataset.src;
    video.preload = 'auto';
    const patience = setTimeout(() => { if (!video.classList.contains('is-ready')) giveUp(); }, 9000);
    video.addEventListener('canplay', () => {
      if (done) return;
      clearTimeout(patience);
      video.play().then(() => video.classList.add('is-ready')).catch(giveUp);
    }, { once: true });
    let stall;
    video.addEventListener('waiting', () => { stall = setTimeout(giveUp, 5000); });
    video.addEventListener('playing', () => clearTimeout(stall));
    video.addEventListener('error', giveUp);
  };
  const go = () => ('requestIdleCallback' in window ? requestIdleCallback(start, { timeout: 2500 }) : setTimeout(start, 600));
  if (document.readyState === 'complete') go(); else window.addEventListener('load', go, { once: true });
})();

// Landing: scroll progress (0..1) drives the hero zoom/fade, and the nav turns into glass after the first scroll.
(() => {
  const hero = document.querySelector('.hero-wrapper');
  if (!hero) return;
  let ticking = false;
  const update = () => {
    const p = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 0.85)));
    hero.style.setProperty('--hp', p.toFixed(3));
    hero.classList.toggle('is-scrolled', window.scrollY > 24);
    ticking = false;
  };
  window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();
})();
