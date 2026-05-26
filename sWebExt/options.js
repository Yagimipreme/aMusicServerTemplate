const input      = document.getElementById('urlInput');
const saveBtn    = document.getElementById('saveBtn');
const testBtn    = document.getElementById('testBtn');
const status     = document.getElementById('status');
const testResult = document.getElementById('testResult');

const DEFAULT_URL = 'http://localhost:5000';

// Cross-Browser API Wrapper
const browserAPI = window.browser || window.chrome;

// Load stored URL, or seed the field with the localhost default on first run.
browserAPI.storage.sync.get(['lanUrl'], (result) => {
  input.value = result.lanUrl || DEFAULT_URL;
});

function normalizeUrl(raw) {
  let u = (raw || '').trim();
  if (!u) return null;
  if (!u.startsWith('http')) u = 'http://' + u;
  try {
    return new URL(u).toString().replace(/\/$/, '');
  } catch (e) {
    return null;
  }
}

function showTest(state, message) {
  testResult.className = 'test-result show ' + (state || '');
  testResult.textContent = message;
}

// ── Test connection ─────────────────────────────────────────────────────────
//
// Hit GET <url>/ and verify the JSON body looks like aMusicServer
// (server.py's do_GET returns {"status":"ok", "pid":N, "cwd":"..."}).
// Catches typos and "I forgot to start.sh" before the user goes hunting.

testBtn.addEventListener('click', () => {
  const url = normalizeUrl(input.value);
  if (!url) { showTest('err', 'Invalid URL.'); return; }

  showTest('pending', `Testing ${url} …`);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);

  fetch(url + '/', { method: 'GET', signal: controller.signal, cache: 'no-store' })
    .then(async (resp) => {
      clearTimeout(timer);
      if (!resp.ok) {
        showTest('err', `Server responded ${resp.status} ${resp.statusText}.`);
        return;
      }
      try {
        const data = await resp.json();
        if (data && data.status === 'ok') {
          const pid = data.pid != null ? ` (pid ${data.pid})` : '';
          showTest('ok', `Connected — aMusicServer responding${pid}.`);
        } else {
          showTest('err', "Reachable, but doesn't look like aMusicServer.");
        }
      } catch (e) {
        showTest('err', "Reachable, but response isn't JSON — probably a different server.");
      }
    })
    .catch((err) => {
      clearTimeout(timer);
      const msg = err && err.name === 'AbortError'
        ? 'No response within 4 seconds.'
        : (err && err.message) || String(err);
      showTest('err', `Connection failed: ${msg}`);
    });
});

// ── Save ────────────────────────────────────────────────────────────────────

saveBtn.addEventListener('click', () => {
  const rawUrl = normalizeUrl(input.value);
  if (!rawUrl) { alert('Invalid URL format.'); return; }

  const urlObj = new URL(rawUrl);
  const originPattern = `${urlObj.protocol}//${urlObj.hostname}${urlObj.port ? ':' + urlObj.port : ''}/*`;

  browserAPI.permissions.request({ origins: [originPattern] }, (granted) => {
    if (granted) {
      browserAPI.storage.sync.set({ lanUrl: rawUrl }, () => {
        status.classList.add('success');
        setTimeout(() => status.classList.remove('success'), 3000);
      });
    } else {
      alert('Permission denied. Firefox requires explicit permission for local network requests.');
    }
  });
});