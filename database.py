"""
StableGuard — database.py
Layer 4: SQLite storage
Tables:
  - signal_history : every score reading timestamped
  - alerts         : every alert fired with full context
"""

import os
import sqlite3
import logging
import json
from datetime import datetime, timezone

log = logging.getLogger("database")

DB_PATH = os.path.join(os.path.dirname(__file__), "stableguard.db")


# ── Setup ──────────────────────────────────────────────────────
def init_db():
    """Creates tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    # Signal history — every reading
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            alert_level     TEXT,
            composite_score INTEGER,
            liq_score       INTEGER,
            mb_score        INTEGER,
            arb_score       INTEGER,
            burn_zscore     REAL,
            arb_zscore      REAL,
            peg_dev_bps     REAL,
            pillars_active  INTEGER,
            total_flags     INTEGER,
            active_flags    TEXT,
            risk_trend      TEXT,
            total_supply_m  REAL,
            net_change_m    REAL
        )
    """)

    # Alerts log — only non-HEALTHY events
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            alert_level     TEXT,
            composite_score INTEGER,
            pillars_active  INTEGER,
            active_flags    TEXT,
            guidance        TEXT,
            signals_json    TEXT
        )
    """)

    conn.commit()
    conn.close()
    log.info(f"Database initialized at {DB_PATH}")


# ── Save scores ────────────────────────────────────────────────
def save_scores(scores: dict):
    """Saves current risk scores to signal_history table."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    timestamp = datetime.now(timezone.utc).isoformat()
    rows      = []

    for symbol, data in scores.items():
        signals = data.get("signals", {})
        rows.append((
            timestamp,
            symbol,
            data.get("alert_level"),
            data.get("composite_score"),
            signals.get("liquidity", {}).get("score"),
            signals.get("mintBurn",  {}).get("score"),
            signals.get("arb",       {}).get("score"),
            signals.get("mintBurn",  {}).get("burn_zscore"),
            signals.get("arb",       {}).get("arb_zscore"),
            signals.get("liquidity", {}).get("peg_dev_bps"),
            data.get("pillars_active"),
            data.get("total_flags"),
            json.dumps(data.get("active_flags", [])),
            data.get("raw", {}).get("risk_trend"),
            data.get("raw", {}).get("total_supply_m"),
            data.get("raw", {}).get("net_change_m"),
        ))

    c.executemany("""
        INSERT INTO signal_history (
            timestamp, symbol, alert_level, composite_score,
            liq_score, mb_score, arb_score, burn_zscore,
            arb_zscore, peg_dev_bps, pillars_active, total_flags,
            active_flags, risk_trend, total_supply_m, net_change_m
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)

    conn.commit()
    conn.close()
    log.info(f"Saved {len(rows)} signal readings to database")


# ── Save alerts ────────────────────────────────────────────────
def save_alerts(alerts: dict):
    """Saves active alerts to alerts table."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    timestamp = datetime.now(timezone.utc).isoformat()
    rows      = []

    for symbol, data in alerts.items():
        rows.append((
            timestamp,
            symbol,
            data.get("alert_level"),
            data.get("composite_score"),
            data.get("pillars_active"),
            json.dumps(data.get("active_flags", [])),
            data.get("guidance"),
            json.dumps(data.get("signals", {})),
        ))

    c.executemany("""
        INSERT INTO alerts (
            timestamp, symbol, alert_level, composite_score,
            pillars_active, active_flags, guidance, signals_json
        ) VALUES (?,?,?,?,?,?,?,?)
    """, rows)

    conn.commit()
    conn.close()
    log.info(f"Saved {len(rows)} alerts to database")


# ── Query helpers ──────────────────────────────────────────────
def get_latest_scores() -> list:
    """Returns the most recent score for each symbol."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT * FROM signal_history
        WHERE timestamp = (SELECT MAX(timestamp) FROM signal_history)
        ORDER BY composite_score DESC
    """)

    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_alert_history(symbol: str = None, limit: int = 100) -> list:
    """Returns recent alerts, optionally filtered by symbol."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if symbol:
        c.execute("""
            SELECT * FROM alerts WHERE symbol = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (symbol.upper(), limit))
    else:
        c.execute("""
            SELECT * FROM alerts
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))

    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    print("\n=== StableGuard Database — Test ===\n")
    init_db()
    print(f"Database created at: {DB_PATH}")

    # Test with dummy data
    test_score = {
        "USDT": {
            "alert_level": "🟠 REDUCE",
            "composite_score": 65,
            "pillars_active": 2,
            "total_flags": 5,
            "active_flags": ["ARB_STOPPED", "PEG_EXTREME"],
            "guidance": "Reduce exposure by 30-50%.",
            "risk_trend": "↑ Rising",
            "signals": {
                "liquidity": {"score": 100, "peg_dev_bps": 25},
                "mintBurn":  {"score": 0,   "burn_zscore": -0.3},
                "arb":       {"score": 85,  "arb_zscore": -2.36},
            },
            "raw": {
                "risk_trend": "↑ Rising",
                "total_supply_m": 143000,
                "net_change_m": -500,
            }
        }
    }

    save_scores(test_score)
    save_alerts(test_score)

    print("\nLatest scores from DB:")
    for row in get_latest_scores():
        print(f"  {row['symbol']} | {row['alert_level']} | score={row['composite_score']}")

    print("\nAlert history:")
    for row in get_alert_history():
        print(f"  {row['timestamp'][:19]} | {row['symbol']} | {row['alert_level']}")

    print("\n✅ Database test complete")