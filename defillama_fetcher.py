"""
StableGuard — defillama_fetcher.py
Layer 2: DefiLlama API fetcher (replaces dune_fetcher)
100% free, no API key, no rate limits
Fetches: stablecoin supply, mint/burn, peg deviation, chain data
"""

import time
import logging
import requests
import numpy as np
from datetime import datetime, timezone, timedelta

log = logging.getLogger("defillama_fetcher")

BASE_URL   = "https://stablecoins.llama.fi"
PRICES_URL = "https://stablecoins.llama.fi/stablecoinprices"

# ── StableGuard monitored coins ───────────────────────────────
# Maps our symbol → DefiLlama ID
STABLECOINS = {
    "USDT":   1,
    "USDC":   2,
    "DAI":    5,
    "FRAX":   6,
    "PYUSD":  151,
    "crvUSD": 170,
    "LUSD":   50,
}

# Reverse lookup
ID_TO_SYMBOL = {v: k for k, v in STABLECOINS.items()}

HEADERS = {"Accept": "application/json"}
TIMEOUT = 20


# ── HTTP helper ───────────────────────────────────────────────
def _get(url: str, retries: int = 3) -> dict | list | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            else:
                log.warning(f"HTTP {resp.status_code} for {url}. Attempt {attempt+1}/{retries}")
                time.sleep(3)
        except requests.RequestException as e:
            log.error(f"Request error: {e}. Attempt {attempt+1}/{retries}")
            time.sleep(3)
    log.error(f"All {retries} attempts failed for {url}")
    return None


# ── Fetch 1: All stablecoin metadata + current supply ─────────
def fetch_all_stablecoins() -> dict:
    """
    Endpoint: /stablecoins
    Returns current circulating supply and metadata for all stablecoins.
    """
    data = _get(f"{BASE_URL}/stablecoins?includePrices=true")
    if not data:
        return {}

    result = {}
    peggedAssets = data.get("peggedAssets", [])

    for coin in peggedAssets:
        coin_id = coin.get("id")
        if coin_id not in ID_TO_SYMBOL:
            continue

        symbol = ID_TO_SYMBOL[coin_id]

        # Current circulating supply
        circulating = coin.get("circulating", {})
        supply_usd  = circulating.get("peggedUSD", 0) or 0

        # Price from DefiLlama
        price = coin.get("price", 1.0) or 1.0

        # Peg deviation in basis points
        peg_dev_bps = round(abs(price - 1.0) * 10000, 2)

        # Chain breakdown
        chains = coin.get("chainCirculating", {})

        result[symbol] = {
            "symbol":       symbol,
            "defillama_id": coin_id,
            "name":         coin.get("name", symbol),
            "price_usd":    round(price, 6),
            "peg_dev_bps":  peg_dev_bps,
            "supply_usd":   round(supply_usd / 1e6, 2),  # in millions
            "chains":       {
                c: round((v.get("current", {}).get("peggedUSD", 0) or 0) / 1e6, 2)
                for c, v in chains.items()
            },
            "peg_alert": (
                "EXIT"    if peg_dev_bps > 100 else
                "REDUCE"  if peg_dev_bps > 50  else
                "WATCH"   if peg_dev_bps > 20  else
                "HEALTHY"
            ),
        }

    log.info(f"fetch_all_stablecoins: {len(result)} coins fetched")
    return result


# ── Fetch 2: Historical supply for mint/burn signal ───────────
def fetch_supply_history(defillama_id: int, symbol: str) -> dict:
    """
    Endpoint: /stablecoin/{id}
    Returns historical circulating supply — used to compute
    weekly mint/burn and z-score baseline.
    """
    data = _get(f"{BASE_URL}/stablecoin/{defillama_id}")
    if not data:
        return {}

    tokens = data.get("tokens", [])
    if not tokens:
        return {}

    # Build weekly supply series
    weekly = {}
    for entry in tokens:
        date_str = entry.get("date")
        if not date_str:
            continue

        # Normalize to week
        try:
            dt   = datetime.fromtimestamp(int(date_str), tz=timezone.utc)
            week = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        circulating = entry.get("circulating", {})
        supply      = (circulating.get("peggedUSD", 0) or 0) / 1e6

        if week not in weekly or supply > weekly[week]:
            weekly[week] = supply

    if not weekly:
        return {}

    # Sort by date
    sorted_weeks = sorted(weekly.keys())
    supplies     = [weekly[w] for w in sorted_weeks]

    # Compute weekly net changes (mint = positive, burn = negative)
    net_changes = [0.0] + [
        supplies[i] - supplies[i-1]
        for i in range(1, len(supplies))
    ]

    # Last 8 weeks of data
    recent_weeks   = sorted_weeks[-8:]
    recent_supply  = supplies[-8:]
    recent_net     = net_changes[-8:]

    # Separate mints and burns
    mints  = [max(0, n)  for n in recent_net]
    burns  = [abs(min(0, n)) for n in recent_net]

    # Current values
    current_supply     = supplies[-1] if supplies else 0
    current_net        = net_changes[-1] if net_changes else 0
    current_mint       = max(0, current_net)
    current_burn       = abs(min(0, current_net))

    # 4-week baseline (prior 4 weeks, exclude current)
    baseline_burns     = burns[-5:-1] if len(burns) >= 5 else burns[:-1]
    baseline_mints     = mints[-5:-1] if len(mints) >= 5 else mints[:-1]

    avg_burn_4w        = float(np.mean(baseline_burns))  if baseline_burns else 0
    std_burn_4w        = float(np.std(baseline_burns))   if baseline_burns else 0
    avg_mint_4w        = float(np.mean(baseline_mints))  if baseline_mints else 0

    # Burn z-score (core anomaly signal)
    burn_zscore = round(
        (current_burn - avg_burn_4w) / std_burn_4w
        if std_burn_4w > 0 else 0.0,
        2
    )

    # Burn % of baseline
    burn_pct_baseline = round(
        100.0 * current_burn / avg_burn_4w
        if avg_burn_4w > 0 else 0.0,
        0
    )

    # Mint defense flag: minting while supply contracting
    flag_mint_defense = int(
        current_mint > avg_mint_4w * 2 and current_net < 0
    )

    # Supply contraction flag
    is_contracting = int(current_net < 0)

    # Burn tx count proxy (large burns = whale cluster)
    # DefiLlama doesn't give tx count — use burn size as proxy
    flag_whale_cluster = int(current_burn > avg_burn_4w * 3)

    log.info(
        f"{symbol}: supply={current_supply:.0f}M "
        f"burn_z={burn_zscore:.2f} "
        f"pct={burn_pct_baseline:.0f}%"
    )

    return {
        "symbol":             symbol,
        "total_supply_m":     round(current_supply, 2),
        "net_change_m":       round(current_net, 2),
        "mints_m":            round(current_mint, 2),
        "burns_m":            round(current_burn, 2),
        "baseline_burn_m":    round(avg_burn_4w, 2),
        "burn_zscore":        burn_zscore,
        "burn_pct_baseline":  int(burn_pct_baseline),
        "flag_mint_defense":  flag_mint_defense,
        "flag_whale_cluster": flag_whale_cluster,
        "is_contracting":     is_contracting,
        "recent_weeks":       recent_weeks,
        "recent_supply":      [round(s, 2) for s in recent_supply],
        "recent_net":         [round(n, 2) for n in recent_net],
    }


# ── Fetch 3: Peg deviation history (liquidity signal) ─────────
def fetch_peg_history(defillama_id: int, symbol: str) -> dict:
    """
    Uses supply history price data to compute peg deviation z-score.
    This is our Liquidity Pillar proxy without DEX pool data.
    """
    data = _get(f"{BASE_URL}/stablecoin/{defillama_id}")
    if not data:
        return {}

    tokens = data.get("tokens", [])
    prices = []

    for entry in tokens:
        price = entry.get("price")
        if price and isinstance(price, (int, float)):
            prices.append(float(price))

    if len(prices) < 5:
        return {}

    # Recent prices (last 8 weeks)
    recent = prices[-8:]
    current_price = recent[-1]
    peg_devs = [abs(p - 1.0) * 10000 for p in recent]

    # Baseline (prior 4 weeks)
    baseline_devs = peg_devs[-5:-1] if len(peg_devs) >= 5 else peg_devs[:-1]
    avg_dev = float(np.mean(baseline_devs)) if baseline_devs else 0
    std_dev = float(np.std(baseline_devs))  if baseline_devs else 0

    current_dev = peg_devs[-1] if peg_devs else 0

    # Liq z-score
    liq_zscore = round(
        (current_dev - avg_dev) / std_dev
        if std_dev > 0 else 0.0,
        2
    )

    return {
        "symbol":      symbol,
        "price_usd":   round(current_price, 6),
        "peg_dev_bps": round(current_dev, 2),
        "liq_zscore":  liq_zscore,
        "flag_peg_dev":   int(current_dev > 20),
        "flag_peg_spike": int(current_dev > 100),
    }


# ── Fetch 4: Chain flow asymmetry (cross-chain signal) ────────
def fetch_chain_flows(defillama_id: int, symbol: str) -> dict:
    """
    Detects unusual one-directional outflows from any chain.
    Ethereum dominance drop = potential bridge exodus signal.
    """
    data = _get(f"{BASE_URL}/stablecoin/{defillama_id}")
    if not data:
        return {}

    chain_circ = data.get("chainBalances", {})
    eth_supply  = 0
    total_supply = 0

    for chain, val in chain_circ.items():
        tokens = val.get("tokens", [])
        if tokens:
            latest = tokens[-1].get("circulating", {})
            amount = (latest.get("peggedUSD", 0) or 0) / 1e6
            total_supply += amount
            if chain.lower() == "ethereum":
                eth_supply = amount

    eth_dominance = round(
        100 * eth_supply / total_supply
        if total_supply > 0 else 0, 1
    )

    return {
        "symbol":         symbol,
        "eth_supply_m":   round(eth_supply, 2),
        "total_supply_m": round(total_supply, 2),
        "eth_dominance":  eth_dominance,
        # Flag if ETH dominance drops below 40% (cross-chain flight)
        "flag_chain_flight": int(eth_dominance < 40 and total_supply > 100),
    }


# ── Master fetch: all signals for all coins ───────────────────
def fetch_all_signals() -> dict:
    """
    Master function called by api_server.py every 5 minutes.
    Returns complete signal dict keyed by symbol — same format
    as old dune_fetcher output so risk_scorer.py works unchanged.
    """
    log.info("=== DefiLlama fetch cycle starting ===")

    # Step 1: Get current supply + prices for all coins
    all_coins = fetch_all_stablecoins()
    if not all_coins:
        log.error("fetch_all_stablecoins returned empty. Check connectivity.")
        return {}

    signals = {}

    for symbol, coin_id in STABLECOINS.items():
        log.info(f"Fetching history for {symbol}...")

        try:
            # Step 2: Supply history → mint/burn signal
            supply_data = fetch_supply_history(coin_id, symbol)
            time.sleep(0.5)  # gentle rate limiting

            # Step 3: Peg deviation history → liquidity signal
            peg_data = fetch_peg_history(coin_id, symbol)
            time.sleep(0.5)

            # Step 4: Combine with current data
            current = all_coins.get(symbol, {})

            # Use real-time price from current data (more accurate)
            current_peg_dev = current.get("peg_dev_bps", 0)
            current_price   = current.get("price_usd", 1.0)

            # Arb z-score proxy:
            # When peg deviation is high AND supply is contracting = arb breakdown
            # This replaces the DEX arb signal from Dune
            peg_dev       = current_peg_dev or peg_data.get("peg_dev_bps", 0)
            is_contracting = supply_data.get("is_contracting", 0)
            burn_zscore    = supply_data.get("burn_zscore", 0)

            # Arb z-score: negative = arb declining
            # Formula: more peg deviation + less arb activity = lower (worse) score
            arb_zscore = round(
                -1.0 * (peg_dev / 20.0) if peg_dev > 0 else 0.0,
                2
            )
            arb_zscore = max(-3.0, min(3.0, arb_zscore))  # cap at ±3

            flag_arb_decline     = int(peg_dev > 30 and burn_zscore > 1.0)
            flag_whale_dominance = int(
                supply_data.get("flag_whale_cluster", 0) == 1 and peg_dev > 20
            )

            # Risk trend (based on burn z-score direction)
            if burn_zscore > 0.5:
                risk_trend = "↑ Rising"
            elif burn_zscore < -0.5:
                risk_trend = "↓ Falling"
            else:
                risk_trend = "→ Stable"

            signals[symbol] = {
                # Metadata
                "symbol":    symbol,
                "week":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),

                # Alert output (will be set by risk_scorer)
                "alert_level":     "🟢 HEALTHY",
                "composite_score": 0,
                "risk_trend":      risk_trend,
                "score_delta":     0,
                "pillars_active":  0,
                "total_flags":     0,

                # Pillar 1: Mint/Burn
                "mints_m":             supply_data.get("mints_m", 0),
                "burns_m":             supply_data.get("burns_m", 0),
                "burn_tx_count":       0,
                "net_change_m":        supply_data.get("net_change_m", 0),
                "total_supply_m":      supply_data.get("total_supply_m", 0),
                "baseline_burn_m":     supply_data.get("baseline_burn_m", 0),
                "burn_zscore":         burn_zscore,
                "burn_pct_baseline":   supply_data.get("burn_pct_baseline", 0),
                "flag_mint_defense":   supply_data.get("flag_mint_defense", 0),
                "flag_whale_cluster":  supply_data.get("flag_whale_cluster", 0),

                # Pillar 2: Liquidity
                "avg_peg_dev_bps":  peg_dev,
                "max_peg_dev_bps":  peg_dev,
                "liq_zscore":       peg_data.get("liq_zscore", 0),
                "flag_peg_dev":     peg_data.get("flag_peg_dev", int(peg_dev > 20)),
                "flag_peg_spike":   peg_data.get("flag_peg_spike", int(peg_dev > 100)),

                # Pillar 3: Arb (proxy)
                "arb_trades":          0,
                "whale_trades":        0,
                "baseline_arb_trades": 0,
                "arb_zscore":          arb_zscore,
                "flag_arb_decline":    flag_arb_decline,
                "flag_whale_dominance":flag_whale_dominance,

                # Price data (real-time)
                "price_usd":    current_price,
                "peg_dev_bps":  current_peg_dev,
            }

            log.info(
                f"{symbol}: supply={supply_data.get('total_supply_m',0):.0f}M "
                f"burn_z={burn_zscore:.2f} "
                f"peg_dev={peg_dev:.1f}bps "
                f"arb_z={arb_zscore:.2f}"
            )

        except Exception as e:
            log.error(f"Error fetching {symbol}: {e}", exc_info=True)
            continue

    log.info(f"=== DefiLlama fetch complete: {len(signals)} coins ===")
    return signals


# ── Standalone test ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    print("\n=== StableGuard DefiLlama Fetcher — Test ===\n")

    print("--- Testing fetch_all_stablecoins ---")
    coins = fetch_all_stablecoins()
    for sym, data in coins.items():
        print(f"  {sym:8} price=${data['price_usd']:.4f} "
              f"peg_dev={data['peg_dev_bps']:.1f}bps "
              f"supply={data['supply_usd']:.0f}M "
              f"alert={data['peg_alert']}")

    print("\n--- Testing full signal fetch ---")
    signals = fetch_all_signals()
    print(f"\nSignals for {len(signals)} coins:\n")
    for sym, s in signals.items():
        print(f"  {sym:8} | supply={s['total_supply_m']:8.0f}M | "
              f"burn_z={s['burn_zscore']:6.2f} | "
              f"peg_dev={s['peg_dev_bps']:6.1f}bps | "
              f"arb_z={s['arb_zscore']:6.2f} | "
              f"trend={s['risk_trend']}")