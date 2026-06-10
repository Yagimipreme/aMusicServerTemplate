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
    if (data.status === "connecting") {
      showError("sc-user-list", `SoundCloud connecting… retrying in ${data.retry_after || 30}s`);
      setTimeout(scSearch, (data.retry_after || 30) * 1000);
      return;
    }
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
    if (usersData.status === "connecting") {
      showError("sc-user-list", `SoundCloud connecting… retrying in ${usersData.retry_after || 30}s`);
      setTimeout(scSearch, (usersData.retry_after || 30) * 1000);
      return;
    }
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
    const tracksWithSource = (data.tracks || []).map(t => {
      const url = t.url || t.permalink_url || "";
      let source = t.source || "unknown";
      if (!t.source) {
        if (url.includes("soundcloud.com")) source = "sc";
        else if (url.includes("youtube.com") || url.includes("youtu.be")) source = "yt";
      }
      return {...t, source};
    });
    renderTracks(tracksWithSource);
  } else {
    showError("import-track-list", data.error || "Parse error");
  }
}

// ── Share (outgoing) ──────────────────────────────────────────────────────────

async function shareTrack(track) {
  const params = new URLSearchParams({
    artist: track.artist || "",
    title: track.title || "",
    url: track.permalink_url || track.url || "",
  });
  try {
    const resp = await fetch(`/share/link?${params}`);
    const data = await resp.json();
    if (data.url) {
      await navigator.clipboard.writeText(data.url);
      showToast("Share link copied!");
    }
  } catch (e) {
    showToast("Could not copy link");
  }
}

async function shareAllTracks() {
  if (!allTracks.length) return;
  const name = document.getElementById("playlist-name").value.trim() || "Shared Playlist";
  const body = { name, tracks: allTracks.map(t => ({
    artist: t.artist || "", title: t.title || "",
    url: t.permalink_url || t.url || ""
  }))};
  try {
    const resp = await fetch("/share/code", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const data = await resp.json();
    if (data.text) {
      showShareModal(data.text);
    }
  } catch (e) {
    showToast("Could not generate share code");
  }
}

function showToast(msg) {
  let toast = document.getElementById("share-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "share-toast";
    toast.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:10px 20px;border-radius:8px;z-index:9999;font-size:14px;opacity:0;transition:opacity .2s";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.opacity = "1";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toast.style.opacity = "0"; }, 2000);
}

function showShareModal(text) {
  let modal = document.getElementById("share-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "share-modal";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    modal.innerHTML = `
      <div style="background:#fff;border-radius:12px;padding:24px;max-width:480px;width:90%;position:relative">
        <button onclick="document.getElementById('share-modal').remove()" style="position:absolute;top:12px;right:16px;background:none;border:none;font-size:18px;cursor:pointer">✕</button>
        <h3 style="margin:0 0 12px">Share Playlist</h3>
        <textarea id="share-modal-text" rows="10" style="width:100%;box-sizing:border-box;font-family:monospace;font-size:12px;resize:vertical" readonly></textarea>
        <button onclick="navigator.clipboard.writeText(document.getElementById('share-modal-text').value).then(()=>showToast('Copied!'))" style="margin-top:12px;padding:8px 20px;background:#967BB6;color:#fff;border:none;border-radius:6px;cursor:pointer">Copy All</button>
      </div>`;
    document.body.appendChild(modal);
  }
  document.getElementById("share-modal-text").value = text;
  document.getElementById("share-modal").style.display = "flex";
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
  const isYt = !isSpotify && track.source !== "sc" &&
    (track.url || "").match(/youtube\.com|youtu\.be/);
  const duration = track.duration_ms ? formatDuration(track.duration_ms) : "";
  const sourceLabel = isSpotify ? "SP" : (track.source === "sc" ? "SC" : (isYt ? "YT" : "?"));
  const sourceClass = isSpotify ? "sp" : (track.source === "sc" ? "sc" : (isYt ? "yt" : ""));

  // Checkbox
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.addEventListener("change", () => {
    if (cb.checked) selectedIds.add(idx);
    else selectedIds.delete(idx);
    updateSelectedCount();
  });

  // Cover art
  const img = document.createElement("img");
  img.className = "cover";
  img.src = track.artwork_url || "";
  img.alt = "";
  img.onerror = () => { img.style.display = "none"; };

  // Info block: title + meta row (artist · badge · duration · status)
  const info = document.createElement("div");
  info.className = "info";

  const titleEl = document.createElement("div");
  titleEl.className = "title";
  titleEl.textContent = track.title || "";

  const meta = document.createElement("div");
  meta.className = "meta";

  const artistEl = document.createElement("span");
  artistEl.className = "artist";
  artistEl.textContent = track.artist || "";

  const badge = document.createElement("span");
  badge.className = `source-badge ${sourceClass}`;
  badge.textContent = sourceLabel;

  const dur = document.createElement("span");
  dur.className = "duration";
  dur.textContent = duration;

  const statusIcon = document.createElement("span");
  statusIcon.className = "status-icon";
  statusIcon.id = `status-${idx}`;

  meta.appendChild(artistEl);
  meta.appendChild(badge);
  if (duration) meta.appendChild(dur);
  meta.appendChild(statusIcon);

  info.appendChild(titleEl);
  info.appendChild(meta);

  // Actions: preview + share
  const actions = document.createElement("div");
  actions.className = "actions";

  let previewEl;
  if (isSpotify) {
    previewEl = document.createElement("span");
    previewEl.className = "no-preview";
    previewEl.textContent = "—";
  } else {
    previewEl = document.createElement("button");
    previewEl.className = "preview-btn";
    previewEl.textContent = "▶";
    previewEl.addEventListener("click", e => { e.stopPropagation(); playPreview(track, previewEl); });
  }

  const shareBtn = document.createElement("button");
  shareBtn.className = "share-btn";
  shareBtn.title = "Copy share link";
  shareBtn.textContent = "⬆";
  shareBtn.addEventListener("click", e => { e.stopPropagation(); shareTrack(track); });

  actions.appendChild(previewEl);
  actions.appendChild(shareBtn);

  li.appendChild(cb);
  li.appendChild(img);
  li.appendChild(info);
  li.appendChild(actions);

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

document.getElementById("share-playlist-btn").addEventListener("click", shareAllTracks);

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
