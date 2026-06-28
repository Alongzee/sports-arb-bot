"""
scraper.py – Odds scrapers for Tier 1 bookmakers.
SportyBet uses direct API.
Betway uses direct REST API discovery endpoint (unified: events + markets + outcomes + prices).
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
    
    Single unified endpoint: GET /BetBook/Upcoming/
    Returns all data in one response: events, markets, outcomes, prices
    (linked by eventId, marketId, outcomeId)
    """

    DISCOVERY_BASE = "https://www.betway.com.gh/sportsapi/br/v1/BetBook/Upcoming/"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.betway.com.gh/",
    }

    async def get_events(self, sport_id: str = "soccer", skip: int = 0, take: int = 100) -> list:
        """Fetch list of upcoming events for a sport with odds."""
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
                "marketTypes": "[Win/Draw/Win]",  # Literal string, NOT array
            }
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=10) as client:
                r = await client.get(self.DISCOVERY_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                
                # Build lookup tables: eventId/marketId/outcomeId → data
                markets_by_event = {}
                outcomes_by_market = {}
                prices_by_outcome = {}
                
                for market in data.get("markets", []):
                    markets_by_event[market["eventId"]] = market["marketId"]
                
                for outcome in data.get("outcomes", []):
                    market_id = outcome["marketId"]
                    if market_id not in outcomes_by_market:
                        outcomes_by_market[market_id] = []
                    outcomes_by_market[market_id].append(outcome)
                
                for price in data.get("prices", []):
                    prices_by_outcome[price["outcomeId"]] = price["priceDecimal"]
                
                # Build event records with odds
                for event in data.get("events", []):
                    # Skip finished/inactive events
                    if event.get("isFinished") or not event.get("isActive"):
                        continue
                    
                    event_id = event["eventId"]
                    market_id = markets_by_event.get(event_id)
                    if not market_id:
                        continue
                    
                    outcomes = outcomes_by_market.get(market_id, [])
                    if len(outcomes) < 3:
                        continue
                    
                    # Extract odds: map outcome names to prices
                    home_team = event["homeTeam"]
                    away_team = event["awayTeam"]
                    
                    home_odd = None
                    draw_odd = None
                    away_odd = None
                    
                    for outcome in outcomes:
                        outcome_id = outcome["outcomeId"]
                        price = prices_by_outcome.get(outcome_id)
                        if not price:
                            continue
                        
                        outcome_name = outcome["name"]
                        if outcome_name == "Draw":
                            draw_odd = price
                        elif outcome_name == home_team:
                            home_odd = price
                        elif outcome_name == away_team:
                            away_odd = price
                    
                    # Only add if all 3 odds present
                    if home_odd and draw_odd and away_odd:
                        events.append({
                            "eventId": event_id,
                            "name": event.get("name", f"{home_team} vs {away_team}"),
                            "homeTeam": home_team,
                            "awayTeam": away_team,
                            "expectedStartEpoch": event.get("expectedStartEpoch", 0),
                            "sport": sport_id,
                            "odds": {
                                "home": home_odd,
                                "draw": draw_odd,
                                "away": away_odd,
                            }
                        })
                        
        except Exception as e:
            print(f"Betway discovery error: {e}")
        
        return events

    async def get_odds(self, event_id) -> dict:
        """
        Fetch odds for a specific event.
        
        Note: For efficiency, use get_events() which fetches all events + odds in one call.
        This method exists for compatibility but re-fetches all events to get one.
        """
        odds_data = {}
        try:
            # Fetch all events (inefficient but maintains API compatibility)
            events = await self.get_events()
            
            # Find the event by ID
            for event in events:
                if event["eventId"] == int(event_id) if isinstance(event_id, str) else event_id:
                    if "odds" in event:
                        # Convert to normalized format
                        odds_data["1x2"] = event["odds"]
                    break
                    
        except Exception as e:
            print(f"Betway get_odds error for event {event_id}: {e}")
        
        return odds_data


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
