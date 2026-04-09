"""
Alpaca Paper Trading API client.
Handles account queries, order placement, and position management.
"""

import logging
import requests

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL

logger = logging.getLogger(__name__)

_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json",
}


def _get(path: str) -> dict | list:
    resp = requests.get(f"{ALPACA_BASE_URL}{path}", headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{ALPACA_BASE_URL}{path}", headers=_HEADERS, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _delete(path: str) -> dict | None:
    resp = requests.delete(f"{ALPACA_BASE_URL}{path}", headers=_HEADERS, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json() if resp.text else {}


# ── Account ──────────────────────────────────────────────────────────────────

def get_account() -> dict:
    return _get("/account")


def is_market_open() -> bool:
    data = _get("/clock")
    return bool(data.get("is_open"))


def get_buying_power() -> float:
    acct = get_account()
    return float(acct.get("buying_power", 0))


# ── Positions ─────────────────────────────────────────────────────────────────

def get_positions() -> list[dict]:
    return _get("/positions")


def get_position(symbol: str) -> dict | None:
    try:
        return _get(f"/positions/{symbol}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def close_position(symbol: str) -> dict | None:
    """Close the entire position for a symbol. Returns None if no position exists."""
    result = _delete(f"/positions/{symbol}")
    if result is None:
        logger.info(f"No open position for {symbol} — nothing to close")
    else:
        logger.info(f"Closed position {symbol}: {result}")
    return result


# ── Orders ────────────────────────────────────────────────────────────────────

def place_buy(symbol: str, notional: float) -> dict:
    """
    Place a fractional market buy order for `notional` USD of `symbol`.
    Uses notional (dollar-based) ordering so we don't need to calculate share qty.
    """
    order = {
        "symbol": symbol,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "notional": str(round(notional, 2)),
    }
    result = _post("/orders", order)
    logger.info(f"BUY {symbol} ${notional:.2f} -> order_id={result.get('id')}")
    return result


def place_sell_all(symbol: str) -> dict | None:
    """
    Sell the entire position in `symbol`. Returns None if no position exists.
    Uses close_position for a clean full exit.
    """
    return close_position(symbol)


def get_open_orders() -> list[dict]:
    return _get("/orders?status=open")


def cancel_all_orders() -> None:
    _delete("/orders")
    logger.info("Cancelled all open orders")


if __name__ == "__main__":
    # Quick connectivity test
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    acct = get_account()
    print(f"Account: {acct.get('account_number')}")
    print(f"Portfolio value: ${float(acct.get('portfolio_value', 0)):,.2f}")
    print(f"Buying power:    ${float(acct.get('buying_power', 0)):,.2f}")
    print(f"Market open:     {is_market_open()}")
