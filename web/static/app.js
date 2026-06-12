// SIGNAL app.js — hash router + fetch helper + clock
const API = (path, opts) => fetch(path, opts).then(async r => {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.status), {body: j, status: r.status});
  return j;
});

const screens = {};  // name -> {el, render}

function show(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('on'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('on'));
  const screenEl = document.getElementById('s-' + name);
  const navEl = document.getElementById('nav-' + name);
  if (!screenEl || !navEl) return;
  screenEl.classList.add('on');
  navEl.classList.add('on');
  location.hash = name;
  if (screens[name] && screens[name].render) screens[name].render();
}

window.addEventListener('hashchange', () => {
  const n = location.hash.slice(1);
  if (screens[n]) show(n);
});

document.addEventListener('DOMContentLoaded', () => {
  // Register all screens
  ['mixes', 'library', 'search', 'setup'].forEach(name => {
    const el = document.getElementById('s-' + name);
    screens[name] = {el, render: () => {}};
  });

  // Clock
  function updateClock() {
    const t = new Date();
    const clk = document.getElementById('clk');
    if (clk) clk.textContent = String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
  }
  updateClock();
  setInterval(updateClock, 30000);

  // Nav buttons
  document.querySelectorAll('nav button').forEach(b => {
    b.onclick = () => show(b.id.replace('nav-', ''));
  });

  // Initial screen
  const initial = location.hash.slice(1);
  show(screens[initial] ? initial : 'mixes');
});
