"""
StableGuard — api_server.py
Layer 5: FastAPI server
Serves: /v1/risk/{symbol}, /v1/risk/batch, /v1/alerts, /v1/signals/{symbol}
Runs with: uvicorn api_server:app --reload --port 8000
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("api_server")

# ── App setup ──────────────────────────────────────────────────
app = FastAPI(
    title="StableGuard API",
    description="Real-time stablecoin depeg early warning system",
    version="1.0.0",
    docs_url="/docs",       # Interactive API docs at /docs
    redoc_url="/redoc",
)

# CORS — allow dashboard frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # Restrict to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory state ────────────────────────────────────────────
# Populated by scheduler.py every 5 minutes
# Structure: { "USDC": { risk_object }, "USDT": { ... }, ... }
_risk_cache: dict = {}
_alerts_log: list = []      # Last 500 alerts
_last_updated: str = None

# Supported stablecoins
SUPPORTED_SYMBOLS = {"USDC", "USDT", "DAI", "FRAX", "PYUSD", "crvUSD", "LUSD"}


# ── Cache update (called by scheduler.py) ─────────────────────
def update_cache(risk_scores: dict):
    """
    Called by scheduler.py every 5 minutes with fresh scores.
    Updates in-memory cache and appends new alerts to log.
    """
    global _risk_cache, _alerts_log, _last_updated

    _risk_cache   = risk_scores
    _last_updated = datetime.now(timezone.utc).isoformat()

    # Log any alerts that are not HEALTHY
    for symbol, data in risk_scores.items():
        if data.get("alert_level") != "🟢 HEALTHY":
            alert_entry = {
                "timestamp":      _last_updated,
                "symbol":         symbol,
                "alert_level":    data.get("alert_level"),
                "composite_score":data.get("composite_score"),
                "active_flags":   data.get("active_flags", []),
                "guidance":       data.get("guidance"),
                "pillars_active": data.get("pillars_active"),
            }
            _alerts_log.append(alert_entry)

    # Keep only last 500 alerts
    if len(_alerts_log) > 500:
        _alerts_log = _alerts_log[-500:]

    log.info(f"Cache updated: {len(risk_scores)} coins, {len(_alerts_log)} alerts logged")


# ── Pydantic models ────────────────────────────────────────────
class BatchRequest(BaseModel):
    symbols: list[str]


# ── Helper ─────────────────────────────────────────────────────
def _get_risk(symbol: str) -> dict:
    """Get risk object for a symbol, raise 404 if not found."""
    symbol = symbol.upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Symbol '{symbol}' not supported. Supported: {sorted(SUPPORTED_SYMBOLS)}"
        )
    if symbol not in _risk_cache:
        raise HTTPException(
            status_code=404,
            detail=f"No data available for {symbol} yet. Try again in a few minutes."
        )
    return _risk_cache[symbol]


def _add_rate_limit_headers(response: JSONResponse) -> JSONResponse:
    """Add rate limit headers per spec."""
    response.headers["X-RateLimit-Remaining"] = "99"
    response.headers["X-RateLimit-Reset"]     = "3600"
    response.headers["X-StableGuard-Version"] = "1.0.0"
    return response


# ── Routes ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name":        "StableGuard API",
        "version":     "1.0.0",
        "status":      "operational",
        "last_updated": _last_updated,
        "coins_tracked": len(_risk_cache),
        "docs":        "/docs",
        "endpoints": [
            "/v1/risk/{symbol}",
            "/v1/risk/batch",
            "/v1/alerts",
            "/v1/signals/{symbol}",
            "/v1/summary",
            "/v1/health",
        ]
    }


@app.get("/v1/health")
def health():
    """Health check endpoint."""
    return {
        "status":       "healthy",
        "last_updated": _last_updated,
        "coins_cached": len(_risk_cache),
        "alerts_logged":len(_alerts_log),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/v1/risk/{symbol}")
def get_risk(symbol: str):
    """
    Returns current risk score, alert level, and signal breakdown
    for a single stablecoin.

    Example: GET /v1/risk/USDC
    """
    data = _get_risk(symbol)
    response = JSONResponse(content=data)
    return _add_rate_limit_headers(response)


@app.post("/v1/risk/batch")
def get_risk_batch(request: BatchRequest):
    """
    Returns risk scores for up to 20 stablecoins in one request.

    Example: POST /v1/risk/batch
    Body: { "symbols": ["USDC", "USDT", "DAI"] }
    """
    if len(request.symbols) > 20:
        raise HTTPException(
            status_code=400,
            detail="Maximum 20 symbols per batch request."
        )

    results = {}
    errors  = {}

    for symbol in request.symbols:
        try:
            results[symbol.upper()] = _get_risk(symbol)
        except HTTPException as e:
            errors[symbol.upper()] = e.detail

    response = JSONResponse(content={
        "results":      results,
        "errors":       errors,
        "count":        len(results),
        "last_updated": _last_updated,
    })
    return _add_rate_limit_headers(response)


@app.get("/v1/alerts")
def get_alerts(
    symbol:   Optional[str] = Query(None, description="Filter by stablecoin symbol"),
    level:    Optional[str] = Query(None, description="Filter by alert level: WATCH, REDUCE, EXIT"),
    limit:    int           = Query(50,   description="Number of alerts to return (max 200)"),
    offset:   int           = Query(0,    description="Pagination offset"),
):
    """
    Returns recent alert log filtered by symbol and/or severity.

    Example: GET /v1/alerts?symbol=USDT&level=REDUCE&limit=10
    """
    limit = min(limit, 200)

    filtered = _alerts_log.copy()

    # Filter by symbol
    if symbol:
        filtered = [a for a in filtered if a["symbol"] == symbol.upper()]

    # Filter by level
    if level:
        level_map = {
            "WATCH":   "🟡 WATCH",
            "REDUCE":  "🟠 REDUCE",
            "EXIT":    "🔴 EXIT",
        }
        level_emoji = level_map.get(level.upper(), level)
        filtered = [a for a in filtered if a["alert_level"] == level_emoji]

    # Sort newest first
    filtered = sorted(filtered, key=lambda x: x["timestamp"], reverse=True)

    # Paginate
    paginated = filtered[offset: offset + limit]

    response = JSONResponse(content={
        "alerts":       paginated,
        "total":        len(filtered),
        "limit":        limit,
        "offset":       offset,
        "has_more":     (offset + limit) < len(filtered),
    })
    return _add_rate_limit_headers(response)


@app.get("/v1/signals/{symbol}")
def get_signals(symbol: str):
    """
    Returns raw signal readings for all three pillars —
    current values, flags, and scores.

    Example: GET /v1/signals/USDC
    """
    data    = _get_risk(symbol)
    signals = data.get("signals", {})
    raw     = data.get("raw", {})

    response = JSONResponse(content={
        "symbol":       symbol.upper(),
        "scored_at":    data.get("scored_at"),
        "alert_level":  data.get("alert_level"),
        "signals":      signals,
        "raw":          raw,
        "active_flags": data.get("active_flags", []),
        "guidance":     data.get("guidance"),
    })
    return _add_rate_limit_headers(response)


@app.get("/v1/summary")
def get_summary():
    """
    Returns a summary of all monitored stablecoins —
    alert levels, scores, and system-wide risk status.
    Perfect for the trader dashboard overview screen.

    Example: GET /v1/summary
    """
    if not _risk_cache:
        raise HTTPException(
            status_code=503,
            detail="No data available yet. Signal engine is initializing."
        )

    coins = []
    for symbol, data in sorted(
        _risk_cache.items(),
        key=lambda x: -x[1].get("composite_score", 0)
    ):
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

    # System-wide risk level
    max_score = max((c["composite_score"] or 0) for c in coins)
    exit_count   = sum(1 for c in coins if "EXIT"   in (c["alert_level"] or ""))
    reduce_count = sum(1 for c in coins if "REDUCE" in (c["alert_level"] or ""))
    watch_count  = sum(1 for c in coins if "WATCH"  in (c["alert_level"] or ""))

    system_status = (
        "🔴 CRITICAL" if exit_count > 0   else
        "🟠 ELEVATED" if reduce_count > 0 else
        "🟡 CAUTIOUS" if watch_count > 0  else
        "🟢 NORMAL"
    )

    response = JSONResponse(content={
        "system_status":  system_status,
        "last_updated":   _last_updated,
        "coins":          coins,
        "stats": {
            "total_monitored": len(coins),
            "exit_alerts":     exit_count,
            "reduce_alerts":   reduce_count,
            "watch_alerts":    watch_count,
            "max_risk_score":  max_score,
        }
    })
    return _add_rate_limit_headers(response)


@app.get("/v1/watchlist")
def get_watchlist(
    symbols: str = Query(..., description="Comma-separated symbols: USDC,USDT,DAI")
):
    """
    Returns risk scores for a custom watchlist.

    Example: GET /v1/watchlist?symbols=USDC,USDT,DAI
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    results     = {}
    errors      = {}

    for symbol in symbol_list:
        try:
            results[symbol] = _get_risk(symbol)
        except HTTPException as e:
            errors[symbol] = e.detail

    return JSONResponse(content={
        "watchlist":    results,
        "errors":       errors,
        "last_updated": _last_updated,
    })


# ── Background refresh cycle ───────────────────────────────────
async def refresh_cycle():
    """
    Runs the full signal cycle: fetch Dune + CoinGecko -> score -> cache.
    Called on startup and every 5 minutes via background task.
    This makes the API self-contained on Render with one service.
    """
    log.info("=== Background refresh cycle starting ===")
    try:
        from dune_fetcher import fetch_dune_signals
        from risk_scorer  import score_all

        dune_signals = fetch_dune_signals(force_refresh=False)
        if not dune_signals:
            log.warning("No Dune data returned. Retrying next cycle.")
            return

        gecko_prices = []
        try:
            from stable import fetch_live_prices
            gecko_prices = fetch_live_prices()
            log.info(f"CoinGecko: {len(gecko_prices)} coins fetched")
        except Exception as e:
            log.warning(f"CoinGecko fetch failed: {e}")

        scores = score_all(dune_signals, gecko_prices)
        if scores:
            update_cache(scores)
            log.info(f"Cache updated: {len(scores)} coins scored")

        try:
            from database import save_scores, save_alerts
            save_scores(scores)
            active = {s: d for s, d in scores.items() if d.get("alert_level") != "🟢 HEALTHY"}
            if active:
                save_alerts(active)
        except Exception as e:
            log.warning(f"Database save failed: {e}")

        try:
            from alert_dispatcher import dispatch_all
            dispatch_all(scores)
        except Exception as e:
            log.warning(f"Alert dispatch failed: {e}")

        log.info("=== Background refresh cycle complete ===")

    except Exception as e:
        log.error(f"Refresh cycle error: {e}")


async def background_scheduler():
    """Runs refresh_cycle every 5 minutes indefinitely."""
    import asyncio
    while True:
        await refresh_cycle()
        await asyncio.sleep(300)


# ── Startup event ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    """
    On startup: launch background scheduler.
    Makes API fully self-contained on Render with ONE service only.
    """
    import asyncio
    log.info("StableGuard API starting up...")
    asyncio.create_task(background_scheduler())
    log.info("Background scheduler launched — first cycle starting...")


# ── Run directly ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,       # Auto-reload on code changes
        log_level="info"
    )