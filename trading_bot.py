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

# ── Config ────────────────────────────────────────────────────────────────────

MARKETMASTERS_API_KEY = os.environ["MARKETMASTERS_API_KEY"]
MARKETMASTERS_URL = "https://api.marketmasters.ai/v1/stocks/patterns"

ALPACA_KEY = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]
ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"

PORTFOLIO_PCT = 0.02  # 2% of equity per trade
BREAKOUT_LIMIT_BUFFER = 0.01  # 1% above stop price for stop-limit orders

ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}

# ── MarketMasters API ─────────────────────────────────────────────────────────

def get_active_patterns() -> list:
    """
    Fetch patterns filtered to status=active & bullish=true, then client-filter
    to ensure valid price levels.

    breakoutPrice = entry, stopLoss = stop, target = take-profit.
    """
    resp = requests.get(
        MARKETMASTERS_URL,
        headers={"X-API-Key": MARKETMASTERS_API_KEY},
        params={"status": "active", "bullish": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

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
    try:
        with open(path, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_traded_patterns(traded: set, path="traded_patterns.json") -> None:
    with open(path, "w") as f:
        json.dump(sorted(list(traded)), f, indent=2)

def get_account() -> dict:
    resp = requests.get(f"{ALPACA_BASE_URL}/account", headers=ALPACA_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_existing_symbols() -> set:
    """Return symbols that already have open positions or open orders."""
    pos_resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS, timeout=30)
    pos_resp.raise_for_status()
    positions = {p["symbol"] for p in pos_resp.json()}

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
        # Buy when price breaks out above breakoutPrice
        order["type"] = "stop_limit"
        order["stop_price"] = str(round(entry_price, 2))
        order["limit_price"] = str(round(entry_price * (1 + BREAKOUT_LIMIT_BUFFER), 2))
    else:
        # Price already broke out — enter via limit at slightly above current price
        order["type"] = "limit"
        order["limit_price"] = str(round(entry_price * 1.005, 2))

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

    # 3) deterministic fallback: sha1 of the pattern dict
    s = json.dumps(p, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return f"{sym}_{h}"

def run_bot():
    print(f"\n{'=' * 60}")
    print(f"  Trading Bot  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # Load previously traded pattern IDs
    traded = load_traded_patterns()

    # Account equity → trade size
    account = get_account()
    equity = float(account["equity"])
    trade_amount = equity * PORTFOLIO_PCT
    print(f"  Equity: ${equity:>12,.2f}  |  Trade size (2%): ${trade_amount:,.2f}")

    # Symbols already in portfolio or pending orders
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

        # Already traded this exact pattern signal — only skip if Alpaca still shows position/order
        if pid in traded:
            if symbol in existing_symbols:
                skipped_traded += 1
                continue
            # previously traded but no active Alpaca position/order -> unmark and attempt again
            traded.remove(pid)

        # Already have exposure to this symbol
        if symbol in existing_symbols:
            print(f"  SKIP  {symbol:<8}  now has a position/order (refreshed)")
            traded.add(pid)
            skipped_symbol += 1
            continue

        # Number of shares at 2% of equity
        qty = max(1, int(trade_amount / breakout))

        price_below_breakout = current_price < breakout
        entry_price = breakout if price_below_breakout else current_price

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
