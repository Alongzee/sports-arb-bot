"""
alerter.py – Telegram alert dispatcher for arb opportunities.
Only sends alerts during active hours (config ACTIVE_START – ACTIVE_END).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ACTIVE_START, ACTIVE_END
from arb_engine import ArbOpportunity

log = logging.getLogger("alerter")

# ── Rate limiting ─────────────────────────────────────────────────────────
_last_sent: float = 0.0          # timestamp of last sent message
MIN_INTERVAL = 2.0               # seconds between consecutive Telegram calls


async def _send_telegram(text: str) -> bool:
    """Send a Markdown-formatted message to your Telegram chat."""
    global _last_sent

    # Crash protection – don't spam Telegram
    now = datetime.utcnow().timestamp()
    if now - _last_sent < MIN_INTERVAL:
        await asyncio.sleep(MIN_INTERVAL - (now - _last_sent))

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials missing – cannot send alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
        _last_sent = datetime.utcnow().timestamp()
        if resp.status_code == 200:
            log.info("Telegram alert sent.")
            return True
        else:
            log.error(f"Telegram API error: {resp.status_code} – {resp.text}")
            return False
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def is_active_hours() -> bool:
    """Return True if current UTC hour is within the configured active window."""
    now_utc = datetime.now(timezone.utc).hour
    return ACTIVE_START <= now_utc < ACTIVE_END


def format_alert(arb: ArbOpportunity) -> str:
    """Build a human-readable alert message for one arb opportunity."""
    # Determine label for the two sides based on market type
    if "asian_handicap" in arb.market or "corners" in arb.market:
        side_a = "Home"
        side_b = "Away"
    else:
        side_a = "Over"
        side_b = "Under"

    msg = (
        f"📊 *ARBITRAGE ALERT*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"*Match:*  {arb.match_name}\n"
        f"*Market:* {arb.market}\n"
        f"*Margin:* {arb.margin_pct:.2f}%\n"
        f"\n"
        f"*{side_a}:*  `{arb.best_over_platform}` @ `{arb.best_over_odds}`\n"
        f" ▶ Stake:  *{arb.stake_over:.2f} GHS*\n"
        f"\n"
        f"*{side_b}:* `{arb.best_under_platform}` @ `{arb.best_under_odds}`\n"
        f" ▶ Stake:  *{arb.stake_under:.2f} GHS*\n"
        f"\n"
        f"💰 *Profit:*  {arb.profit:.2f} GHS\n"
        f"⏱  *Settle:* ~{arb.mode}\n"
    )
    return msg


async def alert(arb: ArbOpportunity) -> bool:
    """
    Send an arb alert if within active hours.
    Returns True if alert was sent, False if skipped (outside hours) or failed.
    """
    if not is_active_hours():
        log.info(f"Outside active hours – skipping alert for {arb.match_name}")
        return False

    text = format_alert(arb)
    return await _send_telegram(text)
