// charts.js — inline-SVG chart builders for the INSIGHTS screen.
// Every builder returns an <svg> Element built via createElementNS (no innerHTML).
// Colors come from CSS custom properties so charts track the SIGNAL theme.
const NS = 'http://www.w3.org/2000/svg';
const ACID = 'var(--acid)';
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
  if (!values.length) return svg(320, 120);
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
  if (!vals.length) return svg(w, h);
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
  if (vals.length === 1) {
    const i = points.findIndex(v => v != null);
    s.appendChild(_svgEl('circle', {cx: x(i), cy: y(points[i]), r: 3, fill: ACID}));
  }
  if (opts.labels) {
    const every = Math.ceil(opts.labels.length / 6);
    opts.labels.forEach((lab, i) => {
      if (i % every === 0) s.appendChild(_text(x(i), h - 3, lab));
    });
  }
  return s;
}

// Donut from segments [{label, value}].
function donut(segments, opts = {}) {
  const size = 120, r = 48, cx = size / 2, cy = size / 2, sw = 18;
  const total = segments.reduce((a, d) => a + d.value, 0) || 1;
  const palette = opts.palette || ['var(--acid)', '#7fd4ff', '#ff7fb0', '#ffd166',
    '#b48cff', '#5be0a0', '#ff9f6e', '#9aa0aa'];
  const s = svg(size, size);
  let a0 = -Math.PI / 2;
  segments.forEach((d, i) => {
    const frac = d.value / total, a1 = a0 + frac * 2 * Math.PI;
    const color = palette[i % palette.length];
    if (frac >= 1) {
      s.appendChild(_svgEl('circle', {cx, cy, r, fill: 'none',
        stroke: color, 'stroke-width': sw}));
      a0 = a1; return;
    }
    const large = frac > 0.5 ? 1 : 0;
    const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    s.appendChild(_svgEl('path', {
      d: `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
      fill: 'none', stroke: color, 'stroke-width': sw}));
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
