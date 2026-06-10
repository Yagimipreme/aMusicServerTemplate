/* explore.js — Explore UI vanilla JS
   No framework. Talks to Flask routes via fetch(). */

"use strict";

// ── State ──────────────────────────────────────────────────────────────────────

let allTracks = [];       // full track list for current view
let selectedIds = new Set();
let currentJobId = null;
let pollInterval = null;
let saveAllOffset = 0;
let saveAllTotal = 0;

// ── Tab switching ──────────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.style.display = "none");
    const panel = document.getElementById(`tab-${tab}`);
    if (panel) panel.style.display = "";
    clearTracks();
  });
});

// ── SC sub-tabs ────────────────────────────────────────────────────────────────

document.querySelectorAll(".subtab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.subtab;
    document.querySelectorAll(".subtab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".subtab-panel").forEach(p => p.style.display = "none");
    document.getElementById(`${target}-panel`).style.display = "";
  });
});

// ── SoundCloud ─────────────────────────────────────────────────────────────────

document.getElementById("sc-go").addEventListener("click", scSearch);
document.getElementById("sc-query").addEventListener("keydown", e => { if (e.key === "Enter") scSearch(); });

async function scSearch() {
  const q = document.getElementById("sc-query").value.trim();
  if (!q) return;
  clearTracks();

  const isUrl = q.startsWith("http");
  if (isUrl) {
    const resp = await fetch(`/sc/resolve?url=${encodeURIComponent(q)}`);
    const data = await resp.json();
    if (data.status === "ok") {
      renderTracks(data.tracks || []);
      document.getElementById("sc-subtabs").style.display = "none";
    } else {
      showError("sc-user-list", data.error || data.reason || "Error");
    }
  } else {
    document.getElementById("sc-subtabs").style.display = "flex";
    document.querySelectorAll(".subtab-panel").forEach(p => p.style.display = "none");
    document.getElementById("sc-users-panel").style.display = "";

    const [usersResp, tracksResp] = await Promise.all([
      fetch(`/sc/search/users?q=${encodeURIComponent(q)}`),
      fetch(`/sc/search/tracks?q=${encodeURIComponent(q)}`),
    ]);
    const usersData = await usersResp.json();
    const tracksData = await tracksResp.json();
    renderUsers("sc-user-list", usersData.users || []);
    renderTracksInto("sc-track-list", tracksData.tracks || []);
  }
}

function renderUsers(listId, users) {
  const ul = document.getElementById(listId);
  ul.innerHTML = "";
  users.forEach(u => {
    const li = document.createElement("li");
    li.className = "user-item";
    li.innerHTML = `
      <img src="${u.avatar_url || ""}" alt="" onerror="this.style.display='none'">
      <span class="uname">${esc(u.full_name || u.username)}</span>
    `;
    li.addEventListener("click", async () => {
      const url = `https://soundcloud.com/${u.username}`;
      const resp = await fetch(`/sc/resolve?url=${encodeURIComponent(url)}`);
      const data = await resp.json();
      if (data.status === "ok") renderTracks(data.tracks || []);
    });
    ul.appendChild(li);
  });
}

function renderTracksInto(listId, tracks) {
  const ul = document.getElementById(listId);
  ul.innerHTML = "";
  tracks.forEach((t, i) => ul.appendChild(makeTrackRow(t, i, "sc")));
  updateSelectedCount();
}

// ── Spotify ─────────────────────────────────────────────────────────────────────

document.getElementById("sp-go").addEventListener("click", spSearch);
document.getElementById("sp-query").addEventListener("keydown", e => { if (e.key === "Enter") spSearch(); });

async function spSearch() {
  const q = document.getElementById("sp-query").value.trim();
  if (!q) return;
  clearTracks();

  const isArtistUrl = q.includes("spotify.com/artist/");
  const isPlaylistUrl = q.includes("spotify.com/playlist/");

  if (isArtistUrl) {
    const resp = await fetch("/spotify/artist", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({url: q})});
    const data = await resp.json();
    if (data.status === "ok") renderTracks(data.top_tracks || []);
  } else if (isPlaylistUrl) {
    const resp = await fetch("/spotify/playlist", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({url: q})});
    const data = await resp.json();
    if (data.status === "ok") renderTracks(data.tracks || []);
  } else {
    const resp = await fetch(`/spotify/search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();
    renderSpArtists(data.artists || []);
  }
}

function renderSpArtists(artists) {
  const ul = document.getElementById("sp-artist-list");
  ul.innerHTML = "";
  artists.forEach(a => {
    const li = document.createElement("li");
    li.className = "user-item";
    li.innerHTML = `
      <img src="${a.artwork_url || ""}" alt="" onerror="this.style.display='none'">
      <span class="uname">${esc(a.name)}</span>
    `;
    li.addEventListener("click", async () => {
      const resp = await fetch("/spotify/artist", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({uri: a.uri})});
      const data = await resp.json();
      if (data.status === "ok") renderTracks(data.top_tracks || []);
    });
    ul.appendChild(li);
  });
}

// ── Import Share ───────────────────────────────────────────────────────────────

document.getElementById("import-parse").addEventListener("click", parseShare);

async function parseShare() {
  const text = document.getElementById("import-paste").value.trim();
  if (!text) return;
  const resp = await fetch("/share/parse", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({text})});
  const data = await resp.json();
  if (data.status === "ok") {
    renderTracks(data.tracks || []);
  } else {
    showError("import-track-list", data.error || "Parse error");
  }
}

// Pre-load import payload from URL fragment
window.addEventListener("load", () => {
  const hash = location.hash;
  if (hash.startsWith("#import:")) {
    const payload = hash.slice("#import:".length);
    // Switch to Import tab
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelector('[data-tab="import"]').classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.style.display = "none");
    document.getElementById("tab-import").style.display = "";
    // Decode and fill textarea
    try {
      const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
      const obj = JSON.parse(decoded);
      if (obj.type === "track") {
        const text = `/share/import?v=1&d=${payload}`;
        document.getElementById("import-paste").value = text;
        parseShare();
      }
    } catch (e) {
      document.getElementById("import-paste").value = decodeURIComponent(payload);
    }
  }
});

// ── Track rendering ────────────────────────────────────────────────────────────

function clearTracks() {
  allTracks = [];
  selectedIds.clear();
  document.getElementById("main-track-list").innerHTML = "";
  document.getElementById("sc-user-list").innerHTML = "";
  document.getElementById("sc-track-list").innerHTML = "";
  document.getElementById("sp-artist-list").innerHTML = "";
  document.getElementById("sp-track-list").innerHTML = "";
  document.getElementById("sc-subtabs").style.display = "none";
  updateSelectedCount();
}

function renderTracks(tracks) {
  allTracks = tracks;
  selectedIds.clear();
  const ul = document.getElementById("main-track-list");
  ul.innerHTML = "";
  tracks.forEach((t, i) => ul.appendChild(makeTrackRow(t, i)));
  updateSelectedCount();
}

function makeTrackRow(track, idx) {
  const li = document.createElement("li");
  li.className = "track-item";
  li.dataset.idx = idx;

  const isSpotify = track.source === "spotify";
  const duration = track.duration_ms ? formatDuration(track.duration_ms) : "—";
  const sourceLabel = isSpotify ? "SP" : (track.source === "sc" ? "SC" : "?");
  const sourceClass = isSpotify ? "sp" : (track.source === "sc" ? "sc" : "");

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.addEventListener("change", () => {
    if (cb.checked) selectedIds.add(idx);
    else selectedIds.delete(idx);
    updateSelectedCount();
  });

  const img = document.createElement("img");
  img.className = "cover";
  img.src = track.artwork_url || "";
  img.alt = "";
  img.onerror = () => { img.style.display = "none"; };

  const info = document.createElement("div");
  info.className = "info";
  info.innerHTML = `<div class="artist">${esc(track.artist || "")}</div><div class="title">${esc(track.title || "")}</div>`;

  const badge = document.createElement("span");
  badge.className = `source-badge ${sourceClass}`;
  badge.textContent = sourceLabel;

  const dur = document.createElement("span");
  dur.className = "duration";
  dur.textContent = duration;

  const statusIcon = document.createElement("span");
  statusIcon.className = "status-icon";
  statusIcon.id = `status-${idx}`;

  let previewEl;
  if (isSpotify) {
    previewEl = document.createElement("span");
    previewEl.className = "no-preview";
    previewEl.textContent = "—";
  } else {
    previewEl = document.createElement("button");
    previewEl.className = "preview-btn";
    previewEl.textContent = "▶";
    previewEl.addEventListener("click", () => playPreview(track, previewEl));
  }

  li.appendChild(cb);
  li.appendChild(img);
  li.appendChild(info);
  li.appendChild(badge);
  li.appendChild(dur);
  li.appendChild(previewEl);
  li.appendChild(statusIcon);

  return li;
}

// ── Preview ────────────────────────────────────────────────────────────────────

async function playPreview(track, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    let params;
    if (track.source === "sc" && track.stream_url) {
      params = `source=sc&url=${encodeURIComponent(track.stream_url)}`;
    } else if (track.url && track.url.includes("soundcloud.com")) {
      params = `source=sc&url=${encodeURIComponent(track.url)}`;
    } else if (track.url) {
      params = `source=yt&url=${encodeURIComponent(track.url)}`;
    } else {
      params = `source=unknown&artist=${encodeURIComponent(track.artist || "")}&title=${encodeURIComponent(track.title || "")}`;
    }
    const resp = await fetch(`/preview?${params}`);
    const data = await resp.json();
    if (data.stream_url) {
      const player = document.getElementById("player");
      player.src = data.stream_url;
      player.play();
      document.getElementById("player-label").textContent = `${track.artist} — ${track.title}`;
      btn.textContent = "▶";
      btn.disabled = false;
    } else {
      btn.textContent = "✕";
      btn.title = "Preview unavailable";
    }
  } catch (e) {
    btn.textContent = "✕";
    btn.title = "Preview unavailable";
  }
}

// ── Save / Import ──────────────────────────────────────────────────────────────

document.getElementById("save-selected").addEventListener("click", () => {
  const tracks = [...selectedIds].map(i => allTracks[i]);
  if (tracks.length === 0) { alert("Select at least one track."); return; }
  startImport(tracks);
});

document.getElementById("save-all").addEventListener("click", () => {
  if (allTracks.length === 0) { alert("No tracks loaded."); return; }
  saveAllOffset = 0;
  saveAllTotal = allTracks.length;
  sendNextBatch();
});

async function sendNextBatch() {
  const batch = allTracks.slice(saveAllOffset, saveAllOffset + 50);
  if (batch.length === 0) return;
  saveAllOffset += batch.length;
  await startImport(batch, true);
}

async function startImport(tracks, batchMode = false) {
  const name = document.getElementById("playlist-name").value.trim() || "Import";
  const resp = await fetch("/import/tracks", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({tracks, playlist_name: name, batch_size: 50}),
  });
  const data = await resp.json();
  if (data.job_id) {
    currentJobId = data.job_id;
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(() => pollImportStatus(batchMode), 2000);
  }
}

async function pollImportStatus(batchMode) {
  if (!currentJobId) return;
  const resp = await fetch(`/import/status?job_id=${currentJobId}`);
  const data = await resp.json();
  if (data.status !== "ok") return;

  (data.tracks || []).forEach((t, i) => {
    const statusEl = document.getElementById(`status-${i}`);
    if (!statusEl) return;
    const icons = {queued: "⏳", downloading: "⬇", done: "✓", error: "✗"};
    statusEl.textContent = icons[t.status] || "";
  });

  if (data.done + data.errors >= data.total) {
    clearInterval(pollInterval);
    pollInterval = null;
    if (batchMode && saveAllOffset < saveAllTotal) {
      sendNextBatch();
    }
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function updateSelectedCount() {
  document.getElementById("selected-count").textContent = `${selectedIds.size} selected`;
}

function esc(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatDuration(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function showError(listId, msg) {
  const el = document.getElementById(listId);
  if (el) el.innerHTML = `<li style="color:#c00;padding:0.5rem">${esc(msg)}</li>`;
}
