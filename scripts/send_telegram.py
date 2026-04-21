#!/usr/bin/env python3
import os
import sys
import json
import requests

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
            text += (
                f"{o.get('symbol')} — entry={o.get('entry_price')} "
                f"sl={o.get('stop_loss')} tp={o.get('take_profit')} "
                f"qty={o.get('qty')} order_id={o.get('order_id')}\n"
            )
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
            data={'chat_id': chat, 'text': text},
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
