# sWebExt — aMusicServer browser extension

A small Firefox / Chrome / Edge / Brave extension that sends the URL of whatever song you're looking at to your local aMusicServer, which downloads the track to your music library.

It only talks to your own server on your home network. No third-party services, no telemetry.

## Loading it into your browser

**Firefox**
1. Open `about:debugging` → **This Firefox** → **Load Temporary Add-on…**
2. Pick any file inside this folder (e.g. `manifest.json`).

**Chrome / Edge / Brave**
1. Open `chrome://extensions` (or your browser's equivalent).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and pick this folder.

## First-run setup

1. Click the extension icon → **Settings / Options**.
2. The URL field is pre-filled with `http://localhost:5000`. If the server runs on a different computer on your home network, change it to that computer's `.local` name — e.g. `http://homeserver.local:5000`.
3. Click **Test connection** to verify, then **Save**.

## Day-to-day use

- **Send one song:** click the extension → **Send current URL**. A green checkmark flashes when the server received it.
- **Use playlists:** click **Pull playlists from Navidrome** to import all your existing playlist names (one-time), or type a new name and click **Add**. Select a playlist with the radio button before sending a song to have it added there automatically.

Full project docs and self-hosting instructions: see the main [README.md](../README.md) one level up.
