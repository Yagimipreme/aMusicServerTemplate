const input = document.getElementById('urlInput');
const saveBtn = document.getElementById('saveBtn');
const status = document.getElementById('status');

// Bestehende URL laden
chrome.storage.sync.get(['lanUrl'], (result) => {
  if (result.lanUrl) input.value = result.lanUrl;
});

saveBtn.addEventListener('click', () => {
  let rawUrl = input.value.trim();
  if (!rawUrl) return;

 
  if (!rawUrl.startsWith('http')) rawUrl = 'http://' + rawUrl;

  try {
    const urlObj = new URL(rawUrl);
    const origin = `${urlObj.protocol}//${urlObj.hostname}/*`;

   
    chrome.permissions.request({ origins: [origin] }, (granted) => {
      if (granted) {
        
        chrome.storage.sync.set({ lanUrl: rawUrl }, () => {
          status.classList.add('success');
          setTimeout(() => status.classList.remove('success'), 3000);
        });
      } else {
        alert("Access Denied. This is needed to function.");
      }
    });
  } catch (e) {
    alert("Wrong URL-format.");
  }
});
