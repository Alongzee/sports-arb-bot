"""
scraper.py – Playwright-based odds scrapers for Tier 1 bookmakers.
SportyBet uses direct API. 1win intercepts internal API with Ghana fingerprint.
Betway uses direct REST API discovery + pricing endpoints.
"""

import asyncio
import re
import json
import httpx
from playwright.async_api import async_playwright
from matcher import normalise_market


class BaseScraper:
    """Shared Playwright setup."""

    async def _get_page(self, url: str):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        return pw, browser, page

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


# ─── 1win (API interception with Ghana fingerprint) ───────────────────────

class OneWinScraper:
    """
    Intercepts 1win's internal betting API by loading the page with a
    Ghana-spoofed Playwright browser. Captures JSON responses containing
    odds data instead of parsing DOM text.
    """

    ONEWIN_DOMAINS = [
        "1wgcmt.com",
        "1wrurq.com",
        "1wkxhq.com",
        "1wzvmo.com",
    ]

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        captured_responses = []

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 12; Infinix X6816) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
                locale="en-GH",
                timezone_id="Africa/Accra",
                geolocation={"latitude": 5.6037, "longitude": -0.1870},
                permissions=["geolocation"],
                extra_http_headers={
                    "Accept-Language": "en-GH,en;q=0.9",
                    "X-Forwarded-For": "154.160.5.1",
                    "CF-IPCountry": "GH",
                },
                viewport={"width": 390, "height": 844},
            )

            # Hide automation fingerprint
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-GH', 'en']});
            """)

            page = await context.new_page()

            # Intercept API responses
            async def handle_response(response):
                try:
                    if "application/json" in response.headers.get("content-type", ""):
                        url = response.url
                        if any(kw in url for kw in ["betting", "events", "odds", "markets", "sport"]):
                            body = await response.json()
                            captured_responses.append((url, body))
                except Exception:
                    pass

            page.on("response", handle_response)

            await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(8000)

            # Parse captured API responses
            for url, body in captured_responses:
                odds_data.update(self._parse_1win_response(body))

            # Fallback: DOM text parsing if API interception got nothing
            if not odds_data:
                odds_data = self._parse_dom_text(await page.evaluate("document.body.innerText"))

        except Exception as e:
            print(f"1win error: {e}")
        finally:
            await browser.close()
            await pw.stop()

        return odds_data

    def _parse_1win_response(self, body: dict) -> dict:
        """Parse 1win API JSON response for odds data."""
        odds_data = {}
        try:
            # Handle different response structures
            events = []
            if isinstance(body, dict):
                if "data" in body:
                    data = body["data"]
                    if isinstance(data, list):
                        events = data
                    elif isinstance(data, dict):
                        events = data.get("events", data.get("items", [data]))
                elif "events" in body:
                    events = body["events"]
                elif "markets" in body:
                    events = [body]

            for event in events:
                if not isinstance(event, dict):
                    continue
                markets = event.get("markets", event.get("market", []))
                if isinstance(markets, dict):
                    markets = [markets]
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    name = market.get("name", market.get("market_name", ""))
                    outcomes = market.get("outcomes", market.get("selections", []))
                    if not name or not outcomes:
                        continue

                    key = normalise_market(name)
                    parsed = {}
                    for outcome in outcomes:
                        if not isinstance(outcome, dict):
                            continue
                        o_name = outcome.get("name", outcome.get("title", "")).lower()
                        o_odds = outcome.get("odds", outcome.get("price", outcome.get("value")))
                        try:
                            o_odds = float(o_odds)
                        except (TypeError, ValueError):
                            continue
                        if o_odds <= 1.0:
                            continue
                        if any(x in o_name for x in ["over", "home", "yes", "1"]):
                            parsed["over"] = o_odds
                        elif any(x in o_name for x in ["under", "away", "no", "2"]):
                            parsed["under"] = o_odds

                    if len(parsed) == 2:
                        odds_data[key] = parsed

        except Exception as e:
            print(f"1win parse error: {e}")

        return odds_data

    def _parse_dom_text(self, text: str) -> dict:
        """Parse 1win page text - collects all Over/Under and pairs them."""
        odds_data = {}
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        
        # First pass: collect all individual Over/Under entries
        ou_collected = {}  # {line_val: {over: x, under: y}}

        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip AI tips and descriptive lines
            if (line.startswith("AI tips") or
                line.startswith("In ") or
                line.startswith("of the last") or
                len(line) > 80 or
                re.match(r"^[A-Z][a-z]+ (has|have|scored|conceded|didn|didn't|lost|won)", line)):
                i += 1
                continue

            # Total (Over/Under): "Total" -> "Over X.X" or "Under X.X" -> odds
            if line == "Total" and i + 2 < len(lines):
                dir_line = lines[i + 1]
                odds_line = lines[i + 2]
                m = re.match(r"(Over|Under)\s+(\d+\.?\d*)", dir_line)
                o = re.match(r"^(\d+\.\d+)$", odds_line)
                if m and o:
                    direction = m.group(1).lower()
                    val = m.group(2)
                    if val not in ou_collected:
                        ou_collected[val] = {}
                    ou_collected[val][direction] = float(o.group(1))
                    i += 3
                    continue

            # Handicap
            if line in ("Handicap", "Asian Handicap") and i + 3 < len(lines):
                val_line = lines[i + 1]
                home_line = lines[i + 2]
                away_line = lines[i + 3]
                vm = re.match(r"([+-]?\d+\.?\d*)", val_line)
                hm = re.match(r"^(\d+\.\d+)$", home_line)
                am = re.match(r"^(\d+\.\d+)$", away_line)
                if vm and hm and am:
                    key = normalise_market(f"asian_handicap_{vm.group(1)}")
                    odds_data[key] = {"home": float(hm.group(1)), "away": float(am.group(1))}
                    i += 4
                    continue

            # Both teams to score
            if "both teams to score" in line.lower() and i + 2 < len(lines):
                side = lines[i + 1].lower()
                om = re.match(r"^(\d+\.\d+)$", lines[i + 2])
                if om and side in ("yes", "no"):
                    key = normalise_market("both_to_score")
                    if key not in odds_data:
                        odds_data[key] = {}
                    odds_data[key][side] = float(om.group(1))
                    i += 3
                    continue

            i += 1

        # Pair collected Over/Under entries (from AI tips section)
        for val, sides in ou_collected.items():
            if len(sides) == 2:
                key = normalise_market(f"over_under_{val}")
                odds_data[key] = sides

        # Second pass: parse the full market table (appears after navigation tabs)
        # Format: "Under 0.5\n10.7\nOver 0.5\n1.43\nUnder 1.5\n..."
        # Find the market table section
        full_text = "\n".join(lines)
        
        # Parse consecutive Over/Under pairs from market table
        table_pattern = re.finditer(
            r"(Over|Under)\s+(\d+\.?\d*)\n(\d+\.\d+)\n(Over|Under)\s+\2\n(\d+\.\d+)",
            full_text
        )
        for m in table_pattern:
            dir1, val, odds1, dir2, odds2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            key = normalise_market(f"over_under_{val}")
            odds_data[key] = {
                dir1.lower(): float(odds1),
                dir2.lower(): float(odds2)
            }

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
                "marketTypes": "[\"Win/Draw/Win\"]",
            }
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=10) as client:
                r = await client.get(self.DISCOVERY_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                events = data.get("events", [])
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
    "1win": OneWinScraper,
    "betway": BetwayScraper,
}

def get_scraper(platform: str):
    scraper_class = SCRAPER_MAP.get(platform.lower())
    if not scraper_class:
        raise ValueError(f"No scraper for platform: {platform}")
    return scraper_class()


# ── Debug utility ─────────────────────────────────────────────────────────

async def debug_1win_network(match_url: str):
    """Print all JSON responses for one minute."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()

    captured = []
    async def log_response(response):
        if "application/json" in response.headers.get("content-type", ""):
            url = response.url
            try:
                body = await response.json()
                captured.append((url, body))
            except:
                pass

    page.on("response", log_response)
    await page.goto(match_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(60000)

    print(f"Captured {len(captured)} JSON responses:")
    for url, body in captured[:10]:
        print(f"\n--- {url} ---")
        print(str(body)[:500])
    await browser.close()
    await pw.stop()
    
