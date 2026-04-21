#!/usr/bin/env python3
import os
import sys
import json
import html
import requests
from urllib.parse import quote_plus

def main():
    fn = 'run_summary.json'
    if not os.path.exists(fn):
        print('No run_summary.json found; nothing to notify')
        return 0

    try:
        s = json.load(open(fn))
    except Exception as e:
        print('Could not read run_summary.json:', e)
        return 0

    new_orders = s.get('new_orders', [])
    insuff = s.get('insufficient_buying_power', False)
    if not new_orders and not insuff:
        print('No new orders and no insufficient buying power; nothing to notify')
        return 0

    run_num = os.environ.get('GITHUB_RUN_NUMBER', 'local')
    text = f"MarketMasters Bot Run #{run_num}\n"
    if new_orders:
        text += f"New orders: {len(new_orders)}\n"
        for o in new_orders:
            # Format numeric values to two decimal places when possible
            def fval(k):
                v = o.get(k)
                try:
                    return f"{float(v):.2f}"
                except Exception:
                    return str(v)

            entry = fval('entry_price')
            sl = fval('stop_loss')
            tp = fval('take_profit')
            sym = str(o.get('symbol'))
            url = f"https://marketmasters.ai/stocks/{quote_plus(sym)}"
            link = f"<a href=\"{url}\">{html.escape(sym)}</a>"
            text += f"• {link} — entry: {entry} | SL: {sl} | TP: {tp}\n"
    if insuff:
        bad = s.get('insufficient_symbols', [])
        text += 'WARNING: insufficient buying power for: ' + ', '.join(bad) + '\n'

    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        print('Missing Telegram secrets; not sending')
        return 0

    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id': chat, 'text': text, 'parse_mode': 'HTML'},
            timeout=15,
        )
        print('Telegram response', resp.status_code, resp.text)
        if resp.status_code != 200:
            return 1
    except Exception as e:
        print('Telegram send failed:', e)
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
