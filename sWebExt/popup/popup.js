let selectedM3U = "";

// Liste beim Öffnen laden
document.addEventListener('DOMContentLoaded', () => {
  renderList();
});

// Datei zur Liste hinzufügen
document.getElementById('addBtn').addEventListener('click', () => {
  const input = document.getElementById('m3uInput');
  const val = input.value.trim();
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
          <input type="radio" name="m3u" value="${name}"> ${name}.m3u
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
      if (selected && selected.value) payload.m3u = selected.value + ".m3u";

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
          alert("Erfolgreich gesendet!");
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
                .then(resp2 => {
                  if (!resp2.ok) throw new Error('Server antwortete mit ' + resp2.status);
                  alert('Erfolgreich gesendet (via alt host)!');
                })
                .catch(altErr => {
                  console.error('Alt-host attempt failed', altErr);
                  // As last resort try no-cors fire-and-forget to give server a chance
                  fetch(altTarget, { method: 'POST', mode: 'no-cors', body: JSON.stringify(payload) })
                    .then(() => alert('Failed to fetch normally; attempted no-cors fallback. Check server log.'))
                    .catch(fallbackErr => {
                      console.error('Fallback also failed', fallbackErr);
                      alert('Fehler beim Senden: ' + (err && err.message ? err.message : err));
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
    const m3uName = selected ? selected.value + '.m3u' : 'default_playlist';

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
        alert('Erfolgreich gesendet!');
      })
      .catch(err => {
        console.error('Manual send failed', err);
        alert('Fehler beim Senden: ' + err.message);
      });
    });
  });
}
