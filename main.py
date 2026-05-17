"""
main.py – Sports Arbitrage Bot (Dual Engine)

Engine 1: Direct API scan (SportyBet)
Engine 2: Aggregator scan

Updated:
- deterministic bookmaker-aware market merging
- canonical side mapping
- best-odds preservation
- scalable multi-bookmaker structure
"""

import asyncio
import logging
import re
import signal
import time

from datetime import timezone
from typing import Dict, List, Optional, Tuple

import httpx

from scraper import SportyBetScraper, OneWinScraper
from matcher import normalise_market
from balancer import Balancer
from arb_engine import ArbEngine, ArbOpportunity

from alerter import (
    send_arb_alert,
    is_active_hours,
    is_cool_off_day,
)

from commands import setup_bot, state as cmd_state

from config import (
    BOOKMAKER_YOU,
    BOOKMAKER_FRIEND,
    ACTIVE_START,
    ACTIVE_END,
    SCAN_INTERVAL,
)

from aggregator_engine import aggregator_loop


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("main")


SPORTYBET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://www.sportybet.com/gh/",
    "Accept": "application/json",
}


# ────────────────────────────────────────────────────────────────────────
# Global state
# ────────────────────────────────────────────────────────────────────────

balancer = Balancer()
engine = ArbEngine(balancer)
telegram_app = setup_bot(balancer)

seen_arbs: set = set()

_match_cache: List[dict] = []
_cache_time: float = 0

CACHE_TTL = 300


# ────────────────────────────────────────────────────────────────────────
# Side mapping
# ────────────────────────────────────────────────────────────────────────

SIDE_MAP = {
    # totals
    "o": "over",
    "u": "under",
    "over": "over",
    "under": "under",

    # winners
    "1": "home",
    "2": "away",
    "x": "draw",

    "home": "home",
    "away": "away",
    "draw": "draw",

    # yes/no
    "yes": "yes",
    "no": "no",

    # gg/ng
    "gg": "yes",
    "ng": "no",
}


# ────────────────────────────────────────────────────────────────────────
# Match discovery
# ────────────────────────────────────────────────────────────────────────

async def fetch_sportybet_matches() -> List[dict]:

    global _match_cache, _cache_time

    if (
        time.time() - _cache_time < CACHE_TTL
        and _match_cache
    ):
        return _match_cache

    matches = []

    try:

        async with httpx.AsyncClient(
            headers=SPORTYBET_HEADERS,
            timeout=15,
        ) as client:

            r = await client.get(
                "https://www.sportybet.com/gh/m/sport/football"
            )

            ids = list(
                set(
                    re.findall(
                        r"sr:match:(\d+)",
                        r.text,
                    )
                )
            )

            ids = [
                i for i in ids
                if not i.startswith("111111")
            ]

            log.info(
                f"Discovered {len(ids)} matches"
            )

            for i in range(
                0,
                min(len(ids), 100),
                20,
            ):

                batch = ids[i:i + 20]

                tasks = [
                    client.get(
                        (
                            "https://www.sportybet.com/"
                            "api/gh/factsCenter/event"
                            f"?eventId=sr%3Amatch%3A{mid}"
                            "&productId=3"
                        )
                    )
                    for mid in batch
                ]

                responses = await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

                for resp in responses:

                    try:

                        if isinstance(resp, Exception):
                            continue

                        data = resp.json()

                        if data.get("bizCode") != 10000:
                            continue

                        d = data["data"]

                        matches.append({
                            "name": (
                                f"{d['homeTeamName']} "
                                f"vs "
                                f"{d['awayTeamName']}"
                            ),

                            "sport": "football",

                            "home": d["homeTeamName"],
                            "away": d["awayTeamName"],

                            "event_id": d["eventId"],

                            "is_live": (
                                d.get("matchStatus")
                                not in ("Not start", "")
                            ),

                            "urls": {
                                "sportybet": (
                                    "https://www.sportybet.com/"
                                    f"gh/m/sport/football/{d['eventId']}"
                                ),

                                "1win": build_1win_url(
                                    d["homeTeamName"],
                                    d["awayTeamName"],
                                ),
                            },
                        })

                    except Exception as e:
                        log.debug(f"Parse error: {e}")

        _match_cache = matches
        _cache_time = time.time()

    except Exception as e:
        log.error(f"Discovery error: {e}")

    return matches


def build_1win_url(
    home: str,
    away: str,
) -> str:

    query = f"{home} {away}".lower()

    query = re.sub(
        r"[^a-z0-9 ]",
        "",
        query,
    )

    query = re.sub(
        r"\s+",
        "-",
        query.strip(),
    )

    return (
        "https://1wgcmt.com/betting/match/sport/"
        f"{query}"
    )


# ────────────────────────────────────────────────────────────────────────
# Scrapers
# ────────────────────────────────────────────────────────────────────────

async def scrape_sportybet(
    event_id: str,
) -> dict:

    try:

        s = SportyBetScraper()

        url = (
            "https://www.sportybet.com/"
            f"gh/m/sport/football/{event_id}"
        )

        return await s.get_odds(url)

    except Exception as e:
        log.error(f"SportyBet scrape error: {e}")
        return {}


async def scrape_1win(
    match_url: str,
) -> dict:

    try:

        s = OneWinScraper()

        return await s.get_odds(match_url)

    except Exception as e:
        log.error(f"1win scrape error: {e}")
        return {}


# ────────────────────────────────────────────────────────────────────────
# Odds normalisation
# ────────────────────────────────────────────────────────────────────────

def normalise_odds_dict(
    raw: dict,
    platform: str,
) -> Dict[str, Dict[str, Tuple[str, float]]]:

    result: Dict[
        str,
        Dict[str, Tuple[str, float]]
    ] = {}

    for market_name, sides in raw.items():

        market_key = normalise_market(
            market_name,
            bookmaker=platform,
        )

        if market_key not in result:
            result[market_key] = {}

        for raw_side, raw_odds in sides.items():

            try:
                odds = float(raw_odds)
            except Exception:
                continue

            if odds <= 1.0:
                continue

            side = raw_side.strip().lower()

            side = SIDE_MAP.get(
                side,
                side,
            )

            existing = result[
                market_key
            ].get(side)

            if existing is None:

                result[market_key][side] = (
                    platform,
                    odds,
                )

            else:

                _, existing_odds = existing

                if odds > existing_odds:

                    result[market_key][side] = (
                        platform,
                        odds,
                    )

    return result


# ────────────────────────────────────────────────────────────────────────
# Market merge engine
# ────────────────────────────────────────────────────────────────────────

def merge_market_sources(
    *sources: Dict[
        str,
        Dict[str, Tuple[str, float]]
    ],
) -> Dict[
    str,
    Dict[str, Tuple[str, float]]
]:

    merged = {}

    all_markets = set()

    for src in sources:
        all_markets.update(src.keys())

    for market_key in all_markets:

        merged[market_key] = {}

        for src in sources:

            if market_key not in src:
                continue

            for side, data in src[
                market_key
            ].items():

                platform, odds = data

                existing = merged[
                    market_key
                ].get(side)

                if existing is None:

                    merged[market_key][side] = (
                        platform,
                        odds,
                    )

                else:

                    _, existing_odds = existing

                    if odds > existing_odds:

                        merged[market_key][side] = (
                            platform,
                            odds,
                        )

        if len(merged[market_key]) < 2:
            del merged[market_key]

    return merged


# ────────────────────────────────────────────────────────────────────────
# Scan cycle
# ────────────────────────────────────────────────────────────────────────

async def scan_cycle() -> Optional[ArbOpportunity]:

    matches = await fetch_sportybet_matches()

    if not matches:
        return None

    candidates = []

    for match in matches[:20]:

        sporty_odds = await scrape_sportybet(
            match["event_id"]
        )

        onewin_odds = await scrape_1win(
            match["urls"]["1win"]
        )

        if not sporty_odds:
            continue

        if not onewin_odds:
            continue

        norm_sporty = normalise_odds_dict(
            sporty_odds,
            BOOKMAKER_FRIEND,
        )

        norm_onewin = normalise_odds_dict(
            onewin_odds,
            BOOKMAKER_YOU,
        )

        merged = merge_market_sources(
            norm_sporty,
            norm_onewin,
        )

        if not merged:
            continue

        result = engine.scan_all(
            sport=match["sport"],
            match_name=match["name"],
            markets=merged,
            is_live=match["is_live"],
            minutes_to_kickoff=20,
        )

        if result.opportunity:
            candidates.append(
                result.opportunity
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda a: a.priority_score,
        reverse=True,
    )

    return candidates[0]


# ────────────────────────────────────────────────────────────────────────
# Alert handler
# ────────────────────────────────────────────────────────────────────────

async def handle_arb(
    arb: ArbOpportunity,
) -> None:

    await send_arb_alert(
        arb_id=arb.arb_id,
        match_name=arb.match_name,
        sport=arb.sport,
        market_display=arb.market_display,
        kickoff_str=(
            "LIVE"
            if arb.is_live
            else f"in {arb.minutes_to_kickoff:.0f} min"
        ),
        score=arb.score_breakdown,
        odds_you=arb.odds_over,
        odds_friend=arb.odds_under,
        stake_you=arb.stake_over,
        stake_friend=arb.stake_under,
        margin_pct=arb.margin_pct,
        speed=arb.speed,
        stability=arb.stability,
        is_live=arb.is_live,
    )

    log.info(
        f"Arb #{arb.arb_id} alerted."
    )


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────

async def main():

    log.info("=" * 60)
    log.info("SPORTS ARBITRAGE BOT")
    log.info("=" * 60)

    while True:

        try:

            if is_cool_off_day():
                await asyncio.sleep(
                    SCAN_INTERVAL
                )
                continue

            if not is_active_hours():
                await asyncio.sleep(
                    SCAN_INTERVAL
                )
                continue

            arb = await scan_cycle()

            if arb:

                key = (
                    f"{arb.match_name}:"
                    f"{arb.market_key}"
                )

                if key not in seen_arbs:

                    seen_arbs.add(key)

                    await handle_arb(arb)

        except Exception as e:
            log.error(f"Main loop error: {e}")

        await asyncio.sleep(
            SCAN_INTERVAL
        )


shutdown_event = asyncio.Event()


def _handle_shutdown(signum, frame):

    log.info("Shutdown signal received.")

    shutdown_event.set()


signal.signal(
    signal.SIGTERM,
    _handle_shutdown,
)

signal.signal(
    signal.SIGINT,
    _handle_shutdown,
)


if __name__ == "__main__":
    asyncio.run(main())
