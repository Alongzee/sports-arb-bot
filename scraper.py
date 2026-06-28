"""
scraper.py – Odds scrapers for Tier 1 bookmakers.
SportyBet uses direct API.
Betway uses direct REST API discovery + pricing endpoints.
"""

import asyncio
import re
import json
import httpx
from matcher import normalise_market


class BaseScraper:
    """Shared utilities."""

    def _parse_odds(self, text: str) -> float:
        text = text.strip()
        try:
            return float(text)
        except ValueError:
            if "/" in text:
                parts = text.split("/")
                if len(parts) == 2:
                    return float(parts[0]) / float(parts[1]) + 1
        return None


# ─── SportyBet (API-based) ────────────────────────────────────────────────

class SportyBetScraper:
    API_BASE = "https://www.sportybet.com/api/gh/factsCenter/event"
    MATCH_LIST_URL = "https://www.sportybet.com/gh/m/sport/football"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sportybet.com/gh/",
        "Origin": "https://www.sportybet.com",
    }

    async def get_events(self, sport: str = "football") -> list:
        """Fetch list of upcoming football matches from SportyBet."""
        events = []
        try:
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=15) as client:
                # Fetch match listing page and extract event IDs
                r = await client.get(self.MATCH_LIST_URL)
                event_ids = list(set(re.findall(r"sr:match:(\d+)", r.text)))
                event_ids = [i for i in event_ids if not i.startswith("111111")]
                
                # Batch fetch event details (20 at a time)
                for i in range(0, min(len(event_ids), 100), 20):
                    batch = event_ids[i:i + 20]
                    tasks = [
                        client.get(
                            f"{self.API_BASE}?eventId=sr%3Amatch%3A{mid}&productId=3"
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
                            events.append({
                                "eventId": d.get("eventId"),
                                "name": f"{d.get('homeTeamName')} vs {d.get('awayTeamName')}",
                                "homeTeam": d.get("homeTeamName"),
                                "awayTeam": d.get("awayTeamName"),
                                "expectedStartEpoch": d.get("kickOffTime", 0),
                                "sport": sport,
                            })
                        except Exception:
                            pass
        except Exception as e:
            print(f"SportyBet discovery error: {e}")
        return events

    def _extract_event_id(self, match_url: str) -> str | None:
        m = re.search(r'(sr:match:\d+)', match_url)
        return m.group(1) if m else None

    def _parse_odds(self, text: str) -> float | None:
        try:
            return float(str(text).strip())
        except (ValueError, TypeError):
            return None

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        event_id = self._extract_event_id(match_url)
        if not event_id:
            print(f"SportyBet error: cannot extract event ID from {match_url}")
            return odds_data

        try:
            url = f"{self.API_BASE}?eventId={event_id.replace(':', '%3A')}&productId=3"
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()

            if data.get("bizCode") != 10000:
                print(f"SportyBet API error: {data.get('message')}")
                return odds_data

            for market in data["data"].get("markets", []):
                if not market.get("status") == 0:
                    continue
                name      = market.get("name", "")
                specifier = market.get("specifier", "")
                outcomes  = market.get("outcomes", [])

                # Over/Under
                if name == "Over/Under":
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        parts = desc.split()
                        if len(parts) == 2:
                            direction = parts[0].lower()
                            line      = parts[1]
                            key = normalise_market(f"over_under_{line}")
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = val

                # Asian Handicap
                elif name == "Asian Handicap":
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        line = specifier.replace("hcp=", "").replace(":", "/")
                        side = desc.lower()
                        key  = normalise_market(f"asian_handicap_{line}")
                        if key not in odds_data:
                            odds_data[key] = {}
                        if side in ("home", "away"):
                            odds_data[key][side] = val

                # Both Teams to Score
                elif name == "Both Teams to Score":
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        key = normalise_market("both_to_score")
                        if key not in odds_data:
                            odds_data[key] = {}
                        odds_data[key][desc.lower()] = val

                # Draw No Bet
                elif name == "Draw No Bet":
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        key = normalise_market("draw_no_bet")
                        if key not in odds_data:
                            odds_data[key] = {}
                        odds_data[key][desc.lower()] = val

                # 10-min Over/Under
                elif "Total Goals from 1 to" in name:
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        parts = desc.split()
                        if len(parts) == 2:
                            direction = parts[0].lower()
                            line      = parts[1]
                            key = normalise_market(f"over_under_10min_{line}")
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = val

                # Corners Over/Under
                elif "Corners" in name and "Over/Under" in name:
                    for o in outcomes:
                        if o.get("isActive") != 1:
                            continue
                        desc = o.get("desc", "")
                        val  = self._parse_odds(o.get("odds"))
                        if not val:
                            continue
                        parts = desc.split()
                        if len(parts) == 2:
                            direction = parts[0].lower()
                            line      = parts[1]
                            key = normalise_market(f"corners_over_under_{line}")
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = val

        except Exception as e:
            print(f"SportyBet error: {e}")

        return odds_data


# ─── Betway (Direct REST API) ────────────────────────────────────────────

class BetwayScraper:
    """
    Betway REST API scraper.
    
    Two-step process:
    1. Discovery: GET /BetBook/Upcoming/ → list all upcoming events
    2. Pricing: GET /MarketGroupings/MarketGroupNamesAndMarketsForEvent → odds per event
    """

    DISCOVERY_BASE = "https://www.betway.com.gh/sportsapi/br/v1/BetBook/Upcoming/"
    PRICING_BASE = "https://www.betway.com.gh/sportsapi/br/v1/MarketGroupings/MarketGroupNamesAndMarketsForEvent"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.betway.com.gh/",
    }

    async def get_events(self, sport_id: str = "soccer", skip: int = 0, take: int = 20) -> list:
        """Fetch list of upcoming events for a sport."""
        events = []
        try:
            params = {
                "countryCode": "GH",
                "sportId": sport_id,
                "Skip": skip,
                "Take": take,
                "cultureCode": "en-US",
                "isEsport": False,
                "boostedOnly": False,
                "marketTypes": "[Win/Draw/Win]",
            }
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=10) as client:
                r = await client.get(self.DISCOVERY_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                for e in data.get("events", []):
                    events.append({
                        "eventId": e.get("eventId"),
                        "name": e.get("name", ""),
                        "homeTeam": e.get("homeTeam", ""),
                        "awayTeam": e.get("awayTeam", ""),
                        "expectedStartEpoch": e.get("expectedStartEpoch", 0),
                        "sport": sport_id,
                    })
        except Exception as e:
            print(f"Betway discovery error: {e}")
        return events

    async def get_odds(self, event_id) -> dict:
        """Fetch odds for a specific event (accepts int or string)."""
        odds_data = {}
        try:
            # Handle both int and string event IDs
            event_id = int(event_id) if isinstance(event_id, str) else event_id
            
            params = {
                "eventId": event_id,
                "marketGroupId": " ",
                "countryCode": "GH",
                "cultureCode": "en-US",
                "skip": 0,
                "take": 20,
                "isBuildABetOnly": False,
                "searchQuery": "",
            }
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=10) as client:
                r = await client.get(self.PRICING_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                odds_data = self._parse_response(data)
        except Exception as e:
            print(f"Betway pricing error for event {event_id}: {e}")
        return odds_data

    def _parse_response(self, data: dict) -> dict:
        """Parse Betway API response into normalized odds dict."""
        odds_data = {}
        try:
            markets_in_group = data.get("marketsInGroup", [])
            outcomes = data.get("outcomes", [])
            prices = data.get("prices", [])

            # Build a map: outcomeId → price
            price_map = {}
            for price in prices:
                outcome_id = price.get("outcomeId")
                odds = price.get("decimalOdds", price.get("odds"))
                if outcome_id and odds:
                    try:
                        price_map[outcome_id] = float(odds)
                    except (TypeError, ValueError):
                        pass

            # Parse markets and outcomes
            for market in markets_in_group:
                market_name = market.get("marketName", market.get("name", ""))
                market_key = normalise_market(market_name)
                
                parsed_sides = {}
                for outcome in outcomes:
                    if outcome.get("marketId") != market.get("marketId"):
                        continue
                    outcome_id = outcome.get("outcomeId")
                    outcome_name = outcome.get("outcomeName", outcome.get("name", "")).lower()
                    
                    if outcome_id not in price_map:
                        continue
                    odds = price_map[outcome_id]
                    
                    # Map outcome name to canonical side
                    side = self._map_side(outcome_name)
                    if side:
                        parsed_sides[side] = odds

                # Only add market if we have at least 2 sides
                if len(parsed_sides) >= 2:
                    odds_data[market_key] = parsed_sides

        except Exception as e:
            print(f"Betway parse error: {e}")

        return odds_data

    def _map_side(self, outcome_name: str) -> str | None:
        """Map Betway outcome names to canonical sides."""
        name = outcome_name.lower().strip()
        
        # Totals
        if "over" in name or name in ("o",):
            return "over"
        if "under" in name or name in ("u",):
            return "under"
        
        # Winners
        if any(x in name for x in ("home", "1", "yes")):
            return "home"
        if any(x in name for x in ("away", "2", "no")):
            return "away"
        if "draw" in name or name in ("x",):
            return "draw"
        
        return None


# ─── Scraper factory ──────────────────────────────────────────────────────

SCRAPER_MAP = {
    "sportybet": SportyBetScraper,
    "betway": BetwayScraper,
}

def get_scraper(platform: str):
    scraper_class = SCRAPER_MAP.get(platform.lower())
    if not scraper_class:
        raise ValueError(f"No scraper for platform: {platform}")
    return scraper_class()
