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
    const currentUrl = tabs[0].url;
    
    chrome.storage.sync.get(['lanUrl'], (res) => {
      const target = res.lanUrl || "http://localhost:5000";
      
      fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          url: currentUrl, 
          m3u: selected.value + ".m3u" 
        })
      })
      .then(() => alert("Erfolgreich gesendet!"))
      .catch(err => alert("Fehler: " + err));
    });
  });
});
