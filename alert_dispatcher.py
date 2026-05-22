"""
StableGuard — alert_dispatcher.py
Production-safe alert dispatcher
"""

import os
import logging
import requests

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("alert_dispatcher")

# ── Environment ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

TIMEOUT = 15

# ── Telegram sender ──────────────────────────────────────────
def send_telegram(message: str) -> bool:

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log.warning("Telegram credentials missing")
        return False

    try:

        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT,
            "text": message,
            "parse_mode": "HTML"
        }

        response = requests.post(
            url,
            json=payload,
            timeout=TIMEOUT
        )

        if response.status_code == 200:
            return True

        log.warning(
            f"Telegram HTTP {response.status_code}"
        )

        return False

    except Exception as e:

        log.error(
            f"Telegram send failed: {e}"
        )

        return False


# ── Discord sender ───────────────────────────────────────────
def send_discord(message: str) -> bool:

    if not DISCORD_WEBHOOK:
        log.warning("Discord webhook missing")
        return False

    try:

        payload = {
            "content": message
        }

        response = requests.post(
            DISCORD_WEBHOOK,
            json=payload,
            timeout=TIMEOUT
        )

        if response.status_code in [200, 204]:
            return True

        log.warning(
            f"Discord HTTP {response.status_code}"
        )

        return False

    except Exception as e:

        log.error(
            f"Discord send failed: {e}"
        )

        return False


# ── Message formatter ────────────────────────────────────────
def build_alert_message(
    symbol: str,
    data: dict
) -> str:

    alert = data.get(
        "alert_level",
        "UNKNOWN"
    )

    score = data.get(
        "composite_score",
        0
    )

    trend = data.get(
        "risk_trend",
        "→ Stable"
    )

    guidance = data.get(
        "guidance",
        "No guidance"
    )

    active_flags = data.get(
        "active_flags",
        []
    )

    peg_dev = data.get(
        "signals",
        {}
    ).get(
        "liquidity",
        {}
    ).get(
        "peg_dev_bps",
        0
    )

    burn_z = data.get(
        "signals",
        {}
    ).get(
        "mintBurn",
        {}
    ).get(
        "burn_zscore",
        0
    )

    message = (
        f"🚨 <b>StableGuard Alert</b>\n\n"
        f"<b>Coin:</b> {symbol}\n"
        f"<b>Alert:</b> {alert}\n"
        f"<b>Score:</b> {score}\n"
        f"<b>Trend:</b> {trend}\n"
        f"<b>Peg Dev:</b> {peg_dev}bps\n"
        f"<b>Burn Z:</b> {burn_z}\n\n"
        f"<b>Flags:</b>\n"
    )

    if active_flags:
        for flag in active_flags:
            message += f"• {flag}\n"
    else:
        message += "• None\n"

    message += (
        f"\n<b>Guidance:</b>\n"
        f"{guidance}"
    )

    return message


# ── Main dispatcher ──────────────────────────────────────────
def dispatch_all(scores: dict):

    if not scores:
        return

    for symbol, data in scores.items():

        try:

            alert_level = data.get(
                "alert_level",
                "🟢 HEALTHY"
            )

            # Only dispatch risky alerts
            if alert_level == "🟢 HEALTHY":
                continue

            log.info(
                f"Dispatching alert: "
                f"{symbol} {alert_level}"
            )

            message = build_alert_message(
                symbol,
                data
            )

            telegram_ok = send_telegram(
                message
            )

            discord_ok = send_discord(
                message
            )

            channels = []

            if telegram_ok:
                channels.append("Telegram")

            if discord_ok:
                channels.append("Discord")

            if channels:

                log.info(
                    f"Alert dispatched to "
                    f"{', '.join(channels)} "
                    f"for {symbol}"
                )

            else:

                log.warning(
                    f"No alert channels succeeded "
                    f"for {symbol}"
                )

        except Exception as e:

            log.error(
                f"Dispatch failed for "
                f"{symbol}: {e}",
                exc_info=True
            )


# ── Local test ───────────────────────────────────────────────
if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    sample = {
        "DAI": {
            "alert_level": "🟡 WATCH",
            "composite_score": 42,
            "risk_trend": "↑ Rising",
            "guidance": "Monitor liquidity conditions.",
            "active_flags": [
                "PEG_DEV",
                "ARB_DECLINE"
            ],
            "signals": {
                "liquidity": {
                    "peg_dev_bps": 24
                },
                "mintBurn": {
                    "burn_zscore": 1.45
                }
            }
        }
    }

    dispatch_all(sample)