// A stand-in for the claude.ai dashboard runtime, so dashboard/index.html also runs as a
// static page (GitHub Pages). It implements what the page uses: dash.data, dash.draw,
// dash.onData and dash.colors, over the JSON files `stt dashboard` writes.
(() => {
  const datasets = {};
  const draws = new Map();
  const handlers = [];
  let loaded = false;

  const shown = (el) => !el.closest('[hidden]');
  const runDraws = () => {
    for (const [name, fn] of draws) {
      document.querySelectorAll(`[data-draw="${name}"]`).forEach((el) => {
        if (!shown(el)) return; // like the dashboard: drawings under [hidden] don't run
        try {
          fn(el);
        } catch (e) {
          console.error(`drawing ${name} failed`, e);
        }
      });
    }
  };
  const refresh = () => {
    handlers.forEach((fn) => {
      try {
        fn();
      } catch (e) {
        console.error('onData handler failed', e);
      }
    });
    runDraws();
  };

  window.dash = {
    colors: ['var(--series-1)'],
    data: (id) => datasets[id] || { status: loaded ? 'missing' : 'loading', data: [] },
    draw: (name, fn) => {
      draws.set(name, fn);
      if (loaded) refresh();
    },
    onData: (fn) => {
      handlers.push(fn);
      fn();
    },
  };

  window.addEventListener('DOMContentLoaded', async () => {
    const index = await fetch('data/index.json').then((r) => r.json());
    await Promise.all(index.map(async (id) => {
      try {
        const data = await fetch(`data/${id}.json`).then((r) => r.json());
        datasets[id] = { status: 'ok', data };
      } catch (e) {
        datasets[id] = { status: 'error', data: [] };
      }
    }));
    loaded = true;
    refresh();
    let width = window.innerWidth;
    window.addEventListener('resize', () => {
      if (Math.abs(window.innerWidth - width) < 24) return;
      width = window.innerWidth;
      runDraws();
    });
  });
})();
