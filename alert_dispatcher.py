"""
StableGuard — alert_dispatcher.py
Layer 5: Alert delivery
"""

import os
import logging
import requests
import pathlib
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load .env from project folder explicitly
env_path = pathlib.Path("C:/Users/PC/Desktop/Emmanuel/Python Courses/Stableguide/.env")
load_dotenv(dotenv_path=env_path, override=True)

# Debug: confirm keys loaded
print(f"DEBUG TOKEN: {os.getenv('TELEGRAM_BOT_TOKEN', 'NOT FOUND')[:15]}...")
print(f"DEBUG CHAT:  {os.getenv('TELEGRAM_CHAT_ID', 'NOT FOUND')}")
print(f"DEBUG DISC:  {os.getenv('DISCORD_WEBHOOK_URL', 'NOT FOUND')[:30]}...")

log = logging.getLogger("alert_dispatcher")

# ── Config from .env ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Alert level to emoji mapping
LEVEL_EMOJI = {
    "🔴 EXIT":    "🔴",
    "🟠 REDUCE":  "🟠",
    "🟡 WATCH":   "🟡",
    "🟢 HEALTHY": "🟢",
}

# Discord color codes per alert level
DISCORD_COLORS = {
    "🔴 EXIT":    0xFF0000,   # Red
    "🟠 REDUCE":  0xFF6600,   # Orange
    "🟡 WATCH":   0xFFCC00,   # Yellow
    "🟢 HEALTHY": 0x00CC44,   # Green
}

# Only alert on these levels (don't spam HEALTHY)
ALERT_LEVELS = {"🔴 EXIT", "🟠 REDUCE", "🟡 WATCH"}


# ── Message formatter ──────────────────────────────────────────
def format_telegram_message(risk: dict) -> str:
    """
    Formats a clean Telegram alert message.
    Per spec: must include what is happening, which signals fired,
    and what the recommended action is.
    """
    symbol      = risk.get("symbol", "UNKNOWN")
    alert_level = risk.get("alert_level", "")
    score       = risk.get("composite_score", 0)
    trend       = risk.get("raw", {}).get("risk_trend", "")
    pillars     = risk.get("pillars_active", 0)
    flags       = risk.get("active_flags", [])
    guidance    = risk.get("guidance", "")
    scored_at   = risk.get("scored_at", "")

    # Signal breakdown
    signals  = risk.get("signals", {})
    liq_score = signals.get("liquidity", {}).get("score", 0)
    mb_score  = signals.get("mintBurn", {}).get("score", 0)
    arb_score = signals.get("arb", {}).get("score", 0)
    burn_z    = signals.get("mintBurn", {}).get("burn_zscore", 0)
    arb_z     = signals.get("arb", {}).get("arb_zscore", 0)
    peg_bps   = signals.get("liquidity", {}).get("peg_dev_bps", 0)

    # Price info
    price     = risk.get("price", {})
    price_usd = price.get("usd")
    price_str = f"${price_usd:.4f}" if price_usd else "N/A"

    emoji = LEVEL_EMOJI.get(alert_level, "⚠️")

    msg = f"""
{emoji} *StableGuard Alert — {symbol}*
━━━━━━━━━━━━━━━━━━━━
*Alert Level:* {alert_level}
*Risk Score:* {score}/100 {trend}
*Pillars Active:* {pillars}/3
*Price:* {price_str}

📊 *Signal Breakdown:*
• Liquidity:  {liq_score}/100 (peg dev: {peg_bps:.1f}bps)
• Mint/Burn:  {mb_score}/100 (burn z: {burn_z:.2f})
• Arb Health: {arb_score}/100 (arb z: {arb_z:.2f})

🚩 *Active Flags:* {', '.join(flags) if flags else 'None'}

💡 *Guidance:*
{guidance}

🕐 {scored_at[:19].replace('T', ' ')} UTC
━━━━━━━━━━━━━━━━━━━━
_StableGuard Early Warning System_
""".strip()

    return msg


def format_discord_embed(risk: dict) -> dict:
    """
    Formats a Discord embed card for the alert.
    Color-coded by severity level.
    """
    symbol      = risk.get("symbol", "UNKNOWN")
    alert_level = risk.get("alert_level", "")
    score       = risk.get("composite_score", 0)
    trend       = risk.get("raw", {}).get("risk_trend", "")
    flags       = risk.get("active_flags", [])
    guidance    = risk.get("guidance", "")
    scored_at   = risk.get("scored_at", "")

    signals   = risk.get("signals", {})
    liq_score = signals.get("liquidity", {}).get("score", 0)
    mb_score  = signals.get("mintBurn", {}).get("score", 0)
    arb_score = signals.get("arb", {}).get("score", 0)

    color = DISCORD_COLORS.get(alert_level, 0x888888)

    embed = {
        "title":       f"{LEVEL_EMOJI.get(alert_level, '⚠️')} StableGuard Alert — {symbol}",
        "description": guidance,
        "color":       color,
        "fields": [
            {
                "name":   "Alert Level",
                "value":  alert_level,
                "inline": True
            },
            {
                "name":   "Risk Score",
                "value":  f"{score}/100 {trend}",
                "inline": True
            },
            {
                "name":   "Signal Scores",
                "value":  f"Liq: {liq_score} | MB: {mb_score} | Arb: {arb_score}",
                "inline": False
            },
            {
                "name":   "Active Flags",
                "value":  ', '.join(flags) if flags else "None",
                "inline": False
            },
        ],
        "footer": {
            "text": f"StableGuard • {scored_at[:19].replace('T', ' ')} UTC"
        },
        "timestamp": scored_at,
    }

    return embed


# ── Telegram sender ────────────────────────────────────────────
def send_telegram(risk: dict) -> bool:
    """
    Sends alert to Telegram channel/chat.
    Returns True if successful.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False

    message = format_telegram_message(risk)
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        resp = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }, timeout=15)

        if resp.status_code == 200:
            log.info(f"Telegram alert sent: {risk['symbol']} {risk['alert_level']}")
            return True
        else:
            log.error(f"Telegram error {resp.status_code}: {resp.text}")
            return False

    except requests.RequestException as e:
        log.error(f"Telegram request failed: {e}")
        return False


# ── Discord sender ─────────────────────────────────────────────
def send_discord(risk: dict) -> bool:
    """
    Sends alert to Discord channel via webhook.
    Returns True if successful.
    """
    if not DISCORD_WEBHOOK_URL:
        log.warning("Discord not configured. Set DISCORD_WEBHOOK_URL in .env")
        return False

    embed = format_discord_embed(risk)

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={
            "username":   "StableGuard",
            "avatar_url": "https://i.imgur.com/placeholder.png",
            "embeds":     [embed],
        }, timeout=15)

        if resp.status_code in (200, 204):
            log.info(f"Discord alert sent: {risk['symbol']} {risk['alert_level']}")
            return True
        else:
            log.error(f"Discord error {resp.status_code}: {resp.text}")
            return False

    except requests.RequestException as e:
        log.error(f"Discord request failed: {e}")
        return False


# ── Master dispatcher ──────────────────────────────────────────
def dispatch_alert(risk: dict) -> dict:
    """
    Main function called by scheduler.py.
    Sends alert to all configured channels.
    Only dispatches for WATCH, REDUCE, EXIT levels.

    Returns delivery report.
    """
    alert_level = risk.get("alert_level", "")
    symbol      = risk.get("symbol", "")

    if alert_level not in ALERT_LEVELS:
        log.debug(f"Skipping dispatch for {symbol} — level is {alert_level}")
        return {"dispatched": False, "reason": "HEALTHY level — no alert needed"}

    log.info(f"Dispatching alert: {symbol} {alert_level}")

    results = {
        "symbol":      symbol,
        "alert_level": alert_level,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "channels":    {}
    }

    # Send to all channels
    results["channels"]["telegram"] = send_telegram(risk)
    results["channels"]["discord"]  = send_discord(risk)

    success_count = sum(1 for v in results["channels"].values() if v)
    results["dispatched"]     = success_count > 0
    results["success_count"]  = success_count

    log.info(f"Alert dispatched to {success_count} channels for {symbol}")
    return results


def dispatch_all(risk_scores: dict) -> list:
    """
    Dispatches alerts for all coins that have non-HEALTHY status.
    Called by scheduler.py every 5 minutes.

    Returns list of dispatch results.
    """
    results = []
    for symbol, risk in risk_scores.items():
        if risk.get("alert_level") in ALERT_LEVELS:
            result = dispatch_alert(risk)
            results.append(result)
    return results


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    from dune_fetcher import fetch_dune_signals
    from risk_scorer  import score_all

    print("\n=== StableGuard Alert Dispatcher — Test ===\n")

    # Get live scores
    dune_signals = fetch_dune_signals(force_refresh=False)
    if not dune_signals:
        print("No Dune data available.")
        exit(1)

    scores = score_all(dune_signals)

    # Find highest risk coin to test with
    top = max(scores.values(), key=lambda x: x["composite_score"])
    print(f"Testing with highest risk coin: {top['symbol']} ({top['alert_level']})")
    print(f"Score: {top['composite_score']}/100\n")

    # Test Telegram
    print("--- Sending Telegram alert ---")
    tg_result = send_telegram(top)
    print(f"Telegram: {'✅ Sent' if tg_result else '❌ Failed'}")

    # Test Discord
    print("\n--- Sending Discord alert ---")
    dc_result = send_discord(top)
    print(f"Discord: {'✅ Sent' if dc_result else '❌ Failed'}")

    # Show formatted message
    print("\n--- Telegram message preview ---")
    print(format_telegram_message(top))