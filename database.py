"""
StableGuard — database.py
Production-optimized SQLite layer
"""

import os
import sqlite3
import logging
import json

from datetime import datetime, timezone

log = logging.getLogger("database")

# ── Database path ────────────────────────────────────────────
DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "stableguard.db"
)

# ── Init guard ───────────────────────────────────────────────
_db_initialized = False


# ── Connection helper ────────────────────────────────────────
def get_connection():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    # WAL mode = better concurrency
    conn.execute("PRAGMA journal_mode=WAL;")

    # Better durability/performance
    conn.execute("PRAGMA synchronous=NORMAL;")

    return conn


# ── Setup ────────────────────────────────────────────────────
def init_db():

    global _db_initialized

    if _db_initialized:
        return

    conn = get_connection()
    c = conn.cursor()

    # Signal history
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,

            alert_level TEXT,
            composite_score INTEGER,

            liq_score INTEGER,
            mb_score INTEGER,
            arb_score INTEGER,

            burn_zscore REAL,
            arb_zscore REAL,
            peg_dev_bps REAL,

            pillars_active INTEGER,
            total_flags INTEGER,

            active_flags TEXT,

            risk_trend TEXT,

            total_supply_m REAL,
            net_change_m REAL
        )
    """)

    # Alerts
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,

            alert_level TEXT,
            composite_score INTEGER,

            pillars_active INTEGER,

            active_flags TEXT,

            guidance TEXT,

            signals_json TEXT
        )
    """)

    # Useful indexes
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_symbol
        ON signal_history(symbol)
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_timestamp
        ON signal_history(timestamp)
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_symbol
        ON alerts(symbol)
    """)

    conn.commit()
    conn.close()

    _db_initialized = True

    log.info(
        f"Database initialized at {DB_PATH}"
    )


# ── Save scores ──────────────────────────────────────────────
def save_scores(scores: dict):

    if not scores:
        return

    init_db()

    conn = get_connection()
    c = conn.cursor()

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    rows = []

    for symbol, data in scores.items():

        signals = data.get(
            "signals",
            {}
        )

        rows.append((

            timestamp,
            symbol,

            data.get("alert_level"),
            data.get("composite_score"),

            signals.get(
                "liquidity",
                {}
            ).get("score"),

            signals.get(
                "mintBurn",
                {}
            ).get("score"),

            signals.get(
                "arb",
                {}
            ).get("score"),

            signals.get(
                "mintBurn",
                {}
            ).get("burn_zscore"),

            signals.get(
                "arb",
                {}
            ).get("arb_zscore"),

            signals.get(
                "liquidity",
                {}
            ).get("peg_dev_bps"),

            data.get("pillars_active"),
            data.get("total_flags"),

            json.dumps(
                data.get(
                    "active_flags",
                    []
                )
            ),

            data.get(
                "raw",
                {}
            ).get("risk_trend"),

            data.get(
                "raw",
                {}
            ).get("total_supply_m"),

            data.get(
                "raw",
                {}
            ).get("net_change_m"),
        ))

    c.executemany("""
        INSERT INTO signal_history (

            timestamp,
            symbol,

            alert_level,
            composite_score,

            liq_score,
            mb_score,
            arb_score,

            burn_zscore,
            arb_zscore,
            peg_dev_bps,

            pillars_active,
            total_flags,

            active_flags,

            risk_trend,

            total_supply_m,
            net_change_m

        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, rows)

    conn.commit()
    conn.close()

    log.info(
        f"Saved {len(rows)} signal readings "
        f"to database"
    )


# ── Save alerts ──────────────────────────────────────────────
def save_alerts(alerts: dict):

    if not alerts:
        return

    init_db()

    conn = get_connection()
    c = conn.cursor()

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    rows = []

    for symbol, data in alerts.items():

        rows.append((

            timestamp,
            symbol,

            data.get("alert_level"),
            data.get("composite_score"),

            data.get("pillars_active"),

            json.dumps(
                data.get(
                    "active_flags",
                    []
                )
            ),

            data.get("guidance"),

            json.dumps(
                data.get(
                    "signals",
                    {}
                )
            ),
        ))

    c.executemany("""
        INSERT INTO alerts (

            timestamp,
            symbol,

            alert_level,
            composite_score,

            pillars_active,

            active_flags,

            guidance,

            signals_json

        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, rows)

    conn.commit()
    conn.close()

    log.info(
        f"Saved {len(rows)} alerts "
        f"to database"
    )


# ── Latest scores ────────────────────────────────────────────
def get_latest_scores():

    init_db()

    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT *
        FROM signal_history
        WHERE timestamp = (
            SELECT MAX(timestamp)
            FROM signal_history
        )
        ORDER BY composite_score DESC
    """)

    rows = [
        dict(r)
        for r in c.fetchall()
    ]

    conn.close()

    return rows


# ── Alert history ────────────────────────────────────────────
def get_alert_history(
    symbol: str = None,
    limit: int = 100
):

    init_db()

    conn = get_connection()
    c = conn.cursor()

    limit = min(limit, 500)

    if symbol:

        c.execute("""
            SELECT *
            FROM alerts
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (
            symbol.upper(),
            limit
        ))

    else:

        c.execute("""
            SELECT *
            FROM alerts
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

    rows = [
        dict(r)
        for r in c.fetchall()
    ]

    conn.close()

    return rows


# ── Local test ───────────────────────────────────────────────
if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    print("\n=== StableGuard DB Test ===\n")

    init_db()

    print(f"DB initialized at:\n{DB_PATH}")

    print("\nDatabase optimization complete.")