#!/usr/bin/env python3
"""
Telegram Bot for MarketMasters
Provides a /trades command that lists open Alpaca positions.
"""
import os
import sys
import json
import logging
import requests
from datetime import datetime
from urllib.parse import quote_plus

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Alpaca configuration (reuse from trading_bot.py)
ALPACA_BASE_URL = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')
ALPACA_KEY = os.environ.get('ALPACA_KEY')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET')
ALPACA_HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

# Configure logging for visibility
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with a /trades button."""
    button = KeyboardButton('/trades')
    reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(
        "Welcome to MarketMasters Bot! Use /trades to see your open Alpaca positions.",
        reply_markup=reply_markup,
    )

async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch open positions from Alpaca and send a formatted list."""
    if not ALPACA_KEY or not ALPACA_SECRET:
        await update.message.reply_text('Alpaca credentials are not configured.')
        return
    try:
        resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS, timeout=30)
        resp.raise_for_status()
        positions = resp.json()
    except Exception as e:
        logger.exception('Failed to fetch Alpaca positions')
        await update.message.reply_text(f'Error retrieving positions: {e}')
        return

    if not positions:
        await update.message.reply_text('No open positions found.')
        return

    lines = []
    for p in positions:
        symbol = p.get('symbol')
        qty = p.get('qty')
        entry = p.get('avg_entry_price')
        market_val = p.get('market_value')
        lines.append(f"• {symbol}: {qty} shares @ ${float(entry):.2f} (value ${float(market_val):.2f})")
    msg = "Open Alpaca Positions:\n" + "\n".join(lines)
    await update.message.reply_text(msg)

async def main() -> None:
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error('TELEGRAM_BOT_TOKEN not set')
        sys.exit(1)
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('trades', trades))
    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
