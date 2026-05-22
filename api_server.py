"""
StableGuard — api_server.py
Layer 5: FastAPI server — Self-contained with internal scheduler
Runs with: uvicorn api_server:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

# ── Fix Python path BEFORE any local imports ──────────────────
# This is the critical fix for Render — must be at the very top
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, '.env'))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("api_server")

log.info(f"BASE_DIR: {BASE_DIR}")
log.info(f"sys.path: {sys.path[:3]}")
log.info(f"Files in dir: {os.listdir(BASE_DIR)}")

# ── App setup ──────────────────────────────────────────────────
app = FastAPI(
    title="StableGuard API",
    description="Real-time stablecoin depeg early warning system",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory state ────────────────────────────────────────────
_risk_cache:   dict = {}
_alerts_log:   list = []
_last_updated: str  = None

SUPPORTED_SYMBOLS = {"USDC", "USDT", "DAI", "FRAX", "PYUSD", "crvUSD", "LUSD"}


# ── Cache update ───────────────────────────────────────────────
def update_cache(risk_scores: dict):
    global _risk_cache, _alerts_log, _last_updated
    _risk_cache   = risk_scores
    _last_updated = datetime.now(timezone.utc).isoformat()

    for symbol, data in risk_scores.items():
        if data.get("alert_level") != "🟢 HEALTHY":
            _alerts_log.append({
                "timestamp":      _last_updated,
                "symbol":         symbol,
                "alert_level":    data.get("alert_level"),
                "composite_score":data.get("composite_score"),
                "active_flags":   data.get("active_flags", []),
                "guidance":       data.get("guidance"),
                "pillars_active": data.get("pillars_active"),
            })

    if len(_alerts_log) > 500:
        _alerts_log = _alerts_log[-500:]

    log.info(f"Cache updated: {len(risk_scores)} coins, {len(_alerts_log)} alerts")


# ── Refresh cycle (pure sync — no async) ──────────────────────
def refresh_cycle():
    """
    Full signal cycle — pure synchronous function.
    Runs in a daemon thread every 5 minutes.
    """
    log.info("=== Refresh cycle starting ===")
    try:
        # Import here so path is already set
        from dune_fetcher import fetch_dune_signals
        from risk_scorer  import score_all

        # Step 1: Dune
        dune_signals = fetch_dune_signals(force_refresh=False)
        if not dune_signals:
            log.warning("No Dune data. Retrying next cycle.")
            return
        log.info(f"Dune OK: {len(dune_signals)} coins")

        # Step 2: CoinGecko
        gecko_prices = []
        try:
            from stable import fetch_live_prices
            gecko_prices = fetch_live_prices()
            log.info(f"CoinGecko OK: {len(gecko_prices)} coins")
        except Exception as e:
            log.warning(f"CoinGecko failed: {e}")

        # Step 3: Score
        scores = score_all(dune_signals, gecko_prices)
        if not scores:
            log.warning("No scores produced.")
            return
        log.info(f"Scored: {len(scores)} coins")

        # Step 4: Update cache
        update_cache(scores)

        # Step 5: Save to DB
        try:
            from database import save_scores, save_alerts
            save_scores(scores)
            active = {s: d for s, d in scores.items()
                      if d.get("alert_level") != "🟢 HEALTHY"}
            if active:
                save_alerts(active)
        except Exception as e:
            log.warning(f"DB save failed: {e}")

        # Step 6: Alerts
        try:
            from alert_dispatcher import dispatch_all
            dispatch_all(scores)
        except Exception as e:
            log.warning(f"Alert dispatch failed: {e}")

        log.info("=== Refresh cycle complete ===")

    except Exception as e:
        log.error(f"Refresh cycle error: {e}", exc_info=True)


def background_scheduler():
    """Daemon thread — runs refresh_cycle every 5 minutes."""
    log.info("Background scheduler thread started.")
    while True:
        try:
            refresh_cycle()
        except Exception as e:
            log.error(f"Scheduler error: {e}")
        log.info("Next cycle in 5 minutes.")
        time.sleep(300)


# ── Pydantic models ────────────────────────────────────────────
class BatchRequest(BaseModel):
    symbols: list[str]


# ── Helpers ────────────────────────────────────────────────────
def _get_risk(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(status_code=400,
            detail=f"Symbol '{symbol}' not supported.")
    if symbol not in _risk_cache:
        raise HTTPException(status_code=503,
            detail=f"No data for {symbol} yet. Engine initializing — try in 2 minutes.")
    return _risk_cache[symbol]


def _rate_headers(response: JSONResponse) -> JSONResponse:
    response.headers["X-RateLimit-Remaining"] = "99"
    response.headers["X-RateLimit-Reset"]     = "3600"
    response.headers["X-StableGuard-Version"] = "1.0.0"
    return response


# ── Routes ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "name":          "StableGuard API",
        "version":       "1.0.0",
        "status":        "operational",
        "last_updated":  _last_updated,
        "coins_tracked": len(_risk_cache),
        "docs":          "/docs",
        "endpoints": [
            "/v1/risk/{symbol}",
            "/v1/risk/batch",
            "/v1/alerts",
            "/v1/signals/{symbol}",
            "/v1/summary",
            "/v1/health",
            "/v1/debug",
            "/v1/trigger",
        ]
    }


@app.get("/v1/health")
def health():
    return {
        "status":        "healthy",
        "last_updated":  _last_updated,
        "coins_cached":  len(_risk_cache),
        "alerts_logged": len(_alerts_log),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


@app.get("/v1/debug")
def debug():
    """Debug endpoint — shows exactly what Render can see."""
    import importlib
    modules = {}
    for mod in ["dune_fetcher", "stable", "risk_scorer",
                "database", "alert_dispatcher"]:
        try:
            importlib.import_module(mod)
            modules[mod] = "OK"
        except Exception as e:
            modules[mod] = f"ERROR: {e}"
    return {
        "base_dir":      BASE_DIR,
        "cwd":           os.getcwd(),
        "sys_path_0":    sys.path[0],
        "files_in_dir":  sorted(os.listdir(BASE_DIR)),
        "modules":       modules,
        "coins_cached":  len(_risk_cache),
        "last_updated":  _last_updated,
    }


@app.get("/v1/trigger")
def trigger():
    """Manually trigger one refresh cycle — for testing."""
    t = threading.Thread(target=refresh_cycle, daemon=True, name="manual-trigger")
    t.start()
    return {"status": "refresh triggered", "thread": t.name}


@app.get("/v1/risk/{symbol}")
def get_risk(symbol: str):
    data = _get_risk(symbol)
    return _rate_headers(JSONResponse(content=data))


@app.post("/v1/risk/batch")
def get_risk_batch(request: BatchRequest):
    if len(request.symbols) > 20:
        raise HTTPException(status_code=400, detail="Max 20 symbols.")
    results, errors = {}, {}
    for s in request.symbols:
        try:
            results[s.upper()] = _get_risk(s)
        except HTTPException as e:
            errors[s.upper()] = e.detail
    return _rate_headers(JSONResponse(content={
        "results": results, "errors": errors,
        "count": len(results), "last_updated": _last_updated,
    }))


@app.get("/v1/alerts")
def get_alerts(
    symbol: Optional[str] = Query(None),
    level:  Optional[str] = Query(None),
    limit:  int           = Query(50),
    offset: int           = Query(0),
):
    limit    = min(limit, 200)
    filtered = _alerts_log.copy()
    if symbol:
        filtered = [a for a in filtered if a["symbol"] == symbol.upper()]
    if level:
        level_map = {"WATCH":"🟡 WATCH","REDUCE":"🟠 REDUCE","EXIT":"🔴 EXIT"}
        filtered  = [a for a in filtered
                     if a["alert_level"] == level_map.get(level.upper(), level)]
    filtered  = sorted(filtered, key=lambda x: x["timestamp"], reverse=True)
    paginated = filtered[offset: offset + limit]
    return _rate_headers(JSONResponse(content={
        "alerts":   paginated, "total": len(filtered),
        "limit":    limit,     "offset": offset,
        "has_more": (offset + limit) < len(filtered),
    }))


@app.get("/v1/signals/{symbol}")
def get_signals(symbol: str):
    data = _get_risk(symbol)
    return _rate_headers(JSONResponse(content={
        "symbol":       symbol.upper(),
        "scored_at":    data.get("scored_at"),
        "alert_level":  data.get("alert_level"),
        "signals":      data.get("signals", {}),
        "raw":          data.get("raw", {}),
        "active_flags": data.get("active_flags", []),
        "guidance":     data.get("guidance"),
    }))


@app.get("/v1/summary")
def get_summary():
    if not _risk_cache:
        raise HTTPException(status_code=503,
            detail="Signal engine initializing. Try /v1/trigger then wait 60 seconds.")
    coins = []
    for symbol, data in sorted(_risk_cache.items(),
                                key=lambda x: -x[1].get("composite_score", 0)):
        coins.append({
            "symbol":          symbol,
            "alert_level":     data.get("alert_level"),
            "composite_score": data.get("composite_score"),
            "risk_trend":      data.get("raw", {}).get("risk_trend"),
            "pillars_active":  data.get("pillars_active"),
            "total_flags":     data.get("total_flags"),
            "price_usd":       data.get("price", {}).get("usd"),
            "peg_dev_bps":     data.get("price", {}).get("peg_dev_bps"),
            "guidance":        data.get("guidance"),
        })
    exit_c   = sum(1 for c in coins if "EXIT"   in (c["alert_level"] or ""))
    reduce_c = sum(1 for c in coins if "REDUCE" in (c["alert_level"] or ""))
    watch_c  = sum(1 for c in coins if "WATCH"  in (c["alert_level"] or ""))
    status   = ("🔴 CRITICAL" if exit_c   > 0 else
                "🟠 ELEVATED" if reduce_c > 0 else
                "🟡 CAUTIOUS" if watch_c  > 0 else "🟢 NORMAL")
    return _rate_headers(JSONResponse(content={
        "system_status": status,
        "last_updated":  _last_updated,
        "coins":         coins,
        "stats": {
            "total_monitored": len(coins),
            "exit_alerts":     exit_c,
            "reduce_alerts":   reduce_c,
            "watch_alerts":    watch_c,
            "max_risk_score":  max((c["composite_score"] or 0) for c in coins),
        }
    }))


@app.get("/v1/watchlist")
def get_watchlist(symbols: str = Query(...)):
    results, errors = {}, {}
    for s in [x.strip().upper() for x in symbols.split(",")]:
        try:
            results[s] = _get_risk(s)
        except HTTPException as e:
            errors[s] = e.detail
    return JSONResponse(content={
        "watchlist": results, "errors": errors, "last_updated": _last_updated
    })


# ── Startup ────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    """Launch background scheduler thread on startup."""
    log.info("StableGuard API starting up...")
    log.info(f"Working directory: {os.getcwd()}")
    log.info(f"Files available: {os.listdir(BASE_DIR)}")

    t = threading.Thread(
        target=background_scheduler,
        daemon=True,
        name="stableguard-scheduler"
    )
    t.start()
    log.info(f"Scheduler thread started: {t.name}")


# ── Run directly ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000,
                reload=False, log_level="info")