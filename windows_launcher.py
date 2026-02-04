import os
import sys
import subprocess
import json
from pathlib import Path
import webdriver_manager.chrome as chrome_mgr # Automatisiert Download

if False:
    import ffmpeg
    import yt_dlp
    import eyed3
    from selenium.webdriver.support import ui
    from selenium.webdriver.support import expected_conditions
    import script2

def resource_path(relative_path):
    """ Holt den Pfad für Ressourcen innerhalb der EXE """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

sys.path.append(resource_path("scripts/Sc2Sp_src/Sc2Sp"))
sys.path.append(resource_path("scripts/sTownload"))

def get_config_path():
    config_dir = Path(os.getenv('APPDATA')) / "MusicServerTemp"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"

def setup_config():
    config_file = get_config_path()
    default_music_dir = str(Path.home() / "Music" / "aMusicServer")

    if not config_file.exists():
        print("=== Initial Setup ===\n")
        config = {}

        # --- Schritt 1: SoundCloud ---
        print("--- SOUNDCLOUD SETUP ---")
        print("The app will now ask your Soundcloud Likes URL. ie.: https://soundcloud.com/user.../likes")
        print("If you don't want to setup a Soundcloud Account, just press Enter.")
        config["sc_profile"] = input("Soundcloud Profile URL: ").strip()

        print("\nYou can provide a Topsong (the script stops when this song is reached).")
        config["sc_topsong"] = input("Soundcloud Topsong URL (optional): ").strip()

        # --- Schritt 2: Spotify ---
        print("\n--- SPOTIFY SETUP ---")
        print("Provide your Spotify Profile URL to download public playlists.")
        print("Again, hit Enter to skip.")
        config["sp_account"] = input("Spotify Account URL: ").strip()

        # --- Schritt 3: Speicherort ---
        print("\n--- STORAGE ---")
        print(f"Where to store the Songfiles? (Default: {default_music_dir})")
        print("Please provide a full path or press Enter for default.")
        # Wir wandeln den Pfad direkt in einen absoluten Pfad um, um ./Songs Probleme zu vermeiden
        user_path = input("Path: ").strip()
        config["song_dir"] = os.path.abspath(user_path) if user_path else default_music_dir

        # --- Schritt 4: Erweiterung ---
        print("\n--- WEB EXTENSION ---")
        ext_choice = input("Use the Web-Extension? (y/n): ").strip().lower()
        config["use_web_ext"] = "y" if ext_choice == "y" else "n"

        # Speichern
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        
        print(f"\n[SUCCESS] Config saved to: {config_file}\n")
        return config
    
    with open(config_file, 'r', encoding='utf-8') as f:
        return json.load(f)
    
def run_python_script(script_relative_path, args=None):
    script_path = resource_path(script_relative_path)
    
    if not os.path.exists(script_path):
        print(f"[Fehler] Datei nicht gefunden: {script_path}")
        return

    print(f"[Launcher] Starte: {os.path.basename(script_relative_path)}..")
    
    # Argumente für das Zielskript (argparse) bereitstellen
    original_argv = sys.argv
    if args:
        sys.argv = [script_path] + args
    else:
        sys.argv = [script_path]

    try:
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()
        
        # Wir führen das Skript in einem sauberen globalen Namespace aus
        global_space = {
            "__name__": "__main__",
            "__file__": script_path,
        }
        exec(code, global_space)
        
    except Exception as e:
        print(f"Fehler beim Ausführen von {os.path.basename(script_relative_path)}: {e}")
        import traceback
        traceback.print_exc() # Zeigt uns genau, wo es im Unter-Skript kracht
    finally:
        # sys.argv wieder zurücksetzen für den nächsten Aufruf (app.py)
        sys.argv = original_argv

def get_chromedriver():
    
    print("Prüfe Chromedriver...")
    # Lädt Treiber automatisch herunter, falls er fehlt oder veraltet ist
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    
    path = ChromeDriverManager().install()
    print(f"Driver bereit unter: {path}")
    return path

if __name__ == "__main__":
    print("=== aMusicServer Launcher ===\n")
    # 1. Config laden oder erstellen
    config = setup_config()
    
    
    # 2. Schritt: Sc2Sp_src/script_web.py (Soundcloud zu Spotify/Playlist Check)
    # Wir übergeben den song_dir als Argument, falls dein Skript argparse nutzt
    if config.get("sc_profile"):
        try:
            run_python_script(
                "scripts/Sc2Sp_src/Sc2Sp/script.py", 
                ["-s", config["song_dir"]]
            )
        except Exception as e:
            print(f"Fehler in Sc2Sp: {e}")
            import traceback
            traceback.print_exc()
    input()
    exit()

    # 3. Schritt: sTownload/app.py (Der eigentliche Downloader/Server)
    if os.path.exists(resource_path("scripts/sTownload/app.py")):
        try:
            run_python_script("scripts/sTownload/app.py")
        except Exception as e:
            print(f"Fehler in sTownload: {e}")
    
    print("\nAlle Prozesse abgeschlossen. Drücke Enter zum Beenden.")
    input()