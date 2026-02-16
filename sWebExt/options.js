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
    
    const originPattern = `${urlObj.protocol}//${urlObj.hostname}${urlObj.port ? ':' + urlObj.port : ''}/*`;

    browserAPI.permissions.request({ origins: [originPattern] }, (granted) => {
      if (granted) {
        browserAPI.storage.sync.set({ lanUrl: rawUrl }, () => {
          status.classList.add('success');
          setTimeout(() => status.classList.remove('success'), 3000);
        });
      } else {
        alert("Permission denied. Firefox requires explicit permission for local network requests.");
      }
    });
  } catch (e) {
    alert("Invalid URL format.");
  }
});