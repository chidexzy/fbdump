import getpass
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

from playwright_stealth import Stealth

_stealth = Stealth()

COOKIES_FILE = Path(__file__).parent / "session_cookies.json"
OUTPUT_DIR = Path(__file__).parent / "output"

CHROMIUM_PATH = None  # Use Playwright's bundled Chromium


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def save_cookies(context):
    cookies = context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"  Session saved to {COOKIES_FILE}")


_VALID_SAMESITE = {"Strict", "Lax", "None"}


def _fix_samesite(value):
    if not value:
        return "None"
    normalised = str(value).strip().capitalize()
    return normalised if normalised in _VALID_SAMESITE else "None"


def load_cookies(context):
    if not COOKIES_FILE.exists():
        return False
    cookies = json.loads(COOKIES_FILE.read_text())
    for c in cookies:
        c["sameSite"] = _fix_samesite(c.get("sameSite"))
    context.add_cookies(cookies)
    return True


def get_cookie(context, name):
    return next((c for c in context.cookies() if c["name"] == name), None)


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def _launch_chromium(playwright, headless=True, slow_mo=0):
    kwargs = {"headless": headless, "slow_mo": slow_mo}
    if CHROMIUM_PATH:
        kwargs["executable_path"] = CHROMIUM_PATH
    return playwright.chromium.launch(**kwargs)


def _new_context(browser):
    return browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def check_logged_in(page):
    try:
        page.goto("https://www.facebook.com/", timeout=30000)
        page.wait_for_load_state("domcontentloaded")
        return "login" not in page.url and page.query_selector('[aria-label="Your profile"]') is not None
    except Exception:
        return False


def click_through_interstitials(page, context, max_rounds=8):
    continue_selectors = [
        '[name="submit[Continue]"]',
        '[name="submit[Save Device]"]',
        'button[type="submit"]',
        '[role="button"]:has-text("Continue")',
        '[role="button"]:has-text("OK")',
        '[role="button"]:has-text("Done")',
        '[role="button"]:has-text("Not now")',
        '[role="button"]:has-text("Save")',
        'a:has-text("Not Now")',
    ]
    for _ in range(max_rounds):
        if get_cookie(context, "c_user"):
            return True
        clicked = False
        for sel in continue_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    time.sleep(2)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            time.sleep(2)
    return bool(get_cookie(context, "c_user"))


def do_login(playwright):
    print("\n--- Facebook Login ---")
    print("Your credentials are used only to log in and are never stored.")
    email = input("Facebook email or phone: ").strip()
    password = getpass.getpass("Password (hidden): ")

    print("\nLogging in (this may take a few seconds)...")
    browser = _launch_chromium(playwright, headless=True, slow_mo=60)
    context = _new_context(browser)
    page = context.new_page()
    _stealth.apply_stealth_sync(page)
    page.goto("https://www.facebook.com/login", timeout=40000)
    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(2)

    for selector in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'button[title="Allow all cookies"]',
        'button[title="Accept all"]',
        '[aria-label="Allow all cookies"]',
    ]:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(1)
                break
        except Exception:
            pass

    email_field = None
    for sel in ['input[name="email"]', '#email', 'input[type="email"]', 'input[type="text"]']:
        email_field = page.query_selector(sel)
        if email_field:
            break
    if not email_field:
        browser.close()
        print("\nCould not find the login form. Facebook may have changed its layout.")
        return False
    email_field.click()
    email_field.fill(email)
    time.sleep(0.5)

    pass_field = None
    for sel in ['input[name="pass"]', '#pass', 'input[type="password"]']:
        pass_field = page.query_selector(sel)
        if pass_field:
            break
    if not pass_field:
        browser.close()
        print("\nCould not find the password field.")
        return False
    pass_field.click()
    pass_field.fill(password)
    time.sleep(0.5)
    pass_field.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(3)

    url = page.url
    if any(k in url for k in ["checkpoint", "two_step", "two-factor", "approvals"]):
        print("\nTwo-factor authentication required.")
        print("Check your phone or authenticator app for a code.")
        code = input("Enter the code: ").strip()
        code_input = (
            page.query_selector('[name="approvals_code"]')
            or page.query_selector('[name="code"]')
            or page.query_selector('input[type="text"]')
        )
        if code_input:
            code_input.fill(code)
            submit = (
                page.query_selector('[name="submit[Continue]"]')
                or page.query_selector('button[type="submit"]')
            )
            if submit:
                submit.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)

    print("  Completing login (handling any extra steps)...")
    success = click_through_interstitials(page, context, max_rounds=10)

    if not success:
        page.goto("https://www.facebook.com/", timeout=20000)
        time.sleep(3)
        success = bool(get_cookie(context, "c_user"))

    if not success:
        browser.close()
        print("\nLogin did not fully complete — the session token was not set.")
        print("This can happen if Facebook flagged the login. Please try again.")
        return False

    save_cookies(context)
    browser.close()
    user_cookie = get_cookie(context, "c_user")
    print(f"\nLogged in successfully (user ID: {user_cookie['value'] if user_cookie else '?'}).")
    print("Session saved — you won't need to log in again unless it expires.")
    return True


# ---------------------------------------------------------------------------
# Friends extraction
# ---------------------------------------------------------------------------

def scroll_to_bottom(page, pause=2.0, max_scrolls=200):
    last_height = 0
    same_count = 0
    for i in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            same_count += 1
            if same_count >= 3:
                break
        else:
            same_count = 0
        last_height = new_height
        print(f"\r  Scrolling... (pass {i + 1})", end="", flush=True)
    print()


import re as _re

_COLLECT_JS = """
() => {
  const SKIP_PATH = new Set([
    'friends','about','photos','videos','groups','events','pages',
    'marketplace','watch','gaming','ads','help','login','share',
    'hashtag','messages','notifications','bookmarks','saved',
    'memories','fundraisers','weather','people','requests',
    'suggestions','birthdays','lists','followers','following',
    'mutual_friends'
  ]);
  const SKIP_HREFS = [
    '/groups/','/pages/','/events/','/marketplace/','/watch/','/gaming/',
    '/ads/','/help/','/login/','/share/','/hashtag/','/messages/',
    '/notifications/','/friends/requests','/friends/suggestions',
    '/friends/birthdays','/followers','/following','javascript:','mailto:'
  ];
  const SKIP_TEXT = new Set([
    'add friend','follow','unfollow','message','more','friends',
    'followers','following','mutual friends','respond','remove',
    'confirm','delete','see all','view profile','unfriend','block','report'
  ]);

  function parseId(href) {
    let m = href.match(/profile\\.php\\?id=(\\d+)/);
    if (m) return m[1];
    m = href.match(/\\/people\\/[^/]+\\/(\\d+)/);
    if (m) return m[1];
    const path = href.split('?')[0].split('#')[0].replace(/\\/$/, '');
    const slug = path.split('/').pop();
    if (slug && !SKIP_PATH.has(slug) && !slug.startsWith('_') && slug !== '')
      return slug;
    return null;
  }

  function skip(href) {
    return SKIP_HREFS.some(k => href.includes(k));
  }

  const out = {};

  for (const img of document.querySelectorAll('img[alt]')) {
    const alt = img.getAttribute('alt').trim();
    if (!alt || alt.length < 2 || alt.length > 80 || /^\\d+$/.test(alt)) continue;
    if (SKIP_TEXT.has(alt.toLowerCase())) continue;
    const a = img.closest('a[href]');
    if (!a) continue;
    const href = a.getAttribute('href') || '';
    if (skip(href)) continue;
    const id = parseId(href);
    if (id) out[id] = alt;
  }

  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (skip(href)) continue;
    const id = parseId(href);
    if (!id || id in out) continue;
    const text = (a.innerText || '').trim();
    if (!text || text.length < 2 || text.length > 80 || text.includes('\\n')) continue;
    if (SKIP_TEXT.has(text.toLowerCase())) continue;
    if (/^\\d+$/.test(text) || text.toLowerCase().includes('facebook')) continue;
    out[id] = text;
  }

  return out;
}
"""


def _collect_friend_entries(page, entries):
    try:
        batch = page.evaluate(_COLLECT_JS)
        if isinstance(batch, dict):
            entries.update(batch)
    except Exception:
        pass


def _fb_id_to_url(fb_id):
    if fb_id.isdigit():
        return f"https://www.facebook.com/profile.php?id={fb_id}"
    return f"https://www.facebook.com/{fb_id}"


def _scrape_one_profile(page, profile_url, all_entries, saved_ids, out_file, stop_event):
    friends_url = profile_url.rstrip("/") + "/friends"
    print(f"\n  Navigating to {friends_url}")

    try:
        page.goto(friends_url, timeout=40000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
    except Exception as e:
        print(f"\n  Could not load page: {e}")
        return []

    if "login" in page.url:
        print("\n  Session expired. Please import fresh cookies (Option 3 in the menu).")
        return None

    time.sleep(2)

    newly_found = {}

    def flush_new():
        count = 0
        for fb_id, name in all_entries.items():
            if fb_id not in saved_ids:
                out_file.write(f"{fb_id}|{name}\n")
                saved_ids.add(fb_id)
                newly_found[fb_id] = name
                count += 1
        if count:
            out_file.flush()
        return count

    scroll_step = 1400
    current_y = 0
    stall_count = 0
    max_stalls = 8
    scroll_num = 0
    last_scroll_height = 0

    while stall_count < max_stalls and not stop_event.is_set():
        before = len(all_entries)
        _collect_friend_entries(page, all_entries)
        flush_new()

        current_y += scroll_step
        page.evaluate(f"window.scrollTo(0, {current_y})")

        after = len(all_entries)
        if after > before:
            stall_count = 0
            time.sleep(0.7)
        else:
            stall_count += 1
            time.sleep(2.0)
            new_h = page.evaluate("document.documentElement.scrollHeight")
            if new_h > last_scroll_height:
                stall_count = 0
                last_scroll_height = new_h

        scroll_num += 1
        print(
            f"\r  Total collected: {len(all_entries)} | +{len(newly_found)} this profile "
            f"(scroll {scroll_num}, stall {stall_count}/{max_stalls})...",
            end="", flush=True,
        )

    _collect_friend_entries(page, all_entries)
    flush_new()
    print(f"\r  Profile done — {len(newly_found)} new entries found.                                       ")
    return list(newly_found.items())


def extract_friends(playwright, profile_url, output_path):
    import random
    import threading

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("\nLaunching browser...")
    browser = _launch_chromium(playwright, headless=True)
    context = _new_context(browser)

    if not load_cookies(context):
        browser.close()
        print("\nNo saved session found. Please import your cookies first (Option 3 in the menu).")
        return []

    page = context.new_page()
    _stealth.apply_stealth_sync(page)

    all_entries = {}
    saved_ids = set()
    visited = set()

    stop_event = threading.Event()

    def _watch_enter():
        try:
            input()
        except Exception:
            pass
        stop_event.set()
        print("\n  [Enter pressed — will stop after the current profile finishes]")

    watcher = threading.Thread(target=_watch_enter, daemon=True)
    watcher.start()

    print(f"  Results will be saved incrementally to: {output_path}")
    print("  Press Enter at any time to stop after the current profile.")
    print("  Press Ctrl-C to abort immediately.\n")

    queue = [profile_url]
    profiles_done = 0

    out_file = open(output_path, "a", encoding="utf-8")
    try:
        while queue and not stop_event.is_set():
            current_url = queue.pop(0)
            norm = current_url.rstrip("/")
            if norm in visited:
                candidates = [
                    (fid, name) for fid, name in all_entries.items()
                    if _fb_id_to_url(fid).rstrip("/") not in visited
                ]
                if not candidates:
                    print("\n  No more unvisited profiles available. Stopping.")
                    break
                fid, name = random.choice(candidates)
                queue.append(_fb_id_to_url(fid))
                continue

            visited.add(norm)
            profiles_done += 1
            print(f"\n{'─' * 60}")
            print(f"  Profile #{profiles_done}: {current_url}")
            print(f"{'─' * 60}")

            result = _scrape_one_profile(
                page, current_url, all_entries, saved_ids, out_file, stop_event
            )

            if result is None:
                break

            if stop_event.is_set():
                break

            candidates = [
                (fid, name) for fid, name in all_entries.items()
                if _fb_id_to_url(fid).rstrip("/") not in visited
            ]
            if not candidates:
                print("\n  No more unvisited profiles available. Stopping.")
                break

            fid, name = random.choice(candidates)
            next_url = _fb_id_to_url(fid)
            print(f"\n  Next → randomly selected: {name} ({fid})")
            queue.append(next_url)

    except KeyboardInterrupt:
        print("\n\n  Ctrl-C received — stopping.")
    finally:
        out_file.close()
        browser.close()

    total = len(all_entries)
    print(f"\n  ══ Session complete ══")
    print(f"  Profiles scraped : {profiles_done}")
    print(f"  Unique entries   : {total}")
    print(f"  Saved to         : {output_path}")
    return sorted(all_entries.items(), key=lambda x: x[1].lower())


# ---------------------------------------------------------------------------
# Comments extraction
# ---------------------------------------------------------------------------

def expand_comments(page, max_clicks=100):
    for attempt in range(max_clicks):
        clicked = False
        for label in [
            "View more comments",
            "View previous comments",
            "See more comments",
            "Load more comments",
        ]:
            buttons = page.query_selector_all(f'[role="button"]:has-text("{label}")')
            for btn in buttons:
                try:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(1.5)
                    clicked = True
                except Exception:
                    pass

        reply_buttons = page.query_selector_all('[role="button"]:has-text("replies")')
        for btn in reply_buttons:
            try:
                btn.scroll_into_view_if_needed()
                btn.click()
                time.sleep(1)
                clicked = True
            except Exception:
                pass

        if not clicked:
            break
        print(f"\r  Expanding comments... (pass {attempt + 1})", end="", flush=True)
    print()


def is_profile_link(href):
    if not href:
        return False
    if "profile.php" in href:
        return True
    import re
    if re.search(r'facebook\.com/(?!watch|groups|events|pages|stories|marketplace|gaming|ads|help|login|share)[^/?#]+', href):
        return True
    return False


def extract_names_from_page(page):
    names = set()

    for el in page.query_selector_all('[role="article"] a[href], [data-testid*="comment"] a[href]'):
        href = el.get_attribute("href") or ""
        if not is_profile_link(href):
            continue
        text = el.inner_text().strip()
        if text and 2 <= len(text) <= 80 and "\n" not in text and not text.startswith("http"):
            names.add(text)

    for el in page.query_selector_all('a[href*="profile.php"], a[href*="/user/"]'):
        text = el.inner_text().strip()
        if text and 2 <= len(text) <= 80 and "\n" not in text and not text.startswith("http"):
            names.add(text)

    for el in page.query_selector_all('a[href]'):
        href = el.get_attribute("href") or ""
        if not is_profile_link(href):
            continue
        text = el.inner_text().strip()
        if (
            text
            and 2 <= len(text) <= 80
            and "\n" not in text
            and not text.startswith("http")
            and "facebook" not in text.lower()
            and "messenger" not in text.lower()
        ):
            names.add(text)

    return names


def extract_comments(playwright, post_url):
    print("\nLaunching browser...")
    browser = _launch_chromium(playwright, headless=True, slow_mo=30)
    context = _new_context(browser)

    if not load_cookies(context):
        browser.close()
        print("\nNo saved session found. Please import your cookies first (Option 3 in the menu).")
        return []

    if not get_cookie(context, "c_user"):
        browser.close()
        print("\nYour saved session is incomplete (missing auth token).")
        print("Please import fresh cookies (Option 3 in the menu).")
        return []

    page = context.new_page()
    _stealth.apply_stealth_sync(page)

    print("  Warming up session...")
    page.goto("https://www.facebook.com/", timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    if "login" in page.url:
        browser.close()
        print("\nSession expired. Please import fresh cookies (Option 3 in the menu).")
        return []

    print("  Navigating to post...")
    page.goto(post_url, timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(3)

    all_links = page.query_selector_all('a[href]')
    if len(all_links) < 10:
        browser.close()
        print("\nFacebook is not showing the post content.")
        print("  - The post may be 'Friends only' and your account isn't friends with the poster.")
        print("  - Your session may have expired — try importing fresh cookies.")
        return []

    print("  Scrolling and expanding comments...")
    scroll_to_bottom(page, pause=2.0, max_scrolls=100)
    expand_comments(page, max_clicks=100)
    scroll_to_bottom(page, pause=1.5, max_scrolls=50)

    print("  Extracting commenter names...")
    names = extract_names_from_page(page)

    browser.close()
    return sorted(names)


# ---------------------------------------------------------------------------
# Session checker
# ---------------------------------------------------------------------------

def check_session(playwright):
    """
    Launch a headless browser, load the saved cookies, and navigate to
    Facebook to determine whether the session is still active.

    Returns a dict:
        status  : "valid" | "expired" | "no_cookies" | "error"
        user_id : the c_user value if known, else None
        message : human-readable detail
    """
    if not COOKIES_FILE.exists():
        return {"status": "no_cookies", "user_id": None,
                "message": "No cookie file found. Import your cookies first (Option 3)."}

    try:
        cookies = json.loads(COOKIES_FILE.read_text())
    except Exception as e:
        return {"status": "error", "user_id": None,
                "message": f"Could not read cookie file: {e}"}

    c_user = next((c for c in cookies if c.get("name") == "c_user"), None)
    user_id = c_user["value"] if c_user else None

    print("\n  Launching browser to verify session...")
    try:
        browser = _launch_chromium(playwright, headless=True)
        context = _new_context(browser)

        if not load_cookies(context):
            browser.close()
            return {"status": "no_cookies", "user_id": None,
                    "message": "Cookie file is empty or unreadable."}

        page = context.new_page()
        _stealth.apply_stealth_sync(page)

        print("  Connecting to Facebook...")
        page.goto("https://www.facebook.com/", timeout=40000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)

        current_url = page.url
        browser.close()

        if "login" in current_url or "recover" in current_url:
            return {"status": "expired", "user_id": user_id,
                    "message": "Session has expired. Import fresh cookies (Option 3)."}

        # Extra check: confirm c_user is still in the live context
        if not c_user:
            return {"status": "expired", "user_id": None,
                    "message": "Logged-in cookie (c_user) is missing. Import fresh cookies."}

        return {"status": "valid", "user_id": user_id,
                "message": f"Session is active. Logged in as user ID {user_id}."}

    except Exception as e:
        return {"status": "error", "user_id": user_id,
                "message": f"Check failed with an error: {e}"}


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_comments_results(names, output_path):
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = Path(output_path)
    path.write_text("\n".join(names) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Cookie import (paste from terminal)
# ---------------------------------------------------------------------------

_VALID_SAMESITE = {"Strict", "Lax", "None"}


def _normalise_cookie(c):
    def fix_ss(v):
        if not v:
            return "None"
        n = str(v).strip().capitalize()
        return n if n in _VALID_SAMESITE else "None"

    entry = {
        "name":     c.get("name", ""),
        "value":    c.get("value", ""),
        "domain":   c.get("domain", ".facebook.com"),
        "path":     c.get("path", "/"),
        "secure":   c.get("secure", True),
        "httpOnly": c.get("httpOnly", False),
        "sameSite": fix_ss(c.get("sameSite")),
    }
    exp = c.get("expirationDate") or c.get("expires")
    if exp:
        entry["expires"] = int(exp)
    return entry


def import_cookies_from_text(raw_text):
    """Parse a JSON cookie list from pasted text and save to session_cookies.json."""
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"\n  Invalid JSON: {e}")
        print("  Make sure you copied the full cookie array from your browser export.")
        return False

    if not isinstance(raw, list):
        print("\n  Expected a JSON array (list) of cookies. Got something else.")
        return False

    cookies = [_normalise_cookie(c) for c in raw]

    key_names = {"c_user", "xs", "datr", "fr", "sb"}
    found = {c["name"] for c in cookies if c["name"] in key_names}

    print(f"\n  Parsed {len(cookies)} cookies total.")
    print(f"  Key auth cookies found: {found if found else 'none'}")

    if "c_user" not in found:
        print("\n  Warning: 'c_user' is missing — you may not be logged in on Facebook.")
    elif "xs" not in found:
        print("\n  Warning: 'xs' session token is missing — the session may not work.")
    else:
        print("  Session looks complete.")

    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"  Saved to {COOKIES_FILE}")
    return True


# ---------------------------------------------------------------------------
# Menu UI
# ---------------------------------------------------------------------------

def _clear():
    os.system("clear" if os.name != "nt" else "cls")


def _banner():
    print("=" * 50)
    print("       Facebook Data Extractor")
    print("=" * 50)


def _separator():
    print("-" * 50)


def _pause():
    input("\nPress Enter to return to the menu...")


def _prompt_output_filename(default_name):
    """Ask the user for an output filename and return the full path."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\n  Output files are saved inside the '{OUTPUT_DIR.name}/' folder.")
    name = input(f"  Enter filename (without extension) [{default_name}]: ").strip()
    if not name:
        name = default_name
    # Sanitise: remove path separators and trailing/leading spaces
    name = name.replace("/", "_").replace("\\", "_").strip()
    if not name:
        name = default_name
    return OUTPUT_DIR / f"{name}.txt"


def menu_extract_friends():
    _clear()
    _banner()
    print("\n  [ Extract Friends from Profile ]\n")

    profile_url = input("  Paste the Facebook profile URL: ").strip()
    if not profile_url:
        print("  No URL entered. Returning to menu.")
        _pause()
        return

    if not profile_url.startswith("http"):
        profile_url = "https://www.facebook.com/" + profile_url.lstrip("/")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = _prompt_output_filename(f"friends_{timestamp}")

    _separator()
    print(f"\n  Profile : {profile_url}")
    print(f"  Output  : {output_path}")
    _separator()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        entries = extract_friends(p, profile_url=profile_url, output_path=output_path)

    if entries:
        print(f"\n  Done. {len(entries)} unique friends saved to: {output_path}")
    else:
        print("\n  No entries found. Check that the profile is public and your session is valid.")

    _pause()


def menu_extract_comments():
    _clear()
    _banner()
    print("\n  [ Extract Commenters from Post ]\n")

    post_url = input("  Paste the Facebook post URL: ").strip()
    if not post_url:
        print("  No URL entered. Returning to menu.")
        _pause()
        return

    if not post_url.startswith("http"):
        print("  That doesn't look like a valid URL. Returning to menu.")
        _pause()
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = _prompt_output_filename(f"commenters_{timestamp}")

    _separator()
    print(f"\n  Post URL : {post_url}")
    print(f"  Output   : {output_path}")
    _separator()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        names = extract_comments(p, post_url=post_url)

    if names:
        save_comments_results(names, output_path)
        print(f"\n  Done. {len(names)} unique commenters saved to: {output_path}")
    else:
        print("\n  No names found. The post may be private or the session may have expired.")

    _pause()


def menu_check_session():
    _clear()
    _banner()
    print("\n  [ Check Session Validity ]\n")
    print("  This opens a hidden browser, loads your cookies, and")
    print("  connects to Facebook to confirm your session is still active.")
    print("  It takes about 10-15 seconds.\n")
    _separator()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        result = check_session(p)

    print()
    _separator()
    status = result["status"]

    if status == "valid":
        print(f"  RESULT : Session is VALID")
        print(f"  User ID: {result['user_id']}")
        print(f"\n  You are good to go — extractions will work.")
    elif status == "expired":
        print(f"  RESULT : Session has EXPIRED")
        if result["user_id"]:
            print(f"  User ID: {result['user_id']} (last known)")
        print(f"\n  {result['message']}")
    elif status == "no_cookies":
        print(f"  RESULT : No cookies found")
        print(f"\n  {result['message']}")
    else:
        print(f"  RESULT : Check failed")
        print(f"\n  {result['message']}")

    _separator()
    _pause()


def menu_import_cookies():
    _clear()
    _banner()
    print("\n  [ Import Cookies from Browser ]\n")
    print("  How to export cookies from your browser:")
    print("  1. Install the 'Cookie-Editor' extension (Chrome/Firefox).")
    print("  2. Log in to https://www.facebook.com")
    print("  3. Open Cookie-Editor, click Export → Copy as JSON.")
    print("  4. Paste the copied JSON below.\n")
    _separator()
    print("  Paste your cookie JSON below.")
    print("  When done, press Enter on a blank line to finish.\n")

    lines = []
    try:
        while True:
            line = input()
            if line == "" and lines:
                # Check if we already have a complete JSON blob
                combined = "".join(lines).strip()
                if combined.endswith("]") or combined.endswith("}"):
                    break
                # Otherwise keep collecting (multi-line paste may not be done)
                lines.append(line)
            else:
                lines.append(line)
    except EOFError:
        pass

    raw_text = "\n".join(lines).strip()

    if not raw_text:
        print("\n  Nothing was pasted. Returning to menu.")
        _pause()
        return

    _separator()
    success = import_cookies_from_text(raw_text)
    if success:
        print("\n  Cookies imported successfully! You can now extract friends or commenters.")
    else:
        print("\n  Cookie import failed. Please try again.")

    _pause()


def run_menu():
    while True:
        _clear()
        _banner()
        print()
        print("  1.  Extract Friends from a Profile")
        print("  2.  Extract Commenters from a Post")
        print("  3.  Import Cookies from Browser")
        print("  4.  Check Session Validity")
        print("  5.  Exit")
        print()
        _separator()

        # Show quick session status (reads file only, no browser)
        if COOKIES_FILE.exists():
            try:
                cookies = json.loads(COOKIES_FILE.read_text())
                c_user = next((c for c in cookies if c.get("name") == "c_user"), None)
                xs     = next((c for c in cookies if c.get("name") == "xs"),     None)
                if c_user and xs:
                    print(f"  Session : cookies loaded (user ID {c_user['value']})")
                    print(f"  Status  : use Option 4 to verify with Facebook")
                elif c_user:
                    print(f"  Session : incomplete — 'xs' token missing")
                    print(f"  Status  : import fresh cookies (Option 3)")
                else:
                    print(f"  Session : 'c_user' missing — not logged in")
                    print(f"  Status  : import fresh cookies (Option 3)")
            except Exception:
                print("  Session : cookie file unreadable")
        else:
            print("  Session : no cookies found")
            print("  Status  : import cookies first (Option 3)")

        _separator()
        choice = input("\n  Select an option (1-5): ").strip()

        if choice == "1":
            menu_extract_friends()
        elif choice == "2":
            menu_extract_comments()
        elif choice == "3":
            menu_import_cookies()
        elif choice == "4":
            menu_check_session()
        elif choice == "5":
            _clear()
            print("Goodbye.\n")
            sys.exit(0)
        else:
            print("\n  Invalid option. Please enter 1, 2, 3, 4, or 5.")
            time.sleep(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    run_menu()


if __name__ == "__main__":
    main()
