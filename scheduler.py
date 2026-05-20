"""
StableGuard — scheduler.py
Layer 5: Master orchestrator
Runs every 5 minutes:
  1. Fetch Dune signals      (dune_fetcher.py)
  2. Fetch CoinGecko prices  (stable.py / coingecko_fetcher.py)
  3. Score all coins         (risk_scorer.py)
  4. Update API cache        (api_server.py)
  5. Dispatch alerts         (alert_dispatcher.py)
  6. Save to database        (database.py)

Run with: python scheduler.py
"""

import os
import logging
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

load_dotenv()

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("scheduler")

# ── Config ─────────────────────────────────────────────────────
FETCH_INTERVAL_MINUTES  = 5     # How often to run the full cycle
REFRESH_INTERVAL_CYCLES = 12    # Force Dune re-execution every 12 cycles (= 1 hour)

# Track cycles for smart refresh
_cycle_count = 0
_last_scores = {}


# ── Main cycle ─────────────────────────────────────────────────
def run_cycle():
    """
    Full StableGuard signal cycle.
    Runs every 5 minutes via APScheduler.
    """
    global _cycle_count, _last_scores
    _cycle_count += 1

    cycle_start = datetime.now(timezone.utc)
    log.info(f"\n{'='*60}")
    log.info(f"CYCLE #{_cycle_count} — {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"{'='*60}")

    # ── Step 1: Fetch Dune signals ─────────────────────────────
    try:
        from dune_fetcher import fetch_dune_signals

        # Force re-execution every hour to get fresh on-chain data
        # Use cached results every other cycle to save credits
        force_refresh = (_cycle_count % REFRESH_INTERVAL_CYCLES == 0)

        log.info(f"Step 1: Fetching Dune signals (force_refresh={force_refresh})...")
        dune_signals = fetch_dune_signals(force_refresh=force_refresh)

        if not dune_signals:
            log.error("Step 1 FAILED: No Dune signals. Skipping cycle.")
            return

        log.info(f"Step 1 OK: {len(dune_signals)} coins from Dune")

    except Exception as e:
        log.error(f"Step 1 ERROR: {e}")
        return

    # ── Step 2: Fetch CoinGecko prices ────────────────────────
    gecko_prices = []
    try:
        from stable import fetch_live_prices

        log.info("Step 2: Fetching CoinGecko live prices...")
        gecko_prices = fetch_live_prices()

        if gecko_prices:
            log.info(f"Step 2 OK: {len(gecko_prices)} coins from CoinGecko")
        else:
            log.warning("Step 2 WARNING: No CoinGecko data. Continuing with Dune only.")

    except Exception as e:
        log.warning(f"Step 2 WARNING: CoinGecko fetch failed: {e}. Continuing without price data.")

    # ── Step 3: Score all coins ────────────────────────────────
    try:
        from risk_scorer import score_all

        log.info("Step 3: Scoring all coins...")
        scores = score_all(dune_signals, gecko_prices)

        if not scores:
            log.error("Step 3 FAILED: No scores produced.")
            return

        log.info(f"Step 3 OK: {len(scores)} coins scored")

        # Log current standings
        log.info("Current risk standings:")
        for symbol, data in sorted(scores.items(), key=lambda x: -x[1]["composite_score"]):
            log.info(
                f"  {symbol:8} {data['alert_level']:18} "
                f"score={data['composite_score']:3d} "
                f"trend={data['raw'].get('risk_trend', 'N/A')}"
            )

    except Exception as e:
        log.error(f"Step 3 ERROR: {e}")
        return

    # ── Step 4: Update API cache ───────────────────────────────
    try:
        from api_server import update_cache

        log.info("Step 4: Updating API cache...")
        update_cache(scores)
        log.info("Step 4 OK: API cache updated")

    except Exception as e:
        log.warning(f"Step 4 WARNING: API cache update failed: {e}")

    # ── Step 5: Dispatch alerts ────────────────────────────────
    try:
        from alert_dispatcher import dispatch_all

        log.info("Step 5: Checking and dispatching alerts...")

        # Only dispatch if alert level changed or escalated vs last cycle
        alerts_to_send = {}
        for symbol, data in scores.items():
            current_level  = data.get("alert_level")
            previous_level = _last_scores.get(symbol, {}).get("alert_level")

            # Send if: new alert, level changed, or level escalated
            should_send = (
                current_level != "🟢 HEALTHY" and (
                    previous_level is None or
                    previous_level != current_level or
                    _is_escalation(previous_level, current_level)
                )
            )

            if should_send:
                alerts_to_send[symbol] = data
                log.info(
                    f"  Alert queued: {symbol} "
                    f"{previous_level or 'NEW'} → {current_level}"
                )

        if alerts_to_send:
            results = dispatch_all(alerts_to_send)
            sent    = sum(1 for r in results if r.get("dispatched"))
            log.info(f"Step 5 OK: {sent}/{len(alerts_to_send)} alerts dispatched")
        else:
            log.info("Step 5 OK: No new or changed alerts to dispatch")

    except Exception as e:
        log.warning(f"Step 5 WARNING: Alert dispatch failed: {e}")

    # ── Step 6: Save to database ───────────────────────────────
    try:
        from database import save_scores, save_alerts

        log.info("Step 6: Saving to database...")
        save_scores(scores)

        # Save only active alerts
        active_alerts = {
            s: d for s, d in scores.items()
            if d.get("alert_level") != "🟢 HEALTHY"
        }
        if active_alerts:
            save_alerts(active_alerts)

        log.info(f"Step 6 OK: Saved {len(scores)} scores, {len(active_alerts)} alerts")

    except Exception as e:
        log.warning(f"Step 6 WARNING: Database save failed: {e}")

    # ── Cycle complete ─────────────────────────────────────────
    _last_scores = scores
    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    log.info(f"\nCycle #{_cycle_count} complete in {elapsed:.1f}s")
    log.info(f"Next cycle in {FETCH_INTERVAL_MINUTES} minutes\n")


def _is_escalation(previous: str, current: str) -> bool:
    """
    Returns True if alert level escalated (got worse).
    HEALTHY < WATCH < REDUCE < EXIT
    """
    order = {
        "🟢 HEALTHY": 0,
        "🟡 WATCH":   1,
        "🟠 REDUCE":  2,
        "🔴 EXIT":    3,
    }
    return order.get(current, 0) > order.get(previous, 0)


# ── Startup summary ────────────────────────────────────────────
def print_startup_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║          STABLEGUARD — SIGNAL ENGINE STARTING            ║
║     Real-time Stablecoin Depeg Early Warning System      ║
╠══════════════════════════════════════════════════════════╣
║  Monitoring: USDC, USDT, DAI, FRAX, PYUSD, crvUSD, LUSD ║
║  Cycle:      Every 5 minutes                             ║
║  API:        http://localhost:8000                        ║
║  Docs:       http://localhost:8000/docs                  ║
╚══════════════════════════════════════════════════════════╝
""")


# ── Main entry point ───────────────────────────────────────────
if __name__ == "__main__":
    print_startup_banner()

    # Run first cycle immediately on startup
    log.info("Running initial cycle on startup...")
    try:
        run_cycle()
    except Exception as e:
        log.error(f"Initial cycle failed: {e}")

    # Start the scheduler
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        func=run_cycle,
        trigger=IntervalTrigger(minutes=FETCH_INTERVAL_MINUTES),
        id="stableguard_cycle",
        name="StableGuard Signal Cycle",
        replace_existing=True,
        max_instances=1,        # Never run two cycles simultaneously
    )

    log.info(f"Scheduler started. Running every {FETCH_INTERVAL_MINUTES} minutes.")
    log.info("Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user.")
        scheduler.shutdown()