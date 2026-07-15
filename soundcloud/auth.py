"""Headless SoundCloud login + OAuth token capture.

Reuses the same headless-Chrome / CDP-network-sniffing technique as
scripts/Sc2Sp_src/script_web.py's fetch_client_id_via_selenium — no visible
browser, no manual interaction at run time.
"""
import json
import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    webdriver = None
    ChromeService = None
    ChromeOptions = None
    By = None
    WebDriverWait = None
    EC = None

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

_SIGNIN_URL = "https://soundcloud.com/signin"
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEBUG_HTML_PATH = os.path.join(_PROJECT_ROOT, "logs", "sc_login_debug.html")


def _make_driver():
    """Start headless Chrome with network logging enabled. Returns driver or None."""
    if webdriver is None:
        logger.warning("[SC-AUTH] Selenium not available")
        return None

    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    system_cd = shutil.which("chromedriver")
    if system_cd:
        service = ChromeService(system_cd)
    elif ChromeDriverManager is not None:
        service = ChromeService(ChromeDriverManager().install())
    else:
        logger.error("[SC-AUTH] No chromedriver available")
        return None

    chromium_bin = (shutil.which("chromium") or shutil.which("chromium-browser")
                     or shutil.which("google-chrome"))
    if chromium_bin:
        options.binary_location = chromium_bin

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        logger.exception("[SC-AUTH] Failed to start ChromeDriver")
        return None

    driver.execute_cdp_cmd("Network.enable", {})
    return driver


def _dump_debug_html(driver):
    """Best-effort dump of page source for troubleshooting selector drift."""
    try:
        os.makedirs(os.path.dirname(_DEBUG_HTML_PATH), exist_ok=True)
        with open(_DEBUG_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.warning("[SC-AUTH] Dumped page source to %s for troubleshooting", _DEBUG_HTML_PATH)
    except Exception:
        logger.exception("[SC-AUTH] Failed to dump debug HTML")


def _perform_login(driver, username: str, password: str) -> bool:
    """Fill and submit the SC sign-in form. Returns True if the form was submitted.

    Selectors target the direct /signin route (name="username"/"password" fields)
    rather than the header login modal, since a direct URL navigation is more
    reliable headlessly. If SC changes this form, _dump_debug_html captures the
    page source so selectors can be corrected from real data.
    """
    driver.get(_SIGNIN_URL)
    try:
        user_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "username")))
        pass_field = driver.find_element(By.NAME, "password")
        user_field.send_keys(username)
        pass_field.send_keys(password)
        pass_field.submit()
        time.sleep(3)
        return True
    except Exception:
        logger.exception("[SC-AUTH] Login form interaction failed")
        _dump_debug_html(driver)
        return False


def _extract_oauth_token(entries: list) -> str:
    """Scan CDP performance-log entries for an Authorization header on api-v2 calls."""
    for entry in entries:
        msg = json.loads(entry.get("message", "{}")).get("message", {})
        if msg.get("method") != "Network.requestWillBeSent":
            continue
        req = msg.get("params", {}).get("request", {})
        url = req.get("url", "")
        if "api-v2.soundcloud.com" not in url:
            continue
        headers = req.get("headers", {}) or {}
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth:
            return auth
    return None


def login_and_capture_token(username: str, password: str) -> str:
    """Headless-login to SoundCloud and return the OAuth token sniffed from
    subsequent api-v2 network traffic, or None on any failure."""
    driver = _make_driver()
    if driver is None:
        return None
    try:
        if not _perform_login(driver, username, password):
            return None
        driver.get("https://soundcloud.com/")
        time.sleep(3)
        try:
            entries = driver.get_log("performance")
        except Exception:
            logger.exception("[SC-AUTH] Failed to read performance logs")
            return None
        token = _extract_oauth_token(entries)
        if token is None:
            logger.warning("[SC-AUTH] Logged in but no Authorization header found in traffic")
        return token
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _write_dump(dump_path: str, result: dict) -> None:
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def discover_personal_mix_endpoints(username: str, password: str, dump_path: str,
                                     visit_urls: list = None) -> str:
    """Log in headlessly, visit pages likely to surface personal mixes, and dump
    every distinct api-v2 request URL + a truncated response body to dump_path.

    Returns dump_path. The file is always written, even {"entries": []} on
    login/driver failure, so callers never have to special-case a missing file.
    """
    visit_urls = visit_urls or [
        "https://soundcloud.com/",
        "https://soundcloud.com/discover",
        "https://soundcloud.com/you/library/playlists",
    ]
    result = {"entries": []}
    driver = _make_driver()
    if driver is None:
        _write_dump(dump_path, result)
        return dump_path
    try:
        if not _perform_login(driver, username, password):
            _write_dump(dump_path, result)
            return dump_path

        seen_urls = set()
        for page in visit_urls:
            try:
                driver.get(page)
                time.sleep(3)
            except Exception:
                logger.exception("[SC-AUTH] Failed to visit %s", page)
                continue

            try:
                entries = driver.get_log("performance")
            except Exception:
                logger.exception("[SC-AUTH] Failed to read performance logs for %s", page)
                continue

            for entry in entries:
                msg = json.loads(entry.get("message", "{}")).get("message", {})
                if msg.get("method") != "Network.requestWillBeSent":
                    continue
                params = msg.get("params", {})
                url = params.get("request", {}).get("url", "")
                request_id = params.get("requestId")
                if "api-v2.soundcloud.com" not in url or url in seen_urls:
                    continue
                seen_urls.add(url)

                body = ""
                try:
                    body_resp = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id})
                    body = (body_resp or {}).get("body", "")[:2000]
                except Exception:
                    pass  # response body may already be gone (redirects/cache) — non-fatal

                result["entries"].append({"page": page, "url": url, "body_sample": body})

        _write_dump(dump_path, result)
        return dump_path
    finally:
        try:
            driver.quit()
        except Exception:
            pass
