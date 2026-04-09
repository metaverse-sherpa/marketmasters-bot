#!/usr/bin/env python3
"""
TSLA Trailing Stop + Ladder Strategy Monitor

Rules:
  - Entry: 10 shares at market price
  - Stop loss:    Sell everything if price drops 10% below original entry
  - Trailing:     Once price is up 10%, switch to 5% trailing stop (Alpaca native — only moves up)
  - Ladder -15%:  Buy 10 more shares; reset stop to 10% below ladder price
  - Ladder -25%:  Buy 20 more shares; reset stop to 10% below ladder price
  - Ladder -40%:  Buy 30 more shares; reset stop to 10% below ladder price

  Ladder sizing rationale: buy MORE at deeper discounts (higher conviction at better prices).
  After each ladder, stop resets to 10% below the price paid so the old stop
  doesn't sit above current price and fire immediately.

Run this on a cron schedule during market hours. State is persisted in tsla_state.json.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL    = "https://paper-api.alpaca.markets/v2"
DATA_URL    = "https://data.alpaca.markets/v2"
API_KEY     = "PKLV5DFFHO4CBENVI763GOPHLT"
API_SECRET  = "6nRZAvfdDeh64YeN57CgQaXGhaY1mWeVC9bnazCELwqp"
SYMBOL      = "TSLA"
STATE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsla_state.json")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json",
}

# ── Strategy parameters ───────────────────────────────────────────────────────
STOP_LOSS_PCT     = 0.10   # initial stop: 10% below original entry
TRAILING_TRIGGER  = 1.10   # activate trailing once price is up 10% from entry
TRAIL_PCT         = 5.0    # trail at 5% below current high (Alpaca native)

# (state_key, price_multiplier_below_entry, shares_to_buy)
# Levels: -15%, -25%, -40% — increasing size at deeper discounts
LADDERS = [
    ("ladder_15", 0.85, 10),
    ("ladder_25", 0.75, 20),
    ("ladder_40", 0.60, 30),
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _request(method, url, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {"error": str(e)}, e.code

def api_get(path, base=BASE_URL):
    result, _ = _request("GET", f"{base}{path}")
    return result

def api_post(path, data):
    result, code = _request("POST", f"{BASE_URL}{path}", data)
    return result, code

def api_delete(path):
    _, code = _request("DELETE", f"{BASE_URL}{path}")
    return code


# ── State ─────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Market data ───────────────────────────────────────────────────────────────
def get_latest_price():
    data = api_get(f"/stocks/{SYMBOL}/trades/latest", base=DATA_URL)
    try:
        return float(data["trade"]["p"])
    except (KeyError, TypeError):
        return None


# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsla_strategy.log")
    with open(log_path, "a") as f:
        f.write(line + "\n")


# ── Order helpers ─────────────────────────────────────────────────────────────
def place_stop_loss(qty, stop_price):
    order, _ = api_post("/orders", {
        "symbol": SYMBOL, "qty": str(qty), "side": "sell",
        "type": "stop", "time_in_force": "gtc",
        "stop_price": str(round(stop_price, 2)),
    })
    if "id" in order:
        log(f"  STOP LOSS placed | qty={qty} stop=${stop_price:.2f} | id={order['id']}")
    else:
        log(f"  STOP LOSS failed: {order}")
    return order

def place_trailing_stop(qty, trail_pct):
    order, _ = api_post("/orders", {
        "symbol": SYMBOL, "qty": str(qty), "side": "sell",
        "type": "trailing_stop", "time_in_force": "gtc",
        "trail_percent": str(trail_pct),
    })
    if "id" in order:
        log(f"  TRAILING STOP placed | qty={qty} trail={trail_pct}% | id={order['id']}")
    else:
        log(f"  TRAILING STOP failed: {order}")
    return order

def place_ladder_buy(qty, reason):
    order, _ = api_post("/orders", {
        "symbol": SYMBOL, "qty": str(qty), "side": "buy",
        "type": "market", "time_in_force": "day",
    })
    if "id" in order:
        log(f"  LADDER BUY placed | {reason} | qty={qty} | id={order['id']}")
    else:
        log(f"  LADDER BUY failed: {order}")
    return order

def cancel_order(order_id, label="order"):
    code = api_delete(f"/orders/{order_id}")
    log(f"  Cancelled {label} {order_id} → HTTP {code}")

def refresh_stop(state, new_qty, new_stop_price):
    """Cancel existing stop and replace it with updated qty and price."""
    if state.get("stop_order_id"):
        cancel_order(state["stop_order_id"], label="old-stop")
        state["stop_order_id"] = None

    if state.get("trailing_active"):
        order = place_trailing_stop(new_qty, TRAIL_PCT)
    else:
        order = place_stop_loss(new_qty, new_stop_price)

    if "id" in order:
        state["stop_order_id"] = order["id"]
        state["current_stop"]  = round(new_stop_price, 2)


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    state = load_state()
    if not state:
        log("ERROR: State file missing. Re-run setup.")
        sys.exit(1)

    # 1. Wait for initial buy to fill
    if not state.get("entry_filled"):
        order = api_get(f"/orders/{state['initial_order_id']}")
        if order.get("status") == "filled":
            entry = float(order["filled_avg_price"])
            state["entry_price"]          = entry
            state["original_entry_price"] = entry   # fixed reference for ladders
            state["entry_filled"]         = True
            state["total_qty"]            = int(float(order["filled_qty"]))
            log(f"Initial buy FILLED | {state['total_qty']} shares @ ${entry:.2f}")
            save_state(state)
        else:
            log(f"Waiting for initial fill (status={order.get('status')})")
            return

    original_entry = state["original_entry_price"]
    total_qty      = state["total_qty"]

    # 2. Get current price
    price = get_latest_price()
    if price is None:
        log("Could not fetch price — skipping")
        return

    pct = (price - original_entry) / original_entry * 100
    log(f"TSLA ${price:.2f} | orig_entry=${original_entry:.2f} | {pct:+.2f}% | "
        f"qty={total_qty} | trailing={'YES' if state.get('trailing_active') else 'NO'}")

    # 3. Place initial stop loss if not yet set
    if not state.get("stop_order_id"):
        stop_price = original_entry * (1 - STOP_LOSS_PCT)
        order = place_stop_loss(total_qty, stop_price)
        if "id" in order:
            state["stop_order_id"] = order["id"]
            state["current_stop"]  = round(stop_price, 2)
            save_state(state)
        return

    # 4. Activate trailing stop once price is up 10%
    if not state.get("trailing_active") and price >= original_entry * TRAILING_TRIGGER:
        log(f"Price up {TRAILING_TRIGGER*100-100:.0f}%+ — switching to {TRAIL_PCT}% trailing stop")
        cancel_order(state["stop_order_id"], label="stop-loss")
        order = place_trailing_stop(total_qty, TRAIL_PCT)
        if "id" in order:
            state["stop_order_id"]   = order["id"]
            state["trailing_active"] = True
            state["current_stop"]    = round(price * (1 - TRAIL_PCT / 100), 2)
            save_state(state)

    # 5. Ladders: -15% → +10 shares, -25% → +20 shares, -40% → +30 shares
    for key, multiplier, qty in LADDERS:
        trigger_price = original_entry * multiplier
        if not state.get(f"{key}_triggered") and price <= trigger_price:
            label = f"-{round((1-multiplier)*100):.0f}% ladder"
            log(f"${price:.2f} <= ${trigger_price:.2f} ({label}) — buying {qty} shares")
            order = place_ladder_buy(qty, reason=label)
            if "id" in order:
                state[f"{key}_triggered"] = True
                state[f"{key}_order_id"]  = order["id"]
                new_qty       = total_qty + qty
                state["total_qty"] = new_qty
                total_qty     = new_qty
                # Reset stop to 10% below where we just bought (not the old entry)
                new_stop = trigger_price * (1 - STOP_LOSS_PCT)
                refresh_stop(state, new_qty, new_stop)
                save_state(state)

    save_state(state)
    log(f"Strategy check complete. Stop=${state.get('current_stop','?')} qty={state['total_qty']}")


if __name__ == "__main__":
    run()
