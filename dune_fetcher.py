"""
StableGuard — dune_fetcher.py
Layer 2: Dune Analytics API fetcher
Pulls: all 3 signal pillars from StableGuard master query (ID: 7504106)
Runs every 5 min via scheduler.py
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("dune_fetcher")

# ── Config ─────────────────────────────────────────────────────
DUNE_API_KEY  = os.getenv("DUNE_API_KEY", "YOUR_DUNE_API_KEY_HERE")
QUERY_ID      = 7504106   # StableGuard master signal engine query
BASE_URL      = "https://api.dune.com/api/v1"

HEADERS = {
    "X-Dune-API-Key": DUNE_API_KEY,
    "Content-Type":   "application/json"
}

# How long to wait for query execution (seconds)
MAX_WAIT     = 120
POLL_INTERVAL = 5


# ── Helper ─────────────────────────────────────────────────────
def _get(endpoint: str, params: dict = {}) -> dict | None:
    """Simple GET request to Dune API."""
    url = f"{BASE_URL}{endpoint}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            log.error("Invalid Dune API key. Check your .env file.")
            return None
        else:
            log.warning(f"HTTP {resp.status_code} on {endpoint}: {resp.text}")
            return None
    except requests.RequestException as e:
        log.error(f"Request error: {e}")
        return None


def _post(endpoint: str, body: dict = {}) -> dict | None:
    """Simple POST request to Dune API."""
    url = f"{BASE_URL}{endpoint}"
    try:
        resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        else:
            log.warning(f"HTTP {resp.status_code} on {endpoint}: {resp.text}")
            return None
    except requests.RequestException as e:
        log.error(f"Request error: {e}")
        return None


# ── Step 1: Execute the query ──────────────────────────────────
def execute_query() -> str | None:
    """
    Triggers a fresh execution of the StableGuard master query.
    Returns execution_id to poll for results.
    """
    log.info(f"Triggering execution of query {QUERY_ID}...")
    data = _post(f"/query/{QUERY_ID}/execute", body={
        "performance": "medium"   # medium = faster execution, reasonable credit cost
    })
    if not data:
        log.error("Failed to trigger query execution.")
        return None

    execution_id = data.get("execution_id")
    log.info(f"Execution started: {execution_id}")
    return execution_id


# ── Step 2: Poll for completion ────────────────────────────────
def wait_for_completion(execution_id: str) -> bool:
    """
    Polls execution status until complete or timeout.
    Returns True if successful, False if failed or timed out.
    """
    log.info(f"Waiting for execution {execution_id} to complete...")
    elapsed = 0

    while elapsed < MAX_WAIT:
        data = _get(f"/execution/{execution_id}/status")
        if not data:
            log.error("Failed to get execution status.")
            return False

        state = data.get("state", "")
        log.info(f"  State: {state} ({elapsed}s elapsed)")

        if state == "QUERY_STATE_COMPLETED":
            log.info("Query completed successfully.")
            return True
        elif state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            log.error(f"Query ended with state: {state}")
            return False

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    log.error(f"Query timed out after {MAX_WAIT}s")
    return False


# ── Step 3: Fetch results ──────────────────────────────────────
def fetch_results(execution_id: str) -> list[dict]:
    """
    Fetches the results of a completed execution.
    Returns list of rows from the StableGuard master query.
    """
    log.info(f"Fetching results for execution {execution_id}...")
    data = _get(f"/execution/{execution_id}/results", params={
        "limit":  1000,   # max rows per page
        "offset": 0
    })

    if not data:
        log.error("Failed to fetch results.")
        return []

    rows = data.get("result", {}).get("rows", [])
    log.info(f"Fetched {len(rows)} rows from Dune.")
    return rows


# ── Step 4: Fetch latest results (no re-execution) ────────────
def fetch_latest_results() -> list[dict]:
    """
    Fetches the most recent cached results without re-running the query.
    Much cheaper on Dune credits — use this for most fetches.
    Only call execute_query() when you need fresh on-chain data.
    """
    log.info(f"Fetching latest cached results for query {QUERY_ID}...")
    data = _get(f"/query/{QUERY_ID}/results", params={
        "limit":  1000,
        "offset": 0
    })

    if not data:
        log.error("Failed to fetch latest results.")
        return []

    rows = data.get("result", {}).get("rows", [])
    log.info(f"Fetched {len(rows)} cached rows from Dune.")
    return rows


# ── Step 5: Parse rows into signal objects ─────────────────────
def parse_signal_rows(rows: list[dict]) -> dict:
    """
    Converts raw Dune rows into a structured signal dict
    keyed by symbol for easy lookup by risk_scorer.py.

    Returns:
    {
        "USDC": { latest week's signal data },
        "USDT": { ... },
        ...
    }
    """
    if not rows:
        log.warning("No rows to parse.")
        return {}

    # Sort by week descending to get latest first
    sorted_rows = sorted(
        rows,
        key=lambda r: r.get("week", ""),
        reverse=True
    )

    signals = {}
    seen    = set()

    for row in sorted_rows:
        symbol = row.get("symbol")
        if not symbol or symbol in seen:
            continue  # keep only the most recent week per symbol
        seen.add(symbol)

        signals[symbol] = {
            # Metadata
            "symbol":               symbol,
            "week":                 row.get("week"),
            "fetched_at":           datetime.now(timezone.utc).isoformat(),

            # Alert output
            "alert_level":          row.get("alert_level", "🟢 HEALTHY"),
            "composite_score":      float(row.get("composite_score") or 0),
            "risk_trend":           row.get("risk_trend", "→ Stable"),
            "score_delta":          float(row.get("score_delta") or 0),
            "pillars_active":       int(row.get("pillars_active") or 0),
            "total_flags":          int(row.get("total_flags") or 0),

            # Pillar 1: Mint/Burn
            "mints_m":              float(row.get("mints_m") or 0),
            "burns_m":              float(row.get("burns_m") or 0),
            "burn_tx_count":        int(row.get("burn_tx_count") or 0),
            "net_change_m":         float(row.get("net_change_m") or 0),
            "total_supply_m":       float(row.get("total_supply_m") or 0),
            "baseline_burn_m":      float(row.get("baseline_burn_m") or 0),
            "burn_zscore":          float(row.get("burn_zscore") or 0),
            "burn_pct_baseline":    float(row.get("burn_pct_baseline") or 0),
            "flag_mint_defense":    int(row.get("flag_mint_defense") or 0),
            "flag_whale_cluster":   int(row.get("flag_whale_cluster") or 0),

            # Pillar 2: Liquidity
            "avg_peg_dev_bps":      float(row.get("avg_peg_dev_bps") or 0),
            "max_peg_dev_bps":      float(row.get("max_peg_dev_bps") or 0),
            "liq_zscore":           float(row.get("liq_zscore") or 0),
            "flag_peg_dev":         int(row.get("flag_peg_dev") or 0),
            "flag_peg_spike":       int(row.get("flag_peg_spike") or 0),

            # Pillar 3: Arb
            "arb_trades":           int(row.get("arb_trades") or 0),
            "whale_trades":         int(row.get("whale_trades") or 0),
            "baseline_arb_trades":  int(row.get("baseline_arb_trades") or 0),
            "arb_zscore":           float(row.get("arb_zscore") or 0),
            "flag_arb_decline":     int(row.get("flag_arb_decline") or 0),
            "flag_whale_dominance": int(row.get("flag_whale_dominance") or 0),
        }

    log.info(f"Parsed signals for: {list(signals.keys())}")
    return signals


# ── Master fetch function ──────────────────────────────────────
def fetch_dune_signals(force_refresh: bool = False) -> dict:
    """
    Called by scheduler.py every 5 minutes.

    force_refresh=False  → use cached results (saves credits, fast)
    force_refresh=True   → re-execute query (use once per hour max)

    Returns parsed signal dict keyed by symbol.
    """
    log.info("=== Dune fetch cycle starting ===")

    if force_refresh:
        # Full re-execution — costs Dune credits
        execution_id = execute_query()
        if not execution_id:
            return {}
        success = wait_for_completion(execution_id)
        if not success:
            return {}
        rows = fetch_results(execution_id)
    else:
        # Use cached results — free, instant
        rows = fetch_latest_results()

    signals = parse_signal_rows(rows)
    log.info(f"=== Dune fetch complete: {len(signals)} coins ===")
    return signals


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    print("\n--- Testing fetch_latest_results (cached, no credit cost) ---")
    signals = fetch_dune_signals(force_refresh=False)

    if not signals:
        print("No data returned. Check your DUNE_API_KEY in .env")
    else:
        print(f"\nSignals received for {len(signals)} coins:\n")
        for symbol, data in signals.items():
            print(f"  {symbol:8} | "
                  f"alert={data['alert_level']:15} | "
                  f"score={data['composite_score']:5.0f} | "
                  f"trend={data['risk_trend']:12} | "
                  f"pillars={data['pillars_active']} | "
                  f"burn_z={data['burn_zscore']:5.2f} | "
                  f"arb_z={data['arb_zscore']:5.2f}")

    print("\n--- Testing force_refresh (triggers new execution) ---")
    print("Skipped in test mode to save Dune credits.")
    print("Call fetch_dune_signals(force_refresh=True) to trigger fresh execution.")