#!/usr/bin/env python3
"""
Trading Bot: MarketMasters.ai Chart Patterns -> Alpaca Paper Trading

- Fetches all active chart patterns from MarketMasters API
- Places bracket orders (entry + stop loss + take profit) on Alpaca
- Skips symbols that already have an open position or order in Alpaca
- Designed to run on a 24-hour schedule
"""

import os
import sys
import requests
from datetime import datetime
import json
import hashlib

# Senior-dev notes:
# - This script bridges MarketMasters pattern signals to Alpaca paper trading.
# - Security: API keys should never be committed. Use a local .env (gitignored)
#   for development and GitHub Actions Secrets for CI runs. See README.md.
# - Operation model: run daily (or on-demand). The bot will:
#   1) fetch active bullish patterns from MarketMasters
#   2) skip symbols that already have positions or open orders in Alpaca
#   3) place bracket orders (entry, stop loss, take profit) using limit or
#      stop-limit order types depending on whether the breakout has occurred.
# - Persistence: `traded_patterns.json` stores pattern ids already acted on to
#   avoid duplicate entries across runs. The bot will re-check Alpaca — if
#   no position/order exists for a recorded pattern, it will attempt again.

# ── Config ────────────────────────────────────────────────────────────────────

MARKETMASTERS_API_KEY = os.environ.get("MARKETMASTERS_API_KEY")
MARKETMASTERS_URL = os.environ.get("MARKETMASTERS_URL", "https://api.marketmasters.ai/v1/stocks/patterns")

# Default params used to fetch patterns from MarketMasters. This can be
# overridden by setting `MARKETMASTERS_PARAMS` in the environment. The
# variable may be a JSON object (recommended) or a comma-separated list
# of `key=value` pairs (convenience).
DEFAULT_MARKETMASTERS_PARAMS = {"status": "active", "bullish": "true"}
_mm_params_raw = os.environ.get("MARKETMASTERS_PARAMS")
if _mm_params_raw:
    try:
        MARKETMASTERS_PARAMS = json.loads(_mm_params_raw)
        if not isinstance(MARKETMASTERS_PARAMS, dict):
            raise ValueError("MARKETMASTERS_PARAMS must decode to a JSON object")
    except Exception:
        # Fallback: parse simple comma-separated key=value pairs
        try:
            MARKETMASTERS_PARAMS = {}
            for part in _mm_params_raw.split(","):
                if not part:
                    continue
                if "=" in part:
                    k, v = part.split("=", 1)
                    MARKETMASTERS_PARAMS[k.strip()] = v.strip()
        except Exception:
            print("[WARN] Could not parse MARKETMASTERS_PARAMS; using default")
            MARKETMASTERS_PARAMS = DEFAULT_MARKETMASTERS_PARAMS
else:
    MARKETMASTERS_PARAMS = DEFAULT_MARKETMASTERS_PARAMS

ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
ALPACA_KEY = os.environ.get("ALPACA_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET")

_pct_raw = os.environ.get("ALPCACA_PERCENTAGE_PER_TRADE")
# Default to 2% if unset or invalid. Accept empty-string from env as unset.
if _pct_raw is None or _pct_raw == "":
    PORTFOLIO_PCT = 0.02
else:
    try:
        PORTFOLIO_PCT = float(_pct_raw)
    except Exception:
        print("[WARN] Invalid ALPCACA_PERCENTAGE_PER_TRADE; using default 0.02")
        PORTFOLIO_PCT = 0.02

# Buffer applied to the limit leg for stop-limit entries when the market
# price is below the breakout. Default is 1% (0.01). Make configurable
# via the BREAKOUT_LIMIT_BUFFER environment variable (fractional).
try:
    BREAKOUT_LIMIT_BUFFER = float(os.environ.get("BREAKOUT_LIMIT_BUFFER", "0.01"))
except Exception:
    print("[WARN] Invalid BREAKOUT_LIMIT_BUFFER environment variable; using default 0.01")
    BREAKOUT_LIMIT_BUFFER = 0.01

ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}


REQUIRED_SECRETS = [
    "MARKETMASTERS_API_KEY",
    "ALPACA_KEY",
    "ALPACA_SECRET",
]

def _check_required_secrets() -> bool:
    """Check required environment secrets and print helpful instructions if missing.

    Returns True if all secrets present, False otherwise.
    """
    missing = [k for k in REQUIRED_SECRETS if not os.environ.get(k)]
    if not missing:
        return True

    print("\n[ERROR] Missing required environment secrets:")
    for k in missing:
        print(f" - {k}")

    print("\nPlease set them either in a local .env file (place in project root) or as GitHub Actions repository secrets:")
    print(" - To add to a local .env file, create a file named .env and add lines like:")
    for k in missing:
        print(f"     {k}=your_value_here")
    print("\n - To add secrets in GitHub: Repository → Settings → Secrets and variables → Actions → New repository secret")
    print("\nThe bot will exit until the missing secrets are provided.\n")
    return False

# ── MarketMasters API ─────────────────────────────────────────────────────────

def get_active_patterns() -> list:
    """
    Fetch patterns filtered to status=active & bullish=true, then client-filter
    to ensure valid price levels.

    breakoutPrice = entry, stopLoss = stop, target = take-profit.
    """
    # Prefer server-side filtering when API supports it to reduce payload size.
    # We still defensively filter client-side because API field names/values
    # can vary between environments or versions.
    resp = requests.get(
        MARKETMASTERS_URL,
        headers={"X-API-Key": MARKETMASTERS_API_KEY},
        params=MARKETMASTERS_PARAMS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Helper: normalize possible representations of a bullish flag.
    # API responses may use booleans, string "true"/"false", or omit the
    # field entirely. Treat missing/None as not bullish.
    def is_bullish_flag(val):
        if isinstance(val, bool):
            return val
        if val is None:
            return False
        return str(val).lower() == "true"

    return [
        p for p in data.get("patterns", [])
        if p.get("status") == "active"
        and is_bullish_flag(p.get("bullish") or p.get("isBullish"))
        and p.get("breakoutPrice", 0) > 0
        and p.get("stopLoss", 0) > 0
        and p.get("target", 0) > 0
        and p.get("stopLoss") < p.get("breakoutPrice")
        and p.get("target") > p.get("breakoutPrice")
    ]

# ── Alpaca helpers ────────────────────────────────────────────────────────────

def load_traded_patterns(path="traded_patterns.json") -> set:
    # Load previously-traded pattern ids.
    # Stored as a JSON array of strings. Returning a set provides O(1)
    # membership checks when deciding whether to skip a pattern.
    try:
        with open(path, "r") as f:
            return set(json.load(f))
    except Exception:
        # Any error (missing file, malformed JSON) results in an empty set.
        # This is safe: the bot will attempt trades normally if no history.
        return set()

def save_traded_patterns(traded: set, path="traded_patterns.json") -> None:
    # Persist pattern ids deterministically (sorted list) so diffs are stable
    # and human-readable during debugging.
    with open(path, "w") as f:
        json.dump(sorted(list(traded)), f, indent=2)

def get_account() -> dict:
    resp = requests.get(f"{ALPACA_BASE_URL}/account", headers=ALPACA_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_existing_symbols() -> set:
    """Return symbols that already have open positions or open orders."""
    # Query Alpaca for current open positions and open orders. We treat both
    # as preventing a new entry for the same symbol to avoid accidental
    # duplicate exposure or conflicting orders.
    pos_resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS, timeout=30)
    pos_resp.raise_for_status()
    positions = {p["symbol"] for p in pos_resp.json()}

    # Limit the number of returned open orders; 500 is large enough for most
    # accounts but can be adjusted if needed. We only extract the symbol field
    # since we only need to know whether the symbol is represented.
    ord_resp = requests.get(
        f"{ALPACA_BASE_URL}/orders?status=open&limit=500",
        headers=ALPACA_HEADERS,
        timeout=30,
    )
    ord_resp.raise_for_status()
    orders = {o["symbol"] for o in ord_resp.json()}

    return positions | orders


def place_bracket_order(
    symbol: str,
    qty: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    price_below_breakout: bool,
) -> tuple[dict | None, str | None]:
    """
    Place a bracket order on Alpaca.

    - price_below_breakout=True  → stop-limit buy (triggers when price hits breakout level)
    - price_below_breakout=False → limit buy at current market price (already broke out)

    Bracket legs:
      take_profit: limit order at target
      stop_loss:   stop order at stopLoss
    """
    # Build Alpaca bracket order payload. We use string prices because Alpaca
    # API expects string-serialized decimals for price fields in JSON.
    # Notes:
    # - `order_class: bracket` creates the primary entry and attached legs.
    # - `take_profit.limit_price` and `stop_loss.stop_price` are rounded to
    #   2 decimal places for currency formatting.
    order: dict = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": str(round(take_profit, 2))},
        "stop_loss": {"stop_price": str(round(stop_loss, 2))},
    }

    if price_below_breakout:
        # If current market price is below the breakout level, we want to
        # enter only if price reaches the breakout: use a stop-limit buy
        # (trigger at stop_price, create a limit at a small buffer above to
        # increase fill probability). The buffer is configurable.
        order["type"] = "stop_limit"
        order["stop_price"] = str(round(entry_price, 2))
        order["limit_price"] = str(round(entry_price * (1 + BREAKOUT_LIMIT_BUFFER), 2))
    else:
        # If price has already reached/broken out, place a limit at the
        # breakout price to avoid market slippage. Using a limit guarantees
        # the entry price or better; if you prefer a more aggressive fill
        # strategy, add a small buffer (e.g. *1.001).
        order["type"] = "limit"
        order["limit_price"] = str(round(entry_price, 2))

    headers = {**ALPACA_HEADERS, "Content-Type": "application/json"}
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=headers, json=order, timeout=30)

    if resp.status_code in (200, 201):
        return resp.json(), None
    return None, f"HTTP {resp.status_code}: {resp.text}"

# ── Main bot logic ────────────────────────────────────────────────────────────

def pattern_id(p: dict) -> str:
    """
    Return a stable pattern id string. Tries common explicit id/timestamp keys first,
    then falls back to a SHA1 of the pattern payload, prefixed by the symbol.
    """
    # The generated id should be stable across runs for the same signal so
    # we can persist it in `traded_patterns.json` and avoid duplicate actions.
    sym = p.get("symbol", "").upper()

    # 1) explicit id-like keys
    for key in ("id", "patternId", "pattern_id"):
        if key in p and p[key]:
            return f"{sym}_{p[key]}"

    # 2) timestamp-like keys (use integer ms if possible)
    for key in ("timestamp", "ts", "time", "detectedAt", "createdAt", "signalTime"):
        if key in p and p[key]:
            val = p[key]
            try:
                # numeric (seconds or milliseconds)
                iv = int(float(val))
                # if looks like seconds (10 digits), convert to ms
                if iv < 10**11:
                    iv = int(iv * 1000)
                return f"{sym}_{iv}"
            except Exception:
                # try ISO date string -> ms
                try:
                    dt = datetime.fromisoformat(str(val))
                    return f"{sym}_{int(dt.timestamp() * 1000)}"
                except Exception:
                    pass

    # 3) deterministic fallback: sha1 of the pattern dict. We serialize with
    # `sort_keys=True` so the same logical pattern yields the same hash even
    # if the API returns fields in different orders.
    s = json.dumps(p, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return f"{sym}_{h}"

def run_bot():
    print(f"\n{'=' * 60}")
    print(f"  Trading Bot  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # Verify required secrets are present before proceeding
    if not _check_required_secrets():
        sys.exit(1)

    # Log which Alpaca base URL we're using (helpful for debugging)
    print(f"  ALPACA_BASE_URL: {ALPACA_BASE_URL}")

    # Load previously traded pattern IDs (persistence across runs). We keep
    # this history to avoid acting twice on the same signal. Note: we
    # additionally verify Alpaca state at runtime to handle cases where the
    # local cache and remote state diverge.
    traded = load_traded_patterns()

    # Account equity → trade size
    account = get_account()
    equity = float(account["equity"])
    trade_amount = equity * PORTFOLIO_PCT
    print(f"  Equity: ${equity:>12,.2f}  |  Trade size (2%): ${trade_amount:,.2f}")

    # Query Alpaca for current exposure and pending orders. We do this early
    # so we can skip any symbol that already has an open position/order.
    existing_symbols = get_existing_symbols()
    print(f"  Existing positions/orders: {len(existing_symbols)} symbols")

    # Fetch active patterns
    patterns = get_active_patterns()
    print(f"  Active patterns (valid prices): {len(patterns)}")
    print()

    new_trades = 0
    skipped_traded = 0
    skipped_symbol = 0
    errors = 0

    for p in patterns:
        pid = pattern_id(p)
        symbol = p["symbol"]
        breakout = p["breakoutPrice"]
        stop_loss = p["stopLoss"]
        target = p["target"]
        current_price = p["price"]
        pattern_type = p["type"]

        # Check whether we've already acted on this exact pattern (by id).
        # Behavior:
        # - If the id is recorded AND Alpaca still has exposure for the symbol,
        #   skip the trade.
        # - If the id is recorded but Alpaca no longer shows exposure, remove
        #   the id so we can re-attempt the trade (handles manual cancels or
        #   other state changes outside this bot).
        if pid in traded:
            if symbol in existing_symbols:
                skipped_traded += 1
                continue
            traded.remove(pid)

        # If the symbol currently has an open position or order, skip it and
        # mark the pattern as traded to avoid repeated attempts during this run.
        if symbol in existing_symbols:
            print(f"  SKIP  {symbol:<8}  now has a position/order (refreshed)")
            traded.add(pid)
            skipped_symbol += 1
            continue

        # Number of shares at 2% of equity
        qty = max(1, int(trade_amount / breakout))

        price_below_breakout = current_price < breakout
        # Always attempt entry at the breakout price (place a limit there)
        entry_price = breakout

        direction = "PENDING" if price_below_breakout else "BROKE OUT"
        print(
            f"  {direction:<9} {symbol:<8}  {pattern_type:<22}"
            f"  entry={entry_price:>8.2f}  sl={stop_loss:>8.2f}  tp={target:>8.2f}  qty={qty}"
        )

        order, err = place_bracket_order(
            symbol, qty, entry_price, stop_loss, target, price_below_breakout
        )

        if order:
            print(f"           {'':8}  Order placed: {order['id']}  ({order['type']})")
            traded.add(pid)
            existing_symbols.add(symbol)
            new_trades += 1
        else:
            print(f"           {'':8}  ERROR: {err}")
            errors += 1

    save_traded_patterns(traded)

    print()
    print(f"  New trades: {new_trades}  |  Skipped (already traded): {skipped_traded}"
          f"  |  Skipped (symbol exists): {skipped_symbol}  |  Errors: {errors}")
    print(f"{'=' * 60}\n")

    return new_trades, errors


if __name__ == "__main__":
    _, errs = run_bot()
    sys.exit(1 if errs else 0)
