# aMusicServer 🎵

A robust local automation suite designed to bridge the gap between cloud streaming and local high-fidelity libraries. **aMusicServer** synchronizes your SoundCloud likes and Spotify playlists directly to your machine.

---

## 🚀 Quick Start (No Python Required)
**Designed for end-users. No environment configuration needed.**

* **Download**: Grab the latest `MusicServerLauncher.exe` from the [Releases Page](https://github.com/Yagimipreme/aMusicServerTemplate/releases).
* **Run**: Place the executable in any directory and launch it.
* **Setup**: Follow the interactive CLI prompts to link your profiles.
* **Enjoy**: The launcher orchestrates all background drivers and dependencies automatically.

> [!TIP]
> **Persistence:** Your configuration is stored in `%AppData%/Roaming/MusicServerTemp/`. Updating the `.exe` will not delete your settings or history.

---

## 🏗 System Architecture

The application utilizes a **Manager-Worker pattern**. A central launcher coordinates specialized Python sub-modules to handle network traffic, browser automation, and media encoding.

### 1. Interactive Configuration
Upon the first execution, `windows_launcher.py` initiates a setup wizard to map your cloud footprint to your local filesystem.

<details>
<summary><b>For interested ppl (Technical Details: Config Mapping)</b></summary>

#### Core Logic
The configuration is serialized into `config.json`. To maintain cross-platform compatibility during development while ensuring Windows stability, the launcher resolves all paths using `pathlib.Path` and `os.path.abspath`. 

* **Entry Points:** The launcher uses `subprocess.run` with `sys.executable` to spawn child processes, ensuring the bundled Python interpreter is utilized across all sub-scripts.
* **Path Sanitization:** `_ensure_dir()` handles recursive directory creation and validates write permissions before the sync begins.
</details>

### 2. The Synchronization Engine
The core logic resides in `script.py` (SoundCloud) and `app.py` (Spotify/sTownload).

* **SoundCloud Sync**: Automates discovery of new likes via Selenium.
* **Spotify Sync (WIP)**: Parses public metadata for playlist reconstruction.
* **Delta-Downloader**: Only fetches tracks not present in the local `song_dir`.

<details>
<summary><b>For interested ppl (Technical Details: Selenium & Traffic Analysis)</b></summary>

#### Browser Automation
The scraper utilizes **Selenium WebDriver** in `headless` mode. 

* **Traffic Interception:** `grab_client_id2()` parses Chrome's `performance` logs to sniff the `client_id` directly from SoundCloud's API-v2 requests.
* **Anti-Bot Measures:** It utilizes `uBlock Origin` (via `ublock.crx`) to reduce DOM noise and `AutomationControlled` flags to mimic human interaction.

#### Media Pipeline
Audio extraction targets HLS (HTTP Live Streaming) playlists for high-quality archival:

* **Encoding:** The source code calls an embedded **FFmpeg** binary.
* **Bitstream Filtering:** Uses `-bsf:a aac_adtstoasc` to ensure the resulting MP3 container is correctly structured for metadata tagging via `eyeD3`.
</details>

### 3. Web-Extension Integration (WIP)
Bridge the gap between active browsing and your local server.

* **Platform:** Chrome-based (Chrome, Brave, Edge). *Firefox pending.*
* **Protocol:** Communicates via a local `HTTP POST` request to a listener (see `sWebExt/py-server/server.py`).

---

## ⚠️ Project Status: Work in Progress
The **sTownload** module and **Web-Extension** are currently in **Active Beta**. 

* **Development focus:** Handling SoundCloud's dynamic DOM updates and Spotify's OAuth flow.
* **Contribution:** Issues and PRs are welcome in the `scripts/` directory.

---

## 🛠 Developer & Source Setup
To run from source or contribute to the logic:

### Requirements
| Requirement | Minimum Version | Note |
| :--- | :--- | :--- |
| **Python** | 3.10+ | Required for type-hinting support |
| **FFmpeg** | Latest | Must be in System PATH |
| **Chrome** | Latest | Required for Selenium WebDriver |

### Dependencies
```bash
pip install selenium yt-dlp eyeD3 ffmpeg-python webdriver-manager
