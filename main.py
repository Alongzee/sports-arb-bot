"""
main.py – Sports Arbitrage Bot (Dual Engine)

Engine 1: Direct API scan (SportyBet) — fast, 2-5% arbs
Engine 2: OddsPortal aggregator scan  — slower, 3-40% arbs

Both run as parallel async tasks. Same Telegram alerts, same balancer.
Engine 2 alerts are tagged [AGG] so you know the source.
"""

import asyncio
import logging
import re
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx
from scraper import SportyBetScraper, OneWinScraper
from matcher import normalise_market, match_teams
from market_ai import get_speed_score, get_stability_score, ULTRA_FAST, FAST, MEDIUM, SLOW
from balancer import Balancer
from arb_engine import ArbEngine, ArbOpportunity
from alerter import send_arb_alert, send_nonarb, send_status, is_active_hours, is_cool_off_day
from commands import setup_bot, state as cmd_state
from config import (
    BOOKMAKER_YOU, BOOKMAKER_FRIEND,
    ACTIVE_START, ACTIVE_END,
    SCAN_INTERVAL, COOL_OFF_DAYS, DB_PATH,
)
from aggregator_engine import aggregator_loop

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("main")

SPORTYBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Referer": "https://www.sportybet.com/gh/",
    "Accept": "application/json",
}

# ── Global state ──────────────────────────────────────────────────────────

balancer       = Balancer()
engine         = ArbEngine(balancer)
telegram_app   = setup_bot(balancer)

# Shared seen-arbs set — prevents both engines alerting the same opportunity
seen_arbs: set = set()

# Match cache (Engine 1)
_match_cache: List[dict] = []
_cache_time:  float = 0
CACHE_TTL = 300

# ── Engine 1: Auto-discovery ───────────────────────────────────────────────

async def fetch_sportybet_matches() -> List[dict]:
    """Fetch all live and upcoming football matches from SportyBet."""
    global _match_cache, _cache_time

    if time.time() - _cache_time < CACHE_TTL and _match_cache:
        return _match_cache

    matches = []
    try:
        async with httpx.AsyncClient(headers=SPORTYBET_HEADERS, timeout=15) as client:
            r = await client.get("https://www.sportybet.com/gh/m/sport/football")
            ids = list(set(re.findall(r"sr:match:(\d+)", r.text)))
            ids = [i for i in ids if not i.startswith("111111")]
            log.info(f"Discovered {len(ids)} matches from SportyBet")

            for i in range(0, min(len(ids), 100), 20):
                batch = ids[i:i+20]
                tasks = [
                    client.get(
                        f"https://www.sportybet.com/api/gh/factsCenter/event"
                        f"?eventId=sr%3Amatch%3A{mid}&productId=3"
                    )
                    for mid in batch
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                for resp in responses:
                    try:
                        if isinstance(resp, Exception):
                            continue
                        data = resp.json()
                        if data.get("bizCode") != 10000:
                            continue
                        d = data["data"]
                        matches.append({
                            "name":     f"{d['homeTeamName']} vs {d['awayTeamName']}",
                            "sport":    "football",
                            "home":     d["homeTeamName"],
                            "away":     d["awayTeamName"],
                            "status":   d.get("matchStatus", "Not start"),
                            "event_id": d["eventId"],
                            "is_live":  d.get("matchStatus") not in ("Not start", ""),
                            "urls": {
                                "sportybet": f"https://www.sportybet.com/gh/m/sport/football/{d['eventId']}",
                                "1win":      build_1win_url(d["homeTeamName"], d["awayTeamName"]),
                            }
                        })
                    except Exception as e:
                        log.debug(f"Event parse error: {e}")

        _match_cache = matches
        _cache_time  = time.time()
        log.info(f"Match cache updated: {len(matches)} matches ready")

    except Exception as e:
        log.error(f"Match discovery error: {e}")

    return matches


def build_1win_url(home: str, away: str) -> str:
    query = f"{home} {away}".lower()
    query = re.sub(r'[^a-z0-9 ]', '', query)
    query = re.sub(r'\s+', '-', query.strip())
    return f"https://1wgcmt.com/betting/match/sport/{query}"


# ── Engine 1: Scrapers ─────────────────────────────────────────────────────

async def scrape_sportybet(event_id: str) -> dict:
    try:
        s = SportyBetScraper()
        url = f"https://www.sportybet.com/gh/m/sport/football/{event_id}"
        return await s.get_odds(url)
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


def normalise_odds_dict(raw: dict, platform: str) -> Dict[str, Dict[str, Tuple[str, float]]]:
    result = {}
    for key, sides in raw.items():
        norm_key = normalise_market(key)
        entry = {}
        for side, odds in sides.items():
            entry[side] = (platform, odds)
        if len(entry) == 2:
            result[norm_key] = entry
    return result


# ── Engine 1: Scan cycle ───────────────────────────────────────────────────

async def scan_cycle() -> Optional[ArbOpportunity]:
    matches = await fetch_sportybet_matches()
    if not matches:
        log.warning("No matches found")
        return None

    live     = [m for m in matches if m["is_live"]]
    prematch = [m for m in matches if not m["is_live"]]
    ordered  = live + prematch[:20]

    log.info(f"[E1] Scanning {len(live)} live + {min(len(prematch), 20)} pre-match")

    candidates = []

    for match in ordered:
        sporty_odds = await scrape_sportybet(match["event_id"])
        onewin_odds = await scrape_1win(match["urls"]["1win"])

        if not sporty_odds or not onewin_odds:
            continue

        norm_sporty = normalise_odds_dict(sporty_odds, BOOKMAKER_FRIEND)
        norm_onewin = normalise_odds_dict(onewin_odds, BOOKMAKER_YOU)

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

        result = engine.scan_all(
            sport=match.get("sport", "football"),
            match_name=match["name"],
            markets=merged,
            is_live=match["is_live"],
            minutes_to_kickoff=20,
        )

        if result.opportunity:
            candidates.append(result.opportunity)

    if not candidates:
        return None

    candidates.sort(key=lambda a: a.priority_score, reverse=True)
    return candidates[0]


# ── Engine 1: Alert handler ────────────────────────────────────────────────

async def handle_arb(arb: ArbOpportunity) -> None:
    if arb.platform_over == BOOKMAKER_YOU:
        odds_you, odds_friend     = arb.odds_over, arb.odds_under
        stake_you, stake_friend   = arb.stake_over, arb.stake_under
        bet_you    = f"{arb.market_display} (Over/Home)"
        bet_friend = f"{arb.market_display} (Under/Away)"
    else:
        odds_you, odds_friend     = arb.odds_under, arb.odds_over
        stake_you, stake_friend   = arb.stake_under, arb.stake_over
        bet_you    = f"{arb.market_display} (Under/Away)"
        bet_friend = f"{arb.market_display} (Over/Home)"

    ok = await send_arb_alert(
        arb_id=arb.arb_id,
        match_name=arb.match_name,
        sport=arb.sport,
        market_display=arb.market_display,
        kickoff_str=f"in {arb.minutes_to_kickoff:.0f} min" if not arb.is_live else "LIVE",
        score=arb.score_breakdown,
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
        if cmd_state:
            from commands import PendingArb
            cmd_state.current_arb = PendingArb(arb.arb_id, {
                "sport":        arb.sport,
                "market_key":   arb.market_key,
                "stake_you":    stake_you,
                "stake_friend": stake_friend,
            })
        log.info(f"[E1] Arb #{arb.arb_id} alerted.")


# ── Engine 2: Telegram callback ────────────────────────────────────────────

async def send_aggregator_alert(text: str) -> None:
    """Send a plain-text Telegram message for aggregator arbs."""
    from config import TELEGRAM_TOKEN, CHAT_ID
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id":    CHAT_ID,
                    "text":       text,
                    "parse_mode": "Markdown",
                },
            )
        log.info("[AGG] Alert sent to Telegram.")
    except Exception as e:
        log.error(f"[AGG] Telegram send error: {e}")


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 60)
    log.info("  SPORTS ARBITRAGE BOT — DUAL ENGINE MODE")
    log.info(f"  Engine 1: SportyBet direct API")
    log.info(f"  Engine 2: OddsPortal aggregator (OddsHarvester)")
    log.info(f"  Platforms: {BOOKMAKER_YOU} + {BOOKMAKER_FRIEND}")
    log.info(f"  Active hours: {ACTIVE_START}:00-{ACTIVE_END}:00 UTC")
    log.info("=" * 60)

    # Start Telegram command handler
    async def start_telegram():
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        log.info("Telegram command handler started.")
        while True:
            await asyncio.sleep(3600)

    asyncio.create_task(start_telegram())

    from commands import arb_timeout_watchdog
    asyncio.create_task(arb_timeout_watchdog())

    # ── Engine 2: start aggregator as background task ──────────────────
    asyncio.create_task(
        aggregator_loop(
            balancer=balancer,
            alert_callback=send_aggregator_alert,
            seen_arbs=seen_arbs,
        )
    )
    log.info("Engine 2 (aggregator) task created.")

    # ── Engine 1: main scan loop ───────────────────────────────────────
    scan_count  = 0
    last_status = time.time()
    log.info("Engine 1 scan loop started.")

    while True:
        try:
            if is_cool_off_day():
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            if not is_active_hours():
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            if cmd_state and not cmd_state.can_alert:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            arb = await scan_cycle()
            scan_count += 1

            if arb:
                # Deduplicate against Engine 2 hits
                key = f"e1|{arb.match_name}|{arb.market_key}"
                if key not in seen_arbs:
                    seen_arbs.add(key)
                    await handle_arb(arb)

            if time.time() - last_status > 300:
                log.info(
                    f"[E1] Heartbeat | Scans: {scan_count} | "
                    f"Bal: {balancer.get_balance(BOOKMAKER_YOU):.2f} / "
                    f"{balancer.get_balance(BOOKMAKER_FRIEND):.2f}"
                )
                last_status = time.time()

        except Exception as e:
            log.error(f"[E1] Scan loop error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


shutdown_event = asyncio.Event()

def _handle_shutdown(signum, frame):
    log.info("Shutdown signal received.")
    shutdown_event.set()

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
