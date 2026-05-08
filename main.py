"""
main.py – Sports Arbitrage Bot (Hybrid Mode)
==============================================
Orchestrates scraping, matching, scoring, alerting, and Telegram commands.
Runs on Binance / Gate.io VPS alongside existing bots.
"""

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Our modules
from scraper import SportyBetScraper, OneWinScraper
from matcher import normalise_market, match_teams
from market_ai import (
    get_speed_score, get_stability_score,
    ULTRA_FAST, FAST, MEDIUM, SLOW,
)
from balancer import Balancer
from arb_engine import ArbEngine, ArbOpportunity
from alerter import (
    send_arb_alert, send_nonarb, send_status,
    is_active_hours, is_cool_off_day,
)
from commands import setup_bot, state as cmd_state
from config import (
    BOOKMAKER_YOU, BOOKMAKER_FRIEND,
    ACTIVE_START, ACTIVE_END,
    SCAN_INTERVAL,
    COOL_OFF_DAYS,
    DB_PATH,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

# ── Global state ──────────────────────────────────────────────────────────
balancer = Balancer()
engine = ArbEngine(balancer)
telegram_app = setup_bot(balancer)   # commands.py

# ── Match list (hardcoded for now – expand as needed) ────────────────────
MATCHES = [
    {
        "name": "Crystal Palace vs Shakhtar",
        "sport": "football",
        "urls": {
            "sportybet": "https://www.sportybet.com/gh/m/sport/football/International_Clubs/UEFA_Conference_League/Crystal_Palace_vs_Shakhtar_D/sr:match:69340062",
            "1win": "https://1wgcmt.com/betting/match/sport/crystal-palace-vs-fc-shakhtar-donetsk-34525789?p=32p4",
        },
    },
    # Add more matches here as you find them
]


# ── Scraper helpers ───────────────────────────────────────────────────────

async def scrape_sportybet(match_url: str) -> dict:
    try:
        s = SportyBetScraper()
        return await s.get_odds(match_url)
    except Exception as e:
        log.error(f"SportyBet scrape error: {e}")
        return {}


async def scrape_1win(match_url: str) -> dict:
    try:
        s = OneWinScraper()
        return await s.get_odds(match_url)
    except Exception as e:
        log.error(f"1win scrape error: {e}")
        return {}


# ── Market normalisation ──────────────────────────────────────────────────

def normalise_odds_dict(raw: dict, platform: str) -> Dict[str, Dict[str, Tuple[str, float]]]:
    """
    Convert a raw scraper dict into a standardised form:
    { "over_under_2.5": { "over": ("platform", odds), "under": ("platform", odds) }, ... }
    """
    result = {}
    for key, sides in raw.items():
        norm_key = normalise_market(key)
        entry = {}
        for side, odds in sides.items():
            entry[side] = (platform, odds)
        if len(entry) == 2:
            result[norm_key] = entry
    return result


# ── Scan loop ─────────────────────────────────────────────────────────────

async def scan_cycle() -> Optional[ArbOpportunity]:
    """
    One full scan cycle across all matches.
    Returns the single best arb (if any), or None.
    """
    candidates = []

    for match in MATCHES:
        sporty_odds = await scrape_sportybet(match["urls"]["sportybet"])
        onewin_odds = await scrape_1win(match["urls"]["1win"])

        if not sporty_odds or not onewin_odds:
            continue

        # Normalise
        norm_sporty = normalise_odds_dict(sporty_odds, BOOKMAKER_FRIEND)
        norm_onewin = normalise_odds_dict(onewin_odds, BOOKMAKER_YOU)

        # Merge: for each market, we want both sides from whichever platform has the best
        merged = {}
        for market_key in set(norm_sporty.keys()) | set(norm_onewin.keys()):
            sides = {}
            if market_key in norm_sporty:
                sides.update(norm_sporty[market_key])
            if market_key in norm_onewin:
                sides.update(norm_onewin[market_key])
            if len(sides) >= 2:
                merged[market_key] = sides

        if not merged:
            continue

        # Determine match state (simplified – assume pre‑match for now)
        is_live = False
        minutes_to_kickoff = 20   # can be fed from scraper later

        result = engine.scan_all(
            sport=match.get("sport", "football"),
            match_name=match["name"],
            markets=merged,
            is_live=is_live,
            minutes_to_kickoff=minutes_to_kickoff,
        )

        if result.opportunity:
            candidates.append(result.opportunity)

    if not candidates:
        return None

    # Return the single highest‑scoring arb
    candidates.sort(key=lambda a: a.priority_score, reverse=True)
    return candidates[0]


# ── Alert handler ─────────────────────────────────────────────────────────

async def handle_arb(arb: ArbOpportunity) -> None:
    """Send the arb alert via Telegram and lock capital."""
    # Determine which side goes to which person
    if arb.platform_over == BOOKMAKER_YOU:
        odds_you = arb.odds_over
        odds_friend = arb.odds_under
        stake_you = arb.stake_over
        stake_friend = arb.stake_under
        bet_you = f"{arb.market_display} (Over/Home)"
        bet_friend = f"{arb.market_display} (Under/Away)"
    else:
        odds_you = arb.odds_under
        odds_friend = arb.odds_over
        stake_you = arb.stake_under
        stake_friend = arb.stake_over
        bet_you = f"{arb.market_display} (Under/Away)"
        bet_friend = f"{arb.market_display} (Over/Home)"

    # Deep links – we have the URLs from MATCHES
    # For simplicity, we'll just pass empty for now; can be refined later
    ok = await send_arb_alert(
        arb_id=arb.arb_id,
        match_name=arb.match_name,
        sport=arb.sport,
        market_display=arb.market_display,
        kickoff_str=f"in {arb.minutes_to_kickoff:.0f} min" if not arb.is_live else "LIVE",
        score={},
        odds_you=odds_you,
        odds_friend=odds_friend,
        stake_you=stake_you,
        stake_friend=stake_friend,
        margin_pct=arb.margin_pct,
        speed=arb.speed,
        stability=arb.stability,
        is_live=arb.is_live,
    )

    if ok:
        # Register the pending arb with commands.py state
        if cmd_state:
            from commands import PendingArb
            cmd_state.current_arb = PendingArb(arb.arb_id, {
                "sport": arb.sport,
                "market_key": arb.market_key,
                "stake_you": stake_you,
                "stake_friend": stake_friend,
            })
        log.info(f"Arb #{arb.arb_id} alerted.")


# ── Main loop ────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 55)
    log.info("  SPORTS ARBITRAGE BOT — LIVE")
    log.info(f"  Platforms: {BOOKMAKER_YOU} + {BOOKMAKER_FRIEND}")
    log.info(f"  Active hours: {ACTIVE_START}:00–{ACTIVE_END}:00 UTC")
    log.info(f"  Cool‑off days: {COOL_OFF_DAYS}")
    log.info("=" * 55)

    # Start Telegram bot in background
    async def start_telegram():
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        log.info("Telegram command handler started.")
        # Keep running
        while True:
            await asyncio.sleep(3600)

    telegram_task = asyncio.create_task(start_telegram())

    # Start arb timeout watchdog
    from commands import arb_timeout_watchdog
    watchdog_task = asyncio.create_task(arb_timeout_watchdog())

    scan_count = 0
    last_status = time.time()

    log.info("Scan loop started. Press Ctrl+C to stop.")

    while True:
        try:
            # ── Respect cool‑off ────────────────────────────────────
            if is_cool_off_day():
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            # ── Respect active hours ────────────────────────────────
            if not is_active_hours():
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            # ── Check both users available ──────────────────────────
            if cmd_state and not cmd_state.can_alert:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            arb = await scan_cycle()
            scan_count += 1

            if arb:
                await handle_arb(arb)

            # Heartbeat
            if time.time() - last_status > 300:   # every 5 minutes
                log.info(f"💓 Heartbeat | Scans: {scan_count} | "
                         f"Bal: {balancer.get_balance(BOOKMAKER_YOU):.2f} / "
                         f"{balancer.get_balance(BOOKMAKER_FRIEND):.2f}")
                last_status = time.time()

        except Exception as e:
            log.error(f"Scan loop error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


# ── Shutdown ──────────────────────────────────────────────────────────────
shutdown_event = asyncio.Event()

def _handle_shutdown(signum, frame):
    log.info("Shutdown signal received.")
    shutdown_event.set()

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


if __name__ == "__main__":
    asyncio.run(main()) 
