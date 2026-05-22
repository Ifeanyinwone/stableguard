"""
StableGuard — coingecko_fetcher.py
Layer 2: CoinGecko Pro API fetcher
Pulls: live price + peg deviation, OHLC history, volume spikes, market cap trend
Runs every 5 min via scheduler.py
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("coingecko_fetcher")

# ── Config ─────────────────────────────────────────────────────
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL          = "https://pro-api.coingecko.com/api/v3"

HEADERS = {
    "x-cg-pro-api-key": COINGECKO_API_KEY,
    "Content-Type": "application/json"
}

# StableGuard monitored coins — CoinGecko IDs
STABLECOINS = {
    "usd-coin":        "USDC",
    "tether":          "USDT",
    "dai":             "DAI",
    "frax":            "FRAX",
    "paypal-usd":      "PYUSD",
    "crvusd":          "crvUSD",
    "liquity-usd":     "LUSD",
}

# Peg target for each coin (all USD = 1.0)
PEG_TARGET = {symbol: 1.0 for symbol in STABLECOINS.values()}


# ── Helper ─────────────────────────────────────────────────────
def _get(endpoint: str, params: dict = {}, retries: int = 3) -> dict | list | None:
    """GET request with retry logic and rate limit handling."""
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                log.warning(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            elif resp.status_code == 401:
                log.error("Invalid CoinGecko API key.")
                return None
            else:
                log.warning(f"HTTP {resp.status_code} on {endpoint}. Attempt {attempt+1}/{retries}")
                time.sleep(5)
        except requests.RequestException as e:
            log.error(f"Request error: {e}. Attempt {attempt+1}/{retries}")
            time.sleep(5)
    log.error(f"All {retries} attempts failed for {endpoint}")
    return None


# ── Fetch 1: Live Price + Peg Deviation ────────────────────────
def fetch_live_prices() -> list[dict]:
    """
    Endpoint: /coins/markets?ids=usd-coin,tether,dai...
    Returns live price, 24h volume, market cap, peg deviation per coin.
    """
    coin_ids = ",".join(STABLECOINS.keys())
    data = _get("/coins/markets", params={
        "vs_currency":           "usd",
        "ids":                   coin_ids,
        "order":                 "market_cap_desc",
        "per_page":              50,
        "page":                  1,
        "price_change_percentage": "1h,24h,7d",
        "sparkline":             "true",   # for market cap trend widget
    })

    if not data:
        log.error("fetch_live_prices: no data returned")
        return []

    results = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    for coin in data:
        cg_id  = coin.get("id")
        symbol = STABLECOINS.get(cg_id, cg_id.upper())
        price  = coin.get("current_price", 0)
        peg    = PEG_TARGET.get(symbol, 1.0)

        # Peg deviation in basis points (100bps = 1 cent depeg)
        peg_dev_bps = round(abs(price - peg) * 10000, 2)

        results.append({
            "fetched_at":           fetched_at,
            "symbol":               symbol,
            "coingecko_id":         cg_id,
            "price_usd":            price,
            "peg_target":           peg,
            "peg_deviation_bps":    peg_dev_bps,
            "peg_deviation_pct":    round(abs(price - peg) / peg * 100, 4),
            "market_cap_usd":       coin.get("market_cap", 0),
            "volume_24h_usd":       coin.get("total_volume", 0),
            "price_change_1h_pct":  coin.get("price_change_percentage_1h_in_currency", 0),
            "price_change_24h_pct": coin.get("price_change_percentage_24h_in_currency", 0),
            "price_change_7d_pct":  coin.get("price_change_percentage_7d_in_currency", 0),
            "sparkline_7d":         coin.get("sparkline_in_7d", {}).get("price", []),
            # Signal flag: peg deviation > 20bps = WATCH level
            "peg_alert": (
                "EXIT"   if peg_dev_bps > 100 else
                "REDUCE" if peg_dev_bps > 50  else
                "WATCH"  if peg_dev_bps > 20  else
                "HEALTHY"
            ),
        })

    log.info(f"fetch_live_prices: {len(results)} coins fetched")
    return results


# ── Fetch 2: Historical OHLC for Backtesting ───────────────────
def fetch_ohlc_history(coingecko_id: str, days: int = 90) -> list[dict]:
    """
    Endpoint: /coins/{id}/ohlc?days=90
    Returns OHLC candles for backtesting peg deviation patterns.
    Used by signal_liquidity.py for historical stress event replay.
    """
    data = _get(f"/coins/{coingecko_id}/ohlc", params={
        "vs_currency": "usd",
        "days":        days,
    })

    if not data:
        log.error(f"fetch_ohlc_history: no data for {coingecko_id}")
        return []

    symbol = STABLECOINS.get(coingecko_id, coingecko_id.upper())
    results = []

    for candle in data:
        # CoinGecko OHLC: [timestamp_ms, open, high, low, close]
        ts, open_, high, low, close = candle
        peg = PEG_TARGET.get(symbol, 1.0)
        results.append({
            "symbol":            symbol,
            "timestamp":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
            "open":              open_,
            "high":              high,
            "low":               low,
            "close":             close,
            "peg_dev_bps_close": round(abs(close - peg) * 10000, 2),
            "peg_dev_bps_high":  round(abs(high  - peg) * 10000, 2),
        })

    log.info(f"fetch_ohlc_history: {len(results)} candles for {symbol}")
    return results


# ── Fetch 3: Volume Spike Detection ────────────────────────────
def fetch_volume_history(coingecko_id: str, days: int = 30) -> list[dict]:
    """
    Endpoint: /coins/{id}/market_chart?interval=hourly
    Returns hourly volume for spike detection.
    A volume spike on a stablecoin = panic buying/selling = liquidity signal.
    """
    data = _get(f"/coins/{coingecko_id}/market_chart", params={
        "vs_currency": "usd",
        "days":        days,
        "interval":    "hourly",
    })

    if not data:
        log.error(f"fetch_volume_history: no data for {coingecko_id}")
        return []

    symbol  = STABLECOINS.get(coingecko_id, coingecko_id.upper())
    volumes = data.get("total_volumes", [])
    prices  = data.get("prices", [])

    # Build price lookup for peg deviation per hour
    price_map = {p[0]: p[1] for p in prices}
    peg       = PEG_TARGET.get(symbol, 1.0)

    results = []
    vol_values = [v[1] for v in volumes if v[1] > 0]
    avg_vol    = sum(vol_values) / len(vol_values) if vol_values else 1

    for ts_ms, volume in volumes:
        price       = price_map.get(ts_ms, peg)
        peg_dev_bps = round(abs(price - peg) * 10000, 2)
        vol_ratio   = round(volume / avg_vol, 2) if avg_vol > 0 else 0

        results.append({
            "symbol":        symbol,
            "timestamp":     datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
            "volume_usd":    round(volume, 0),
            "price_usd":     price,
            "peg_dev_bps":   peg_dev_bps,
            "vol_vs_avg":    vol_ratio,
            # Volume spike flag: 3x average = anomaly
            "volume_spike":  vol_ratio > 3.0,
        })

    log.info(f"fetch_volume_history: {len(results)} hourly points for {symbol}")
    return results


# ── Fetch 4: Market Cap Trend (Sparkline) ─────────────────────
def fetch_market_cap_trend() -> list[dict]:
    """
    Endpoint: /coins/markets with sparkline=true
    Returns 7-day sparkline for market cap trend widget on dashboard.
    A falling market cap = redemption pressure = supply contraction signal.
    """
    coin_ids = ",".join(STABLECOINS.keys())
    data = _get("/coins/markets", params={
        "vs_currency": "usd",
        "ids":         coin_ids,
        "sparkline":   "true",
        "per_page":    50,
    })

    if not data:
        return []

    results = []
    for coin in data:
        cg_id      = coin.get("id")
        symbol     = STABLECOINS.get(cg_id, cg_id.upper())
        sparkline  = coin.get("sparkline_in_7d", {}).get("price", [])
        market_cap = coin.get("market_cap", 0)
        ath_mcap   = coin.get("ath", 0)  # useful for relative size context

        # Trend: is the last price lower than the first? = contraction
        trend = "contracting" if (
            len(sparkline) > 1 and sparkline[-1] < sparkline[0]
        ) else "expanding"

        results.append({
            "symbol":           symbol,
            "market_cap_usd":   market_cap,
            "sparkline_7d":     sparkline,
            "mcap_trend":       trend,
            "fetched_at":       datetime.now(timezone.utc).isoformat(),
        })

    log.info(f"fetch_market_cap_trend: {len(results)} coins")
    return results


# ── Master fetch: runs all 4 fetches in one call ───────────────
def fetch_all() -> dict:
    """
    Called by scheduler.py every 5 minutes.
    Returns all CoinGecko data needed by the signal engine.
    """
    log.info("=== CoinGecko fetch cycle starting ===")

    # Always fetch live prices (fast, low credit cost)
    live = fetch_live_prices()

    # Fetch volume history for all coins (spike detection)
    volume_data = {}
    for cg_id, symbol in STABLECOINS.items():
        vol = fetch_volume_history(cg_id, days=7)  # 7 days hourly = enough for z-score
        if vol:
            volume_data[symbol] = vol
        time.sleep(0.5)  # be gentle on rate limits

    log.info("=== CoinGecko fetch cycle complete ===")

    return {
        "live_prices":   live,
        "volume_data":   volume_data,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import json

    print("\n--- Testing fetch_live_prices ---")
    prices = fetch_live_prices()
    for p in prices:
        print(f"  {p['symbol']:8} price={p['price_usd']:.4f}  "
              f"peg_dev={p['peg_deviation_bps']:.2f}bps  "
              f"alert={p['peg_alert']}")

    print("\n--- Testing fetch_ohlc_history (USDC, 30 days) ---")
    ohlc = fetch_ohlc_history("usd-coin", days=30)
    if ohlc:
        print(f"  {len(ohlc)} candles. Latest: {ohlc[-1]}")

    print("\n--- Testing fetch_volume_history (DAI, 7 days) ---")
    vol = fetch_volume_history("dai", days=7)
    spikes = [v for v in vol if v["volume_spike"]]
    print(f"  {len(vol)} hourly points. Volume spikes detected: {len(spikes)}")

    print("\n--- Testing fetch_market_cap_trend ---")
    mcap = fetch_market_cap_trend()
    for m in mcap:
        print(f"  {m['symbol']:8} mcap=${m['market_cap_usd']:,.0f}  trend={m['mcap_trend']}")