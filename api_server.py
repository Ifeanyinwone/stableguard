"""
StableGuard — api_server.py
Production-stable FastAPI backend
"""

import os
import sys
import time
import logging
import threading

from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

# ── Path setup ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.chdir(BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── FastAPI ──────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

log = logging.getLogger("api_server")

# ── App ──────────────────────────────────────────────────────
app = FastAPI(
    title="StableGuard API",
    version="2.0.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ─────────────────────────────────────────────
_risk_cache = {}
_alerts_log = []

_last_updated = None
_last_cycle_ok = None
_last_cycle_error = None

_scheduler_running = False

SUPPORTED_SYMBOLS = {
    "USDT",
    "USDC",
    "DAI",
    "FRAX",
    "PYUSD",
    "crvUSD",
    "LUSD",
}

FETCH_INTERVAL_SECONDS = 300

# ── Models ───────────────────────────────────────────────────
class BatchRequest(BaseModel):
    symbols: list[str]


# ── Cache updater ────────────────────────────────────────────
def update_cache(risk_scores: dict):

    global _risk_cache
    global _last_updated

    # NEVER replace full cache
    _risk_cache.update(risk_scores)

    _last_updated = datetime.now(
        timezone.utc
    ).isoformat()

    # Alerts
    for symbol, data in risk_scores.items():

        if data.get("alert_level") != "🟢 HEALTHY":

            _alerts_log.append({
                "timestamp": _last_updated,
                "symbol": symbol,
                "alert_level": data.get("alert_level"),
                "composite_score": data.get("composite_score"),
                "guidance": data.get("guidance"),
            })

    # Keep last 500
    if len(_alerts_log) > 500:
        del _alerts_log[:-500]

    log.info(
        f"Cache updated | "
        f"coins={len(_risk_cache)} | "
        f"alerts={len(_alerts_log)}"
    )


# ── Refresh cycle ────────────────────────────────────────────
def refresh_cycle():

    global _last_cycle_ok
    global _last_cycle_error

    try:

        log.info("=== Refresh cycle starting ===")

        from defillama_fetcher import fetch_all_signals
        from risk_scorer import score_all

        # Fetch signals
        signals = fetch_all_signals()

        if not signals:
            raise Exception("No signals returned")

        log.info(f"Signals fetched: {len(signals)}")

        # Score
        scores = score_all(signals, [])

        if not scores:
            raise Exception("No scores produced")

        log.info(f"Scores produced: {len(scores)}")

        # Update cache
        update_cache(scores)

        # Save DB
        try:

            from database import (
                save_scores,
                save_alerts
            )

            save_scores(scores)

            active_alerts = {
                s: d
                for s, d in scores.items()
                if d.get("alert_level") != "🟢 HEALTHY"
            }

            if active_alerts:
                save_alerts(active_alerts)

        except Exception as db_error:

            log.warning(
                f"Database warning: {db_error}"
            )

        # Dispatch alerts
        try:

            from alert_dispatcher import dispatch_all

            dispatch_all(scores)

        except Exception as alert_error:

            log.warning(
                f"Alert dispatch warning: {alert_error}"
            )

        _last_cycle_ok = datetime.now(
            timezone.utc
        ).isoformat()

        _last_cycle_error = None

        log.info("=== Refresh cycle complete ===")

    except Exception as e:

        _last_cycle_error = str(e)

        log.error(
            f"Refresh cycle failed: {e}",
            exc_info=True
        )


# ── Background scheduler ─────────────────────────────────────
def scheduler_loop():

    global _scheduler_running

    if _scheduler_running:
        log.warning("Scheduler already running")
        return

    _scheduler_running = True

    log.info("Background scheduler started")

    while True:

        cycle_start = time.time()

        refresh_cycle()

        elapsed = time.time() - cycle_start

        sleep_time = max(
            30,
            FETCH_INTERVAL_SECONDS - elapsed
        )

        log.info(
            f"Next refresh in "
            f"{sleep_time:.0f}s"
        )

        time.sleep(sleep_time)


# ── Helpers ──────────────────────────────────────────────────
def get_symbol_data(symbol: str):

    symbol = symbol.upper()

    if symbol not in SUPPORTED_SYMBOLS:

        raise HTTPException(
            status_code=400,
            detail=f"{symbol} not supported"
        )

    if symbol not in _risk_cache:

        raise HTTPException(
            status_code=503,
            detail=f"No data yet for {symbol}"
        )

    return _risk_cache[symbol]


def api_response(data):

    response = JSONResponse(content=data)

    response.headers["X-StableGuard-Version"] = "2.0.0"

    return response


# ── Routes ───────────────────────────────────────────────────
@app.get("/")
def root():

    return {
        "name": "StableGuard API",
        "status": "operational",
        "coins_tracked": len(_risk_cache),
        "last_updated": _last_updated,
        "scheduler_running": _scheduler_running,
        "version": "2.0.0",
    }


@app.get("/v1/health")
def health():

    return {
        "status": "healthy",
        "scheduler_running": _scheduler_running,
        "coins_cached": len(_risk_cache),
        "last_updated": _last_updated,
        "last_cycle_ok": _last_cycle_ok,
        "last_cycle_error": _last_cycle_error,
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }


@app.get("/v1/debug")
def debug():

    return {
        "base_dir": BASE_DIR,
        "cwd": os.getcwd(),
        "coins_cached": len(_risk_cache),
        "symbols": list(_risk_cache.keys()),
        "scheduler_running": _scheduler_running,
        "last_updated": _last_updated,
        "last_cycle_ok": _last_cycle_ok,
        "last_cycle_error": _last_cycle_error,
    }


@app.get("/v1/trigger")
def trigger():

    thread = threading.Thread(
        target=refresh_cycle,
        daemon=True
    )

    thread.start()

    return {
        "status": "triggered"
    }


@app.get("/v1/risk/{symbol}")
def get_risk(symbol: str):

    return api_response(
        get_symbol_data(symbol)
    )


@app.get("/v1/signals/{symbol}")
def get_signals(symbol: str):

    data = get_symbol_data(symbol)

    return api_response({

        "symbol": symbol.upper(),

        "scored_at": data.get(
            "scored_at"
        ),

        "alert_level": data.get(
            "alert_level"
        ),

        "signals": data.get(
            "signals",
            {}
        ),

        "raw": data.get(
            "raw",
            {}
        ),

        "active_flags": data.get(
            "active_flags",
            []
        ),

        "guidance": data.get(
            "guidance"
        ),
    })


@app.post("/v1/risk/batch")
def batch_risk(request: BatchRequest):

    results = {}
    errors = {}

    for symbol in request.symbols:

        try:

            results[symbol.upper()] = (
                get_symbol_data(symbol)
            )

        except HTTPException as e:

            errors[symbol.upper()] = e.detail

    return api_response({
        "results": results,
        "errors": errors,
        "count": len(results),
        "last_updated": _last_updated,
    })


@app.get("/v1/summary")
def summary():

    if not _risk_cache:

        raise HTTPException(
            status_code=503,
            detail="Engine initializing"
        )

    coins = []

    for symbol, data in sorted(
        _risk_cache.items(),
        key=lambda x: -x[1].get(
            "composite_score",
            0
        )
    ):

        price_data = data.get("price", {})

        coins.append({

            "symbol": symbol,

            "alert_level": data.get(
                "alert_level"
            ),

            "composite_score": data.get(
                "composite_score"
            ),

            "risk_trend": data.get(
                "raw",
                {}
            ).get(
                "risk_trend"
            ),

            "pillars_active": data.get(
                "pillars_active"
            ),

            "total_flags": data.get(
                "total_flags"
            ),

            "price_usd": (
                price_data.get("usd")
                if isinstance(price_data, dict)
                else data.get("price_usd")
            ),

            "peg_dev_bps": (
                price_data.get("peg_dev_bps")
                if isinstance(price_data, dict)
                else data.get("peg_dev_bps")
            ),

            "guidance": data.get(
                "guidance"
            ),
        })

    exit_alerts = len([
        c for c in coins
        if "EXIT" in str(c.get("alert_level"))
    ])

    reduce_alerts = len([
        c for c in coins
        if "REDUCE" in str(c.get("alert_level"))
    ])

    watch_alerts = len([
        c for c in coins
        if "WATCH" in str(c.get("alert_level"))
    ])

    return api_response({

        "system_status": "operational",

        "coins": coins,

        "count": len(coins),

        "last_updated": _last_updated,

        "stats": {

            "total_monitored": len(coins),

            "exit_alerts": exit_alerts,

            "reduce_alerts": reduce_alerts,

            "watch_alerts": watch_alerts
        }
    })


@app.get("/v1/alerts")
def alerts(
    limit: int = Query(50)
):

    limit = min(limit, 200)

    return api_response({
        "alerts": _alerts_log[-limit:],
        "count": len(_alerts_log),
    })


@app.get("/v1/watchlist")
def watchlist(
    symbols: str = Query(...)
):

    results = {}

    for symbol in symbols.split(","):

        symbol = symbol.strip().upper()

        if symbol in _risk_cache:
            results[symbol] = _risk_cache[symbol]

    return api_response({
        "watchlist": results,
        "count": len(results),
    })


# ── Startup ──────────────────────────────────────────────────
@app.on_event("startup")
async def startup():

    log.info("StableGuard starting")

    thread = threading.Thread(
        target=scheduler_loop,
        daemon=True,
        name="stableguard-scheduler"
    )

    thread.start()

    log.info(
        f"Scheduler thread started: "
        f"{thread.name}"
    )


# ── Direct run ───────────────────────────────────────────────
if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )