aMusicServer 🎵
A powerful local automation tool to synchronize your SoundCloud likes and Spotify playlists directly to your machine. It ensures your music library is always up to date and ready for local playback or DJing.

🚀 How it Works
The application acts as a central launcher that manages different specialized scripts. Here is the typical workflow:

1. Initial Setup
When you run the MusicServerLauncher.exe for the first time, it will guide you through a CLI setup:

Profiles: Enter your SoundCloud and Spotify profile URLs.

Topsong: Set a "marker" song. The script will sync everything new until it hits this song.

Storage: Choose where you want your .mp3 files to live (e.g., C:\Users\Name\Music\Songs).

<details>
<summary><b>For interested ppl (Technical Details: Configuration)</b></summary>
The launcher stores your settings in AppData/Roaming/MusicServerTemp/config.json. This ensures your data persists even if you move the EXE. It uses absolute path resolution to prevent issues with relative Windows directories.
</details>

2. The Synchronization Process
Once configured, the launcher starts the sync scripts:

SoundCloud Sync: It opens a hidden (headless) browser to fetch your latest likes.

Spotify Sync (WIP): It parses your public playlists.

Downloader: It compares your local files with the online list and only downloads what is missing.

<details>
<summary><b>For interested ppl (Technical Details: Scrapers & FFmpeg)</b></summary>
The system uses <b>Selenium</b> with a <b>uBlock Origin</b> extension to bypass ads and efficiently extract <code>client_id</code> tokens from network traffic. The actual audio extraction is handled by an embedded <b>FFmpeg</b> binary, which converts HLS streams into high-quality MP3s with <code>aac_adtstoasc</code> bitstream filters.
</details>

3. Web-Extension (WIP)
You can use the companion Web-Extension to send songs directly from your browser to the server.

Compatibility: Currently works with Chrome-based browsers (Google Chrome, Brave, Edge, etc.). Firefox support is coming soon.

Requirement: Needs a local Python HTTP server or LAN connection to receive and process requests.

⚠️ Work in Progress (WIP)
Please note that sTownload and the Web-Extension are currently under active development. Expect bugs and frequent updates as we move toward a stable release.

🛠 Prerequisites for Developers
If you are running from source instead of the EXE:

Python 3.10+

FFmpeg installed in your system PATH.

Chrome Browser (for Selenium).

<details>
<summary><b>For interested ppl (Technical Details: Bundling)</b></summary>
The standalone EXE is built using <b>PyInstaller</b>. It bundles the Python interpreter, Selenium drivers, and FFmpeg into a single binary. We use <code>--hidden-import</code> to ensure dynamic imports (like <code>yt_dlp</code> and <code>eyed3</code>) are correctly packaged even if not explicitly called in the main launcher script.
</details>

📦 Installation
Download the latest MusicServerLauncher.exe from the Releases page.

Place it in a folder of your choice.

Run it and follow the on-screen instructions.