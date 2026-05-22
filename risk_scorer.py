"""
StableGuard — risk_scorer.py
Layer 3: Signal Engine Core
Combines Dune on-chain signals + CoinGecko price signals
into composite risk score + alert level per stablecoin.

Alert tiers (per StableGuard spec):
  🟢 HEALTHY : no pillars triggered
  🟡 WATCH   : 1 pillar triggered OR peg dev > 20bps
  🟠 REDUCE  : 2 pillars triggered OR burn z > 2 + peg dev
  🔴 EXIT    : 3 pillars triggered OR arb stopped + burn z > 3
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger("risk_scorer")

# ── Signal weights (must sum to 1.0) ──────────────────────────
WEIGHTS = {
    "liquidity": 0.35,   # Pillar 1: peg deviation + pool ratio
    "mintburn":  0.30,   # Pillar 2: burn z-score + whale clusters
    "arb":       0.35,   # Pillar 3: arb frequency decline
}

# ── Thresholds per spec ────────────────────────────────────────
THRESHOLDS = {
    # Peg deviation (basis points)
    "peg_watch":        20,     # 0.20 cents = WATCH
    "peg_reduce":       50,     # 0.50 cents = REDUCE
    "peg_exit":         100,    # 1.00 cent  = EXIT

    # Burn z-score
    "burn_z_watch":     1.5,
    "burn_z_reduce":    2.0,
    "burn_z_exit":      3.0,

    # Burn % of baseline
    "burn_pct_watch":   150,    # 150% of baseline = WATCH
    "burn_pct_reduce":  250,    # 250% of baseline = REDUCE

    # Arb z-score (negative = bots pulled back)
    "arb_z_watch":     -1.0,
    "arb_z_reduce":    -1.5,
    "arb_z_exit":      -2.0,

    # Composite score bands
    "score_watch":      20,
    "score_reduce":     45,
    "score_exit":       70,
}


# ── Individual pillar scorers ──────────────────────────────────

def score_liquidity_pillar(data: dict, gecko: dict | None) -> dict:
    """
    Pillar 1: Liquidity score (0-100)
    Sources: DefiLlama peg_dev_bps + liq_zscore
             CoinGecko peg_deviation_bps (real-time price, if available)
    Higher score = more risk.
    """
    score  = 0
    flags  = []
    details = {}

    # ── From DefiLlama ──
    liq_zscore     = data.get("liq_zscore", 0) or 0
    avg_peg_bps    = data.get("avg_peg_dev_bps", 0) or 0
    flag_peg_dev   = data.get("flag_peg_dev", 0) or 0
    flag_peg_spike = data.get("flag_peg_spike", 0) or 0

    # Use real-time price if available
    rt_peg_bps = data.get("peg_dev_bps", avg_peg_bps) or avg_peg_bps
    best_peg   = max(avg_peg_bps, rt_peg_bps)

    # Liq z-score contribution (0-40 points)
    score += min(40, max(0, liq_zscore * 15))

    # Peg deviation contribution (0-40 points)
    if best_peg > THRESHOLDS["peg_exit"]:
        score += 40
        flags.append("PEG_EXTREME")
    elif best_peg > THRESHOLDS["peg_reduce"]:
        score += 25
        flags.append("PEG_HIGH")
    elif best_peg > THRESHOLDS["peg_watch"]:
        score += 12
        flags.append("PEG_ELEVATED")

    # Flag bonuses (0-20 points)
    if flag_peg_spike:
        score += 15
        flags.append("PEG_SPIKE")
    if flag_peg_dev and "PEG_ELEVATED" not in flags:
        score += 5
        flags.append("PEG_DEV")

    # ── CoinGecko enrichment (if available) ──
    if gecko:
        cg_peg = gecko.get("peg_deviation_bps", 0) or 0
        details["cg_peg_bps"]   = cg_peg
        details["cg_peg_alert"] = gecko.get("peg_alert", "HEALTHY")
        if cg_peg > best_peg:
            if cg_peg > THRESHOLDS["peg_exit"]:
                score = max(score, 80)
                flags.append("REALTIME_PEG_EXIT")
            elif cg_peg > THRESHOLDS["peg_reduce"]:
                score = max(score, 55)
                flags.append("REALTIME_PEG_HIGH")
            elif cg_peg > THRESHOLDS["peg_watch"]:
                score = max(score, 25)

    final_score = min(100, max(0, round(score, 0)))
    details.update({
        "liq_zscore":   liq_zscore,
        "avg_peg_bps":  best_peg,
        "pillar_score": final_score,
        "flags":        flags,
    })
    return {"score": final_score, "flags": flags, "details": details}


def score_mintburn_pillar(dune: dict) -> dict:
    """
    Pillar 2: Mint/Burn anomaly score (0-100)
    Sources: Dune burn_zscore, burn_pct_baseline,
             flag_mint_defense, flag_whale_cluster

    Higher score = more risk.
    """
    score  = 0
    flags  = []
    details = {}

    burn_zscore       = dune.get("burn_zscore", 0) or 0
    burn_pct_baseline = dune.get("burn_pct_baseline", 0) or 0
    flag_mint_defense = dune.get("flag_mint_defense", 0) or 0
    flag_whale_cluster = dune.get("flag_whale_cluster", 0) or 0
    net_change_m      = dune.get("net_change_m", 0) or 0

    # Burn z-score contribution (0-50 points)
    if burn_zscore > THRESHOLDS["burn_z_exit"]:
        score += 50
        flags.append("BURN_CRITICAL")
    elif burn_zscore > THRESHOLDS["burn_z_reduce"]:
        score += 35
        flags.append("BURN_HIGH")
    elif burn_zscore > THRESHOLDS["burn_z_watch"]:
        score += 20
        flags.append("BURN_ELEVATED")
    elif burn_zscore > 1.0:
        score += 10

    # Burn % of baseline contribution (0-25 points)
    if burn_pct_baseline > THRESHOLDS["burn_pct_reduce"]:
        score += 25
        flags.append("BURN_PCT_CRITICAL")
    elif burn_pct_baseline > THRESHOLDS["burn_pct_watch"]:
        score += 15
        flags.append("BURN_PCT_HIGH")

    # Mint defense flag — protocol minting to defend peg (0-15 points)
    # Per spec: "signal of desperation, not stability"
    if flag_mint_defense:
        score += 15
        flags.append("MINT_DEFENSE")

    # Whale cluster flag (0-10 points)
    if flag_whale_cluster:
        score += 10
        flags.append("WHALE_CLUSTER")

    # Supply contraction bonus
    if net_change_m < 0 and burn_zscore > 1.5:
        score += 5
        flags.append("SUPPLY_CONTRACTING")

    final_score = min(100, max(0, round(score, 0)))
    details.update({
        "burn_zscore":        burn_zscore,
        "burn_pct_baseline":  burn_pct_baseline,
        "flag_mint_defense":  flag_mint_defense,
        "flag_whale_cluster": flag_whale_cluster,
        "net_change_m":       net_change_m,
        "pillar_score":       final_score,
        "flags":              flags,
    })

    return {"score": final_score, "flags": flags, "details": details}


def score_arb_pillar(dune: dict) -> dict:
    """
    Pillar 3: Arbitrage breakdown score (0-100)
    Sources: Dune arb_zscore, flag_arb_decline, flag_whale_dominance

    INVERTED: lower arb activity = HIGHER risk score.
    Per spec: "when arb bots stop, they signal they don't trust the peg"
    """
    score  = 0
    flags  = []
    details = {}

    arb_zscore          = dune.get("arb_zscore", 0) or 0
    flag_arb_decline    = dune.get("flag_arb_decline", 0) or 0
    flag_whale_dominance = dune.get("flag_whale_dominance", 0) or 0
    arb_trades          = dune.get("arb_trades", 0) or 0
    baseline_arb        = dune.get("baseline_arb_trades", 0) or 0

    # Arb z-score contribution (inverted — negative z = higher risk)
    # (0-60 points)
    if arb_zscore < THRESHOLDS["arb_z_exit"]:
        score += 60
        flags.append("ARB_STOPPED")        # loudest signal in the system
    elif arb_zscore < THRESHOLDS["arb_z_reduce"]:
        score += 40
        flags.append("ARB_DECLINING")
    elif arb_zscore < THRESHOLDS["arb_z_watch"]:
        score += 20
        flags.append("ARB_SOFTENING")

    # Arb decline flag (0-25 points)
    if flag_arb_decline:
        score += 25
        flags.append("ARB_BELOW_BASELINE")

    # Whale dominance: whales trading more than arb bots = directional pressure
    if flag_whale_dominance:
        score += 15
        flags.append("WHALE_DOMINANCE")

    # Absolute arb count check
    if baseline_arb > 0 and arb_trades < baseline_arb * 0.3:
        score = max(score, 70)             # less than 30% of normal = critical
        flags.append("ARB_NEAR_ZERO")

    final_score = min(100, max(0, round(score, 0)))
    details.update({
        "arb_zscore":           arb_zscore,
        "arb_trades":           arb_trades,
        "baseline_arb_trades":  baseline_arb,
        "flag_arb_decline":     flag_arb_decline,
        "flag_whale_dominance": flag_whale_dominance,
        "pillar_score":         final_score,
        "flags":                flags,
    })

    return {"score": final_score, "flags": flags, "details": details}


# ── Alert tier logic ───────────────────────────────────────────

def determine_alert_level(
    composite_score: float,
    pillars_active:  int,
    liq:  dict,
    mb:   dict,
    arb:  dict,
) -> str:
    """
    Maps signal state to alert tier per StableGuard spec:

    EXIT   : all 3 pillars OR arb stopped + burn z > 3
    REDUCE : 2 pillars elevated OR burn z > 2 + peg dev
    WATCH  : 1 pillar triggered OR peg dev > 20bps
    HEALTHY: nothing triggered
    """
    liq_flags = liq.get("flags", [])
    mb_flags  = mb.get("flags", [])
    arb_flags = arb.get("flags", [])

    # EXIT conditions
    if pillars_active >= 3:
        return "🔴 EXIT"
    if "ARB_STOPPED" in arb_flags and "BURN_CRITICAL" in mb_flags:
        return "🔴 EXIT"
    if "ARB_NEAR_ZERO" in arb_flags and pillars_active >= 2:
        return "🔴 EXIT"
    if composite_score >= THRESHOLDS["score_exit"]:
        return "🔴 EXIT"

    # REDUCE conditions
    if pillars_active >= 2:
        return "🟠 REDUCE"
    if "BURN_HIGH" in mb_flags and "PEG_HIGH" in liq_flags:
        return "🟠 REDUCE"
    if "ARB_DECLINING" in arb_flags and "BURN_HIGH" in mb_flags:
        return "🟠 REDUCE"
    if composite_score >= THRESHOLDS["score_reduce"]:
        return "🟠 REDUCE"

    # WATCH conditions
    if pillars_active >= 1:
        return "🟡 WATCH"
    if "PEG_ELEVATED" in liq_flags or "REALTIME_PEG_HIGH" in liq_flags:
        return "🟡 WATCH"
    if composite_score >= THRESHOLDS["score_watch"]:
        return "🟡 WATCH"

    return "🟢 HEALTHY"


# ── Response guidance (per spec: alerts must be explainable) ───

def generate_guidance(alert_level: str, mb: dict, liq: dict, arb: dict) -> str:
    """
    Per StableGuard design principle:
    'Every alert must tell the user what is happening,
     which signals triggered it, and what the historical precedent is.'
    """
    mb_flags  = mb.get("flags", [])
    liq_flags = liq.get("flags", [])
    arb_flags = arb.get("flags", [])
    all_flags = mb_flags + liq_flags + arb_flags

    if alert_level == "🔴 EXIT":
        guidance = "EXIT stablecoin positions immediately. "
        if "ARB_STOPPED" in all_flags:
            guidance += "Arbitrage bots have stopped defending the peg — " \
                        "they no longer trust the mechanism to restore. " \
                        "This is the loudest signal in the system. "
        if "BURN_CRITICAL" in all_flags:
            guidance += "Burn rate is critically elevated (z-score > 3). " \
                        "UST showed this pattern 18 hours before collapse. "
        if "MINT_DEFENSE" in all_flags:
            guidance += "Protocol treasury is minting to defend the peg — " \
                        "a signal of desperation, not stability. "
        guidance += "Builders: activate circuit breakers. " \
                    "Risk managers: escalate to senior review."

    elif alert_level == "🟠 REDUCE":
        guidance = "Reduce stablecoin exposure by 30-50%. Prepare exit strategy. "
        if "BURN_HIGH" in all_flags:
            guidance += "Burn rate is significantly above baseline. "
        if "ARB_DECLINING" in all_flags:
            guidance += "Arbitrage activity is declining — bots are losing confidence. "
        if "WHALE_CLUSTER" in all_flags:
            guidance += "Multiple large redemption events detected in a short window. "
        guidance += "Monitor closely. Two signal pillars are now elevated."

    elif alert_level == "🟡 WATCH":
        guidance = "Monitor position closely. No action required but elevated attention warranted. "
        if "PEG_ELEVATED" in all_flags or "REALTIME_PEG_HIGH" in all_flags:
            guidance += "Peg deviation is above normal threshold. "
        if "BURN_ELEVATED" in all_flags:
            guidance += "Burn activity is slightly above baseline — watch for escalation. "
        if "ARB_SOFTENING" in all_flags:
            guidance += "Arbitrage activity is softening. Not critical yet. "
        guidance += "One signal pillar has crossed threshold."

    else:
        guidance = "All signals normal. No action required."

    return guidance.strip()


# ── Master scorer ──────────────────────────────────────────────

def score_stablecoin(
    symbol:      str,
    dune_data:   dict,
    gecko_data:  dict | None = None
) -> dict:
    """
    Master scoring function — called by scheduler.py for each coin.
    Combines all 3 pillars into a final risk assessment.

    Args:
        symbol:     e.g. 'USDC'
        dune_data:  dict from dune_fetcher.parse_signal_rows()[symbol]
        gecko_data: dict from coingecko_fetcher.fetch_live_prices() for this coin

    Returns:
        Complete risk assessment object matching /v1/risk API schema.
    """
    scored_at = datetime.now(timezone.utc).isoformat()

    # Score each pillar
    liq = score_liquidity_pillar(dune_data, gecko_data)
    mb  = score_mintburn_pillar(dune_data)
    arb = score_arb_pillar(dune_data)

    # Weighted composite score
    composite = round(
        WEIGHTS["liquidity"] * liq["score"] +
        WEIGHTS["mintburn"]  * mb["score"]  +
        WEIGHTS["arb"]       * arb["score"],
        0
    )
    composite = min(100, max(0, composite))

    # Count how many pillars are elevated (score >= 25)
    pillars_active = sum([
        1 if liq["score"] >= 25 else 0,
        1 if mb["score"]  >= 25 else 0,
        1 if arb["score"] >= 25 else 0,
    ])

    # Determine alert level
    alert_level = determine_alert_level(
        composite, pillars_active, liq, mb, arb
    )

    # Generate plain-language guidance
    guidance = generate_guidance(alert_level, mb, liq, arb)

    # All flags combined
    all_flags = liq["flags"] + mb["flags"] + arb["flags"]

    # Build risk object — matches /v1/risk API schema from spec
    risk_object = {
        # Core output
        "symbol":           symbol,
        "chain":            "ethereum",
        "scored_at":        scored_at,
        "alert_level":      alert_level,
        "composite_score":  int(composite),
        "pillars_active":   pillars_active,
        "guidance":         guidance,

        # Signal breakdown (per API schema in spec)
        "signals": {
            "liquidity": {
                "score":       int(liq["score"]),
                "liq_zscore":  liq["details"].get("liq_zscore", 0),
                "peg_dev_bps": liq["details"].get("avg_peg_bps", 0),
                "anomaly":     len(liq["flags"]) > 0,
                "flags":       liq["flags"],
            },
            "mintBurn": {
                "score":            int(mb["score"]),
                "burn_zscore":      mb["details"].get("burn_zscore", 0),
                "burn_pct_baseline":mb["details"].get("burn_pct_baseline", 0),
                "anomaly":          len(mb["flags"]) > 0,
                "flags":            mb["flags"],
            },
            "arb": {
                "score":        int(arb["score"]),
                "arb_zscore":   arb["details"].get("arb_zscore", 0),
                "arb_trades":   arb["details"].get("arb_trades", 0),
                "baseline_arb": arb["details"].get("baseline_arb_trades", 0),
                "anomaly":      len(arb["flags"]) > 0,
                "flags":        arb["flags"],
            },
        },

        # Real-time price data (from CoinGecko if available)
        "price": {
            "usd":             gecko_data.get("price_usd", None)     if gecko_data else None,
            "peg_dev_bps":     gecko_data.get("peg_deviation_bps", 0) if gecko_data else None,
            "volume_24h_usd":  gecko_data.get("volume_24h_usd", 0)   if gecko_data else None,
            "peg_alert":       gecko_data.get("peg_alert", "UNKNOWN") if gecko_data else None,
        },

        # All active flags for dashboard
        "active_flags":     all_flags,
        "total_flags":      len(all_flags),

        # Raw Dune data passthrough (for analyst workbench)
        "raw": {
            "week":             dune_data.get("week"),
            "risk_trend":       dune_data.get("risk_trend"),
            "total_supply_m":   dune_data.get("total_supply_m"),
            "net_change_m":     dune_data.get("net_change_m"),
            "mints_m":          dune_data.get("mints_m"),
            "burns_m":          dune_data.get("burns_m"),
        }
    }

    log.info(
        f"{symbol:8} | {alert_level} | score={int(composite):3d} | "
        f"liq={int(liq['score']):3d} mb={int(mb['score']):3d} arb={int(arb['score']):3d} | "
        f"flags={all_flags}"
    )

    return risk_object


def score_all(dune_signals: dict, gecko_prices: list = None) -> dict:
    """
    Scores all stablecoins.
    Called by scheduler.py with output from both fetchers.

    Returns dict keyed by symbol:
    {
        "USDC": { risk_object },
        "USDT": { risk_object },
        ...
    }
    """
    log.info("=== Risk scoring cycle starting ===")

    # Build CoinGecko lookup by symbol
    gecko_map = {}
    if gecko_prices:
        for g in gecko_prices:
            gecko_map[g.get("symbol")] = g

    results = {}
    for symbol, dune_data in dune_signals.items():
        gecko_data = gecko_map.get(symbol)
        results[symbol] = score_stablecoin(symbol, dune_data, gecko_data)

    log.info(f"=== Risk scoring complete: {len(results)} coins scored ===")
    return results


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Import fetchers
    from dune_fetcher import fetch_dune_signals

    print("\n=== StableGuard Risk Scorer — Live Test ===\n")

    # Fetch Dune signals
    dune_signals = fetch_dune_signals(force_refresh=False)

    if not dune_signals:
        print("No Dune data. Check dune_fetcher.py")
        exit(1)

    # Score all coins (no CoinGecko for now — VPN pending)
    results = score_all(dune_signals)

    print("\n" + "="*80)
    print(f"{'SYMBOL':<8} {'ALERT':<18} {'SCORE':>5} {'LIQ':>5} {'MB':>5} {'ARB':>5} {'FLAGS'}")
    print("="*80)

    for symbol, r in sorted(results.items(), key=lambda x: -x[1]["composite_score"]):
        sig = r["signals"]
        print(
            f"{symbol:<8} "
            f"{r['alert_level']:<18} "
            f"{r['composite_score']:>5} "
            f"{sig['liquidity']['score']:>5} "
            f"{sig['mintBurn']['score']:>5} "
            f"{sig['arb']['score']:>5}  "
            f"{r['active_flags']}"
        )

    print("\n--- Guidance for highest risk coin ---")
    top = max(results.values(), key=lambda x: x["composite_score"])
    print(f"\n{top['symbol']} ({top['alert_level']}):")
    print(f"  {top['guidance']}")

    print("\n--- Full risk object sample (USDT) ---")
    import json
    if "USDT" in results:
        print(json.dumps(results["USDT"], indent=2, default=str))