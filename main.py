"""
main.py – Sports Arbitrage Bot (N-way Multi-Bookmaker)

Orchestrates:
1. Hourly discovery: fetch match lists from all bookmakers, cluster them
2. Every 2 seconds: fetch current odds, evaluate N-way arbs, alert
3. Telegram commands: /done, /cancel for manual execution tracking
"""

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from scraper import get_scraper, SportyBetScraper, OneWinScraper, BetwayScraper
from discovery import fetch_all_bookmaker_events, cluster_matches
from matcher import match_teams
from arb_engine import ArbEngine, ArbOpportunity
from balancer import Balancer
from alerter import send_arb_alert, is_active_hours, is_cool_off_day
from commands import setup_bot, state as cmd_state
from config import (
    BOOKMAKERS,
    ACTIVE_START,
    ACTIVE_END,
    SCAN_INTERVAL,
    DISCOVERY_REFRESH_INTERVAL,
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

# ────────────────────────────────────────────────────────────────────────────
# Global state
# ────────────────────────────────────────────────────────────────────────────

balancer = Balancer()
engine = ArbEngine(balancer)
telegram_app = setup_bot(balancer)

# Scraper instances
scrapers: Dict[str, object] = {}

# Match registry (updated hourly)
match_registry: Dict[str, dict] = {}
registry_lock = asyncio.Lock()
last_discovery_time = 0

# Track seen arbs to avoid duplicate alerts
seen_arbs: set = set()


# ────────────────────────────────────────────────────────────────────────────
# Discovery & Registry Management
# ────────────────────────────────────────────────────────────────────────────

async def initialize_scrapers():
    """Create scraper instances for all bookmakers."""
    global scrapers
    for bookie in BOOKMAKERS:
        try:
            scrapers[bookie] = get_scraper(bookie)
            log.info(f"Initialized {bookie} scraper")
        except Exception as e:
            log.error(f"Failed to initialize {bookie}: {e}")


async def refresh_match_registry():
    """
    Fetch match lists from all bookmakers and cluster them.
    Called once per hour.
    """
    global match_registry, last_discovery_time, registry_lock
    
    now = time.time()
    if now - last_discovery_time < DISCOVERY_REFRESH_INTERVAL:
        return  # Too soon, skip
    
    log.info("Refreshing match registry...")
    
    try:
        # Fetch events from all bookmakers in parallel
        all_events = await fetch_all_bookmaker_events(scrapers, sport_id="soccer")
        
        # Cluster events by team names + kickoff time
        new_registry = cluster_matches(all_events)
        
        # Update registry atomically
        async with registry_lock:
            match_registry = new_registry
            last_discovery_time = now
        
        log.info(f"Registry updated: {len(match_registry)} unique matches")
        
    except Exception as e:
        log.error(f"Registry refresh error: {e}")


# ────────────────────────────────────────────────────────────────────────────
# Pricing & Odds Merging
# ────────────────────────────────────────────────────────────────────────────

async def fetch_odds_for_match(match_info: dict) -> dict:
    """
    Fetch odds from all bookmakers that have this match.
    
    Returns: {market_key: {side: [(platform, odds), ...]}}
    """
    platforms = match_info.get("platforms", {})
    markets_by_platform = {}
    
    # Fetch odds from each platform
    for bookie, event_id in platforms.items():
        scraper = scrapers.get(bookie)
        if not scraper:
            continue
        
        try:
            if bookie == "sportybet":
                # SportyBet uses event ID directly
                odds = await scraper.get_odds(f"sr:match:{event_id}")
            elif bookie == "betway":
                # Betway uses event ID
                odds = await scraper.get_odds(event_id)
            else:
                # Generic fallback
                odds = await scraper.get_odds(event_id)
            
            if odds:
                markets_by_platform[bookie] = odds
                log.debug(f"{bookie}: got odds for {len(odds)} markets")
        except Exception as e:
            log.debug(f"{bookie} pricing error: {e}")
    
    if not markets_by_platform:
        return {}
    
    # Merge: {market_key: {side: [(platform, odds), ...]}}
    merged = {}
    all_markets = set()
    for market_dict in markets_by_platform.values():
        all_markets.update(market_dict.keys())
    
    for market_key in all_markets:
        sides_by_platform = {}
        for platform, market_dict in markets_by_platform.items():
            if market_key not in market_dict:
                continue
            sides = market_dict[market_key]
            for side, odds in sides.items():
                if side not in sides_by_platform:
                    sides_by_platform[side] = []
                sides_by_platform[side].append((platform, float(odds)))
        
        if sides_by_platform:
            merged[market_key] = sides_by_platform
    
    return merged


# ────────────────────────────────────────────────────────────────────────────
# Scan Cycle
# ────────────────────────────────────────────────────────────────────────────

async def scan_cycle():
    """
    Main scan loop: iterate through all matches, fetch odds, evaluate arbs.
    """
    global match_registry
    
    async with registry_lock:
        matches_to_scan = list(match_registry.values())
    
    if not matches_to_scan:
        log.debug("No matches in registry")
        return
    
    log.info(f"Scanning {len(matches_to_scan)} matches...")
    candidates = []
    
    for match_info in matches_to_scan:
        match_name = match_info.get("name", "Unknown")
        kickoff_epoch = match_info.get("kickoff", 0)
        
        # Determine if live or prematch
        now_epoch = datetime.now(timezone.utc).timestamp()
        minutes_to_kickoff = (kickoff_epoch - now_epoch) / 60.0
        is_live = minutes_to_kickoff < 0
        
        # Fetch odds from all platforms
        markets = await fetch_odds_for_match(match_info)
        if not markets:
            continue
        
        # Evaluate N-way arbs
        result = engine.scan_all(
            sport="football",
            match_name=match_name,
            markets=markets,
            is_live=is_live,
            minutes_to_kickoff=max(0, minutes_to_kickoff),
        )
        
        if result.opportunity:
            candidates.append(result.opportunity)
    
    if not candidates:
        log.debug("No viable arbs found")
        return
    
    # Sort by priority and alert on best
    candidates.sort(key=lambda a: a.priority_score, reverse=True)
    best = candidates[0]
    
    # Dedup check
    arb_key = f"{best.match_name}:{best.market_key}"
    if arb_key in seen_arbs:
        log.debug(f"Duplicate arb: {arb_key}")
        return
    
    seen_arbs.add(arb_key)
    
    # Send alert
    await alert_arb(best)


async def alert_arb(arb: ArbOpportunity):
    """Send Telegram alert for an arb opportunity."""
    try:
        # Build sides info
        sides_info = []
        for side, (platform, odds) in arb.sides.items():
            sides_info.append(f"{side.upper()}: {odds:.2f} @ {platform}")
        sides_str = " | ".join(sides_info)
        
        # Format message
        msg = (
            f"🎯 *ARB FOUND*\n"
            f"Match: {arb.match_name}\n"
            f"Market: {arb.market_display}\n"
            f"Profit: {arb.margin_pct:.2f}%\n"
            f"{sides_str}\n"
            f"Platforms: {arb.num_platforms}-way\n"
            f"Score: {arb.priority_score:.2f}"
        )
        
        # Send via alerter (or direct Telegram call)
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        import httpx
        
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "Markdown",
                },
            )
        log.info(f"Alert sent: {arb.match_name} {arb.margin_pct:.2f}%")
    except Exception as e:
        log.error(f"Alert error: {e}")


# ────────────────────────────────────────────────────────────────────────────
# Main Loop
# ────────────────────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 60)
    log.info("SPORTS ARBITRAGE BOT — N-WAY MULTI-BOOKMAKER")
    log.info(f"Bookmakers: {', '.join(BOOKMAKERS)}")
    log.info(f"Active hours: {ACTIVE_START}:00-{ACTIVE_END}:00 UTC")
    log.info("=" * 60)
    
    # Initialize
    await initialize_scrapers()
    
    # Start Telegram command handler
    async def run_telegram():
        try:
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling()
            log.info("Telegram bot started")
            while True:
                await asyncio.sleep(3600)
        except Exception as e:
            log.error(f"Telegram error: {e}")
    
    telegram_task = asyncio.create_task(run_telegram())
    
    # Discovery refresh loop (hourly)
    async def discovery_loop():
        while True:
            try:
                await refresh_match_registry()
            except Exception as e:
                log.error(f"Discovery loop error: {e}")
            await asyncio.sleep(60)  # Check every minute if refresh is needed
    
    discovery_task = asyncio.create_task(discovery_loop())
    
    # Main scan loop (every 2 seconds)
    scan_count = 0
    last_status = time.time()
    
    try:
        while True:
            try:
                if is_cool_off_day() or not is_active_hours():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                
                if cmd_state and not cmd_state.can_alert:
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                
                await scan_cycle()
                scan_count += 1
                
                # Status heartbeat
                if time.time() - last_status > 300:
                    log.info(
                        f"Heartbeat | Scans: {scan_count} | "
                        f"Matches in registry: {len(match_registry)} | "
                        f"Balance: {balancer.get_balance('all'):.2f} GHS"
                    )
                    last_status = time.time()
            
            except Exception as e:
                log.error(f"Scan cycle error: {e}")
            
            await asyncio.sleep(SCAN_INTERVAL)
    
    except KeyboardInterrupt:
        log.info("Shutdown signal received")
    except Exception as e:
        log.error(f"Main loop error: {e}")
    finally:
        telegram_task.cancel()
        discovery_task.cancel()


# ────────────────────────────────────────────────────────────────────────────
# Entry Point
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(main())
