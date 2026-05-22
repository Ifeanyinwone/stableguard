"""
StableGuard — scheduler.py
Layer 5: Master orchestrator (local development)
Runs every 5 minutes:
  1. Fetch DefiLlama signals  (defillama_fetcher.py)
  2. Fetch CoinGecko prices   (stable.py)
  3. Score all coins          (risk_scorer.py)
  4. Update API cache         (api_server.py)
  5. Dispatch alerts          (alert_dispatcher.py)
  6. Save to database         (database.py)

NOTE: On Render, api_server.py runs its own internal scheduler.
      This file is for LOCAL DEVELOPMENT only.

Run with: python scheduler.py
"""

import os
import sys
import logging
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

# ── Path fix ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval   import IntervalTrigger

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("scheduler")

# ── Config ────────────────────────────────────────────────────
FETCH_INTERVAL_MINUTES  = 5
REFRESH_INTERVAL_CYCLES = 12   # force fresh fetch every hour

_cycle_count = 0
_last_scores = {}


# ── Main cycle ────────────────────────────────────────────────
def run_cycle():
    global _cycle_count, _last_scores
    _cycle_count += 1

    cycle_start = datetime.now(timezone.utc)
    log.info(f"\n{'='*60}")
    log.info(f"CYCLE #{_cycle_count} — {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"{'='*60}")

    # ── Step 1: DefiLlama signals ─────────────────────────────
    try:
        from defillama_fetcher import fetch_all_signals
        log.info("Step 1: Fetching DefiLlama signals...")
        signals = fetch_all_signals()
        if not signals:
            log.error("Step 1 FAILED: No signals returned. Skipping cycle.")
            return
        log.info(f"Step 1 OK: {len(signals)} coins from DefiLlama")
    except Exception as e:
        log.error(f"Step 1 ERROR: {e}")
        return

    # ── Step 2: CoinGecko prices (optional enrichment) ────────
    gecko_prices = []
    try:
        from stable import fetch_live_prices
        log.info("Step 2: Fetching CoinGecko live prices...")
        gecko_prices = fetch_live_prices()
        if gecko_prices:
            log.info(f"Step 2 OK: {len(gecko_prices)} coins from CoinGecko")
        else:
            log.warning("Step 2: No CoinGecko data. Continuing with DefiLlama prices.")
    except Exception as e:
        log.warning(f"Step 2 WARNING: CoinGecko failed: {e}")

    # ── Step 3: Score all coins ───────────────────────────────
    try:
        from risk_scorer import score_all
        log.info("Step 3: Scoring all coins...")
        scores = score_all(signals, gecko_prices)
        if not scores:
            log.error("Step 3 FAILED: No scores produced.")
            return
        log.info(f"Step 3 OK: {len(scores)} coins scored")

        # Print standings
        log.info("Current standings:")
        for sym, d in sorted(scores.items(), key=lambda x: -x[1]["composite_score"]):
            log.info(
                f"  {sym:8} {d['alert_level']:18} "
                f"score={d['composite_score']:3d} "
                f"trend={d['raw'].get('risk_trend','N/A')}"
            )
    except Exception as e:
        log.error(f"Step 3 ERROR: {e}")
        return

    # ── Step 4: Update API cache ──────────────────────────────
    try:
        from api_server import update_cache
        log.info("Step 4: Updating API cache...")
        update_cache(scores)
        log.info("Step 4 OK: API cache updated")
    except Exception as e:
        log.warning(f"Step 4 WARNING: {e}")

    # ── Step 5: Dispatch alerts ───────────────────────────────
    try:
        from alert_dispatcher import dispatch_all
        log.info("Step 5: Checking alerts...")

        alerts_to_send = {}
        for symbol, data in scores.items():
            current  = data.get("alert_level")
            previous = _last_scores.get(symbol, {}).get("alert_level")
            if (current != "🟢 HEALTHY" and
                    (previous is None or previous != current or
                     _is_escalation(previous, current))):
                alerts_to_send[symbol] = data
                log.info(f"  Alert queued: {symbol} {previous or 'NEW'} → {current}")

        if alerts_to_send:
            results = dispatch_all(alerts_to_send)
            sent    = sum(1 for r in results if r.get("dispatched"))
            log.info(f"Step 5 OK: {sent}/{len(alerts_to_send)} alerts dispatched")
        else:
            log.info("Step 5 OK: No new alerts")
    except Exception as e:
        log.warning(f"Step 5 WARNING: {e}")

    # ── Step 6: Save to database ──────────────────────────────
    try:
        from database import save_scores, save_alerts
        log.info("Step 6: Saving to database...")
        save_scores(scores)
        active = {s: d for s, d in scores.items()
                  if d.get("alert_level") != "🟢 HEALTHY"}
        if active:
            save_alerts(active)
        log.info(f"Step 6 OK: {len(scores)} scores, {len(active)} alerts saved")
    except Exception as e:
        log.warning(f"Step 6 WARNING: {e}")

    # ── Complete ──────────────────────────────────────────────
    _last_scores = scores
    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    log.info(f"\nCycle #{_cycle_count} complete in {elapsed:.1f}s")
    log.info(f"Next cycle in {FETCH_INTERVAL_MINUTES} minutes\n")


def _is_escalation(previous: str, current: str) -> bool:
    order = {"🟢 HEALTHY": 0, "🟡 WATCH": 1, "🟠 REDUCE": 2, "🔴 EXIT": 3}
    return order.get(current, 0) > order.get(previous, 0)


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║          STABLEGUARD — SIGNAL ENGINE STARTING            ║
║     Real-time Stablecoin Depeg Early Warning System      ║
╠══════════════════════════════════════════════════════════╣
║  Data:   DefiLlama (free) + CoinGecko Pro                ║
║  Coins:  USDC, USDT, DAI, FRAX, PYUSD, crvUSD, LUSD     ║
║  Cycle:  Every 5 minutes                                 ║
║  API:    http://localhost:8000                            ║
╚══════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    print_banner()

    log.info("Running initial cycle on startup...")
    try:
        run_cycle()
    except Exception as e:
        log.error(f"Initial cycle failed: {e}")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        func=run_cycle,
        trigger=IntervalTrigger(minutes=FETCH_INTERVAL_MINUTES),
        id="stableguard_cycle",
        name="StableGuard Signal Cycle",
        replace_existing=True,
        max_instances=1,
    )

    log.info(f"Scheduler running every {FETCH_INTERVAL_MINUTES} minutes.")
    log.info("Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")
        scheduler.shutdown()