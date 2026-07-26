#!/usr/bin/env python3
"""Monitor the Lacanau fire bulletin page and push a phone notification on change.

Fetches https://www.lacanau.fr/actualite/incendie-en-cours-saumos/, extracts the
article title + body, normalizes and hashes it, and compares against the last-seen
hash in state.json. On change it sends an ntfy.sh push, records history, and refreshes
the dashboard data (docs/data.json).

Environment:
  NTFY_URL   Full ntfy topic URL, e.g. https://ntfy.sh/lacanau-fire-<random>.
             If unset, notifications are printed to stdout instead of sent (dry run).

Usage:
  python monitor.py           # normal run
  python monitor.py --test    # send a test push and exit (verifies ntfy works)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# Make console output UTF-8 safe (Windows defaults to cp1252 and chokes on emoji).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SOURCE_URL = "https://www.lacanau.fr/actualite/incendie-en-cours-saumos/"
NTFY_URL = os.environ.get("NTFY_URL", "").strip()

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(ROOT, "state.json")
HISTORY_FILE = os.path.join(ROOT, "history.json")
DOCS_DIR = os.path.join(ROOT, "docs")
DATA_FILE = os.path.join(DOCS_DIR, "data.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FAIL_THRESHOLD = 3               # consecutive failures before alerting
HEARTBEAT_HOURS = 24             # send an "alive" ping if silent this long
EXCERPT_CHARS = 400              # max chars of body sent in a notification


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# State / history persistence
# --------------------------------------------------------------------------- #
def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# Fetch + extract
# --------------------------------------------------------------------------- #
def fetch_html() -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(SOURCE_URL, headers=headers, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except Exception as err:  # noqa: BLE001 - any failure retries then bubbles up
            last_err = err
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed after 3 attempts: {last_err}")


def extract(html: str) -> tuple[str, str]:
    """Return (title, body_text) from the bulletin article.

    The page is WordPress: title lives in `.article-title`, the bulletin body in
    `.article-content`. We fall back to the whole <article>, then <body>, so a
    theme change degrades to "still detects something" rather than crashing.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title_el = soup.select_one(".article-title") or soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    body_el = (
        soup.select_one(".article-content")
        or soup.select_one("article")
        or soup.body
        or soup
    )
    body_text = normalize(body_el.get_text(" ", strip=True))
    return title, body_text


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Notification
# --------------------------------------------------------------------------- #
def notify(title: str, message: str, priority: int = 3, tags: str = "fire") -> None:
    """Send an ntfy push. Prints instead of sending when NTFY_URL is unset."""
    if not NTFY_URL:
        print(f"[DRY RUN notify] ({priority}) {title}\n{message}\n")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": str(priority),
                "Tags": tags,
                "Click": SOURCE_URL,
            },
            timeout=15,
        )
        print(f"[notify sent] ({priority}) {title}")
    except Exception as err:  # noqa: BLE001 - never let a push failure crash the run
        print(f"[notify FAILED] {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def add_history(kind: str, title: str, excerpt: str = "") -> list:
    history = load_json(HISTORY_FILE, [])
    history.insert(0, {
        "timestamp": iso(now()),
        "kind": kind,           # baseline | change | error | heartbeat
        "title": title,
        "excerpt": excerpt[:EXCERPT_CHARS],
    })
    history = history[:200]
    save_json(HISTORY_FILE, history)
    return history


def write_dashboard(state: dict, status: str) -> None:
    save_json(DATA_FILE, {
        "source_url": SOURCE_URL,
        "status": status,                                   # ok | error
        "last_checked": state.get("last_checked"),
        "last_changed": state.get("last_changed"),
        "current_title": state.get("last_title", ""),
        "fail_count": state.get("fail_count", 0),
        "generated_at": iso(now()),
        "history": load_json(HISTORY_FILE, []),
    })


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run() -> int:
    state = load_json(STATE_FILE, {})

    # --- fetch (with failure handling) ---
    try:
        html = fetch_html()
        title, body = extract(html)
    except Exception as err:  # noqa: BLE001
        fail_count = state.get("fail_count", 0) + 1
        state["fail_count"] = fail_count
        state["last_checked"] = iso(now())
        print(f"[fetch error] {err} (consecutive={fail_count})", file=sys.stderr)
        if fail_count == FAIL_THRESHOLD:
            notify(
                "⚠️ Can't reach the Lacanau fire page",
                f"{FAIL_THRESHOLD} checks in a row failed:\n{err}\n\nCheck manually: {SOURCE_URL}",
                priority=4,
                tags="warning",
            )
            add_history("error", state.get("last_title", ""), str(err))
        save_json(STATE_FILE, state)
        write_dashboard(state, status="error")
        return 1

    new_hash = content_hash(title, body)
    prev_hash = state.get("last_hash")
    checked_at = iso(now())

    state["last_checked"] = checked_at
    state["last_title"] = title
    state["fail_count"] = 0

    if prev_hash is None:
        # First ever run: establish baseline, one confirmation ping, no change alert.
        state["last_hash"] = new_hash
        state["last_text"] = body
        state["last_changed"] = checked_at
        state["last_heartbeat"] = checked_at
        add_history("baseline", title, body)
        notify(
            "✅ Lacanau fire monitor active",
            f"Now watching for updates.\nCurrent bulletin: {title}",
            priority=2,
            tags="white_check_mark",
        )
        print("[baseline recorded]")

    elif new_hash != prev_hash:
        # Page changed → urgent alert.
        state["last_hash"] = new_hash
        state["last_text"] = body
        state["last_changed"] = checked_at
        state["last_heartbeat"] = checked_at
        excerpt = body[:EXCERPT_CHARS] + ("…" if len(body) > EXCERPT_CHARS else "")
        add_history("change", title, body)
        notify(
            f"🔥 Lacanau fire page UPDATED",
            f"{title}\n\n{excerpt}",
            priority=5,
            tags="fire,rotating_light",
        )
        print(f"[CHANGE detected] {title}")

    else:
        # No change. Emit a heartbeat if we've been silent too long.
        last_hb = parse_iso(state.get("last_heartbeat"))
        if last_hb is None or now() - last_hb >= timedelta(hours=HEARTBEAT_HOURS):
            state["last_heartbeat"] = checked_at
            add_history("heartbeat", title, "")
            notify(
                "🟢 Lacanau monitor: no change",
                f"Still watching. Latest bulletin unchanged:\n{title}",
                priority=1,
                tags="green_circle",
            )
            print("[heartbeat sent]")
        else:
            print(f"[no change] {title}")

    save_json(STATE_FILE, state)
    write_dashboard(state, status="ok")
    return 0


def main() -> int:
    if "--test" in sys.argv:
        notify(
            "🧪 Lacanau monitor test",
            "If you can read this on your phone, ntfy is wired up correctly.",
            priority=3,
            tags="test_tube",
        )
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
