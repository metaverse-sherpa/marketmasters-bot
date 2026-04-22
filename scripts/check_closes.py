#!/usr/bin/env python3
"""
check_closes.py
----------------
Periodic poller for recently-closed Alpaca orders. Designed to be run
from a scheduler (e.g. GitHub Actions cron) every few minutes.

Responsibilities and design notes (senior-dev level):
- Keep runtime logic minimal and idempotent. The script should be safe to
  run frequently and should not raise on transient API or I/O failures.
- Use a best-effort persisted mapping `placed_brackets.json` (written by
  the main `trading_bot.py`) to attribute fills to original TP/SL targets.
- Avoid noisy duplicate notifications; this script leaves deduplication
  to the scheduler or to an optional local state file. That keeps the
  implementation simple and side-effect free for now.
- Make clear where to extend (e.g., storing a `seen_closes.json` for
  deduplication, or publishing events to an external system).

This script intentionally keeps external dependencies small: `requests`
and `python-dateutil` (for robust ISO parsing) are used.
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta
import requests
from dateutil import parser as dateparser
from urllib.parse import quote_plus


# Configuration via environment variables keeps the workflow secure and
# easy to configure via repository secrets.
try:
    # Optional: load local .env for development (non-invasive in CI)
    from dotenv import load_dotenv
    _loaded = load_dotenv()
    if _loaded:
        print("[INFO] Loaded environment variables from .env")
except Exception:
    pass

ALPACA_BASE_URL = os.environ.get(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2"
)
ALPACA_KEY = os.environ.get("ALPACA_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Lookback window for the poller. Set conservatively higher than the
# schedule frequency to account for action runtime and network delays.
CHECK_MINUTES = int(os.environ.get("CHECK_MINUTES", "6"))

# When inferring TP/SL we use an absolute tolerance floor (0.01) and a
# small percentage of the target to allow for tick differences and small
# slippage. This prevents false positives when targets are far away.
PRICE_TOLERANCE_PCT = float(os.environ.get("PRICE_TOLERANCE_PCT", "0.003"))


# Alpaca REST headers
HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}


def load_brackets(path="placed_brackets.json"):
    """
    Load the persisted mapping written by `trading_bot.py`.

    The mapping is keyed by primary_order_id (or a synthetic key) and
    contains the original entry/stop/target. If the file is missing or
    malformed we return an empty mapping — inference will still attempt
    heuristic matching but with lower confidence.
    """
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def load_seen(path="seen_closes.json"):
    """
    Load a set of previously-notified close keys to avoid duplicate
    notifications across runs. Returns a Python set of keys.
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def save_seen(seen: set, path="seen_closes.json"):
    """
    Persist the seen keys as a JSON list. Best-effort; failures are
    non-fatal and logged.
    """
    try:
        with open(path, "w") as f:
            json.dump(sorted(list(seen)), f, indent=2)
    except Exception as e:
        print("Warning: failed to save seen_closes.json:", e)


def order_key(o):
    """Generate a stable key for an order/close event.

    Prefer broker-assigned `id`, fall back to `client_order_id`, and as
    a last resort use a composite of symbol + filled_at timestamp.
    """
    oid = o.get("id") or o.get("client_order_id")
    if oid:
        return str(oid)
    symbol = o.get("symbol", "?")
    ts = o.get("filled_at") or o.get("updated_at") or ""
    return f"{symbol}:{ts}"


def since_iso(minutes=CHECK_MINUTES):
    """Return an ISO timestamp X minutes ago (UTC) for the Alpaca query.

    Alpaca's REST API expects a RFC3339-like timestamp. Using a 'Z'
    suffix for UTC (instead of '+00:00') avoids encoding issues and
    prevents `422 Unprocessable Entity` errors caused by unexpected
    timestamp formats in the `after` query parameter.
    """
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    # Use compact Zulu format (RFC3339-compatible) which Alpaca accepts.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_recent_closed_orders(since_iso_ts):
    """
    Query Alpaca for closed orders after the given ISO timestamp.

    Use `params=` so requests handles proper URL-encoding. Return the
    parsed JSON on success; raise or surface HTTP errors with the
    response body to aid debugging.
    """
    url = f"{ALPACA_BASE_URL}/orders"
    params = {"status": "closed", "limit": 500, "after": since_iso_ts}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    try:
        resp.raise_for_status()
    except Exception:
        # Surface response content to logs for easier debugging (e.g. 422)
        print("Alpaca error response:", resp.status_code, resp.text)
        raise
    return resp.json()


def guess_reason(fill_price, symbol, brackets):
    """
    Attempt to classify a close as TP or SL by comparing the fill price
    to any recorded targets in `brackets` for the same symbol.

    This is a pragmatic heuristic: exact matching isn't reliable due to
    slippage and tick sizes, so we use a small absolute floor and a small
    percentage-based tolerance.
    """
    try:
        price = float(fill_price)
    except Exception:
        return None

    # Collect candidate bracket records for this symbol. In more advanced
    # implementations we would index by order id (preferred) or by symbol
    # + timestamp window to avoid false matches.
    candidates = []
    for pid, data in brackets.items():
        if str(data.get("symbol", "")).upper() == str(symbol).upper():
            candidates.append(data)

    for c in candidates:
        try:
            tp = float(c.get("target"))
            sl = float(c.get("stop")) if c.get("stop") is not None else None
        except Exception:
            continue

        tol = max(0.01, PRICE_TOLERANCE_PCT * abs(tp))
        if abs(price - tp) <= tol:
            return "TP"
        if sl is not None and abs(price - sl) <= max(0.01, PRICE_TOLERANCE_PCT * abs(sl)):
            return "SL"

    return None


def send_telegram_message(text):
    """
    Send a short HTML-formatted Telegram message. If Telegram is not
    configured we simply print the text so the script's output can still
    be inspected in CI logs.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("Telegram not configured; would send:\n", text)
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    # Surface response for debugging in CI logs.
    print("Telegram response", resp.status_code, resp.text)
    return resp.status_code == 200


def main():
    # Validate credentials early and fail fast with clear exit codes for
    # the scheduler to act upon.
    if not ALPACA_KEY or not ALPACA_SECRET:
        print("Alpaca credentials missing; set ALPACA_KEY and ALPACA_SECRET")
        return 2

    since = since_iso()
    print(f"Checking closed orders since {since} (UTC) ...")
    try:
        orders = fetch_recent_closed_orders(since)
    except Exception as e:
        # Network or API error; exit non-zero so the scheduler can retry.
        print("Failed to fetch orders:", e)
        return 1

    brackets = load_brackets()

    seen = load_seen()
    found = []
    to_mark_seen = set()

    for o in orders:
        # Use filled_at when available; otherwise updated_at is a reasonable
        # fallback. Some broker responses vary across endpoints/versions.
        filled_at = o.get("filled_at") or o.get("updated_at")
        if not filled_at:
            continue
        try:
            when = dateparser.isoparse(filled_at)
        except Exception:
            continue

        # Only consider recent fills within our configured lookback window.
        if when < (datetime.now(timezone.utc) - timedelta(minutes=CHECK_MINUTES)):
            continue

        symbol = o.get("symbol")
        side = o.get("side")
        filled_avg = o.get("filled_avg_price") or o.get("filled_price") or o.get("limit_price")

        reason = None
        if filled_avg:
            reason = guess_reason(filled_avg, symbol, brackets)

        # Deduplicate: skip orders we've already notified about.
        key = order_key(o)
        if key in seen:
            # already notified in a previous run
            continue

        found.append({
            "symbol": symbol,
            "side": side,
            "price": filled_avg,
            "when": filled_at,
            "reason": reason,
            "_order_key": key,
        })
        to_mark_seen.add(key)

    if not found:
        print("No recent closes found.")
        return 0

    # Build a single message summarizing closes. Keep the message compact
    # so it fits in notifications and is easy to scan.
    text = f"Recent closes (last {CHECK_MINUTES}m):\n"
    for f in found:
        sym = f.get("symbol")
        reason = f.get("reason") or "manual/other"
        price = f.get("price")
        price_str = f"{float(price):.2f}" if price else "n/a"
        url = f"https://marketmasters.ai/stocks/{quote_plus(str(sym))}"
        text += f"• <a href=\"{url}\">{sym}</a> — {reason} at ${price_str}\n"

    ok = send_telegram_message(text)

    # If message sent (or printed), persist seen keys so subsequent runs
    # don't re-notify the same closes. We mark seen even if the Telegram
    # send failed when the message was printed locally, to avoid spam in
    # CI logs; adjust behavior here if you prefer retry semantics.
    if ok:
        seen.update(to_mark_seen)
        save_seen(seen)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
