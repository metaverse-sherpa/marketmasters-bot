"""
Scraper for Capitol Trades (capitoltrades.com)
Uses the Next.js RSC (React Server Components) payload to extract trade data
without needing a headless browser.
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

# Capitol Trades uses Next.js App Router with RSC streaming.
# Requesting with 'RSC: 1' returns raw component data as text/x-component,
# which embeds the full trade JSON inline.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/x-component, */*",
    "RSC": "1",
}


def _clean_ticker(raw: str) -> str:
    """Strip exchange suffix  ('JPM:US' -> 'JPM', '8376923Z:LN' stays as-is for non-US)."""
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) == 2 and parts[1].upper() == "US":
        return parts[0].strip()
    # Non-US exchange — skip (Alpaca only trades US equities)
    if len(parts) == 2 and parts[1].upper() != "US":
        return ""
    return raw.strip()


def _extract_trades_array(text: str) -> list:
    """
    Pull the trades data array out of the RSC payload.
    The payload contains inline JSON: "data":[{"_issuerId":...}]
    """
    marker = '"data":[{"_issuerId"'
    pos = text.find(marker)
    if pos == -1:
        logger.warning("Could not find trades data marker in RSC response")
        return []

    arr_start = text.find("[{", pos)
    if arr_start == -1:
        return []

    # Walk forward balancing brackets to find the array end
    depth = 0
    for i, ch in enumerate(text[arr_start:]):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                arr_end = arr_start + i + 1
                break
    else:
        logger.warning("Unterminated JSON array in RSC response")
        return []

    try:
        return json.loads(text[arr_start:arr_end])
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in trades array: {e}")
        return []


def fetch_trades(politician_id: str, page: int = 1, page_size: int = 20) -> list[dict]:
    """
    Fetch recent trades for a politician from Capitol Trades.

    Returns a list of normalized trade dicts:
        id          – stable unique trade ID
        ticker      – US ticker symbol (empty string if non-US exchange)
        issuer_name – company name
        tx_type     – 'buy' or 'sell'
        tx_date     – ISO date of the actual trade
        pub_date    – ISO datetime when the disclosure was filed
        value       – raw numeric value (USD) or 0
        asset_type  – 'stock', 'option', etc.
        option_type – 'call' or 'put' if applicable, else ''
    """
    url = (
        f"https://www.capitoltrades.com/trades"
        f"?politician={politician_id}&page={page}&pageSize={page_size}"
    )
    logger.debug(f"GET {url}")

    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    raw_trades = _extract_trades_array(resp.text)
    if not raw_trades:
        return []

    normalized = []
    for t in raw_trades:
        issuer = t.get("issuer") or {}
        raw_ticker = issuer.get("issuerTicker") or ""
        ticker = _clean_ticker(raw_ticker)

        asset_type = (t.get("assetType") or t.get("asset_type") or "stock").lower()

        option_type = ""
        if asset_type == "option":
            comment = (t.get("comment") or t.get("txTypeExtended") or "").lower()
            if "call" in comment:
                option_type = "call"
            elif "put" in comment:
                option_type = "put"

        tx_type = (t.get("txType") or "").lower()
        if tx_type in ("sale", "sold"):
            tx_type = "sell"

        trade_id = str(
            t.get("_txId") or t.get("txId")
            or f"{t.get('txDate','')}-{raw_ticker}-{tx_type}"
        )

        normalized.append({
            "id": trade_id,
            "ticker": ticker,
            "raw_ticker": raw_ticker,
            "issuer_name": issuer.get("issuerName") or "",
            "tx_type": tx_type,
            "tx_date": t.get("txDate") or "",
            "pub_date": (t.get("pubDate") or "")[:10],  # date only
            "value": t.get("value") or 0,
            "asset_type": asset_type,
            "option_type": option_type,
        })

    logger.info(f"Fetched {len(normalized)} trades for politician {politician_id}")
    return normalized


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from config import POLITICIAN_ID, POLITICIAN_NAME

    print(f"Fetching latest trades for {POLITICIAN_NAME} ({POLITICIAN_ID})...\n")
    results = fetch_trades(POLITICIAN_ID, page_size=10)
    print(f"{'Date':<12} {'Filed':<12} {'Type':<5} {'Ticker':<8} {'Asset':<8} {'Company'}")
    print("-" * 70)
    for t in results:
        print(
            f"{t['tx_date']:<12} {t['pub_date']:<12} "
            f"{t['tx_type'].upper():<5} {t['ticker'] or '(non-US)':<8} "
            f"{t['asset_type']:<8} {t['issuer_name']}"
        )
