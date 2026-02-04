const input = document.getElementById('urlInput');
const saveBtn = document.getElementById('saveBtn');
const status = document.getElementById('status');

// Cross-Browser API Wrapper
const browserAPI = window.browser || window.chrome;

// Bestehende URL laden
browserAPI.storage.sync.get(['lanUrl'], (result) => {
  if (result.lanUrl) input.value = result.lanUrl;
});

saveBtn.addEventListener('click', () => {
  let rawUrl = input.value.trim();
  if (!rawUrl) return;

  if (!rawUrl.startsWith('http')) rawUrl = 'http://' + rawUrl;

  try {
    const urlObj = new URL(rawUrl);
    // WICHTIG für Firefox: Das Origin-Pattern muss präzise sein
    const origin = `${urlObj.protocol}//${urlObj.hostname}:${urlObj.port || (urlObj.protocol === 'https:' ? '443' : '80')}/`;

    // Firefox bevorzugt die Promise-basierte API, 
    // wir nutzen hier den Callback-Stil für maximale Kompatibilität
    browserAPI.permissions.request({ origins: [origin] }, (granted) => {
      if (granted) {
        browserAPI.storage.sync.set({ lanUrl: rawUrl }, () => {
          status.classList.add('success');
          setTimeout(() => status.classList.remove('success'), 3000);
        });
      } else {
        // Fehler-Handling für Firefox (Berechtigungen müssen oft explizit bestätigt werden)
        alert("Permission denied. Firefox requires explicit permission for local network requests.");
      }
    });
  } catch (e) {
    alert("Invalid URL format.");
  }
});