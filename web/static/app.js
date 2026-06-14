// SIGNAL app.js — hash router + fetch helper + clock
const API = (path, opts) => fetch(path, opts).then(async r => {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.status), {body: j, status: r.status});
  return j;
});

const screens = {};  // name -> {el, render}
let _currentScreen = null;

function show(name) {
  if (name === _currentScreen) return;
  _currentScreen = name;
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('on'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('on'));
  const screenEl = document.getElementById('s-' + name);
  const navEl = document.getElementById('nav-' + name);
  if (!screenEl || !navEl) return;
  screenEl.classList.add('on');
  navEl.classList.add('on');
  if (location.hash.slice(1) !== name) location.hash = name;
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
  const lastRuns = data.last_runs || {};

  mixes.forEach(mix => {
    el.appendChild(buildMixCard(mix, nextRuns, false, lastRuns));
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

function isFresh(mix, lastRuns) {
  const lr = lastRuns && lastRuns[mix.id];
  if (!lr) return false;
  const lastMs = new Date(lr).getTime();
  const nowMs = Date.now();
  const cadence = (mix.schedule || {}).cadence === 'weekly' ? 7 * 24 * 3600 * 1000 : 24 * 3600 * 1000;
  return (nowMs - lastMs) < cadence;
}

function buildMixCard(mix, nextRuns, isNew, lastRuns) {
  const fresh = !isNew && isFresh(mix, lastRuns);
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
        const result = await API('/mixes/' + encodeURIComponent(mix.id) + '/run', {method: 'POST'});
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
        await API('/mixes/' + encodeURIComponent(mix.id), {method: 'DELETE'});
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

// ── Library screen ────────────────────────────────────────────────────────────

// Hoisted to module scope so timers survive across re-renders and can be cleared
let enrichPollTimer = null;
let repairPollTimer = null;

async function renderLibrary() {
  if (enrichPollTimer) { clearInterval(enrichPollTimer); enrichPollTimer = null; }
  if (repairPollTimer) { clearInterval(repairPollTimer); repairPollTimer = null; }

  const el = document.getElementById('s-library');
  el.textContent = '';

  // ── Card builder ──────────────────────────────────────────────────────────────
  function makeToolCard(title, desc, goLabel, goHandler) {
    const tool = document.createElement('div');
    tool.className = 'tool';
    const nameEl = document.createElement('div');
    nameEl.className = 't-name';
    const b = document.createElement('b');
    b.textContent = title;
    const span = document.createElement('span');
    span.textContent = desc;
    nameEl.appendChild(b);
    nameEl.appendChild(span);
    const btn = document.createElement('button');
    btn.className = 'go';
    btn.textContent = goLabel;
    btn.onclick = goHandler;
    tool.appendChild(nameEl);
    tool.appendChild(btn);
    tool._statusSpan = span;
    tool._btn = btn;
    return tool;
  }

  function makeBar() {
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.display = 'none';
    const inner = document.createElement('i');
    inner.style.width = '0%';
    bar.appendChild(inner);
    bar._inner = inner;
    return bar;
  }

  // ── 1. Enrich metadata ────────────────────────────────────────────────────────
  const enrichCard = makeToolCard(
    'Enrich metadata',
    'add Last.fm genre tags to your MP3s',
    'run',
    async () => {
      enrichCard._btn.disabled = true;
      try { await API('/library/enrich', {method: 'POST'}); pollEnrich(); }
      catch(e) { enrichCard._statusSpan.textContent = 'Error: ' + (e.message || 'unknown'); enrichCard._btn.disabled = false; }
    }
  );
  const enrichBar = makeBar();
  enrichCard.querySelector('.t-name').appendChild(enrichBar);
  el.appendChild(enrichCard);  // appended synchronously — no race

  function pollEnrich() {
    if (enrichPollTimer) clearInterval(enrichPollTimer);
    enrichPollTimer = setInterval(async () => {
      try {
        const s = await API('/library/enrich/status');
        if (s.status === 'running' || s.status === 'started') {
          enrichCard._btn.textContent = 'running';
          enrichBar.style.display = '';
          if (s.files_total && s.files_done !== undefined) {
            enrichBar._inner.style.width = Math.round(s.files_done / s.files_total * 100) + '%';
            enrichCard._statusSpan.textContent = s.files_done + ' / ' + s.files_total + ' files';
          } else { enrichCard._statusSpan.textContent = 'running…'; }
        } else {
          clearInterval(enrichPollTimer); enrichPollTimer = null;
          enrichCard._btn.disabled = false; enrichCard._btn.textContent = 'run';
          enrichBar.style.display = 'none';
          if (s.status === 'ok' && s.enriched !== undefined) {
            enrichCard._statusSpan.textContent = 'last run: ' + s.enriched + ' enriched';
          } else if (s.status === 'idle') {
            enrichCard._statusSpan.textContent = 'not run yet';
          } else {
            enrichCard._statusSpan.textContent = s.status + (s.reason ? ': ' + s.reason : '');
          }
        }
      } catch(e) {}
    }, 2000);
  }

  // ── 2. Repair library ─────────────────────────────────────────────────────────
  // Scans MP3s with missing artist tags, looks them up in MusicBrainz, writes the tag.
  const repairCard = makeToolCard(
    'Repair library',
    'fill missing artist tags via MusicBrainz',
    'run',
    async () => {
      repairCard._btn.disabled = true;
      try { await API('/library/repair', {method: 'POST'}); pollRepair(); }
      catch(e) { repairCard._statusSpan.textContent = 'Error: ' + (e.message || 'unknown'); repairCard._btn.disabled = false; }
    }
  );
  const repairBar = makeBar();
  repairCard.querySelector('.t-name').appendChild(repairBar);
  el.appendChild(repairCard);  // appended synchronously

  function pollRepair() {
    if (repairPollTimer) clearInterval(repairPollTimer);
    repairPollTimer = setInterval(async () => {
      try {
        const s = await API('/library/repair/status');
        if (s.status === 'running' || s.status === 'started') {
          repairCard._btn.textContent = 'running';
          repairBar.style.display = '';
          if (s.files_total && s.files_done !== undefined) {
            repairBar._inner.style.width = Math.round(s.files_done / s.files_total * 100) + '%';
          }
          repairCard._statusSpan.textContent = 'running…';
        } else {
          clearInterval(repairPollTimer); repairPollTimer = null;
          repairCard._btn.disabled = false; repairCard._btn.textContent = 'run';
          repairBar.style.display = 'none';
          if (s.status === 'ok' && s.fixed !== undefined) {
            repairCard._statusSpan.textContent = 'last run: ' + s.fixed + ' fixed';
          } else if (s.status === 'idle') {
            repairCard._statusSpan.textContent = 'not run yet';
          } else {
            repairCard._statusSpan.textContent = s.status + (s.reason ? ': ' + s.reason : '');
          }
        }
      } catch(e) {}
    }, 2000);
  }

  // ── 3. De-duplicate ───────────────────────────────────────────────────────────
  const dedupCard = makeToolCard('De-duplicate', 'find files with identical titles', 'run', async () => {
    dedupCard._btn.disabled = true;
    dedupCard._statusSpan.textContent = 'scanning…';
    try {
      const r = await API('/library/dedup/run', {method: 'POST'});
      const n = (r.would_delete || []).length;
      dedupCard._statusSpan.textContent = n ? n + ' duplicates found' : 'no duplicates';
    } catch(e) {
      dedupCard._statusSpan.textContent = 'Error: ' + (e.message || 'unknown');
    } finally {
      dedupCard._btn.disabled = false;
    }
  });
  el.appendChild(dedupCard);

  // ── 4. Title cleanup ──────────────────────────────────────────────────────────
  let suffixesExpanded = false;
  const suffixCard = makeToolCard('Title cleanup', 'strip junk suffixes from track titles', 'edit', () => {
    suffixesExpanded = !suffixesExpanded;
    suffixPanel.style.display = suffixesExpanded ? '' : 'none';
    suffixCard._btn.textContent = suffixesExpanded ? 'close' : 'edit';
    if (suffixesExpanded) loadSuffixes();
  });

  const suffixPanel = document.createElement('div');
  suffixPanel.style.display = 'none';
  suffixPanel.style.padding = '10px 14px';
  suffixPanel.style.borderTop = '1px solid var(--line)';
  const suffixTextarea = document.createElement('textarea');
  suffixTextarea.style.cssText = 'width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--txt);font-family:JetBrains Mono,monospace;font-size:.72rem;padding:8px;border-radius:3px;height:120px;resize:vertical';
  const suffixSaveBtn = document.createElement('button');
  suffixSaveBtn.className = 'btn ghost';
  suffixSaveBtn.style.marginTop = '8px';
  suffixSaveBtn.textContent = 'save';
  suffixSaveBtn.onclick = async () => {
    const lines = suffixTextarea.value.split('\n').map(s => s.trim()).filter(Boolean);
    suffixSaveBtn.disabled = true;
    try {
      await API('/library/suffixes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({suffixes: lines})});
      suffixCard._statusSpan.textContent = lines.length + ' rules';
    } catch(e) {
      suffixCard._statusSpan.textContent = 'save failed';
    } finally { suffixSaveBtn.disabled = false; }
  };
  suffixPanel.appendChild(suffixTextarea);
  suffixPanel.appendChild(suffixSaveBtn);

  const suffixWrapper = document.createElement('div');
  suffixWrapper.className = 'tool';
  suffixWrapper.style.cssText = 'flex-direction:column;align-items:stretch;padding:0';
  const suffixRow = document.createElement('div');
  suffixRow.style.cssText = 'display:flex;align-items:center;gap:12px;padding:15px 14px';
  suffixRow.appendChild(suffixCard.querySelector('.t-name'));
  suffixRow.appendChild(suffixCard.querySelector('.go'));
  suffixWrapper.appendChild(suffixRow);
  suffixWrapper.appendChild(suffixPanel);
  el.appendChild(suffixWrapper);  // appended synchronously

  // ── Async status checks (cards already in DOM, just update text) ─────────────
  async function loadSuffixes() {
    try {
      const r = await API('/library/suffixes');
      suffixTextarea.value = (r.suffixes || []).join('\n');
      suffixCard._statusSpan.textContent = (r.suffixes || []).length + ' rules';
    } catch(e) {}
  }
  loadSuffixes();

  try {
    const s = await API('/library/enrich/status');
    if (s.status === 'running' || s.status === 'started') { pollEnrich(); }
    else if (s.status === 'ok' && s.enriched !== undefined) { enrichCard._statusSpan.textContent = 'last run: ' + s.enriched + ' enriched'; }
    else if (s.status === 'idle') { enrichCard._statusSpan.textContent = 'not run yet'; }
  } catch(e) {}

  try {
    const s = await API('/library/repair/status');
    if (s.status === 'running' || s.status === 'started') { pollRepair(); }
    else if (s.status === 'ok' && s.fixed !== undefined) { repairCard._statusSpan.textContent = 'last run: ' + s.fixed + ' fixed'; }
    else if (s.status === 'idle') { repairCard._statusSpan.textContent = 'not run yet'; }
  } catch(e) {}
}

// ── Search screen ─────────────────────────────────────────────────────────────

async function renderSearch() {
  const el = document.getElementById('s-search');
  el.textContent = '';

  // Searchbox
  const box = document.createElement('div');
  box.className = 'searchbox';
  const input = document.createElement('input');
  input.placeholder = 'artist, track or paste URL…';
  const goBtn = document.createElement('button');
  goBtn.className = 'btn run';
  goBtn.style.flex = '0 0 auto';
  goBtn.textContent = 'GO';
  box.appendChild(input);
  box.appendChild(goBtn);
  el.appendChild(box);

  // Source pills
  const srcRow = document.createElement('div');
  srcRow.className = 'src-row';
  let activeFilter = 'all';
  const filterPills = {};
  ['all', 'soundcloud', 'youtube'].forEach(f => {
    const pill = document.createElement('span');
    pill.className = 'src' + (f === 'all' ? ' on' : '');
    pill.textContent = f;
    pill.onclick = () => {
      activeFilter = f;
      Object.values(filterPills).forEach(p => p.classList.remove('on'));
      pill.classList.add('on');
      applyFilter();
    };
    filterPills[f] = pill;
    srcRow.appendChild(pill);
  });
  el.appendChild(srcRow);

  // Artist chips container (SC users)
  const artistsEl = document.createElement('div');
  artistsEl.className = 'artists-row';
  artistsEl.style.display = 'none';
  el.appendChild(artistsEl);

  // Results container
  const resultsEl = document.createElement('div');
  el.appendChild(resultsEl);

  let allResults = [];

  function applyFilter() {
    resultsEl.textContent = '';
    const filtered = activeFilter === 'all' ? allResults : allResults.filter(r => r.source === activeFilter);
    filtered.forEach(r => resultsEl.appendChild(r.el));
  }

  function formatDur(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60);
    const s = String(Math.floor(sec % 60)).padStart(2, '0');
    return m + ':' + s;
  }

  function buildResultRow(item) {
    // item: {source:'sc'|'yt', title, artist, duration (seconds), url, artwork_url?}
    const row = document.createElement('div');
    row.className = 'result';

    const cover = document.createElement('div');
    cover.className = 'cover';
    if (item.artwork_url) {
      const img = document.createElement('img');
      img.src = item.artwork_url;
      img.alt = '';
      img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:3px';
      img.onerror = () => { img.remove(); cover.textContent = 'SC'; };
      cover.appendChild(img);
    } else {
      cover.textContent = item.source === 'sc' ? 'SC' : 'YT';
    }
    row.appendChild(cover);

    const meta = document.createElement('div');
    meta.className = 'r-meta';
    const title = document.createElement('b');
    title.textContent = item.title || '(untitled)';
    const sub = document.createElement('span');
    const rawArtist = item.artist || '';
    const displayArtist = rawArtist.length > 30 ? rawArtist.slice(0, 30) + '…' : rawArtist;
    const subParts = [displayArtist];
    if (item.duration) subParts.push(formatDur(item.duration));
    sub.textContent = subParts.filter(Boolean).join(' · ');
    meta.appendChild(title);
    meta.appendChild(sub);
    row.appendChild(meta);

    const getBtn = document.createElement('button');
    getBtn.className = 'get';
    getBtn.textContent = '+';
    getBtn.onclick = async () => {
      if (getBtn.classList.contains('have')) return;
      getBtn.disabled = true;
      getBtn.textContent = '…';
      try {
        await API('/acquire', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: item.url})});
        getBtn.textContent = '✓';
        getBtn.classList.add('have');
        getBtn.disabled = false;
      } catch(e) {
        if (e.status === 409) {
          getBtn.textContent = 'dup';
          getBtn.disabled = false;
          setTimeout(() => { getBtn.textContent = '+'; }, 3000);
        } else {
          getBtn.textContent = '!';
          getBtn.disabled = false;
          setTimeout(() => { getBtn.textContent = '+'; }, 2000);
        }
      }
    };
    row.appendChild(getBtn);

    return {el: row, source: item.source === 'sc' ? 'soundcloud' : 'youtube'};
  }

  function buildArtistChip(user) {
    const chip = document.createElement('div');
    chip.className = 'artist-chip';

    const av = document.createElement('div');
    av.className = 'a-av';
    if (user.avatar_url) {
      const img = document.createElement('img');
      img.src = user.avatar_url;
      img.alt = '';
      img.onerror = () => { img.style.display = 'none'; av.textContent = 'SC'; };
      av.appendChild(img);
    } else {
      av.textContent = 'SC';
    }

    const info = document.createElement('div');
    const name = document.createElement('span');
    name.className = 'a-name';
    name.textContent = user.full_name || user.username || '';
    const sub = document.createElement('span');
    sub.className = 'a-sub';
    const foll = user.followers_count ? (user.followers_count >= 1000 ? Math.round(user.followers_count / 1000) + 'k' : user.followers_count) + ' followers' : 'SC artist';
    sub.textContent = foll;
    info.appendChild(name);
    info.appendChild(sub);

    chip.appendChild(av);
    chip.appendChild(info);

    chip.onclick = async () => {
      artistsEl.style.display = 'none';
      resultsEl.textContent = '';
      allResults = [];
      const loading = document.createElement('div');
      loading.className = 'warn';
      loading.textContent = 'Loading tracks for ' + (user.username || '') + '…';
      resultsEl.appendChild(loading);
      try {
        const scUrl = 'https://soundcloud.com/' + encodeURIComponent(user.username);
        const data = await API('/sc/resolve?url=' + encodeURIComponent(scUrl));
        resultsEl.textContent = '';
        if (data.status === 'ok' && data.tracks && data.tracks.length) {
          data.tracks.forEach(t => {
            const item = {
              source: 'sc',
              title: t.title || '',
              artist: user.full_name || user.username || '',
              duration: t.duration_ms ? Math.round(t.duration_ms / 1000) : null,
              url: t.permalink_url || '',
              artwork_url: t.artwork_url || null,
            };
            if (item.url) allResults.push(buildResultRow(item));
          });
          applyFilter();
        } else {
          const none = document.createElement('div');
          none.className = 'warn';
          none.textContent = 'No tracks found for this artist.';
          resultsEl.appendChild(none);
        }
      } catch(e) {
        resultsEl.textContent = '';
        const err = document.createElement('div');
        err.className = 'warn';
        err.textContent = 'Failed to load artist tracks: ' + (e.message || 'unknown');
        resultsEl.appendChild(err);
      }
    };

    return chip;
  }

  async function doSearch(q) {
    artistsEl.textContent = '';
    artistsEl.style.display = 'none';
    resultsEl.textContent = '';
    allResults = [];

    const loadingMsg = document.createElement('div');
    loadingMsg.className = 'warn';
    loadingMsg.textContent = 'Searching…';
    resultsEl.appendChild(loadingMsg);

    // Parallel search — SC users, SC tracks, YT tracks
    const [scUsersRes, scRes, ytRes] = await Promise.allSettled([
      API('/sc/search/users?q=' + encodeURIComponent(q)),
      API('/sc/search/tracks?q=' + encodeURIComponent(q)),
      API('/yt/search?q=' + encodeURIComponent(q)),
    ]);

    resultsEl.textContent = '';
    allResults = [];

    if (scRes.status === 'rejected' && ytRes.status === 'rejected') {
      const errEl = document.createElement('div');
      errEl.className = 'warn';
      errEl.textContent = 'Search failed: ' + ((scRes.reason && scRes.reason.message) || 'network error');
      resultsEl.appendChild(errEl);
      return;
    }

    // SC artist chips
    if (scUsersRes.status === 'fulfilled' && scUsersRes.value.users && scUsersRes.value.users.length) {
      scUsersRes.value.users.forEach(u => artistsEl.appendChild(buildArtistChip(u)));
      artistsEl.style.display = '';
    }

    // SC tracks first — shape: {title, artist, permalink_url, duration_ms}
    if (scRes.status === 'fulfilled' && scRes.value.tracks) {
      scRes.value.tracks.forEach(t => {
        const item = {
          source: 'sc',
          title: t.title || '',
          artist: t.artist || '',
          duration: t.duration_ms ? Math.round(t.duration_ms / 1000) : null,
          url: t.permalink_url || '',
          artwork_url: t.artwork_url || null,
        };
        if (item.url) allResults.push(buildResultRow(item));
      });
    }

    // YT
    if (ytRes.status === 'fulfilled' && ytRes.value.results) {
      ytRes.value.results.forEach(r => {
        allResults.push(buildResultRow({
          source: 'yt',
          title: r.title,
          artist: r.artist,
          duration: r.duration,
          url: r.url,
        }));
      });
    }

    if (allResults.length === 0 && artistsEl.style.display === 'none') {
      const none = document.createElement('div');
      none.className = 'warn';
      none.textContent = 'No results.';
      resultsEl.appendChild(none);
      return;
    }

    applyFilter();
  }

  async function doAcquireUrl(url) {
    resultsEl.textContent = '';
    const msg = document.createElement('div');
    msg.className = 'warn';
    msg.textContent = 'Downloading…';
    resultsEl.appendChild(msg);
    try {
      const r = await API('/acquire', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url})});
      msg.textContent = r.status === 'ok' ? ('Downloaded: ' + (r.path || url)) : ('Error: ' + (r.path || 'unknown'));
    } catch(e) {
      if (e.status === 409) {
        msg.textContent = 'Already downloading this URL.';
      } else if (e.status === 400) {
        msg.textContent = 'Unsupported URL. Only YouTube and SoundCloud URLs are supported.';
      } else {
        msg.textContent = 'Error: ' + (e.message || 'unknown');
      }
    }
  }

  function onGo() {
    const q = input.value.trim();
    if (!q) return;
    if (/^https?:\/\//.test(q)) {
      doAcquireUrl(q);
    } else {
      doSearch(q);
    }
  }

  goBtn.onclick = onGo;
  input.onkeydown = e => { if (e.key === 'Enter') onGo(); };
}

// ── Setup screen ──────────────────────────────────────────────────────────────

async function renderSetup() {
  const el = document.getElementById('s-setup');
  el.textContent = '';

  let schema, values;
  try {
    const data = await API('/settings');
    schema = data.schema;
    values = data.values;
  } catch(e) {
    const err = document.createElement('div');
    err.className = 'warn';
    err.textContent = 'Failed to load settings: ' + (e.message || 'unknown');
    el.appendChild(err);
    return;
  }

  // Group entries by group name
  const groups = {};
  const groupOrder = [];
  schema.forEach(entry => {
    if (!groups[entry.group]) {
      groups[entry.group] = [];
      groupOrder.push(entry.group);
    }
    groups[entry.group].push(entry);
  });

  // Credentials warning banner
  const hasCredentials = groupOrder.includes('Credentials');
  if (hasCredentials) {
    const warn = document.createElement('div');
    warn.className = 'warn';
    warn.textContent = '⚠ no auth on this page — anyone on your network can change these.';
    el.appendChild(warn);
  }

  let firstGroup = true;

  groupOrder.forEach((groupName) => {
    const entries = groups[groupName];
    const group = document.createElement('div');
    group.className = 'sgroup' + (firstGroup ? ' open' : '');
    firstGroup = false;

    // Header
    const h3 = document.createElement('h3');
    const h3Title = document.createElement('span');
    h3Title.textContent = groupName;
    const h3Arrow = document.createElement('span');
    h3Arrow.textContent = group.classList.contains('open') ? '▾' : '▸';
    h3.appendChild(h3Title);
    h3.appendChild(h3Arrow);
    h3.onclick = () => {
      group.classList.toggle('open');
      h3Arrow.textContent = group.classList.contains('open') ? '▾' : '▸';
    };
    group.appendChild(h3);

    // Rows container
    const rows = document.createElement('div');
    rows.className = 'rows';

    // Track original values and input elements for diffing
    const origValues = {};
    const inputs = {};

    entries.forEach(entry => {
      const path = entry.path;
      const rawVal = values[path];

      const row = document.createElement('div');
      row.className = 'srow';

      const labelDiv = document.createElement('label');
      const labelText = document.createElement('span');
      labelText.textContent = entry.label;
      labelDiv.appendChild(labelText);
      if (entry.hint) {
        const hint = document.createElement('span');
        hint.className = 'hint';
        hint.textContent = entry.hint;
        labelDiv.appendChild(hint);
      }
      row.appendChild(labelDiv);

      if (entry.type === 'bool') {
        const sw = document.createElement('span');
        sw.className = 'sw' + (rawVal ? ' on' : '');
        sw.onclick = () => sw.classList.toggle('on');
        inputs[path] = {
          el: sw,
          getValue: () => sw.classList.contains('on'),
        };
        origValues[path] = !!rawVal;
        row.appendChild(sw);
      } else if (entry.type === 'secret') {
        const inp = document.createElement('input');
        inp.type = 'password';
        inp.placeholder = '(unchanged)';
        inp.value = '';
        inputs[path] = {
          el: inp,
          getValue: () => inp.value,
        };
        origValues[path] = '';
        row.appendChild(inp);
      } else if (entry.type === 'list[str]') {
        const inp = document.createElement('textarea');
        inp.style.cssText = 'background:var(--panel2);border:1px solid var(--line);color:var(--txt);font-family:JetBrains Mono,monospace;font-size:.7rem;padding:8px;border-radius:3px;width:140px;height:60px;resize:vertical';
        const listVal = Array.isArray(rawVal) ? rawVal.join('\n') : (rawVal || '');
        inp.value = listVal;
        inputs[path] = {
          el: inp,
          getValue: () => inp.value.split('\n').map(s => s.trim()).filter(Boolean),
        };
        origValues[path] = listVal;
        row.appendChild(inp);
      } else if (entry.type === 'int') {
        const inp = document.createElement('input');
        inp.type = 'number';
        if (entry.min != null) inp.min = entry.min;
        if (entry.max != null) inp.max = entry.max;
        const displayVal = rawVal != null ? String(rawVal) : '';
        inp.value = displayVal;
        inputs[path] = {
          el: inp,
          getValue: () => {
            const v = parseInt(inp.value, 10);
            return isNaN(v) ? inp.value : v;
          },
        };
        origValues[path] = rawVal != null ? rawVal : '';
        row.appendChild(inp);
      } else {
        // str
        const inp = document.createElement('input');
        inp.type = 'text';
        const displayVal = rawVal != null && rawVal !== undefined ? String(rawVal) : '';
        inp.value = displayVal;
        inputs[path] = {
          el: inp,
          getValue: () => inp.value,
        };
        origValues[path] = displayVal;
        row.appendChild(inp);
      }

      rows.appendChild(row);
    });

    // Save button for this group
    const saveBtn = document.createElement('button');
    saveBtn.className = 'btn ghost';
    saveBtn.style.margin = '8px 0 4px';
    saveBtn.textContent = 'save';

    const groupStatus = document.createElement('div');
    groupStatus.className = 'warn';
    groupStatus.style.display = 'none';

    saveBtn.onclick = async () => {
      // Changed-fields-only diffing
      const changed = {};
      entries.forEach(entry => {
        const path = entry.path;
        const inp = inputs[path];
        if (!inp) return;
        const current = inp.getValue();
        if (entry.type === 'secret') {
          // Only include if user typed something
          if (current !== '') changed[path] = current;
        } else if (entry.type === 'bool') {
          if (current !== origValues[path]) changed[path] = current;
        } else if (entry.type === 'list[str]') {
          const currentStr = Array.isArray(current) ? current.join('\n') : current;
          if (currentStr !== origValues[path]) changed[path] = current;
        } else if (entry.type === 'int') {
          if (JSON.stringify(current) !== JSON.stringify(origValues[path])) changed[path] = current;
        } else {
          if (current !== origValues[path]) changed[path] = current;
        }
      });

      if (Object.keys(changed).length === 0) {
        groupStatus.style.display = '';
        groupStatus.textContent = 'No changes.';
        setTimeout(() => { groupStatus.style.display = 'none'; }, 2000);
        return;
      }

      saveBtn.disabled = true;
      groupStatus.style.display = '';
      groupStatus.textContent = 'Saving…';

      try {
        const result = await API('/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(changed)});
        groupStatus.textContent = 'Saved: ' + (result.updated || []).join(', ');
        // Update origValues for non-secret fields
        entries.forEach(entry => {
          if (entry.type !== 'secret' && changed[entry.path] !== undefined) {
            if (entry.type === 'list[str]') {
              origValues[entry.path] = Array.isArray(changed[entry.path]) ? changed[entry.path].join('\n') : changed[entry.path];
            } else if (entry.type === 'int') {
              origValues[entry.path] = changed[entry.path];
            } else {
              origValues[entry.path] = String(changed[entry.path]);
            }
          }
        });
      } catch(e) {
        if (e.body && e.body.fields) {
          groupStatus.textContent = 'Errors: ' + Object.entries(e.body.fields).map(([k, v]) => k + ': ' + v).join('; ');
        } else {
          groupStatus.textContent = 'Save failed: ' + (e.message || 'unknown');
        }
      } finally {
        saveBtn.disabled = false;
      }
    };

    rows.appendChild(groupStatus);
    rows.appendChild(saveBtn);
    group.appendChild(rows);
    el.appendChild(group);
  });
}

// ── Follows screen ────────────────────────────────────────────────────────────

function updateFollowsBadge(count) {
  const badge = document.getElementById('follows-badge');
  if (!badge) return;
  if (count && count > 0) {
    badge.textContent = String(count);
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

async function renderFollows() {
  const el = document.getElementById('s-follows');
  el.textContent = '';

  // ── Section header helper ──
  function makeSection(title) {
    const sec = document.createElement('div');
    sec.className = 'follows-section';
    const h = document.createElement('div');
    h.className = 'follows-heading';
    h.textContent = title;
    sec.appendChild(h);
    return sec;
  }

  // ── Status line helper ──
  function makeStatus() {
    const s = document.createElement('div');
    s.className = 'warn';
    s.style.display = 'none';
    return s;
  }

  function showStatus(s, msg) {
    s.textContent = msg;
    s.style.display = '';
  }

  function hideStatus(s) {
    s.style.display = 'none';
  }

  // ═══════════════════════════════════════════════════════
  // 1. SEARCH section
  // ═══════════════════════════════════════════════════════
  const searchSec = makeSection('Search artists');
  el.appendChild(searchSec);

  const searchBox = document.createElement('div');
  searchBox.className = 'searchbox';
  const searchInput = document.createElement('input');
  searchInput.placeholder = 'artist name…';
  const searchBtn = document.createElement('button');
  searchBtn.className = 'btn run';
  searchBtn.style.flex = '0 0 auto';
  searchBtn.textContent = 'Search';
  searchBox.appendChild(searchInput);
  searchBox.appendChild(searchBtn);
  searchSec.appendChild(searchBox);

  const searchStatus = makeStatus();
  searchSec.appendChild(searchStatus);

  const searchResults = document.createElement('div');
  searchSec.appendChild(searchResults);

  async function doArtistSearch() {
    const q = searchInput.value.trim();
    if (!q) return;
    searchResults.textContent = '';
    searchBtn.disabled = true;
    showStatus(searchStatus, 'Searching…');
    let data;
    try {
      data = await API('/follow/search?q=' + encodeURIComponent(q));
      hideStatus(searchStatus);
    } catch(e) {
      showStatus(searchStatus, 'Search failed: ' + (e.message || 'unknown'));
      searchBtn.disabled = false;
      return;
    }
    searchBtn.disabled = false;
    const results = data.results || [];
    if (!results.length) {
      showStatus(searchStatus, 'No results.');
      return;
    }
    results.forEach(artist => {
      const row = document.createElement('div');
      row.className = 'follows-row';

      const info = document.createElement('div');
      info.className = 'follows-info';

      const name = document.createElement('span');
      name.className = 'follows-name';
      name.textContent = artist.name || '';
      info.appendChild(name);

      if (artist.disambiguation) {
        const dis = document.createElement('span');
        dis.className = 'follows-dis';
        dis.textContent = artist.disambiguation;
        info.appendChild(dis);
      }

      row.appendChild(info);

      const followBtn = document.createElement('button');
      followBtn.className = 'btn ghost';
      followBtn.style.flex = '0 0 auto';
      followBtn.textContent = 'Follow';
      followBtn.onclick = async () => {
        followBtn.disabled = true;
        followBtn.textContent = '…';
        try {
          await API('/follow', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              mbid: artist.mbid,
              name: artist.name,
              disambiguation: artist.disambiguation || '',
            }),
          });
          followBtn.textContent = '✓';
          loadFollowedList();
        } catch(e) {
          followBtn.textContent = '!';
          followBtn.disabled = false;
          setTimeout(() => { followBtn.textContent = 'Follow'; followBtn.disabled = false; }, 2000);
        }
      };
      row.appendChild(followBtn);
      searchResults.appendChild(row);
    });
  }

  searchBtn.onclick = doArtistSearch;
  searchInput.onkeydown = e => { if (e.key === 'Enter') doArtistSearch(); };

  // ═══════════════════════════════════════════════════════
  // 2. FOLLOWED ARTISTS section
  // ═══════════════════════════════════════════════════════
  const followedSec = makeSection('Following');
  el.appendChild(followedSec);

  const followedStatus = makeStatus();
  followedSec.appendChild(followedStatus);

  const followedList = document.createElement('div');
  followedSec.appendChild(followedList);

  async function loadFollowedList() {
    followedList.textContent = '';
    let data;
    try {
      data = await API('/follow');
    } catch(e) {
      showStatus(followedStatus, 'Failed to load: ' + (e.message || 'unknown'));
      return;
    }
    hideStatus(followedStatus);
    const artists = data.artists || [];
    if (!artists.length) {
      showStatus(followedStatus, 'Not following anyone yet.');
      return;
    }
    artists.forEach(artist => {
      const row = document.createElement('div');
      row.className = 'follows-row';

      const info = document.createElement('div');
      info.className = 'follows-info';

      const name = document.createElement('span');
      name.className = 'follows-name';
      name.textContent = artist.name || '';
      info.appendChild(name);

      if (artist.disambiguation) {
        const dis = document.createElement('span');
        dis.className = 'follows-dis';
        dis.textContent = artist.disambiguation;
        info.appendChild(dis);
      }

      row.appendChild(info);

      const unfollowBtn = document.createElement('button');
      unfollowBtn.className = 'btn del';
      unfollowBtn.style.flex = '0 0 auto';
      unfollowBtn.textContent = 'Unfollow';
      unfollowBtn.onclick = async () => {
        unfollowBtn.disabled = true;
        try {
          await API('/follow/' + encodeURIComponent(artist.mbid), {method: 'DELETE'});
          loadFollowedList();
        } catch(e) {
          unfollowBtn.disabled = false;
          showStatus(followedStatus, 'Unfollow failed: ' + (e.message || 'unknown'));
        }
      };
      row.appendChild(unfollowBtn);
      followedList.appendChild(row);
    });
  }

  // ═══════════════════════════════════════════════════════
  // 3. NEW RELEASES feed section
  // ═══════════════════════════════════════════════════════
  const feedSec = makeSection('New releases');
  el.appendChild(feedSec);

  const runStatus = makeStatus();
  feedSec.appendChild(runStatus);

  const runBtn = document.createElement('button');
  runBtn.className = 'btn run';
  runBtn.style.marginBottom = '10px';
  runBtn.textContent = '▶ run now';
  runBtn.onclick = async () => {
    runBtn.disabled = true;
    runBtn.textContent = '…';
    showStatus(runStatus, 'Running…');
    try {
      const r = await API('/follow/run', {method: 'POST'});
      if (r.status === 'disabled') {
        showStatus(runStatus, 'Disabled — Navidrome credentials missing.');
      } else {
        showStatus(runStatus, 'Done. acquired: ' + (r.acquired || 0) + ', unavailable: ' + (r.unavailable || 0));
        loadFeed();
      }
    } catch(e) {
      showStatus(runStatus, 'Run failed: ' + (e.message || 'unknown'));
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = '▶ run now';
    }
  };
  feedSec.appendChild(runBtn);

  const feedList = document.createElement('div');
  feedSec.appendChild(feedList);

  async function loadFeed() {
    feedList.textContent = '';
    let data;
    try {
      data = await API('/follow/feed');
    } catch(e) {
      const err = document.createElement('div');
      err.className = 'warn';
      err.textContent = 'Failed to load feed: ' + (e.message || 'unknown');
      feedList.appendChild(err);
      return;
    }
    const items = data.feed || [];
    updateFollowsBadge(0);
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'warn';
      empty.textContent = 'No releases yet — follow some artists and run.';
      feedList.appendChild(empty);
      return;
    }
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'follows-row';

      const info = document.createElement('div');
      info.className = 'follows-info';

      const title = document.createElement('span');
      title.className = 'follows-name';
      title.textContent = (item.artist || '') + ' – ' + (item.title || '');
      info.appendChild(title);

      const meta = document.createElement('span');
      meta.className = 'follows-dis';
      const metaParts = [];
      if (item.release_date) metaParts.push(item.release_date);
      if (item.primary_type) metaParts.push(item.primary_type);
      meta.textContent = metaParts.join(' · ');
      info.appendChild(meta);

      row.appendChild(info);

      const chip = document.createElement('span');
      chip.className = 'chip feed-chip' + (item.status === 'acquired' ? ' chip-acquired' : ' chip-unavail');
      chip.textContent = item.status || '';
      row.appendChild(chip);

      feedList.appendChild(row);
    });
  }

  // ═══════════════════════════════════════════════════════
  // 4. SETTINGS section
  // ═══════════════════════════════════════════════════════
  const settingsSec = makeSection('Settings');
  el.appendChild(settingsSec);

  // Load current follow state to pre-fill
  let followState = {};
  try {
    const d = await API('/follow');
    followState = d.state || {};
  } catch(e) { /* ignore */ }

  function makeSettingRow(labelText, inputEl) {
    const row = document.createElement('div');
    row.className = 'srow';
    const label = document.createElement('label');
    label.textContent = labelText;
    row.appendChild(label);
    row.appendChild(inputEl);
    return row;
  }

  const settingsRows = document.createElement('div');
  settingsRows.style.borderTop = '1px solid var(--line)';
  settingsRows.style.paddingTop = '4px';

  // run_hour
  const runHourInput = document.createElement('input');
  runHourInput.type = 'number';
  runHourInput.min = '0';
  runHourInput.max = '23';
  runHourInput.value = '4';
  runHourInput.style.cssText = 'background:var(--panel2);border:1px solid var(--line);color:var(--txt);font-family:JetBrains Mono,monospace;font-size:.7rem;padding:8px;border-radius:3px;width:80px;text-align:right';
  settingsRows.appendChild(makeSettingRow('run hour (0–23)', runHourInput));

  // lookback_days
  const lookbackInput = document.createElement('input');
  lookbackInput.type = 'number';
  lookbackInput.min = '1';
  lookbackInput.value = '7';
  lookbackInput.style.cssText = runHourInput.style.cssText;
  settingsRows.appendChild(makeSettingRow('lookback days', lookbackInput));

  // default_backfill_days
  const backfillInput = document.createElement('input');
  backfillInput.type = 'number';
  backfillInput.min = '0';
  backfillInput.value = '30';
  backfillInput.style.cssText = runHourInput.style.cssText;
  settingsRows.appendChild(makeSettingRow('backfill days', backfillInput));

  // playlist_cap
  const capInput = document.createElement('input');
  capInput.type = 'number';
  capInput.min = '1';
  capInput.value = '100';
  capInput.style.cssText = runHourInput.style.cssText;
  settingsRows.appendChild(makeSettingRow('playlist cap', capInput));

  // webhook_url
  const webhookInput = document.createElement('input');
  webhookInput.type = 'text';
  webhookInput.placeholder = 'https://…';
  webhookInput.style.cssText = 'background:var(--panel2);border:1px solid var(--line);color:var(--txt);font-family:JetBrains Mono,monospace;font-size:.7rem;padding:8px;border-radius:3px;width:140px;text-align:right';
  settingsRows.appendChild(makeSettingRow('webhook URL', webhookInput));

  // ntfy_topic
  const ntfyInput = document.createElement('input');
  ntfyInput.type = 'text';
  ntfyInput.placeholder = 'my-topic';
  ntfyInput.style.cssText = webhookInput.style.cssText;
  settingsRows.appendChild(makeSettingRow('ntfy topic', ntfyInput));

  settingsSec.appendChild(settingsRows);

  // Load current settings from /follow
  async function loadSettings() {
    try {
      const d = await API('/follow');
      // /follow doesn't return settings — we rely on defaults already shown
      // next_run is in d.state
      const st = d.state || {};
      if (st.next_run) {
        const nextRunInfo = document.createElement('div');
        nextRunInfo.className = 'warn';
        nextRunInfo.style.marginTop = '8px';
        nextRunInfo.textContent = 'next run: ' + st.next_run.slice(0, 16).replace('T', ' ');
        settingsRows.appendChild(nextRunInfo);
      }
    } catch(e) { /* ignore */ }
  }
  loadSettings();

  const settingsStatus = makeStatus();
  settingsSec.appendChild(settingsStatus);

  const saveSettingsBtn = document.createElement('button');
  saveSettingsBtn.className = 'btn ghost';
  saveSettingsBtn.style.marginTop = '8px';
  saveSettingsBtn.textContent = 'save settings';
  saveSettingsBtn.onclick = async () => {
    saveSettingsBtn.disabled = true;
    showStatus(settingsStatus, 'Saving…');
    const body = {
      run_hour: parseInt(runHourInput.value, 10) || 4,
      lookback_days: parseInt(lookbackInput.value, 10) || 7,
      default_backfill_days: parseInt(backfillInput.value, 10) || 30,
      playlist_cap: parseInt(capInput.value, 10) || 100,
      notify: {
        webhook_url: webhookInput.value.trim(),
        ntfy_topic: ntfyInput.value.trim(),
      },
    };
    try {
      await API('/follow/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      showStatus(settingsStatus, 'Saved.');
    } catch(e) {
      showStatus(settingsStatus, 'Save failed: ' + (e.message || 'unknown'));
    } finally {
      saveSettingsBtn.disabled = false;
    }
  };
  settingsSec.appendChild(saveSettingsBtn);

  // ═══════════════════════════════════════════════════════
  // Initial data load
  // ═══════════════════════════════════════════════════════
  loadFollowedList();

  // Mark feed as seen and load it
  try { await API('/follow/feed/seen', {method: 'POST'}); } catch(e) { /* ignore */ }
  updateFollowsBadge(0);
  loadFeed();
}

async function initFollowsBadge() {
  try {
    const d = await API('/follow');
    const count = (d.state || {}).unseen_count || 0;
    updateFollowsBadge(count);
  } catch(e) { /* ignore */ }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Register all screens
  screens['mixes']   = {el: document.getElementById('s-mixes'),   render: renderMixes};
  screens['library'] = {el: document.getElementById('s-library'), render: renderLibrary};
  screens['search']  = {el: document.getElementById('s-search'),  render: renderSearch};
  screens['follows'] = {el: document.getElementById('s-follows'), render: renderFollows};
  screens['setup']   = {el: document.getElementById('s-setup'),   render: renderSetup};

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

  // Follows badge — fetch unseen count on init
  initFollowsBadge();

  // Initial screen
  const initial = location.hash.slice(1);
  show(screens[initial] ? initial : 'mixes');
});
