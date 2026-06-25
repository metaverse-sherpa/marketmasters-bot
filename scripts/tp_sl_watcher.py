#!/usr/bin/env python3
import os
import json
import sys
import requests
from datetime import datetime, timezone

ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
ALPACA_KEY = os.environ.get("ALPACA_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET")
DATA_API_URL = "https://data.alpaca.markets/v2" if "paper" not in ALPACA_BASE_URL else "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}

def load_active_positions(path="active_positions.json") -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_active_positions(positions: dict, path="active_positions.json") -> None:
    try:
        with open(path, "w") as f:
            json.dump(positions, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"Failed to save active positions: {e}")

def get_alpaca_positions() -> set:
    resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return {p["symbol"] for p in resp.json()}

def get_latest_1h_candles(symbols: list) -> dict:
    if not symbols:
        return {}
    
    symbol_str = ",".join(symbols)
    # Alpaca data API uses /v2/stocks/bars for historical data
    url = f"{DATA_API_URL}/stocks/bars"
    params = {
        "symbols": symbol_str,
        "timeframe": "1Hour",
        "limit": 2, # Get last 2 to ensure we get the most recently closed candle
    }
    
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    
    data = resp.json().get("bars", {})
    candles = {}
    for sym, bars in data.items():
        if bars:
            # Aggregate over all returned bars (up to 2) to ensure we don't miss
            # a spike that happened right before the hour rolled over.
            high = max(float(b["h"]) for b in bars)
            low = min(float(b["l"]) for b in bars)
            close = float(bars[-1]["c"])
            candles[sym] = {
                "high": high,
                "low": low,
                "close": close
            }
    return candles

def close_position(symbol: str) -> bool:
    resp = requests.delete(f"{ALPACA_BASE_URL}/positions/{symbol}", headers=HEADERS, timeout=10)
    if resp.status_code in (200, 201):
        return True
    print(f"Failed to close position {symbol}: {resp.status_code} {resp.text}")
    return False

def main():
    if not ALPACA_KEY or not ALPACA_SECRET:
        print("Missing Alpaca credentials.")
        return 1

    positions = load_active_positions()
    if not positions:
        print("No active positions to track.")
        return 0

    actual_symbols = get_alpaca_positions()
    
    # Sync: Remove any tracked positions that are no longer in Alpaca
    keys_to_remove = []
    for pid, data in positions.items():
        if data["symbol"] not in actual_symbols:
            print(f"Removing {data['symbol']} from tracking (no longer open in Alpaca).")
            keys_to_remove.append(pid)
            
    for k in keys_to_remove:
        del positions[k]

    if not positions:
        save_active_positions(positions)
        print("No active positions left after sync.")
        return 0

    # Get unique symbols
    symbols = list(set([p["symbol"] for p in positions.values()]))
    print(f"Tracking symbols: {symbols}")
    
    try:
        candles = get_latest_1h_candles(symbols)
    except Exception as e:
        print(f"Failed to fetch market data: {e}")
        return 1
    
    closed_keys = []
    for pid, data in positions.items():
        sym = data["symbol"]
        tp = float(data["target"])
        sl = float(data["stop"])
        
        if sym not in candles:
            print(f"No candle data for {sym}, skipping...")
            continue
            
        high = candles[sym]["high"]
        low = candles[sym]["low"]
        close = candles[sym]["close"]
        
        reason = None
        if high >= tp:
            reason = f"Target Profit Hit (High: {high} >= TP: {tp})"
        elif low <= sl:
            reason = f"Stop Loss Hit (Low: {low} <= SL: {sl})"
            
        if reason:
            print(f"[{sym}] {reason} -> Closing position.")
            if close_position(sym):
                closed_keys.append(pid)
            
    for k in closed_keys:
        del positions[k]
        
    save_active_positions(positions)
    print("Watcher cycle complete.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
