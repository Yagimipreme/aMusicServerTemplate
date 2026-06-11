/* explore.js — aMusicServer Explore UI
   No framework. Talks to Flask routes via fetch(). */

"use strict";

// ── State ──────────────────────────────────────────────────────────────────────

let allTracks = [];
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

// ── Unified search ─────────────────────────────────────────────────────────────

document.getElementById("search-go").addEventListener("click", doSearch);
document.getElementById("search-query").addEventListener("keydown", e => {
  if (e.key === "Enter") doSearch();
});

async function doSearch() {
  const q = document.getElementById("search-query").value.trim();
  if (!q) return;
  clearTracks();
  setHint("Loading…");

  try {
    if (q.startsWith("http")) {
      if (q.includes("soundcloud.com"))          await handleScUrl(q);
      else if (q.includes("spotify.com/artist/")) await handleSpArtistUrl(q);
      else if (q.includes("spotify.com/playlist/")) await handleSpPlaylistUrl(q);
      else if (q.includes("spotify.com/track/"))  await handleSpTrackUrl(q);
      else if (q.match(/youtube\.com|youtu\.be/)) await handleYtUrl(q);
      else                                         await handleScUrl(q); // fallback
    } else {
      await handleTextSearch(q);
    }
  } catch (e) {
    setHint("Error — check the server log.");
  }

  setHint("");
}

// ── URL handlers ───────────────────────────────────────────────────────────────

async function handleScUrl(url) {
  const resp = await fetch(`/sc/resolve?url=${encodeURIComponent(url)}`);
  const data = await resp.json();
  if (data.status === "connecting") {
    setHint(`SoundCloud connecting… retrying in ${data.retry_after || 30}s`);
    setTimeout(doSearch, (data.retry_after || 30) * 1000);
    return;
  }
  if (data.status === "ok") {
    renderArtists(data.users || []);
    renderTracks(data.tracks || []);
  } else {
    setHint(data.error || data.reason || "SoundCloud error");
  }
}

async function handleSpArtistUrl(url) {
  const resp = await fetch("/spotify/artist", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url}),
  });
  const data = await resp.json();
  if (data.status === "ok") renderTracks(data.top_tracks || []);
  else setHint(data.error || "Spotify error");
}

async function handleSpPlaylistUrl(url) {
  const resp = await fetch("/spotify/playlist", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url}),
  });
  const data = await resp.json();
  if (data.status === "ok") renderTracks(data.tracks || []);
  else setHint(data.error || "Spotify error");
}

async function handleSpTrackUrl(url) {
  // Single Spotify track — wrap it as a one-item list so the user can save it
  renderTracks([{title: url.split("/track/")[1]?.split("?")[0] || "Spotify track",
                 artist: "", url, source: "spotify"}]);
}

async function handleYtUrl(url) {
  // Strip playlist/index params — keep only the video URL
  let cleanUrl = url;
  try {
    const u = new URL(url);
    const v = u.searchParams.get("v");
    if (v) cleanUrl = `https://www.youtube.com/watch?v=${v}`;
  } catch (e) {}

  const track = {title: "Resolving…", artist: "", url: cleanUrl, source: "yt", artwork_url: ""};
  renderTracks([track]);
  setHint("Fetching title…");

  // Resolve real title via preview endpoint (yt-dlp metadata)
  try {
    const resp = await fetch(`/preview?source=yt&url=${encodeURIComponent(cleanUrl)}`);
    const data = await resp.json();
    if (data.title) {
      track.title = data.title;
      track.artist = data.artist || "";
      // Patch the rendered row in place
      const titleEl = document.querySelector("#main-track-list .track-item .title");
      const artistEl = document.querySelector("#main-track-list .track-item .artist");
      if (titleEl) titleEl.textContent = data.title;
      if (artistEl) artistEl.textContent = data.artist || "";
    }
  } catch (e) {
    track.title = "YouTube video";
  }
  setHint("Ready — hit Save to download.");
}

// ── Text search (parallel SC + Spotify) ───────────────────────────────────────

async function handleTextSearch(q) {
  const [scUsers, scTracks, spArtists] = await Promise.all([
    fetch(`/sc/search/users?q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => ({})),
    fetch(`/sc/search/tracks?q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => ({})),
    fetch(`/spotify/search?q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => ({})),
  ]);

  if (scUsers.status === "connecting") {
    setHint(`SoundCloud connecting… retrying in ${scUsers.retry_after || 30}s`);
    setTimeout(() => handleTextSearch(q), (scUsers.retry_after || 30) * 1000);
    return;
  }

  // Merge artist/user lists: SC users first, then Spotify artists
  const artists = [
    ...(scUsers.users || []).map(u => ({...u, _source: "sc"})),
    ...(spArtists.artists || []).map(a => ({
      username: a.uri, full_name: a.name, avatar_url: a.artwork_url,
      _source: "spotify", _uri: a.uri,
    })),
  ];
  renderArtists(artists);
  renderTracks((scTracks.tracks || []).map(t => ({...t, source: "sc"})));
}

// ── Artist list ────────────────────────────────────────────────────────────────

function renderArtists(artists) {
  const ul = document.getElementById("result-users");
  ul.innerHTML = "";
  artists.forEach(a => {
    const li = document.createElement("li");
    li.className = "user-item";

    const img = document.createElement("img");
    img.src = a.avatar_url || "";
    img.alt = "";
    img.onerror = () => img.style.display = "none";

    const name = document.createElement("span");
    name.className = "uname";
    name.textContent = a.full_name || a.username || a.name || "";

    const badge = document.createElement("span");
    badge.className = `source-badge ${a._source === "spotify" ? "sp" : "sc"}`;
    badge.textContent = a._source === "spotify" ? "SP" : "SC";

    li.appendChild(img);
    li.appendChild(name);
    li.appendChild(badge);

    li.addEventListener("click", async () => {
      clearTracks();
      setHint("Loading…");
      if (a._source === "spotify") {
        const resp = await fetch("/spotify/artist", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({uri: a._uri}),
        });
        const data = await resp.json();
        if (data.status === "ok") renderTracks(data.top_tracks || []);
      } else {
        const url = `https://soundcloud.com/${a.username}`;
        const resp = await fetch(`/sc/resolve?url=${encodeURIComponent(url)}`);
        const data = await resp.json();
        if (data.status === "ok") renderTracks(data.tracks || []);
      }
      setHint("");
    });

    ul.appendChild(li);
  });
}

// ── Import Share ───────────────────────────────────────────────────────────────

document.getElementById("import-parse").addEventListener("click", parseShare);

async function parseShare() {
  const text = document.getElementById("import-paste").value.trim();
  if (!text) return;
  const resp = await fetch("/share/parse", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text}),
  });
  const data = await resp.json();
  if (data.status === "ok") {
    const tracks = (data.tracks || []).map(t => {
      const url = t.url || t.permalink_url || "";
      let source = t.source || "unknown";
      if (!t.source) {
        if (url.includes("soundcloud.com")) source = "sc";
        else if (url.match(/youtube\.com|youtu\.be/)) source = "yt";
      }
      return {...t, source};
    });
    renderTracks(tracks);
  } else {
    setHint(data.error || "Parse error");
  }
}

// Pre-load import payload from URL fragment
window.addEventListener("load", () => {
  const hash = location.hash;
  if (!hash.startsWith("#import:")) return;
  const payload = hash.slice("#import:".length);
  // Switch to Import tab
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelector('[data-tab="import"]').classList.add("active");
  document.querySelectorAll(".tab-panel").forEach(p => p.style.display = "none");
  document.getElementById("tab-import").style.display = "";
  try {
    const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const obj = JSON.parse(decoded);
    if (obj.type === "track") {
      document.getElementById("import-paste").value =
        `http://${window.SERVER_HOSTNAME}:${window.SERVER_PORT}/share/import?v=1&d=${payload}`;
      parseShare();
    }
  } catch (e) {
    document.getElementById("import-paste").value = decodeURIComponent(payload);
  }
});

// ── Share (outgoing) ───────────────────────────────────────────────────────────

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
      await copyText(data.url);
      showToast("Share link copied!");
    }
  } catch (e) {
    showToast("Could not copy link");
  }
}

async function shareAllTracks() {
  if (!allTracks.length) return;
  const name = document.getElementById("playlist-name").value.trim() || "Shared Playlist";
  const body = {
    name,
    tracks: allTracks.map(t => ({
      artist: t.artist || "",
      title: t.title || "",
      url: t.permalink_url || t.url || "",
    })),
  };
  try {
    const resp = await fetch("/share/code", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.text) showShareModal(data.text);
  } catch (e) {
    showToast("Could not generate share code");
  }
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Fallback for plain HTTP (LAN access) — works on all browsers
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;top:-999px;left:-999px;opacity:0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

function showToast(msg) {
  let toast = document.getElementById("share-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "share-toast";
    toast.style.cssText = "position:fixed;bottom:96px;left:50%;transform:translateX(-50%);background:#2C2A35;color:#fff;padding:10px 20px;border-radius:8px;z-index:9999;font-size:14px;opacity:0;transition:opacity .2s;pointer-events:none";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.opacity = "1";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toast.style.opacity = "0"; }, 2200);
}

function showShareModal(text) {
  let modal = document.getElementById("share-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "share-modal";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    modal.innerHTML = `
      <div style="background:#fff;border-radius:12px;padding:24px;max-width:480px;width:90%;position:relative">
        <button onclick="document.getElementById('share-modal').remove()"
                style="position:absolute;top:12px;right:16px;background:none;border:none;font-size:18px;cursor:pointer">✕</button>
        <h3 style="margin:0 0 12px;color:#6B5B9E">Share Playlist</h3>
        <textarea id="share-modal-text" rows="10"
                  style="width:100%;box-sizing:border-box;font-family:monospace;font-size:12px;resize:vertical;border:1px solid #ccc;border-radius:6px;padding:8px"
                  readonly></textarea>
        <button onclick="copyText(document.getElementById('share-modal-text').value).then(()=>showToast('Copied!'))"
                style="margin-top:12px;padding:8px 20px;background:#967BB6;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px">
          Copy All
        </button>
      </div>`;
    document.body.appendChild(modal);
  }
  document.getElementById("share-modal-text").value = text;
  document.getElementById("share-modal").style.display = "flex";
}

// ── Track rendering ────────────────────────────────────────────────────────────

function clearTracks() {
  allTracks = [];
  selectedIds.clear();
  document.getElementById("main-track-list").innerHTML = "";
  document.getElementById("result-users").innerHTML = "";
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
    !!(track.url || "").match(/youtube\.com|youtu\.be/);
  const sourceLabel = isSpotify ? "SP" : (track.source === "sc" ? "SC" : (isYt ? "YT" : "?"));
  const sourceClass = isSpotify ? "sp" : (track.source === "sc" ? "sc" : (isYt ? "yt" : ""));
  const duration = track.duration_ms ? formatDuration(track.duration_ms) : "";

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

  // Info: title + meta row
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

  const statusIcon = document.createElement("span");
  statusIcon.className = "status-icon";
  statusIcon.id = `status-${idx}`;

  meta.appendChild(artistEl);
  meta.appendChild(badge);
  if (duration) {
    const dur = document.createElement("span");
    dur.className = "duration";
    dur.textContent = duration;
    meta.appendChild(dur);
  }
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
      document.getElementById("player-label").textContent =
        `${track.artist} — ${track.title}`;
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
    body: JSON.stringify({tracks, playlist_name: name}),
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
    const el = document.getElementById(`status-${i}`);
    if (!el) return;
    el.textContent = {queued: "⏳", downloading: "⬇", done: "✓", error: "✗"}[t.status] || "";
  });

  if (data.done + data.errors >= data.total) {
    clearInterval(pollInterval);
    pollInterval = null;
    if (batchMode && saveAllOffset < saveAllTotal) sendNextBatch();
  }
}

// ── Discover tab ──────────────────────────────────────────────────────────────

async function loadDiscoverConfig() {
  try {
    const resp = await fetch("/discover/config");
    const cfg = await resp.json();
    document.getElementById("dc-weekly-count").value = cfg.weekly_count ?? 30;
    document.getElementById("dc-per-artist").value   = cfg.per_artist ?? 1;
    document.getElementById("dc-run-hour").value     = cfg.run_hour ?? 22;
    const sched = cfg.schedule ?? "daily";
    document.getElementById("dc-schedule").value = sched;
    document.getElementById("dc-run-day").value  = cfg.run_day ?? "sunday";
    document.getElementById("dc-runday-row").style.display = sched === "weekly" ? "" : "none";
    document.getElementById("dc-playlist-cap").value = cfg.playlist_cap ?? 100;
    document.getElementById("dc-ttl").value = cfg.suggested_ttl_days ?? 90;
    const activePeriods = new Set(cfg.lastfm_periods ?? ["1month", "overall"]);
    document.querySelectorAll("input[name='lfm-period']").forEach(cb => {
      cb.checked = activePeriods.has(cb.value);
    });
  } catch (e) {
    discoverStatus("Failed to load settings");
  }
}

document.getElementById("dc-schedule").addEventListener("change", e => {
  document.getElementById("dc-runday-row").style.display =
    e.target.value === "weekly" ? "" : "none";
});

document.getElementById("dc-save").addEventListener("click", async () => {
  const periods = [...document.querySelectorAll("input[name='lfm-period']:checked")]
    .map(cb => cb.value);
  const body = {
    weekly_count:       parseInt(document.getElementById("dc-weekly-count").value, 10),
    per_artist:         parseInt(document.getElementById("dc-per-artist").value, 10),
    schedule:           document.getElementById("dc-schedule").value,
    run_day:            document.getElementById("dc-run-day").value,
    run_hour:           parseInt(document.getElementById("dc-run-hour").value, 10),
    lastfm_periods:     periods.length ? periods : ["1month"],
    playlist_cap:       parseInt(document.getElementById("dc-playlist-cap").value, 10),
    suggested_ttl_days: parseInt(document.getElementById("dc-ttl").value, 10),
  };
  try {
    const resp = await fetch("/discover/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    discoverStatus(data.status === "ok" ? "Saved" : `Error: ${data.error}`);
  } catch (e) {
    discoverStatus("Save failed");
  }
});

document.getElementById("dc-run").addEventListener("click", async () => {
  discoverStatus("Starting…");
  try {
    const resp = await fetch("/discover/run", {method: "POST"});
    const data = await resp.json();
    discoverStatus(data.acquired !== undefined
      ? `Done — ${data.acquired} track(s) acquired`
      : `Error: ${data.error ?? data.reason ?? "unknown"}`);
  } catch (e) {
    discoverStatus("Run failed");
  }
});

function discoverStatus(msg) {
  const el = document.getElementById("dc-status");
  if (el) el.textContent = msg;
}

// Load discover config when the tab is opened
document.querySelectorAll(".tab-btn").forEach(btn => {
  if (btn.dataset.tab === "discover") {
    btn.addEventListener("click", loadDiscoverConfig);
  }
});

// ── Helpers ────────────────────────────────────────────────────────────────────

function setHint(msg) {
  const el = document.getElementById("search-hint");
  if (el) el.textContent = msg;
}

function updateSelectedCount() {
  document.getElementById("selected-count").textContent = `${selectedIds.size} selected`;
}

function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDuration(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
