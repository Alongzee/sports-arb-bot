"""
alerter.py – Telegram alert dispatcher with deep links,
             stability indicators, and friend‑proof wording.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    ACTIVE_START, ACTIVE_END,
    BOOKMAKER_YOU, BOOKMAKER_FRIEND,
    EXECUTION_TIMEOUT,
    COOL_OFF_DAYS,
)
from market_ai import (
    ULTRA_FAST, FAST, MEDIUM, SLOW,
    get_speed_score, get_stability_score, calculate_priority_score,
)

log = logging.getLogger("alerter")

# ── Rate limiting ─────────────────────────────────────────────────────────
_last_sent: float = 0.0
MIN_INTERVAL = 1.5  # seconds between Telegram calls


async def _send_telegram(text: str) -> bool:
    """Send a Markdown‑formatted message to the Telegram group."""
    global _last_sent

    now = datetime.now(timezone.utc).timestamp()
    if now - _last_sent < MIN_INTERVAL:
        await asyncio.sleep(MIN_INTERVAL - (now - _last_sent))

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials missing.")
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
        _last_sent = datetime.now(timezone.utc).timestamp()
        if resp.status_code == 200:
            return True
        log.error(f"Telegram API error: {resp.status_code} – {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# ── Helpers ──────────────────────────────────────────────────────────────

def _speed_label(speed: int) -> str:
    if speed == ULTRA_FAST: return "🟢 Ultra‑fast (~1‑10 min)"
    if speed == FAST:       return "🟢 Fast (~12‑45 min)"
    if speed == MEDIUM:     return "🟡 Medium (~45‑90 min)"
    return "🔴 Slow (90+ min)"


def _stability_label(stability: float) -> str:
    if stability >= 0.8: return "🟢 High — odds very stable"
    if stability >= 0.4: return "🟡 Moderate — odds may shift"
    return "🔴 Low — execute immediately"


def _settlement_label(speed: int) -> str:
    if speed == ULTRA_FAST: return "~1‑10 min"
    if speed == FAST:       return "~12‑45 min"
    if speed == MEDIUM:     return "~45‑90 min"
    return "90+ min"


def is_active_hours() -> bool:
    now = datetime.now(timezone.utc).hour
    return ACTIVE_START <= now < ACTIVE_END


def is_cool_off_day() -> bool:
    return datetime.now(timezone.utc).weekday() in COOL_OFF_DAYS


# ── Public alert builders ─────────────────────────────────────────────────

def format_arb_alert(
    arb_id: int,
    match_name: str,
    sport: str,
    market_display: str,
    kickoff_str: str,
    score: dict,
    odds_you: float,
    odds_friend: float,
    stake_you: float,
    stake_friend: float,
    margin_pct: float,
    sportybet_url: str = "",
    onewin_url: str = "",
    tab_sportybet: str = "",
    tab_onewin: str = "",
    bet_you: str = "",
    bet_friend: str = "",
    speed: int = FAST,
    stability: float = 0.8,
    is_live: bool = False,
) -> str:
    """
    Build a complete arb alert message.
    """
    settle = _settlement_label(speed)
    speed_str = _speed_label(speed)
    stab_str  = _stability_label(stability)
    profit    = round(stake_you + stake_friend - (stake_you * odds_you), 2)

    lines = [
        f"📊 *ARB #{arb_id}* | Priority: {score['final_score']:.0%}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"*Match:*  {match_name}",
        f"*Market:* {market_display}",
        f"*Kickoff:* {kickoff_str}",
        f"*Settlement:* {settle}",
        "",
        f"🔵 *{BOOKMAKER_YOU.upper()} (You)*",
        f"   Bet:  `{bet_you}`",
        f"   Odds: `{odds_you}`",
        f"   Stake: *GHS {stake_you:.2f}*",
    ]
    if onewin_url:
        lines.append(f"   🔗 [Open match]({onewin_url})")
    if tab_onewin:
        lines.append(f"   📂 Tab: {tab_onewin}")

    lines.append("")
    lines.append(f"🔴 *{BOOKMAKER_FRIEND.upper()} (Friend)*")
    lines.append(f"   Bet:  `{bet_friend}`")
    lines.append(f"   Odds: `{odds_friend}`")
    lines.append(f"   Stake: *GHS {stake_friend:.2f}*")
    if sportybet_url:
        lines.append(f"   🔗 [Open match]({sportybet_url})")
    if tab_sportybet:
        lines.append(f"   📂 Tab: {tab_sportybet}")

    lines.append("")
    lines.append(f"💰 *Profit:* GHS {profit:.2f} ({margin_pct:.2f}%)")
    lines.append(f"⏱ *Speed:* {speed_str}")
    lines.append(f"📉 *Stability:* {stab_str}")
    lines.append(f"⏰ Execute within *{EXECUTION_TIMEOUT}s*")
    lines.append("")
    lines.append("Reply `/done` when both bets are placed.")
    lines.append("Reply `/abort` to cancel this arb.")

    return "\n".join(lines)


def format_nonarb_suggestion(
    person: str,
    match_name: str,
    bet_type: str,
    odds: float,
    stake: float,
    deep_link: str = "",
    time_window: str = "",
) -> str:
    """Build a friendly, simple non‑arb suggestion."""
    lines = [
        f"🎲 *Non‑Arb Bet — {person}*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"*Match:* {match_name}",
        f"*Bet:* {bet_type} @ `{odds}`",
        f"*Stake:* GHS {stake:.2f}",
    ]
    if deep_link:
        lines.append(f"🔗 [Open match]({deep_link})")
    if time_window:
        lines.append(f"⏰ Suggested time: {time_window}")
    lines.append("")
    lines.append("_This is a fun bet for account appearance. No arb required._")
    return "\n".join(lines)


def format_status(
    bal_you: float,
    bal_friend: float,
    locked_you: float = 0.0,
    locked_friend: float = 0.0,
    active_arbs: int = 0,
) -> str:
    """Quick balance / status summary."""
    total = bal_you + bal_friend
    return "\n".join([
        "📊 *Account Status*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🔵 {BOOKMAKER_YOU}: GHS {bal_you:.2f} (locked: GHS {locked_you:.2f})",
        f"🔴 {BOOKMAKER_FRIEND}: GHS {bal_friend:.2f} (locked: GHS {locked_friend:.2f})",
        f"💰 Total: GHS {total:.2f}",
        f"📈 Active arbs: {active_arbs}",
    ])


# ── Alert dispatcher ─────────────────────────────────────────────────────

async def send_arb_alert(
    arb_id: int,
    match_name: str,
    sport: str,
    market_display: str,
    kickoff_str: str,
    score: dict,
    odds_you: float,
    odds_friend: float,
    stake_you: float,
    stake_friend: float,
    margin_pct: float,
    sportybet_url: str = "",
    onewin_url: str = "",
    tab_sportybet: str = "",
    tab_onewin: str = "",
    bet_you: str = "",
    bet_friend: str = "",
    speed: int = FAST,
    stability: float = 0.8,
    is_live: bool = False,
) -> bool:
    """Send arb alert if within active hours and not on cool‑off."""
    if not is_active_hours():
        log.info("Outside active hours — arb alert suppressed.")
        return False
    if is_cool_off_day():
        log.info("Cool‑off day — arb alert suppressed.")
        return False

    text = format_arb_alert(
        arb_id, match_name, sport, market_display, kickoff_str, score,
        odds_you, odds_friend, stake_you, stake_friend, margin_pct,
        sportybet_url, onewin_url, tab_sportybet, tab_onewin,
        bet_you, bet_friend, speed, stability, is_live,
    )
    return await _send_telegram(text)


async def send_nonarb(
    person: str,
    match_name: str,
    bet_type: str,
    odds: float,
    stake: float,
    deep_link: str = "",
    time_window: str = "",
) -> bool:
    """Send a non‑arb suggestion to the group."""
    text = format_nonarb_suggestion(person, match_name, bet_type, odds, stake, deep_link, time_window)
    return await _send_telegram(text)


async def send_status(
    bal_you: float,
    bal_friend: float,
    locked_you: float = 0.0,
    locked_friend: float = 0.0,
    active_arbs: int = 0,
) -> bool:
    """Send a quick balance status."""
    text = format_status(bal_you, bal_friend, locked_you, locked_friend, active_arbs)
    return await _send_telegram(text)
