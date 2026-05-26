let selectedM3U = "";

// Brief success badge — shown when the server returns HTTP 200
function flashOk() {
  const t = document.getElementById('okToast');
  if (!t) return;
  t.classList.add('show');
  clearTimeout(flashOk._h);
  flashOk._h = setTimeout(() => t.classList.remove('show'), 1200);
}

// Storage names are always extension-less ("test", not "test.m3u"). The
// server's write_m3u tolerates either form, but normalizing in the extension
// keeps the UI clean and stops dedup from treating "test" and "test.m3u" as
// different entries.
function stripExt(name) {
  return String(name || '').replace(/\.m3u$/i, '');
}

// One-time migrate any legacy entries that still carry the .m3u suffix, then render.
document.addEventListener('DOMContentLoaded', () => {
  chrome.storage.local.get({ m3uList: [] }, (result) => {
    const cleaned = [...new Set(result.m3uList.map(stripExt).filter(Boolean))];
    const changed = cleaned.length !== result.m3uList.length
                 || cleaned.some((n, i) => n !== result.m3uList[i]);
    if (changed) {
      chrome.storage.local.set({ m3uList: cleaned }, renderList);
    } else {
      renderList();
    }
  });
});

// ── Pull playlists from Navidrome via server proxy ─────────────────────────
//
// Server exposes GET /playlists which returns {"status":"ok","playlists":[{name,songCount}...]}.
// We merge names into the local m3uList, skipping exact dupes and flagging
// case-only mismatches (the bug that bit us earlier with Test vs test).

function showSyncStatus(kind, text) {
  const el = document.getElementById('syncStatus');
  if (!el) return;
  const palette = {
    ok:      { bg: '#d4edda', fg: '#155724' },
    err:     { bg: '#f8d7da', fg: '#721c24' },
    pending: { bg: '#eef',    fg: '#345'    },
    warn:    { bg: '#fff3cd', fg: '#856404' },
  };
  const p = palette[kind] || palette.pending;
  el.style.display = 'block';
  el.style.background = p.bg;
  el.style.color = p.fg;
  el.innerHTML = text;  // text is built by caller; we trust it
}

document.getElementById('syncBtn').addEventListener('click', () => {
  chrome.storage.sync.get(['lanUrl'], (res) => {
    const target = res.lanUrl || 'http://localhost:5000';
    showSyncStatus('pending', 'Asking server for Navidrome playlists…');

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);

    fetch(target.replace(/\/$/, '') + '/playlists', {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    })
      .then(async (resp) => {
        clearTimeout(timer);

        if (resp.status === 404) {
          showSyncStatus('err',
            'Server returned 404 for <code>/playlists</code> — restart <code>start.sh</code> ' +
            'after pulling the latest server code.');
          return;
        }
        let body;
        try { body = await resp.json(); } catch (e) {
          showSyncStatus('err', 'Server response was not JSON.');
          return;
        }
        if (!resp.ok || body.status !== 'ok') {
          showSyncStatus('err', 'Server: ' + (body.error || resp.statusText));
          return;
        }

        const remote = (body.playlists || []).map(p => stripExt(p.name)).filter(Boolean);
        if (remote.length === 0) {
          showSyncStatus('warn', 'Navidrome reports no playlists.');
          return;
        }

        chrome.storage.local.get({ m3uList: [] }, (stored) => {
          const localList = stored.m3uList.slice();
          const localLowerToOriginal = new Map(
            localList.map(n => [n.toLowerCase(), n])
          );

          const added = [];
          const present = [];
          const caseDiffers = [];

          for (const name of remote) {
            const lower = name.toLowerCase();
            if (localLowerToOriginal.has(lower)) {
              const localName = localLowerToOriginal.get(lower);
              if (localName === name) {
                present.push(name);
              } else {
                caseDiffers.push({ remote: name, local: localName });
              }
            } else {
              added.push(name);
              localList.push(name);
              localLowerToOriginal.set(lower, name);
            }
          }

          chrome.storage.local.set({ m3uList: localList }, () => {
            renderList();
            const parts = [
              `<strong>Added ${added.length}</strong>`,
              `${present.length} already in list`,
            ];
            if (caseDiffers.length) {
              const diffs = caseDiffers
                .map(d => `${d.local} ↔ ${d.remote}`)
                .join(', ');
              parts.push(`<strong>${caseDiffers.length} case-differs</strong>: ${diffs}`);
            }
            showSyncStatus(
              caseDiffers.length ? 'warn' : 'ok',
              parts.join(' · ')
            );
          });
        });
      })
      .catch((err) => {
        clearTimeout(timer);
        const msg = err && err.name === 'AbortError'
          ? 'No response within 8 seconds.'
          : (err && err.message) || String(err);
        showSyncStatus('err', 'Connection failed: ' + msg);
      });
  });
});

// Datei zur Liste hinzufügen
document.getElementById('addBtn').addEventListener('click', () => {
  const input = document.getElementById('m3uInput');
  const val = stripExt(input.value.trim());
  if (!val) return;

  chrome.storage.local.get({m3uList: []}, (result) => {
    const list = result.m3uList;
    if (!list.includes(val)) {
      list.push(val);
      chrome.storage.local.set({m3uList: list}, renderList);
    }
    input.value = "";
  });
});

function renderList() {
  const container = document.getElementById('listContainer');
  container.innerHTML = "";
  
  chrome.storage.local.get({m3uList: []}, (result) => {
    result.m3uList.forEach(name => {
      const div = document.createElement('div');
      div.className = "item";
      
      // Radio-Button zur Auswahl (Toggle-Logik)
      div.innerHTML = `
        <label>
          <input type="radio" name="m3u" value="${name}"> ${name}
        </label>
        <button class="delete-btn" data-name="${name}">X</button>
      `;
      
      div.querySelector('.delete-btn').onclick = (e) => deleteItem(e.target.dataset.name);
      container.appendChild(div);
    });
  });
}

function deleteItem(name) {
  chrome.storage.local.get({m3uList: []}, (result) => {
    const newList = result.m3uList.filter(item => item !== name);
    chrome.storage.local.set({m3uList: newList}, renderList);
  });
}

// Senden an den Server
document.getElementById('sendBtn').addEventListener('click', () => {
  const selected = document.querySelector('input[name="m3u"]:checked');

  chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
    if (!tabs || !tabs[0]) {
      alert('Aktive Seite nicht gefunden.');
      return;
    }

    const currentUrl = tabs[0].url || '';

    chrome.storage.sync.get(['lanUrl'], (res) => {
      const target = res.lanUrl || "http://localhost:5000";
      const payload = { url: currentUrl };
      if (selected && selected.value) payload.m3u = selected.value;

      console.log('Diagnostic: attempting GET to', target);
      // Diagnostic GET to verify reachability and CORS
      fetch(target, { method: 'GET' })
        .then(gresp => {
          console.log('Diagnostic GET response', gresp.status, gresp.statusText);

          console.log('Sending POST to', target, payload);
          return fetch(target, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
        })
        .then(resp => {
          if (!resp.ok) throw new Error('Server antwortete mit ' + resp.status);
          flashOk();
        })
        .catch(err => {
          console.error('Send failed', err && err.name, err && err.message, err);

          // If this looks like a network failure, try switching localhost <-> 127.0.0.1 and retry once
          const isNetworkErr = (err && (err.message && err.message.toLowerCase().includes('failed to fetch'))) || (err instanceof TypeError);
          if (isNetworkErr) {
            let altTarget = null;
            try {
              if (target.includes('localhost')) altTarget = target.replace('localhost', '127.0.0.1');
              else if (target.includes('127.0.0.1')) altTarget = target.replace('127.0.0.1', 'localhost');
            } catch (e) {
              altTarget = null;
            }

            if (altTarget) {
              console.log('Network error detected — retrying with alternative host', altTarget);
              // Try diagnostic GET then POST to altTarget
              fetch(altTarget, { method: 'GET' })
                .then(gresp => {
                  console.log('Alt diagnostic GET', gresp.status, gresp.statusText);
                  return fetch(altTarget, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                })
                .then(resp => {
                  if (resp && resp.ok) flashOk();
                })
                .catch(altErr => {
                  console.error('Alt-host attempt failed', altErr);
                  // As last resort try no-cors fire-and-forget to give server a chance
                  fetch(altTarget, { method: 'POST', mode: 'no-cors', body: JSON.stringify(payload) })
                    
                    .catch(fallbackErr => {
                      console.error('Fallback also failed', fallbackErr);
                                          });
                });
              return;
            }
          }

          // Attempt no-cors fallback so server receives a fire-and-forget request (diagnostic)
          fetch(target, {
            method: 'POST',
            mode: 'no-cors',
            body: JSON.stringify(payload)
          }).then(() => {
            alert('Failed to fetch normally; attempted no-cors fallback. Check server log.');
          }).catch(fallbackErr => {
            console.error('Fallback also failed', fallbackErr);
            alert('Fehler beim Senden: ' + (err && err.message ? err.message : err));
          });
        });
    });
  });
});

// Manual URL send (top input)
const addUrlBtn = document.getElementById('addUrlBtn');
if (addUrlBtn) {
  addUrlBtn.addEventListener('click', () => {
    const manualInput = document.getElementById('manualUrlInput');
    const urlVal = manualInput ? manualInput.value.trim() : '';

    if (!urlVal) {
      alert('Bitte eine URL eingeben.');
      return;
    }

    const selected = document.querySelector('input[name="m3u"]:checked');
    const m3uName = selected ? selected.value : 'default_playlist';

    chrome.storage.sync.get(['lanUrl'], (res) => {
      const target = res.lanUrl || "http://localhost:5000";
      const payload = { url: urlVal, m3u: m3uName };

      console.log('Manual send POST to', target, payload);

      fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      .then(resp => {
        if (!resp.ok) throw new Error('Server antwortete mit ' + resp.status);
        flashOk();
      })
      .catch(err => {
        console.error('Manual send failed', err);
        alert('Fehler beim Senden: ' + err.message);
      });
    });
  });
}
