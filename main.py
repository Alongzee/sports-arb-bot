"""
main.py — Sports Arbitrage Bot (Hybrid Mode)
==============================================
Coordinates scraping, arb detection, logging, and Telegram alerts.
Phase 1: Observation only. No bets placed.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from config import (
    TIER1, TIER2, MARKETS, SCAN_INTERVAL, ACTIVE_START, ACTIVE_END,
    DB_PATH,
)
from database import init_db, log_opp
from scraper import get_scraper  # Currently only SportyBet implemented
from arb_engine import evaluate_all_markets
from alerter import alert, is_active_hours

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

# ── Configuration ──────────────────────────────────────────────────────────
TOTAL_BANKROLL = 100.0  # GHS — split across your two funded accounts
ACTIVE_MODE = "active"
PASSIVE_MODE = "passive"


async def scan_platform(platform: str, match_url: str) -> dict:
    """
    Scrape odds from one platform for a given match.
    Returns a dict of market → odds, or empty dict on failure.
    """
    try:
        scraper = get_scraper(platform)
        odds = await scraper.get_odds(match_url)
        return odds
    except ValueError:
        log.warning(f"No scraper for {platform} — skipping")
        return {}
    except Exception as e:
        log.error(f"Scrape error ({platform}): {e}")
        return {}


async def scan_match(match_name: str, match_urls: dict) -> None:
    """
    For one match, scrape all platforms, evaluate all markets, log & alert.

    match_urls: {"sportybet": "https://...", "1win": "https://...", ...}
    """
    # ── 1. Scrape all platforms ──────────────────────────────────────────
    platform_odds = {}  # {platform: {market: {"over": X, "under": Y}}}

    for platform, url in match_urls.items():
        odds = await scan_platform(platform, url)
        if odds:
            platform_odds[platform] = odds

    if len(platform_odds) < 2:
        return  # Need at least 2 platforms to compare

    # ── 2. Reorganise odds by market ─────────────────────────────────────
    market_data = {}  # {market: [{"platform": "sportybet", "odds": {...}}, ...]}

    for platform, odds_dict in platform_odds.items():
        for market, odds in odds_dict.items():
            if market not in market_data:
                market_data[market] = []
            market_data[market].append({
                "platform": platform,
                "odds": odds,
            })

    # ── 3. Determine mode (active or passive) ────────────────────────────
    mode = ACTIVE_MODE if is_active_hours() else PASSIVE_MODE

    # ── 4. Evaluate every two-way market for arbitrage ───────────────────
    arbs = evaluate_all_markets(
        market_data=market_data,
        total_bankroll=TOTAL_BANKROLL,
        match_name=match_name,
        mode=mode,
    )

    if not arbs:
        return

    # ── 5. Log and alert ─────────────────────────────────────────────────
    conn = init_db()

    for arb in arbs:
        # Log to SQLite
        log_opp(conn, {
            "match": arb.match_name,
            "market": arb.market,
            "best_over_platform": arb.best_over_platform,
            "best_over_odds": arb.best_over_odds,
            "best_under_platform": arb.best_under_platform,
            "best_under_odds": arb.best_under_odds,
            "combined_imp": arb.combined_imp,
            "margin_pct": arb.margin_pct,
            "stake_over": arb.stake_over,
            "stake_under": arb.stake_under,
            "total_stake": arb.total_stake,
            "payout": arb.payout,
            "profit": arb.profit,
            "mode": arb.mode,
        })

        # Telegram alert (only during active hours)
        await alert(arb)

    conn.close()


async def main():
    log.info("=" * 55)
    log.info("  SPORTS ARBITRAGE BOT — Phase 1 (Observation)")
    log.info(f"  Platforms: {', '.join(TIER1)}")
    log.info(f"  Active hours: {ACTIVE_START}:00–{ACTIVE_END}:00 UTC")
    log.info(f"  Bankroll: {TOTAL_BANKROLL} GHS")
    log.info(f"  Database: {DB_PATH}")
    log.info("=" * 55)

    matches_to_scan = [
        {
            "name": "Crystal Palace vs Shakhtar",
            "urls": {
                "sportybet": "https://www.sportybet.com/gh/m/sport/football/International_Clubs/UEFA_Conference_League/Crystal_Palace_vs_Shakhtar_D/sr:match:69340062",
                "1win": "https://1wgcmt.com/betting/match/sport/crystal-palace-vs-fc-shakhtar-donetsk-34525789?p=32p4",
            },
        },
    ]

    log.info(f"Monitoring {len(matches_to_scan)} matches.")
    log.info("Press Ctrl+C to stop.\n")

    while True:
        scan_start = time.time()

        for match in matches_to_scan:
            await scan_match(match["name"], match["urls"])

        # ⏱ Scan interval — respects your 2-second pause between cycles
        elapsed = time.time() - scan_start
        sleep_time = max(0, SCAN_INTERVAL - elapsed)
        await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    asyncio.run(main())
