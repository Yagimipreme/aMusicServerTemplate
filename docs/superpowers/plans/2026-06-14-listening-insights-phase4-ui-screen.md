# Listening Insights — Phase 4: INSIGHTS UI Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **No JS test harness exists in this project** (vanilla JS SPA, by convention). Verification is VISUAL: build, then run the server and view `#insights`. Each task ends with a concrete "run and look" check, not a unit test. The chart builders in `charts.js` are pure (data → SVG) and are eyeballed against seeded data.

**Goal:** Add a 6th SPA screen, **INSIGHTS**, that renders the Phase 2–3 analytics (Overview, Time, Genres, Sound) with hand-built inline-SVG charts, a period selector, and "Sync now" controls — honoring the no-`innerHTML`-with-data security invariant.

**Architecture:** A new `web/static/charts.js` exposes pure SVG-builder functions (`createElementNS`, never `innerHTML`). `web/static/app.js` gains `renderInsights()` which fetches `/insights/overview|temporal|genres|features`, builds labelled sections from the chart builders, and wires period + sync controls. `web/templates/app.html` gets the screen `<section>` + nav `<button>`; `web/static/app.css` gets chart/section styles using existing SIGNAL tokens.

**Tech Stack:** Vanilla ES (no build, no deps), inline SVG via `document.createElementNS`, existing `API()` fetch helper + `screens`/`show()` router. Colors from `:root` tokens (`--acid #c3ff3d`, `--bg`, `--panel`, `--panel2`, `--line`, `--txt`, `--mut`, `--danger`).

**Spec:** `docs/superpowers/specs/2026-06-14-listening-insights-analytics-design.md` §7.

**Scope:** Overview + Time + Genres + Sound sections. **Discovery is deferred to Phase 5** (`/insights/discovery` not built yet).

**Endpoint payloads (from the Phase 3 final review):**
- `GET /insights/overview` → `{total_scrobbles, unique_artists, unique_tracks, first_ts, last_ts, top_genre, est_listening_seconds, avg_bpm, feature_coverage:{tracks_total,tracks_with_bpm,tracks_with_mood,bpm_pct,mood_pct}}`
- `GET /insights/temporal` → `{clock:{hours:[24]}, heatmap:{matrix:[7][24],dow_labels:[7]}, weekday_weekend:{weekday,weekend,weekday_by_hour:[24],weekend_by_hour:[24]}, over_time:[{date,plays}]}`
- `GET /insights/genres` → `{top:[{genre,plays,share}], by_hour:{genres,data:{g:[24]}}, evolution:{buckets,genres,data}, diversity:{distinct,entropy,normalized_entropy}}`
- `GET /insights/features` → `{bpm_distribution:[{min,max,count}], bpm_curve:{hours:[24|null]}, key_distribution:[{key,scale,count}], mood_distribution:[{mood,count}], mood_by_time:{moods,data}, coverage:{...}}`
- Sync: `POST /insights/sync` + `GET /insights/sync/status`; `POST /insights/features/sync` + `GET /insights/features/sync/status`. Both POSTs return `{"status":"started"}`; poll the status endpoint for terminal `ok`/`error`/`disabled`/`skipped`.

All read endpoints take `?period=&tz=`. tz = `-new Date().getTimezoneOffset()` (JS returns minutes behind UTC, negated to match the server's offset-minutes convention).

---

## File Structure

- Create `web/static/charts.js` — pure SVG builders: `svg()`, `barChart()`, `hBars()`, `heatmap()`, `lineChart()`, `donut()`, `stackedBars()`, `camelotWheel()`. Loaded before `app.js`.
- Modify `web/templates/app.html` — add `<section id="s-insights">`, nav `<button id="nav-insights">`, and a `<script src="/static/charts.js">` before `app.js`.
- Modify `web/static/app.js` — add `renderInsights()` + helpers; register `screens['insights']`.
- Modify `web/static/app.css` — `.insights` section/card/chart styles using SIGNAL tokens.

---

## Task 1: chart builders (`web/static/charts.js`)

**Files:** Create `web/static/charts.js`.

- [ ] **Step 1: Create `web/static/charts.js`** — pure builders, SVG via `createElementNS`, no `innerHTML`:

```javascript
// charts.js — inline-SVG chart builders for the INSIGHTS screen.
// Every builder returns an <svg> Element built via createElementNS (no innerHTML).
// Colors come from CSS custom properties so charts track the SIGNAL theme.
const NS = 'http://www.w3.org/2000/svg';
const ACID = 'var(--acid)';
const LINE = 'var(--line)';
const MUT = 'var(--mut)';

function _svgEl(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function svg(w, h) {
  const s = _svgEl('svg', {viewBox: `0 0 ${w} ${h}`, width: '100%',
    preserveAspectRatio: 'xMidYMid meet', class: 'chart'});
  return s;
}

function _text(x, y, str, opts = {}) {
  const t = _svgEl('text', {x, y, fill: opts.fill || MUT,
    'font-size': opts.size || 9, 'text-anchor': opts.anchor || 'middle',
    'font-family': 'JetBrains Mono, monospace'});
  t.textContent = str;
  return t;
}

// Vertical bar chart. values: number[]; labels: string[] (sparse ok via labelEvery).
function barChart(values, labels, opts = {}) {
  const w = 320, h = 120, pad = 16, bw = (w - pad * 2) / values.length;
  const max = Math.max(1, ...values);
  const s = svg(w, h);
  values.forEach((v, i) => {
    const bh = (h - pad * 2) * (v / max);
    s.appendChild(_svgEl('rect', {x: pad + i * bw + 1, y: h - pad - bh,
      width: Math.max(1, bw - 2), height: bh, fill: ACID, rx: 1}));
  });
  if (labels) {
    const every = opts.labelEvery || Math.ceil(labels.length / 12);
    labels.forEach((lab, i) => {
      if (i % every === 0) s.appendChild(_text(pad + i * bw + bw / 2, h - 4, lab));
    });
  }
  return s;
}

// Horizontal bars with row labels + values (top genres, mood distribution).
function hBars(items, opts = {}) {
  // items: [{label, value}]
  const rowH = 22, w = 320, h = items.length * rowH + 8, labelW = 110;
  const max = Math.max(1, ...items.map(d => d.value));
  const s = svg(w, h);
  items.forEach((d, i) => {
    const y = i * rowH + 4;
    s.appendChild(_text(4, y + 14, String(d.label).slice(0, 16),
      {anchor: 'start', fill: 'var(--txt)', size: 10}));
    const bw = (w - labelW - 40) * (d.value / max);
    s.appendChild(_svgEl('rect', {x: labelW, y: y + 4, width: Math.max(1, bw),
      height: 12, fill: ACID, rx: 2}));
    s.appendChild(_text(labelW + bw + 4, y + 14, opts.fmt ? opts.fmt(d.value) : d.value,
      {anchor: 'start', size: 9}));
  });
  return s;
}

// 7x24 intensity grid. matrix[row][col]; rowLabels length 7.
function heatmap(matrix, rowLabels) {
  const cell = 11, gap = 1, padL = 28, padT = 4;
  const cols = matrix[0] ? matrix[0].length : 24, rows = matrix.length;
  const w = padL + cols * (cell + gap), h = padT + rows * (cell + gap) + 12;
  let max = 1;
  matrix.forEach(r => r.forEach(v => { if (v > max) max = v; }));
  const s = svg(w, h);
  matrix.forEach((row, ri) => {
    s.appendChild(_text(padL - 4, padT + ri * (cell + gap) + cell, rowLabels[ri],
      {anchor: 'end', size: 8}));
    row.forEach((v, ci) => {
      const op = v === 0 ? 0.06 : 0.15 + 0.85 * (v / max);
      const r = _svgEl('rect', {x: padL + ci * (cell + gap), y: padT + ri * (cell + gap),
        width: cell, height: cell, rx: 2, fill: ACID, 'fill-opacity': op.toFixed(3)});
      const title = _svgEl('title', {}); title.textContent = `${rowLabels[ri]} ${ci}:00 — ${v}`;
      r.appendChild(title);
      s.appendChild(r);
    });
  });
  [0, 6, 12, 18, 23].forEach(hr => s.appendChild(
    _text(padL + hr * (cell + gap) + cell / 2, h - 2, hr)));
  return s;
}

// Line/area chart. points: number|null[] (null = gap). 
function lineChart(points, opts = {}) {
  const w = 320, h = 110, pad = 16;
  const vals = points.filter(v => v != null);
  const max = Math.max(1, ...vals), min = opts.min != null ? opts.min : 0;
  const n = points.length;
  const x = i => pad + (w - pad * 2) * (n === 1 ? 0.5 : i / (n - 1));
  const y = v => h - pad - (h - pad * 2) * ((v - min) / (max - min || 1));
  const s = svg(w, h);
  let d = '', pen = false;
  points.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
    pen = true;
  });
  s.appendChild(_svgEl('path', {d: d.trim(), fill: 'none', stroke: ACID,
    'stroke-width': 2, 'stroke-linejoin': 'round'}));
  if (opts.labels) {
    const every = Math.ceil(opts.labels.length / 6);
    opts.labels.forEach((lab, i) => {
      if (i % every === 0) s.appendChild(_text(x(i), h - 3, lab));
    });
  }
  return s;
}

// Donut from segments [{label, value}]. Returns svg with legend rows beside it.
function donut(segments, opts = {}) {
  const size = 120, r = 48, cx = size / 2, cy = size / 2, sw = 18;
  const total = segments.reduce((a, d) => a + d.value, 0) || 1;
  const palette = opts.palette || ['var(--acid)', '#7fd4ff', '#ff7fb0', '#ffd166',
    '#b48cff', '#5be0a0', '#ff9f6e', '#9aa0aa'];
  const s = svg(size, size);
  let a0 = -Math.PI / 2;
  segments.forEach((d, i) => {
    const frac = d.value / total, a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    s.appendChild(_svgEl('path', {
      d: `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
      fill: 'none', stroke: palette[i % palette.length], 'stroke-width': sw}));
    a0 = a1;
  });
  return s;
}

// Stacked bars across N columns. series: {keys:[...], data:{key:[col]}}; colLabels.
function stackedBars(series, colLabels, opts = {}) {
  const w = 320, h = 130, pad = 16, n = colLabels.length;
  const bw = (w - pad * 2) / n;
  const palette = opts.palette || ['var(--acid)', '#7fd4ff', '#ff7fb0', '#ffd166',
    '#b48cff', '#5be0a0', '#ff9f6e', '#9aa0aa'];
  const totals = colLabels.map((_, c) =>
    series.keys.reduce((a, k) => a + (series.data[k][c] || 0), 0));
  const max = Math.max(1, ...totals);
  const s = svg(w, h);
  colLabels.forEach((lab, c) => {
    let yTop = h - pad;
    series.keys.forEach((k, ki) => {
      const v = series.data[k][c] || 0;
      const seg = (h - pad * 2) * (v / max);
      if (seg > 0) {
        s.appendChild(_svgEl('rect', {x: pad + c * bw + 1, y: yTop - seg,
          width: Math.max(1, bw - 2), height: seg, fill: palette[ki % palette.length]}));
        yTop -= seg;
      }
    });
    const every = opts.labelEvery || Math.ceil(n / 12);
    if (c % every === 0) s.appendChild(_text(pad + c * bw + bw / 2, h - 4, lab));
  });
  return s;
}

// Camelot-style key ring: 12 outer spokes, opacity by play count. keyCounts: [{key,scale,count}].
function camelotWheel(keyCounts) {
  const order = ['C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#', 'G#', 'D#', 'A#', 'F'];
  const size = 150, cx = 75, cy = 75, rOuter = 60, rInner = 30;
  const byKey = {};
  keyCounts.forEach(d => { byKey[d.key] = (byKey[d.key] || 0) + d.count; });
  const max = Math.max(1, ...Object.values(byKey));
  const s = svg(size, size);
  order.forEach((k, i) => {
    const a = -Math.PI / 2 + i * (2 * Math.PI / 12);
    const op = byKey[k] ? 0.2 + 0.8 * (byKey[k] / max) : 0.07;
    const x1 = cx + rInner * Math.cos(a), y1 = cy + rInner * Math.sin(a);
    const x2 = cx + rOuter * Math.cos(a), y2 = cy + rOuter * Math.sin(a);
    s.appendChild(_svgEl('line', {x1, y1, x2, y2, stroke: ACID,
      'stroke-opacity': op.toFixed(2), 'stroke-width': 8, 'stroke-linecap': 'round'}));
    const lx = cx + (rOuter + 8) * Math.cos(a), ly = cy + (rOuter + 8) * Math.sin(a) + 3;
    s.appendChild(_text(lx, ly, k, {size: 8}));
  });
  return s;
}
```

- [ ] **Step 2: Smoke-check it parses** — `node --check web/static/charts.js` (Node is available for a syntax check only). Expected: no output (valid). If `node` is unavailable, skip — Task 4's browser load is the real check.

- [ ] **Step 3: Commit**

```bash
git add web/static/charts.js
git commit -m "feat(insights-ui): inline-SVG chart builders (no innerHTML)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: screen shell — app.html + app.css

**Files:** Modify `web/templates/app.html`, `web/static/app.css`.

- [ ] **Step 1: app.html** — add the screen section (after `s-follows`), the nav button (after `nav-follows`), and load charts.js before app.js.

Add after the `s-follows` section line:
```html
<section class="screen" id="s-insights"></section>
```
Add after the `nav-follows` button line:
```html
  <button id="nav-insights"><span class="ico">▦</span>insights</button>
```
Change the script tags at the bottom to load charts first:
```html
<script src="/static/charts.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 2: app.css** — append insights styles using existing tokens (read `:root` first to confirm token names: `--bg --panel --panel2 --line --acid --txt --mut`):

```css
/* ── Insights screen ─────────────────────────────────────────────────────── */
.ins-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.ins-controls select{background:var(--panel2);color:var(--txt);border:1px solid var(--line);
  border-radius:6px;padding:4px 8px;font-family:inherit}
.ins-section{margin:18px 0}
.ins-section h2{font-size:11px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--mut);margin:0 0 8px}
.ins-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
.ins-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.ins-card .k{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.1em}
.ins-card .v{font-size:20px;font-weight:800;color:var(--txt);font-family:'Unbounded',sans-serif}
.ins-chart{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:10px;margin:6px 0}
.ins-chart .cap{font-size:10px;color:var(--mut);margin-bottom:4px}
.ins-empty{color:var(--mut);text-align:center;padding:32px 0}
.ins-cov{font-size:10px;color:var(--mut);margin-top:6px}
svg.chart{display:block;max-width:360px;margin:auto}
```

- [ ] **Step 3: Verify markup** — `grep -n "s-insights\|nav-insights\|charts.js" web/templates/app.html` shows all three additions; `python3 -c "import xml.dom.minidom" ` not needed — just eyeball the file.

- [ ] **Step 4: Commit**

```bash
git add web/templates/app.html web/static/app.css
git commit -m "feat(insights-ui): INSIGHTS screen shell + nav + styles

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: renderInsights() in app.js

**Files:** Modify `web/static/app.js`.

- [ ] **Step 1: Add `renderInsights()` + helpers** near the other `render*` functions (e.g. after `renderFollows`). Uses the existing `API()` helper and the `charts.js` globals. Build everything with `createElement`/`textContent` + the chart builders (NO `innerHTML`).

```javascript
// ── Insights screen ─────────────────────────────────────────────────────────
let _insPeriod = 'all';
const _tz = () => -new Date().getTimezoneOffset();

function _card(k, v) {
  const c = document.createElement('div'); c.className = 'ins-card';
  const kk = document.createElement('div'); kk.className = 'k'; kk.textContent = k;
  const vv = document.createElement('div'); vv.className = 'v'; vv.textContent = v;
  c.append(kk, vv); return c;
}
function _chartCard(caption, svgEl) {
  const w = document.createElement('div'); w.className = 'ins-chart';
  const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = caption;
  w.append(cap, svgEl); return w;
}
function _section(title) {
  const sec = document.createElement('div'); sec.className = 'ins-section';
  const h = document.createElement('h2'); h.textContent = title;
  sec.append(h); return sec;
}
function _fmtDur(sec) {
  const h = Math.round((sec || 0) / 3600); return h >= 1 ? `${h} h` : `${Math.round((sec||0)/60)} min`;
}

async function renderInsights() {
  const root = document.getElementById('s-insights');
  root.replaceChildren();

  // Controls: period selector + sync buttons
  const controls = document.createElement('div'); controls.className = 'ins-controls';
  const sel = document.createElement('select');
  [['all','All time'],['year','Year'],['90d','90 days'],['30d','30 days'],['7d','7 days']]
    .forEach(([v, t]) => { const o = document.createElement('option'); o.value = v;
      o.textContent = t; if (v === _insPeriod) o.selected = true; sel.append(o); });
  sel.onchange = () => { _insPeriod = sel.value; renderInsights(); };
  const syncBtn = document.createElement('button'); syncBtn.textContent = 'Sync scrobbles';
  syncBtn.onclick = () => _runSync('/insights/sync', '/insights/sync/status', syncBtn);
  const featBtn = document.createElement('button'); featBtn.textContent = 'Analyze audio';
  featBtn.onclick = () => _runSync('/insights/features/sync', '/insights/features/sync/status', featBtn);
  controls.append(sel, syncBtn, featBtn);
  root.append(controls);

  const body = document.createElement('div'); root.append(body);
  const q = `?period=${_insPeriod}&tz=${_tz()}`;
  let ov, temporal, genres, features;
  try {
    [ov, temporal, genres, features] = await Promise.all([
      API('/insights/overview' + q), API('/insights/temporal' + q),
      API('/insights/genres' + q), API('/insights/features' + q),
    ]);
  } catch (e) {
    const err = document.createElement('div'); err.className = 'ins-empty';
    err.textContent = 'Could not load insights: ' + e.message; body.append(err); return;
  }

  if (!ov.total_scrobbles) {
    const empty = document.createElement('div'); empty.className = 'ins-empty';
    empty.textContent = 'No scrobbles yet — hit “Sync scrobbles” to populate your insights.';
    body.append(empty); return;
  }

  // Overview
  const sOv = _section('Overview');
  const cards = document.createElement('div'); cards.className = 'ins-cards';
  cards.append(
    _card('Scrobbles', ov.total_scrobbles.toLocaleString()),
    _card('Artists', ov.unique_artists.toLocaleString()),
    _card('Tracks', ov.unique_tracks.toLocaleString()),
    _card('Listening', _fmtDur(ov.est_listening_seconds)),
    _card('Top genre', ov.top_genre || '—'),
    _card('Avg BPM', ov.avg_bpm != null ? ov.avg_bpm : '—'));
  sOv.append(cards); body.append(sOv);

  // Time
  const sT = _section('Time');
  sT.append(
    _chartCard('Plays by hour of day', barChart(temporal.clock.hours,
      temporal.clock.hours.map((_, i) => i), {labelEvery: 3})),
    _chartCard('When you listen (day × hour)', heatmap(temporal.heatmap.matrix,
      temporal.heatmap.dow_labels)),
    _chartCard('Plays over time', lineChart(temporal.over_time.map(d => d.plays),
      {labels: temporal.over_time.map(d => d.date)})));
  body.append(sT);

  // Genres
  const sG = _section('Genres');
  sG.append(
    _chartCard('Top genres', hBars(genres.top.map(g => ({label: g.genre, value: g.plays})),
      {fmt: v => v.toLocaleString()})),
    _chartCard(`Genre by hour`, stackedBars(
      {keys: genres.by_hour.genres, data: genres.by_hour.data},
      Array.from({length: 24}, (_, i) => i), {labelEvery: 3})));
  const div = document.createElement('div'); div.className = 'ins-cov';
  div.textContent = `Genre diversity: ${genres.diversity.distinct} genres · ` +
    `${(genres.diversity.normalized_entropy * 100).toFixed(0)}% even`;
  sG.append(div); body.append(sG);

  // Sound
  const sS = _section('Sound');
  sS.append(
    _chartCard('BPM distribution', barChart(features.bpm_distribution.map(b => b.count),
      features.bpm_distribution.map(b => b.min), {labelEvery: 3})),
    _chartCard('Energy through the day (avg BPM)',
      lineChart(features.bpm_curve.hours, {labels: features.bpm_curve.hours.map((_, i) => i)})),
    _chartCard('Keys', camelotWheel(features.key_distribution)),
    _chartCard('Moods', hBars(features.mood_distribution.map(m => ({label: m.mood, value: m.count})))));
  const cov = document.createElement('div'); cov.className = 'ins-cov';
  cov.textContent = `BPM/mood known for ${(features.coverage.bpm_pct * 100).toFixed(0)}% of tracks`;
  sS.append(cov); body.append(sS);
}

async function _runSync(postUrl, statusUrl, btn) {
  const orig = btn.textContent; btn.disabled = true; btn.textContent = '…syncing';
  try {
    await API(postUrl, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      const st = await API(statusUrl);
      if (st.status && !['running', 'started'].includes(st.status)) break;
    }
  } catch (e) { /* surfaced on re-render */ }
  btn.disabled = false; btn.textContent = orig;
  renderInsights();
}
```

- [ ] **Step 2: Register the screen** — at the registration block (~line 1677, alongside `screens['follows'] = ...`), add:

```javascript
  screens['insights'] = {el: document.getElementById('s-insights'), render: renderInsights};
```

- [ ] **Step 3: Syntax check** — `node --check web/static/app.js` → no output. (If node unavailable, rely on Task 4.)

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js
git commit -m "feat(insights-ui): renderInsights screen — overview/time/genres/sound

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Visual verification (run the app)

**Files:** none (verification only).

> The controller (not a subagent) runs this — it needs the real config + a populated insights.db. Subagents can't drive a browser here.

- [ ] **Step 1: Backend regression** — `/home/taichi/repos/musicServer/aMusicServerTemplate/.venv/bin/python -m pytest tests/ -q` → all green (no backend touched, but confirms nothing broke).

- [ ] **Step 2: Launch + screenshot** — start the server (`app.run` only, no schedulers; a free port; a config pointing at a real/populated `insights.db`), then load `http://127.0.0.1:<port>/#insights` in a headless browser (chromium-cli/playwright) and **screenshot it**. Confirm visually:
  - 6th nav item "insights" appears and is selectable.
  - Overview cards show real counts; Time section shows the clock bars + heatmap + over-time line; Genres shows top-genre bars + genre-by-hour stack; Sound shows BPM distribution + curve + key ring + moods.
  - Empty-DB path: against a fresh db, the empty-state message shows instead of broken charts.
  - DevTools console has no errors; confirm no `innerHTML` usage was introduced (`grep -n innerHTML web/static/charts.js web/static/app.js` → only pre-existing, if any).

- [ ] **Step 3:** If a chart is misshaped, iterate on `charts.js` and re-screenshot. Commit any fixes.

---

## Self-Review

**Spec coverage (§7):** 6th screen + nav ✅ (Task 2); single scrollable screen with Overview/Time/Genres/Sound sections ✅ (Task 3); period selector + browser tz on every request ✅; "Sync now" controls with progress ✅ (`_runSync`); empty state ✅; coverage note in Sound ✅; charts as inline SVG via `createElementNS`, no `innerHTML`, SIGNAL tokens, zero deps ✅ (Task 1). Discovery section deferred to Phase 5 (documented in Scope).

**Placeholder scan:** All code blocks are complete. Task 4 is genuinely verification-only (no FE test harness exists — stated up front).

**Type/contract consistency:** Builders consume exactly the documented endpoint shapes — `temporal.clock.hours` (24), `heatmap.matrix`/`dow_labels`, `over_time[].plays/.date`, `genres.top[].genre/.plays`, `by_hour.genres`/`.data`, `features.bpm_distribution[].count/.min`, `bpm_curve.hours` (24, null-safe in `lineChart`), `key_distribution[].key/.count`, `mood_distribution[].mood/.count`, `coverage.bpm_pct`, `overview.*`. `_tz()` negates `getTimezoneOffset()` to match the server's offset-minutes convention used in Phase 2/3.

---

## Next phase

5. Library cross-ref + discovery integration + `/insights/discovery` → then add the **Discovery** section to this screen (the 5th section the spec lists).
