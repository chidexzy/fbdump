import argparse
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

CHROMIUM_PATH = "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"


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


def check_logged_in(page):
    try:
        page.goto("https://www.facebook.com/", timeout=30000)
        page.wait_for_load_state("domcontentloaded")
        return "login" not in page.url and page.query_selector('[aria-label="Your profile"]') is not None
    except Exception:
        return False


def get_cookie(context, name):
    return next((c for c in context.cookies() if c["name"] == name), None)


def click_through_interstitials(page, context, max_rounds=8):
    """Click past Facebook's post-login screens (Save login info, Trust device, etc.)"""
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
    browser = playwright.chromium.launch(headless=True, slow_mo=60, executable_path=CHROMIUM_PATH)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = context.new_page()
    _stealth.apply_stealth_sync(page)
    page.goto("https://www.facebook.com/login", timeout=40000)
    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(2)

    # Dismiss cookie consent if present
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

    # Fill email
    email_field = None
    for sel in ['input[name="email"]', '#email', 'input[type="email"]', 'input[type="text"]']:
        email_field = page.query_selector(sel)
        if email_field:
            break
    if not email_field:
        browser.close()
        print("\nCould not find the login form. Facebook may have changed its layout.")
        sys.exit(1)
    email_field.click()
    email_field.fill(email)
    time.sleep(0.5)

    # Fill password
    pass_field = None
    for sel in ['input[name="pass"]', '#pass', 'input[type="password"]']:
        pass_field = page.query_selector(sel)
        if pass_field:
            break
    if not pass_field:
        browser.close()
        print("\nCould not find the password field.")
        sys.exit(1)
    pass_field.click()
    pass_field.fill(password)
    time.sleep(0.5)
    pass_field.press("Enter")

    # Wait for navigation after login
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(3)

    # Handle 2FA / checkpoint
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

    # Click through any post-login interstitials and wait for c_user cookie
    print("  Completing login (handling any extra steps)...")
    success = click_through_interstitials(page, context, max_rounds=10)

    if not success:
        # Last resort: check if we're on the feed anyway
        page.goto("https://www.facebook.com/", timeout=20000)
        time.sleep(3)
        success = bool(get_cookie(context, "c_user"))

    if not success:
        browser.close()
        print("\nLogin did not fully complete — the session token was not set.")
        print("This can happen if Facebook flagged the login. Please try again.")
        sys.exit(1)

    save_cookies(context)
    browser.close()
    user_cookie = get_cookie(context, "c_user")
    print(f"Logged in successfully (user ID: {user_cookie['value'] if user_cookie else '?'}).")
    print("Session saved — you won't need to log in again unless it expires.")


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

# Href path segments that are never friend profiles
# ---------------------------------------------------------------------------
# Single-pass JS snippet: collects all visible friend entries in one round-trip.
# Running this inside page.evaluate() avoids hundreds of individual CDP calls
# that made the old per-element Python loop so slow.
# ---------------------------------------------------------------------------
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

  // Primary: profile picture alt text -> enclosing profile link
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

  // Fallback: link inner text for any profile link not yet captured
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
    """Single JS round-trip to harvest all currently visible friend entries."""
    try:
        batch = page.evaluate(_COLLECT_JS)
        if isinstance(batch, dict):
            entries.update(batch)
    except Exception:
        pass


def _fb_id_to_url(fb_id):
    """Build a Facebook profile URL from a numeric ID or username slug."""
    if fb_id.isdigit():
        return f"https://www.facebook.com/profile.php?id={fb_id}"
    return f"https://www.facebook.com/{fb_id}"


def _scrape_one_profile(page, profile_url, all_entries, saved_ids, out_file, stop_event):
    """
    Navigate to profile_url/friends and scroll-collect all friends into
    all_entries, flushing new ones to out_file as they arrive.

    Returns a list of (id, name) tuples newly found in this pass,
    or None if the session has expired.
    """
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
        print("\n  Session expired. Run `python extractor.py login` again.")
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

    # Final harvest
    _collect_friend_entries(page, all_entries)
    flush_new()
    print(f"\r  Profile done — {len(newly_found)} new entries found.                                       ")
    return list(newly_found.items())


def extract_friends(playwright, profile_url, output_path):
    """
    Chain-crawl friends lists starting from profile_url.

    After each profile is exhausted, a random unvisited ID from all collected
    entries is chosen and its friends list is scraped next.  The cycle
    continues indefinitely until:
      - Enter is pressed (finishes the current profile then stops), or
      - Ctrl-C / Ctrl-Z is used to abort immediately.

    All results are written incrementally so nothing is lost on interruption.
    """
    import random
    import threading

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("\nLaunching browser...")
    browser = playwright.chromium.launch(headless=True, executable_path=CHROMIUM_PATH)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )

    if not load_cookies(context):
        browser.close()
        print("No saved session found. Run `python extractor.py login` first.")
        sys.exit(1)

    page = context.new_page()
    _stealth.apply_stealth_sync(page)

    all_entries = {}    # id -> name across all profiles, used for dedup
    saved_ids = set()   # ids already written to disk
    visited = set()     # normalised profile URLs already scraped

    # Stop flag — set by Enter-key thread or Ctrl-C handler
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
                # Pick the next candidate straight away
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
                # Session expired
                break

            if stop_event.is_set():
                break

            # Pick a random unvisited ID from everything collected so far
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
    """Return True if href looks like a Facebook profile link."""
    if not href:
        return False
    # profile.php?id=... or /username style paths
    if "profile.php" in href:
        return True
    import re
    # /user/<numeric id> or /<username> on facebook.com
    if re.search(r'facebook\.com/(?!watch|groups|events|pages|stories|marketplace|gaming|ads|help|login|share)[^/?#]+', href):
        return True
    return False


def extract_names_from_page(page):
    """Extract all unique commenter names from the current page state."""
    names = set()

    # Strategy 1: links inside comment/article blocks
    for el in page.query_selector_all('[role="article"] a[href], [data-testid*="comment"] a[href]'):
        href = el.get_attribute("href") or ""
        if not is_profile_link(href):
            continue
        text = el.inner_text().strip()
        if text and 2 <= len(text) <= 80 and "\n" not in text and not text.startswith("http"):
            names.add(text)

    # Strategy 2: any profile link on the page
    for el in page.query_selector_all('a[href*="profile.php"], a[href*="/user/"]'):
        text = el.inner_text().strip()
        if text and 2 <= len(text) <= 80 and "\n" not in text and not text.startswith("http"):
            names.add(text)

    # Strategy 3: broad sweep — any anchor whose href looks like a profile
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
    browser = playwright.chromium.launch(headless=True, slow_mo=30, executable_path=CHROMIUM_PATH)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )

    if not load_cookies(context):
        browser.close()
        print("No saved session found. Run `python extractor.py login` first.")
        sys.exit(1)

    # Verify session has the critical auth cookie
    if not get_cookie(context, "c_user"):
        browser.close()
        print("Your saved session is incomplete (missing auth token).")
        print("Please run `python extractor.py login` again to refresh it.")
        sys.exit(1)

    page = context.new_page()
    _stealth.apply_stealth_sync(page)

    # Warm up session on homepage first so cookies are fully active
    print("  Warming up session...")
    page.goto("https://www.facebook.com/", timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    if "login" in page.url:
        browser.close()
        print("Session expired. Run `python extractor.py login` again.")
        sys.exit(1)

    print("  Navigating to post...")
    page.goto(post_url, timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(3)

    # Check we actually got content (not a login wall)
    all_links = page.query_selector_all('a[href]')
    if len(all_links) < 10:
        browser.close()
        print("\nFacebook is not showing the post content.")
        print("This usually means:")
        print("  - The post is set to 'Friends only' and your account isn't friends with the poster")
        print("  - Your session expired — try running `python extractor.py login` again")
        sys.exit(1)

    print("  Scrolling and expanding comments...")
    scroll_to_bottom(page, pause=2.0, max_scrolls=100)
    expand_comments(page, max_clicks=100)
    scroll_to_bottom(page, pause=1.5, max_scrolls=50)

    print("  Extracting commenter names...")
    names = extract_names_from_page(page)

    browser.close()
    return sorted(names)


def save_results(names, label):
    """Save a plain list of names (used by comments command)."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = OUTPUT_DIR / f"{label}_{timestamp}.txt"
    filename.write_text("\n".join(names) + "\n")
    return filename


def save_friends_results(entries, output_path):
    """Append ID|Name lines to output_path. Creates the file if it doesn't exist."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    lines = [f"{fb_id}|{name}" for fb_id, name in entries]
    with open(output_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Extract names from Facebook friend list or post comments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="Open browser and log in to Facebook (saves session)")

    friends_parser = subparsers.add_parser(
        "friends",
        help="Extract ID and name of friends from a profile URL (output: ID|Name, one per line)",
    )
    friends_parser.add_argument(
        "--profile",
        required=True,
        metavar="URL",
        help="Facebook profile URL to scrape friends from (e.g. https://www.facebook.com/someprofile).",
    )
    friends_parser.add_argument(
        "--output",
        metavar="FILE",
        default=str(OUTPUT_DIR / "friends.txt"),
        help="File to append results to. Defaults to output/friends.txt.",
    )

    comments_parser = subparsers.add_parser("comments", help="Extract names of people who commented on a post")
    comments_parser.add_argument("--url", required=True, metavar="POST_URL", help="Full URL of the Facebook post")

    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    if args.command == "login":
        with sync_playwright() as p:
            do_login(p)

    elif args.command == "friends":
        with sync_playwright() as p:
            print(f"Extracting friends from {args.profile} ...")
            entries = extract_friends(p, profile_url=args.profile, output_path=args.output)
        if not entries:
            print("No entries found. Check that the profile is public and your session is valid.")
            sys.exit(1)
        print(f"\nFound {len(entries)} friends. Results saved to: {args.output}")

    elif args.command == "comments":
        with sync_playwright() as p:
            print(f"Extracting commenters from post...")
            names = extract_comments(p, post_url=args.url)
        if not names:
            print("No names found. The post may be private, or the session may have expired.")
            sys.exit(1)
        out_file = save_results(names, "commenters")
        print(f"\nFound {len(names)} unique commenters:\n")
        for name in names:
            print(f"  {name}")
        print(f"\nSaved to: {out_file}")


if __name__ == "__main__":
    main()
