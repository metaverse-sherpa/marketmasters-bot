"""
Copy Trading Bot — main runner.

On each run:
  1. Fetches Ro Khanna's latest trades from Capitol Trades
  2. Compares against previously executed trade IDs (state.json)
  3. Mirrors any new BUY/SELL trades on the Alpaca paper account
  4. Saves updated state

Designed to be invoked repeatedly (via cron) without double-executing trades.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import POLITICIAN_ID, POLITICIAN_NAME, STATE_FILE, TRADE_AMOUNT
from capitol_trades import fetch_trades
from alpaca_trader import (
    get_account,
    get_buying_power,
    is_market_open,
    place_buy,
    place_sell_all,
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── State helpers ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    p = Path(STATE_FILE)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"executed_ids": [], "last_run": None, "trades_log": []}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    logger.info(f"{'='*60}")
    logger.info(f"Copy Bot run started: {datetime.now().isoformat()}")
    logger.info(f"Copying: {POLITICIAN_NAME} ({POLITICIAN_ID})")
    if dry_run:
        logger.info("DRY RUN — no real orders will be placed")

    state = load_state()
    executed_ids: set = set(state.get("executed_ids", []))

    # ── Market check ─────────────────────────────────────────────────────────
    market_open = is_market_open()
    if not market_open:
        logger.info("Market is currently closed — trades will queue for next open")
        # We still fetch and log new trades so they're ready when market opens

    # ── Account snapshot ─────────────────────────────────────────────────────
    try:
        acct = get_account()
        buying_power = float(acct.get("buying_power", 0))
        portfolio_val = float(acct.get("portfolio_value", 0))
        logger.info(f"Portfolio: ${portfolio_val:,.2f}  |  Buying power: ${buying_power:,.2f}")
    except Exception as e:
        logger.error(f"Could not fetch account info: {e}")
        buying_power = 0.0

    # ── Fetch politician's trades ─────────────────────────────────────────────
    try:
        trades = fetch_trades(POLITICIAN_ID, page_size=20)
    except Exception as e:
        logger.error(f"Failed to fetch trades from Capitol Trades: {e}")
        state["last_run"] = datetime.now().isoformat()
        save_state(state)
        return

    new_trades = [t for t in trades if t["id"] not in executed_ids]
    logger.info(f"Total recent trades: {len(trades)} | New (unexecuted): {len(new_trades)}")

    if not new_trades:
        logger.info("Nothing new to trade.")
        state["last_run"] = datetime.now().isoformat()
        save_state(state)
        return

    # ── Execute new trades ────────────────────────────────────────────────────
    executed_this_run = []

    for trade in new_trades:
        ticker = trade["ticker"]
        tx_type = trade["tx_type"]
        asset_type = trade.get("asset_type", "stock")
        trade_id = trade["id"]

        if not ticker:
            logger.warning(f"Skipping trade with no ticker: {trade}")
            executed_ids.add(trade_id)  # mark so we don't retry forever
            continue

        if asset_type not in ("stock", "equity", ""):
            logger.info(
                f"Skipping {asset_type} trade for {ticker} "
                f"(only stocks supported via this account)"
            )
            executed_ids.add(trade_id)
            continue

        log_entry = {
            "trade_id": trade_id,
            "ticker": ticker,
            "tx_type": tx_type,
            "tx_date": trade["tx_date"],
            "pub_date": trade["pub_date"],
            "executed_at": datetime.now().isoformat(),
            "status": "pending",
            "order_id": None,
            "error": None,
        }

        try:
            if tx_type == "buy":
                if not market_open:
                    logger.info(f"Market closed — queuing BUY {ticker} ${TRADE_AMOUNT}")
                    # Alpaca will hold the day order until next open; notional orders
                    # require market to be open, so we skip and retry next run
                    # (state is NOT updated, so it will be retried)
                    continue

                if buying_power < TRADE_AMOUNT:
                    logger.warning(
                        f"Insufficient buying power (${buying_power:.2f}) "
                        f"to buy ${TRADE_AMOUNT} of {ticker} — skipping"
                    )
                    log_entry["status"] = "skipped_insufficient_funds"
                else:
                    if not dry_run:
                        result = place_buy(ticker, TRADE_AMOUNT)
                        log_entry["order_id"] = result.get("id")
                    buying_power -= TRADE_AMOUNT
                    log_entry["status"] = "executed" if not dry_run else "dry_run"
                    logger.info(
                        f"{'[DRY] ' if dry_run else ''}BUY {ticker} ${TRADE_AMOUNT} "
                        f"(filed {trade['pub_date']})"
                    )

            elif tx_type == "sell":
                if not dry_run:
                    result = place_sell_all(ticker)
                    if result:
                        log_entry["order_id"] = result.get("id") or "closed"
                        log_entry["status"] = "executed"
                    else:
                        log_entry["status"] = "no_position"
                else:
                    log_entry["status"] = "dry_run"
                logger.info(
                    f"{'[DRY] ' if dry_run else ''}SELL all {ticker} "
                    f"(filed {trade['pub_date']})"
                )

            else:
                logger.info(f"Unknown tx_type '{tx_type}' for {ticker} — skipping")
                log_entry["status"] = "skipped_unknown_type"

        except Exception as e:
            logger.error(f"Error executing {tx_type} {ticker}: {e}")
            log_entry["status"] = "error"
            log_entry["error"] = str(e)

        executed_ids.add(trade_id)
        executed_this_run.append(log_entry)

    # ── Persist state ─────────────────────────────────────────────────────────
    state["executed_ids"] = list(executed_ids)
    state["last_run"] = datetime.now().isoformat()
    state.setdefault("trades_log", []).extend(executed_this_run)
    # Keep log to last 500 entries
    state["trades_log"] = state["trades_log"][-500:]
    save_state(state)

    logger.info(
        f"Run complete. Processed {len(executed_this_run)} new trade(s). "
        f"Total tracked: {len(executed_ids)}"
    )


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
