/* deck.js: the engine of this deck. Navigation, builds, presenter view, overview and print.
   Leave it as it is: the look lives in theme.css. Slides are numbered from 1, like the #/n links.

   Keys: right, down, space, Page Down: next. Left, up, Shift+space, Page Up: back.
   Home and End: first and last slide. f: full screen. p: presenter view. o: overview.
   b: black screen. Escape: leave the overview. In the presenter view, + and - size the notes.

   window.deck: count, index, step, steps(n), go(n, step), next(), prev(), overview(on), presenter().
   go(n, 'last') shows slide n with every build in. A 'deck:change' event fires on each move. */
(() => {
  'use strict';
  const W = 1920, H = 1080;
  const root = document.documentElement;
  root.classList.add('deck-js');

  function start() {
    const main = document.querySelector('.deck');
    if (!main) return;
    const slides = [...main.children].filter((el) => el.classList.contains('slide'));
    const presenterWindow = new URLSearchParams(location.search).get('view') === 'presenter';
    const minutes = parseFloat(main.getAttribute('data-minutes')) || 0;

    // Builds: data-step on its own is the next build in document order; data-step="2" puts several
    // elements in the same build. Builds go in number order.
    const builds = slides.map((slide) => {
      const groups = new Map();
      let last = 0;
      for (const el of slide.querySelectorAll('[data-step]')) {
        if (el.closest('.notes')) continue;
        const v = (el.getAttribute('data-step') || '').trim();
        const k = /^\d+$/.test(v) && +v > 0 ? +v : last + 1;
        last = Math.max(last, k);
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(el);
      }
      return [...groups.keys()].sort((a, b) => a - b).map((k) => groups.get(k));
    });

    const state = { n: 1, step: 0 };
    let mode = 'normal';           // normal | overview | presenter
    let peer = null;               // the other window: the presenter view, or the deck that opened it
    let started = Date.now();
    let blackout = false;
    let notesSize = 22;

    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const stepsOf = (n) => (builds[n - 1] ? builds[n - 1].length : 0);
    const titleOf = (n) => {
      const s = slides[n - 1];
      if (!s) return '';
      const h = s.querySelector('h1, h2, h3');
      return s.getAttribute('data-title') || (h ? h.textContent.replace(/\s+/g, ' ').trim() : '');
    };

    // ---- the parts of the page the engine adds
    const live = document.createElement('div');
    live.className = 'deck-ui deck-live';
    live.setAttribute('aria-live', 'polite');
    document.body.appendChild(live);
    let pv = null;
    if (presenterWindow) {
      root.classList.add('deck-presenter');
      mode = 'presenter';
      pv = document.createElement('div');
      pv.className = 'deck-ui pv';
      pv.innerHTML = '<div class="pv-current" aria-label="Current slide"></div>'
        + '<div class="pv-next" aria-label="Next slide"><span class="pv-end">End of the deck</span></div>'
        + '<div class="pv-notes" tabindex="0" aria-label="Speaker notes"></div>'
        + '<div class="pv-bar"><span class="pv-count"></span><span class="pv-builds"></span>'
        + '<button type="button" class="pv-timer" title="Restart the timer"></button><span class="pv-clock"></span></div>';
      document.body.appendChild(pv);
      pv.querySelector('.pv-timer').addEventListener('click', () => { started = Date.now(); tick(); });
      setInterval(tick, 1000);
      document.title = 'Presenter: ' + document.title;
    }
    let spacer = null;

    function fit(box) {
      const s = Math.min(box.width / W, box.height / H);
      return { x: box.left + (box.width - W * s) / 2, y: box.top + (box.height - H * s) / 2, s };
    }
    function place(el, f) {
      if (f) {
        el.style.setProperty('--x', f.x + 'px');
        el.style.setProperty('--y', f.y + 'px');
        el.style.setProperty('--s', f.s);
      } else {
        el.style.removeProperty('--x');
        el.style.removeProperty('--y');
        el.style.removeProperty('--s');
      }
    }
    function showBuilds(i, count) {
      builds[i].forEach((group, g) => group.forEach((el) => el.classList.toggle('is-shown', g < count)));
    }

    function render() {
      const n = state.n;
      slides.forEach((el, i) => {
        el.classList.toggle('is-current', i === n - 1);
        el.classList.toggle('is-past', i < n - 1);
        el.classList.toggle('is-future', i > n - 1);
        el.classList.toggle('is-next', mode === 'presenter' && i === n);
        // Past slides keep every build, so going back lands on the finished slide.
        const count = mode === 'overview' || i < n - 1 || (mode === 'presenter' && i === n) ? stepsOf(i + 1)
          : i === n - 1 ? state.step : 0;
        showBuilds(i, count);
      });
      const vw = root.clientWidth, vh = root.clientHeight;
      if (mode === 'normal') {
        slides.forEach((el) => place(el, null));
        const f = fit({ left: 0, top: 0, width: vw, height: vh });
        main.style.setProperty('--x', f.x + 'px');
        main.style.setProperty('--y', f.y + 'px');
        main.style.setProperty('--s', f.s);
        if (spacer) spacer.style.height = '0px';
      } else if (mode === 'overview') {
        const cols = clamp(Math.ceil(Math.sqrt(slides.length * 1.2)), 3, 6);
        const gap = 24, pad = 32;
        const cw = (main.clientWidth - pad * 2 - gap * (cols - 1)) / cols, ch = cw * H / W;
        slides.forEach((el, i) => {
          const c = i % cols, r = Math.floor(i / cols);
          place(el, fit({ left: pad + c * (cw + gap), top: pad + r * (ch + gap), width: cw, height: ch }));
        });
        if (!spacer) {
          spacer = document.createElement('div');
          spacer.className = 'deck-spacer';
          main.appendChild(spacer);
        }
        spacer.style.height = (pad * 2 + Math.ceil(slides.length / cols) * (ch + gap)) + 'px';
        const cur = slides[n - 1];
        if (cur) {
          const top = parseFloat(cur.style.getPropertyValue('--y'));
          if (top < main.scrollTop || top + ch > main.scrollTop + main.clientHeight) main.scrollTop = Math.max(0, top - pad);
        }
      } else {
        slides.forEach((el) => place(el, null));
        const box = (sel) => pv.querySelector(sel).getBoundingClientRect();
        if (slides[n - 1]) place(slides[n - 1], fit(box('.pv-current')));
        if (slides[n]) place(slides[n], fit(box('.pv-next')));
        pv.querySelector('.pv-end').hidden = !!slides[n];
        const notes = slides[n - 1] && slides[n - 1].querySelector('.notes');
        const panel = pv.querySelector('.pv-notes');
        panel.replaceChildren(...(notes ? [...notes.childNodes].map((c) => c.cloneNode(true)) : []));
        if (!panel.textContent.trim()) panel.textContent = 'No notes for this slide.';
        panel.style.fontSize = notesSize + 'px';
        pv.querySelector('.pv-count').textContent = `Slide ${n} of ${slides.length}`;
        const left = stepsOf(n) - state.step;
        pv.querySelector('.pv-builds').textContent = left ? `${left} build${left === 1 ? '' : 's'} to go` : '';
        tick();
      }
      root.classList.toggle('deck-blackout', blackout);
    }

    function tick() {
      if (!pv) return;
      const sec = Math.floor((Date.now() - started) / 1000);
      const mmss = (t) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
      const timer = pv.querySelector('.pv-timer');
      timer.textContent = minutes ? `${mmss(sec)} of ${mmss(Math.round(minutes * 60))}` : mmss(sec);
      timer.classList.toggle('is-over', !!minutes && sec > minutes * 60);
      const d = new Date();
      pv.querySelector('.pv-clock').textContent = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }

    function setHash() {
      const h = `#/${state.n}${state.step ? '/' + state.step : ''}`;
      if (location.hash === h) return;
      try { history.replaceState(null, '', location.pathname + location.search + h); } catch { location.replace(h); }
    }

    function post(msg) {
      try { if (peer && !peer.closed) peer.postMessage(Object.assign({ deck: 'killer-slides' }, msg), '*'); } catch { /* the other window is gone */ }
    }

    function go(n, step, opts) {
      const count = slides.length;
      if (!count) return;
      n = clamp(Math.round(Number(n)) || 1, 1, count);
      const max = stepsOf(n);
      step = step === 'last' || step === Infinity ? max : clamp(Math.round(Number(step)) || 0, 0, max);
      const moved = n !== state.n;
      state.n = n;
      state.step = step;
      render();
      setHash();
      if (moved) live.textContent = `Slide ${n} of ${count}${titleOf(n) ? ': ' + titleOf(n) : ''}`;
      document.dispatchEvent(new CustomEvent('deck:change', { detail: { index: n, step, count } }));
      if (!(opts && opts.quiet)) post({ type: 'go', n, step });
    }
    const next = () => (state.step < stepsOf(state.n) ? go(state.n, state.step + 1) : state.n < slides.length && go(state.n + 1, 0));
    const prev = () => (state.step > 0 ? go(state.n, state.step - 1) : state.n > 1 && go(state.n - 1, 'last'));

    function overview(on) {
      if (mode === 'presenter') return;
      mode = (on === undefined ? mode !== 'overview' : on) ? 'overview' : 'normal';
      root.classList.toggle('deck-overview', mode === 'overview');
      if (mode === 'normal') main.scrollTop = 0;
      render();
    }

    function presenter() {
      if (presenterWindow) return null;
      const url = location.href.split('#')[0].split('?')[0] + `?view=presenter#/${state.n}${state.step ? '/' + state.step : ''}`;
      const w = window.open(url, 'deck-presenter', 'popup,width=1280,height=800');
      if (w) peer = w;
      return w;
    }

    function setBlackout(on, opts) {
      blackout = on;
      render();
      if (!(opts && opts.quiet)) post({ type: 'blackout', on });
    }

    window.addEventListener('message', (e) => {
      const d = e.data;
      if (!d || d.deck !== 'killer-slides') return;
      if (presenterWindow ? e.source !== window.opener : peer && e.source !== peer) return;
      if (!presenterWindow && !peer) peer = e.source;
      if (d.type === 'go') go(d.n, d.step, { quiet: true });
      else if (d.type === 'blackout') setBlackout(!!d.on, { quiet: true });
      else if (d.type === 'hello') post({ type: 'go', n: state.n, step: state.step });
    });

    document.addEventListener('keydown', (e) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const t = e.target;
      if (t && t.closest && t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
      const key = e.key;
      let used = true;
      if (key === 'ArrowRight' || key === 'ArrowDown' || key === 'PageDown' || (key === ' ' && !e.shiftKey)) next();
      else if (key === 'ArrowLeft' || key === 'ArrowUp' || key === 'PageUp' || (key === ' ' && e.shiftKey)) prev();
      else if (key === 'Home') go(1, 0);
      else if (key === 'End') go(slides.length, 'last');
      else if (key === 'Enter' && mode === 'overview') overview(false);
      else if (key === 'Escape' && mode === 'overview') overview(false);
      else if (key === 'o' || key === 'O') overview();
      else if (key === 'p' || key === 'P') presenter();
      else if (key === 'b' || key === 'B' || key === '.') setBlackout(!blackout);
      else if ((key === 'f' || key === 'F') && root.requestFullscreen) {
        if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(() => {});
      } else if (mode === 'presenter' && (key === '+' || key === '=')) { notesSize = Math.min(48, notesSize + 2); render(); }
      else if (mode === 'presenter' && key === '-') { notesSize = Math.max(14, notesSize - 2); render(); }
      else used = false;
      if (used) e.preventDefault();
    });

    main.addEventListener('click', (e) => {
      if (mode === 'overview') {
        const s = e.target.closest('.slide');
        if (s) { go(slides.indexOf(s) + 1, 'last'); overview(false); }
        return;
      }
      if (mode !== 'normal' || e.button !== 0) return;
      if (e.target.closest('a, button, input, select, textarea, label, summary, video, audio, [data-no-nav]')) return;
      if (String(window.getSelection && window.getSelection()).trim()) return;
      if (e.clientX >= root.clientWidth / 2) next(); else prev();
    });

    let touch = null;
    main.addEventListener('touchstart', (e) => { if (e.touches.length === 1) touch = { x: e.touches[0].clientX, y: e.touches[0].clientY }; }, { passive: true });
    main.addEventListener('touchend', (e) => {
      if (!touch || mode !== 'normal') return;
      const dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
      touch = null;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) { if (dx < 0) next(); else prev(); }
    });

    const fromHash = () => {
      const m = location.hash.match(/^#\/(\d+)(?:\/(\d+))?/);
      return m ? { n: +m[1], step: m[2] ? +m[2] : 0 } : null;
    };
    window.addEventListener('hashchange', () => {
      const h = fromHash();
      if (h && (h.n !== state.n || h.step !== state.step)) go(h.n, h.step);
    });
    window.addEventListener('resize', render);
    document.addEventListener('fullscreenchange', render);
    window.addEventListener('beforeprint', () => slides.forEach((el, i) => showBuilds(i, stepsOf(i + 1))));
    window.addEventListener('afterprint', render);

    window.deck = {
      version: 1,
      get count() { return slides.length; },
      get index() { return state.n; },
      get step() { return state.step; },
      get mode() { return mode; },
      get slides() { return slides.slice(); },
      steps: stepsOf,
      title: titleOf,
      go, next, prev, overview, presenter,
    };

    const h = fromHash();
    go(h ? h.n : 1, h ? h.step : 0, { quiet: true });
    if (presenterWindow && window.opener) { peer = window.opener; post({ type: 'hello' }); }
    root.classList.add('deck-ready');
    document.dispatchEvent(new CustomEvent('deck:ready', { detail: { count: slides.length } }));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
