import json
import sys
from pathlib import Path

INPUT_FILE = Path(__file__).parent / "facebook_cookies.json"
OUTPUT_FILE = Path(__file__).parent / "session_cookies.json"


VALID_SAMESITE = {"Strict", "Lax", "None"}


def normalise_samesite(value):
    if not value:
        return "None"
    # Capitalise first letter so "strict" -> "Strict", "lax" -> "Lax" etc.
    normalised = str(value).strip().capitalize()
    return normalised if normalised in VALID_SAMESITE else "None"


def convert(cookies):
    """Normalise cookies to the format Playwright expects."""
    out = []
    for c in cookies:
        entry = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ".facebook.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": normalise_samesite(c.get("sameSite")),
        }
        exp = c.get("expirationDate") or c.get("expires")
        if exp:
            entry["expires"] = int(exp)
        out.append(entry)
    return out


def main():
    if not INPUT_FILE.exists():
        print(f"Error: {INPUT_FILE} not found.")
        print(__doc__)
        sys.exit(1)

    try:
        raw = json.loads(INPUT_FILE.read_text())
    except json.JSONDecodeError as e:
        print(f"Error reading {INPUT_FILE}: {e}")
        sys.exit(1)

    cookies = convert(raw)

    key_names = {"c_user", "xs", "datr", "fr", "sb"}
    found = {c["name"] for c in cookies if c["name"] in key_names}
    print(f"Found {len(cookies)} cookies total.")
    print(f"Key auth cookies present: {found}")

    if "c_user" not in found:
        print("\nWarning: 'c_user' cookie is missing — you may not be logged in on Facebook.")
        print("Make sure you are logged in at https://www.facebook.com before exporting.")
    elif "xs" not in found:
        print("\nWarning: 'xs' session token is missing — the session may not work.")
    else:
        print("Session looks complete.")

    OUTPUT_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"\nSaved to {OUTPUT_FILE}")
    print("You can now run:  python3 extractor.py comments --url \"YOUR_POST_URL\"")


if __name__ == "__main__":
    main()
