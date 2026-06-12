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

// ── Mixes screen ──────────────────────────────────────────────────────────────

async function renderMixes() {
  const el = document.getElementById('s-mixes');
  // Show loading skeleton
  el.textContent = '';
  const loading = document.createElement('div');
  loading.className = 'mix skeleton';
  loading.style.height = '60px';
  el.appendChild(loading);

  let data;
  try {
    data = await API('/mixes');
  } catch(e) {
    el.textContent = '';
    const err = document.createElement('div');
    err.className = 'warn';
    err.textContent = 'Failed to load mixes: ' + (e.message || 'unknown error');
    el.appendChild(err);
    return;
  }

  el.textContent = '';
  const mixes = data.mixes || [];
  const nextRuns = data.next_runs || {};

  mixes.forEach(mix => {
    el.appendChild(buildMixCard(mix, nextRuns, false));
  });

  // Suggested mixes button (above "+ NEW MIX" so it appears at top of footer)
  const sugBtn = document.createElement('button');
  sugBtn.className = 'btn ghost';
  sugBtn.style.width = '100%';
  sugBtn.style.marginBottom = '18px';
  sugBtn.textContent = '↻ suggested mixes';
  sugBtn.onclick = async () => {
    sugBtn.disabled = true;
    sugBtn.textContent = 'loading…';
    try {
      await API('/mixes/suggest', {method: 'POST'});
      renderMixes();
    } catch(e) {
      sugBtn.disabled = false;
      sugBtn.textContent = '↻ suggested mixes';
    }
  };
  el.appendChild(sugBtn);

  // "+ NEW MIX" button
  const newBtn = document.createElement('button');
  newBtn.className = 'newmix';
  newBtn.textContent = '+ new mix';
  newBtn.onclick = () => {
    const blank = {
      id: '', name: '', enabled: true, auto_generated: false,
      schedule: {cadence: 'weekly', run_day: 'sunday', run_hour: 22},
      count: 30, cap: 100, new_ratio: 1.0,
      seeds: {mode: 'history', genres: [], artists: [], playlist: ''},
      quality: {}
    };
    const card = buildMixCard(blank, {}, true);
    el.insertBefore(card, sugBtn);
  };
  el.insertBefore(newBtn, sugBtn);
}

function isFresh(mix, nextRuns) {
  const nr = nextRuns[mix.id];
  if (!nr) return false;
  const nextMs = new Date(nr).getTime();
  const nowMs = Date.now();
  const cadence = (mix.schedule || {}).cadence === 'weekly' ? 7 * 24 * 3600 * 1000 : 24 * 3600 * 1000;
  // Fresh = next run is within one cadence from now (last run was recent)
  return (nextMs - nowMs) < cadence;
}

function buildMixCard(mix, nextRuns, isNew) {
  const fresh = !isNew && isFresh(mix, nextRuns);
  const suggested = mix.auto_generated && !mix.enabled;

  const card = document.createElement('div');
  card.className = 'mix' + (fresh ? ' fresh' : '');

  // ── Head ──────────────────────────────────────────────────────────────────
  const head = document.createElement('div');
  head.className = 'mix-head';
  head.onclick = () => { card.classList.toggle('open'); };

  const dot = document.createElement('span');
  dot.className = 'dot';
  head.appendChild(dot);

  const nameSpan = document.createElement('span');
  nameSpan.className = 'name';
  nameSpan.textContent = mix.name || '(new mix)';

  const meta = document.createElement('span');
  meta.className = 'meta';
  const sched = mix.schedule || {};
  const metaParts = [];
  if (mix.count) metaParts.push(mix.count + ' trk');
  if (sched.cadence === 'weekly' && sched.run_day) {
    metaParts.push(sched.run_day + ' ' + String(sched.run_hour || 0).padStart(2, '0') + ':00');
  } else if (sched.cadence === 'daily') {
    metaParts.push('daily ' + String(sched.run_hour || 0).padStart(2, '0') + ':00');
  }
  if (mix.new_ratio !== undefined) metaParts.push(Math.round(mix.new_ratio * 100) + '% new');
  meta.textContent = metaParts.join(' · ');
  nameSpan.appendChild(meta);
  head.appendChild(nameSpan);

  if (fresh) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = 'fresh';
    head.appendChild(tag);
  } else if (suggested) {
    const tag = document.createElement('span');
    tag.className = 'tag sug';
    tag.textContent = 'suggested';
    head.appendChild(tag);
  }

  const chev = document.createElement('span');
  chev.className = 'chev';
  chev.textContent = '▼';
  head.appendChild(chev);

  card.appendChild(head);

  // ── Body ──────────────────────────────────────────────────────────────────
  const body = document.createElement('div');
  body.className = 'mix-body';
  const inner = document.createElement('div');
  inner.className = 'inner';

  // Status line
  const statusLine = document.createElement('div');
  statusLine.className = 'warn';
  statusLine.style.display = 'none';
  inner.appendChild(statusLine);

  // ID + NAME inputs for new cards
  let idInput = null;
  let nameInput = null;
  if (isNew) {
    const idRow = document.createElement('div');
    idRow.className = 'frow';
    const idLabel = document.createElement('label');
    idLabel.textContent = 'ID';
    idInput = document.createElement('input');
    idInput.className = 'txt';
    idInput.type = 'text';
    idInput.placeholder = 'my-mix-id';
    idInput.value = mix.id || '';
    idRow.appendChild(idLabel);
    idRow.appendChild(idInput);
    inner.appendChild(idRow);

    const nameRow = document.createElement('div');
    nameRow.className = 'frow';
    const nameLabel = document.createElement('label');
    nameLabel.textContent = 'NAME';
    nameInput = document.createElement('input');
    nameInput.className = 'txt';
    nameInput.type = 'text';
    nameInput.placeholder = 'My Mix';
    nameInput.value = mix.name || '';
    nameRow.appendChild(nameLabel);
    nameRow.appendChild(nameInput);
    inner.appendChild(nameRow);
  }

  // Blend slider
  const blendRow = document.createElement('div');
  blendRow.className = 'frow';
  const blendLabel = document.createElement('label');
  blendLabel.textContent = 'blend';
  const blendSlider = document.createElement('input');
  blendSlider.type = 'range';
  blendSlider.min = '0';
  blendSlider.max = '100';
  blendSlider.value = String(Math.round((mix.new_ratio || 0) * 100));
  const blendVal = document.createElement('span');
  blendVal.className = 'val';
  blendVal.textContent = blendSlider.value + '%';
  blendSlider.oninput = () => { blendVal.textContent = blendSlider.value + '%'; };
  blendRow.appendChild(blendLabel);
  blendRow.appendChild(blendSlider);
  blendRow.appendChild(blendVal);
  inner.appendChild(blendRow);

  // Schedule row
  const schedRow = document.createElement('div');
  schedRow.className = 'frow';
  const schedLabel = document.createElement('label');
  schedLabel.textContent = 'schedule';
  const cadenceSel = document.createElement('select');
  ['weekly', 'daily'].forEach(c => {
    const o = document.createElement('option');
    o.value = c;
    o.textContent = c;
    if ((mix.schedule || {}).cadence === c) o.selected = true;
    cadenceSel.appendChild(o);
  });
  const daySel = document.createElement('select');
  ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].forEach(d => {
    const o = document.createElement('option');
    o.value = d;
    o.textContent = d;
    if ((mix.schedule || {}).run_day === d) o.selected = true;
    daySel.appendChild(o);
  });
  daySel.style.flex = '0 0 90px';
  const hourSel = document.createElement('select');
  for (let h = 0; h < 24; h++) {
    const o = document.createElement('option');
    o.value = String(h);
    o.textContent = String(h).padStart(2, '0') + ':00';
    if ((mix.schedule || {}).run_hour === h) o.selected = true;
    hourSel.appendChild(o);
  }
  hourSel.style.flex = '0 0 76px';
  function updateDayVisibility() {
    daySel.style.display = cadenceSel.value === 'weekly' ? '' : 'none';
  }
  cadenceSel.onchange = updateDayVisibility;
  updateDayVisibility();
  schedRow.appendChild(schedLabel);
  schedRow.appendChild(cadenceSel);
  schedRow.appendChild(daySel);
  schedRow.appendChild(hourSel);
  inner.appendChild(schedRow);

  // Mode select row
  const modeRow = document.createElement('div');
  modeRow.className = 'frow';
  const modeLabel = document.createElement('label');
  modeLabel.textContent = 'mode';
  const modeSel = document.createElement('select');
  ['history', 'genre', 'manual', 'playlist'].forEach(m => {
    const o = document.createElement('option');
    o.value = m;
    o.textContent = m;
    if ((mix.seeds || {}).mode === m) o.selected = true;
    modeSel.appendChild(o);
  });
  modeRow.appendChild(modeLabel);
  modeRow.appendChild(modeSel);
  inner.appendChild(modeRow);

  // Genre chips row (only when mode=genre)
  const genreRow = document.createElement('div');
  genreRow.className = 'frow';
  const genreLabel = document.createElement('label');
  genreLabel.textContent = 'genres';
  const chipsDiv = document.createElement('div');
  chipsDiv.className = 'chips';
  const genres = Array.isArray((mix.seeds || {}).genres) ? [...mix.seeds.genres] : [];
  function renderChips() {
    chipsDiv.textContent = '';
    genres.forEach((g, i) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = g + ' ✕';
      chip.onclick = () => { genres.splice(i, 1); renderChips(); };
      chipsDiv.appendChild(chip);
    });
    const addChip = document.createElement('span');
    addChip.className = 'chip add';
    addChip.textContent = '+ add';
    addChip.onclick = () => {
      const g = prompt('Genre:');
      if (g && g.trim()) { genres.push(g.trim()); renderChips(); }
    };
    chipsDiv.appendChild(addChip);
  }
  renderChips();
  genreRow.appendChild(genreLabel);
  genreRow.appendChild(chipsDiv);
  inner.appendChild(genreRow);

  // Playlist input row (only when mode=playlist)
  const playlistRow = document.createElement('div');
  playlistRow.className = 'frow';
  const playlistLabel = document.createElement('label');
  playlistLabel.textContent = 'playlist';
  const playlistInput = document.createElement('input');
  playlistInput.className = 'txt';
  playlistInput.type = 'text';
  playlistInput.placeholder = 'playlist id or name';
  playlistInput.value = (mix.seeds || {}).playlist || '';
  playlistRow.appendChild(playlistLabel);
  playlistRow.appendChild(playlistInput);
  inner.appendChild(playlistRow);

  // Combined mode change handler (genre + playlist visibility)
  function updateModeVisibility() {
    genreRow.style.display = modeSel.value === 'genre' ? '' : 'none';
    playlistRow.style.display = modeSel.value === 'playlist' ? '' : 'none';
  }
  modeSel.onchange = updateModeVisibility;
  updateModeVisibility();

  // Count / cap row
  const sizeRow = document.createElement('div');
  sizeRow.className = 'frow';
  const sizeLabel = document.createElement('label');
  sizeLabel.textContent = 'size / cap';
  const countInput = document.createElement('input');
  countInput.className = 'txt';
  countInput.type = 'number';
  countInput.value = String(mix.count || 30);
  countInput.style.flex = '0 0 60px';
  const capInput = document.createElement('input');
  capInput.className = 'txt';
  capInput.type = 'number';
  capInput.value = String(mix.cap || 100);
  capInput.style.flex = '0 0 60px';
  sizeRow.appendChild(sizeLabel);
  sizeRow.appendChild(countInput);
  sizeRow.appendChild(capInput);
  inner.appendChild(sizeRow);

  // Actions row
  const actions = document.createElement('div');
  actions.className = 'actions';

  // RUN button (not for new cards without a saved id)
  if (!isNew) {
    const runBtn = document.createElement('button');
    runBtn.className = 'btn run';
    runBtn.textContent = '▶ run now';
    runBtn.onclick = async () => {
      runBtn.disabled = true;
      runBtn.textContent = '…';
      statusLine.style.display = '';
      statusLine.textContent = 'Starting run…';
      try {
        const result = await API('/mixes/' + mix.id + '/run', {method: 'POST'});
        const parts = [];
        if (result.acquired !== undefined) parts.push('acquired: ' + result.acquired);
        if (result.library_added !== undefined) parts.push('added: ' + result.library_added);
        statusLine.textContent = 'Done. ' + (parts.join(', ') || JSON.stringify(result));
      } catch(e) {
        if (e.status === 409) {
          statusLine.textContent = 'Busy — another run in progress.';
        } else {
          statusLine.textContent = 'Error: ' + (e.message || 'unknown');
        }
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = '▶ run now';
      }
    };
    actions.appendChild(runBtn);
  }

  // SAVE button
  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn ghost';
  saveBtn.textContent = 'save';
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    statusLine.style.display = '';
    statusLine.textContent = 'Saving…';

    const resolvedId   = isNew ? (idInput   ? idInput.value   : mix.id)   : mix.id;
    const resolvedName = isNew ? (nameInput ? nameInput.value : mix.name) : mix.name;

    const profile = {
      id:             resolvedId,
      name:           resolvedName,
      enabled:        mix.enabled !== false,
      auto_generated: false,
      schedule: {
        cadence:  cadenceSel.value,
        run_day:  daySel.value,
        run_hour: parseInt(hourSel.value, 10),
      },
      count:     parseInt(countInput.value, 10) || 30,
      cap:       parseInt(capInput.value, 10)   || 100,
      new_ratio: parseInt(blendSlider.value, 10) / 100,
      seeds: {
        mode:     modeSel.value,
        genres:   modeSel.value === 'genre'    ? [...genres]          : [],
        artists:  (mix.seeds || {}).artists    || [],
        playlist: modeSel.value === 'playlist' ? playlistInput.value : '',
      },
      quality: mix.quality || {},
    };

    try {
      await API('/mixes', {
        method:  'POST',
        headers: {'Content-Type': 'application/json'},
        body:    JSON.stringify(profile),
      });
      statusLine.textContent = 'Saved!';
      setTimeout(() => renderMixes(), 500);
    } catch(e) {
      if (e.body && e.body.errors) {
        const errMsg = Object.entries(e.body.errors).map(([k, v]) => k + ': ' + v).join('; ');
        statusLine.textContent = 'Errors: ' + errMsg;
      } else {
        statusLine.textContent = 'Save failed: ' + (e.message || 'unknown');
      }
    } finally {
      saveBtn.disabled = false;
    }
  };
  actions.appendChild(saveBtn);

  // DELETE button (not for new cards)
  if (!isNew) {
    const delBtn = document.createElement('button');
    delBtn.className = 'btn del';
    delBtn.textContent = '✕';
    delBtn.onclick = async () => {
      const label = mix.name || mix.id;
      if (!confirm('Delete ' + label + '?')) return;
      try {
        await API('/mixes/' + mix.id, {method: 'DELETE'});
        renderMixes();
      } catch(e) {
        statusLine.style.display = '';
        statusLine.textContent = 'Delete failed: ' + (e.message || 'unknown');
      }
    };
    actions.appendChild(delBtn);
  }

  inner.appendChild(actions);
  body.appendChild(inner);
  card.appendChild(body);
  return card;
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Register all screens
  screens['mixes']   = {el: document.getElementById('s-mixes'),   render: renderMixes};
  screens['library'] = {el: document.getElementById('s-library'), render: () => {}};
  screens['search']  = {el: document.getElementById('s-search'),  render: () => {}};
  screens['setup']   = {el: document.getElementById('s-setup'),   render: () => {}};

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
