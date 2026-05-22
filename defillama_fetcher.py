"""
StableGuard — defillama_fetcher.py
Production-stable DefiLlama fetcher
Dune-compatible normalization layer
"""

import logging
import requests
import numpy as np
from datetime import datetime, timezone

log = logging.getLogger("defillama_fetcher")

BASE_URL = "https://stablecoins.llama.fi"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "StableGuard/1.0"
}

TIMEOUT = 15

# ── StableGuard monitored coins ──────────────────────────────
STABLECOINS = {
    "USDT": 1,
    "USDC": 2,
    "DAI": 5,
    "FRAX": 6,
    "PYUSD": 151,
    "crvUSD": 170,
    "LUSD": 50,
}

# ── Default fallback template ────────────────────────────────
def build_empty_signal(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "week": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),

        "alert_level": "🟢 HEALTHY",
        "composite_score": 0,
        "risk_trend": "→ Stable",
        "score_delta": 0,
        "pillars_active": 0,
        "total_flags": 0,

        # Mint/Burn
        "mints_m": 0,
        "burns_m": 0,
        "burn_tx_count": 0,
        "net_change_m": 0,
        "total_supply_m": 0,
        "baseline_burn_m": 0,
        "burn_zscore": 0,
        "burn_pct_baseline": 0,
        "flag_mint_defense": 0,
        "flag_whale_cluster": 0,

        # Liquidity
        "avg_peg_dev_bps": 0,
        "max_peg_dev_bps": 0,
        "liq_zscore": 0,
        "flag_peg_dev": 0,
        "flag_peg_spike": 0,

        # Arb
        "arb_trades": 0,
        "whale_trades": 0,
        "baseline_arb_trades": 0,
        "arb_zscore": 0,
        "flag_arb_decline": 0,
        "flag_whale_dominance": 0,

        # Price
        "price_usd": 1.0,
        "peg_dev_bps": 0,
        "peg_alert": "HEALTHY",
    }


# ── Safe HTTP request ────────────────────────────────────────
def safe_get(url: str):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

        if response.status_code != 200:
            log.warning(f"HTTP {response.status_code} for {url}")
            return None

        return response.json()

    except Exception as e:
        log.error(f"Request failed: {e}")
        return None


# ── Fetch current metadata ───────────────────────────────────
def fetch_metadata():
    url = f"{BASE_URL}/stablecoins?includePrices=true"
    data = safe_get(url)

    if not data:
        return {}

    assets = data.get("peggedAssets", [])

    results = {}

    for asset in assets:
        coin_id = asset.get("id")

        for symbol, sid in STABLECOINS.items():
            if sid == coin_id:

                circulating = (
                    asset.get("circulating", {})
                    .get("peggedUSD", 0)
                )

                price = asset.get("price", 1.0) or 1.0

                peg_dev = round(abs(price - 1.0) * 10000, 2)

                results[symbol] = {
                    "price_usd": round(price, 6),
                    "peg_dev_bps": peg_dev,
                    "total_supply_m": round(circulating / 1e6, 2),

                    "peg_alert": (
                        "EXIT"
                        if peg_dev > 100 else
                        "REDUCE"
                        if peg_dev > 50 else
                        "WATCH"
                        if peg_dev > 20 else
                        "HEALTHY"
                    )
                }

    return results


# ── Analyze historical data ──────────────────────────────────
def analyze_history(symbol: str, coin_id: int):

    signal = build_empty_signal(symbol)

    url = f"{BASE_URL}/stablecoin/{coin_id}"
    data = safe_get(url)

    if not data:
        return signal

    tokens = data.get("tokens", [])

    if not tokens:
        return signal

    supplies = []
    prices = []

    for entry in tokens[-12:]:

        try:
            circulating = (
                entry.get("circulating", {})
                .get("peggedUSD", 0)
            ) / 1e6

            supplies.append(float(circulating))

            price = entry.get("price")

            if isinstance(price, (int, float)):
                prices.append(float(price))

        except Exception:
            continue

    if len(supplies) < 2:
        return signal

    current_supply = supplies[-1]

    net_changes = [
        supplies[i] - supplies[i - 1]
        for i in range(1, len(supplies))
    ]

    latest_net = net_changes[-1]

    current_burn = abs(min(0, latest_net))
    current_mint = max(0, latest_net)

    historical_burns = [
        abs(min(0, x))
        for x in net_changes[:-1]
    ]

    avg_burn = float(np.mean(historical_burns)) if historical_burns else 0
    std_burn = float(np.std(historical_burns)) if historical_burns else 0

    burn_z = round(
        (current_burn - avg_burn) / std_burn,
        2
    ) if std_burn > 0 else 0

    burn_pct = round(
        100 * current_burn / avg_burn,
        0
    ) if avg_burn > 0 else 0

    # Peg deviation
    peg_dev = 0

    if prices:
        current_price = prices[-1]
        peg_dev = round(abs(current_price - 1.0) * 10000, 2)

    # Arb proxy
    arb_z = round(
        -1 * (peg_dev / 20),
        2
    )

    arb_z = max(-3, min(3, arb_z))

    # Trend
    if burn_z > 0.5:
        trend = "↑ Rising"
    elif burn_z < -0.5:
        trend = "↓ Falling"
    else:
        trend = "→ Stable"

    signal.update({

        "risk_trend": trend,

        # Mint/Burn
        "mints_m": round(current_mint, 2),
        "burns_m": round(current_burn, 2),
        "net_change_m": round(latest_net, 2),
        "total_supply_m": round(current_supply, 2),
        "baseline_burn_m": round(avg_burn, 2),
        "burn_zscore": burn_z,
        "burn_pct_baseline": int(burn_pct),

        "flag_mint_defense": int(
            current_mint > avg_burn * 2
            and latest_net < 0
        ),

        "flag_whale_cluster": int(
            current_burn > avg_burn * 3
            if avg_burn > 0 else 0
        ),

        # Liquidity
        "avg_peg_dev_bps": peg_dev,
        "max_peg_dev_bps": peg_dev,
        "liq_zscore": round(peg_dev / 20, 2),

        "flag_peg_dev": int(peg_dev > 20),
        "flag_peg_spike": int(peg_dev > 100),

        # Arb
        "arb_zscore": arb_z,

        "flag_arb_decline": int(
            peg_dev > 30 and burn_z > 1
        ),

        "flag_whale_dominance": int(
            current_burn > avg_burn * 3
            if avg_burn > 0 else 0
        ),

        # Price
        "price_usd": round(
            prices[-1],
            6
        ) if prices else 1.0,

        "peg_dev_bps": peg_dev,
    })

    return signal


# ── Master fetch ─────────────────────────────────────────────
def fetch_all_signals():

    log.info("=== StableGuard DefiLlama cycle starting ===")

    signals = {}

    metadata = fetch_metadata()

    for symbol, coin_id in STABLECOINS.items():

        try:

            signal = analyze_history(symbol, coin_id)

            # Merge metadata
            if symbol in metadata:
                signal.update(metadata[symbol])

            signals[symbol] = signal

            log.info(
                f"{symbol} | "
                f"supply={signal['total_supply_m']:.0f}M | "
                f"burn_z={signal['burn_zscore']:.2f} | "
                f"peg_dev={signal['peg_dev_bps']:.2f}bps"
            )

        except Exception as e:

            log.error(f"{symbol} failed: {e}")

            # NEVER REMOVE COIN
            signals[symbol] = build_empty_signal(symbol)

    log.info(
        f"=== StableGuard fetch complete: "
        f"{len(signals)} coins ==="
    )

    return signals


# ── Local test ───────────────────────────────────────────────
if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    print("\n=== StableGuard DefiLlama Test ===\n")

    data = fetch_all_signals()

    print(f"\nFetched {len(data)} coins\n")

    for symbol, signal in data.items():

        print(
            f"{symbol:8} | "
            f"Supply={signal['total_supply_m']:10.0f}M | "
            f"BurnZ={signal['burn_zscore']:6.2f} | "
            f"PegDev={signal['peg_dev_bps']:6.2f}bps | "
            f"Trend={signal['risk_trend']}"
        )