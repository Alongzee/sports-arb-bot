"""
scraper.py – Playwright-based odds scrapers for Tier 1 bookmakers.
All five platforms implemented. Returns standardised odds dictionaries.
"""

import asyncio
import re
import httpx
from playwright.async_api import async_playwright


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


# ─── SportyBet (API‑based) ─────────────────────────────────────────────

class SportyBetScraper:
    API_BASE = "https://www.sportybet.com/api/gh/factsCenter/event"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sportybet.com/gh/",
        "Origin": "https://www.sportybet.com",
    }

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
                            key = f"over_under_{line}"
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = val

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
                        key  = f"asian_handicap_{line}"
                        if key not in odds_data:
                            odds_data[key] = {}
                        if side in ("home", "away"):
                            odds_data[key][side] = val

        except Exception as e:
            print(f"SportyBet error: {e}")

        return odds_data


# ─── 1win (Network interception + text fallback) ────────────────────────

class OneWinScraper(BaseScraper):
    """
    1win scrapes via:
      1) Direct API call to common internal endpoints (if available)
      2) Playwright network interception – listens for JSON responses whose URL
         matches typical odds-data patterns.
      3) Text parsing on the fully‑loaded page (fallback).
    """

    BASE_URL = "https://1wgcmt.com"

    API_PATTERNS = [
        "/line/",
        "/event/",
        "/odds/",
        "/market/",
        "/sport/",
        "/betting/",
    ]

    async def _try_direct_api(self, match_url: str) -> dict:
        """Attempt to call known 1win API endpoints directly."""
        m = re.search(r'[-/](\d{5,})', match_url)
        if not m:
            return {}
        event_id = m.group(1)

        async with httpx.AsyncClient(timeout=10) as client:
            for pattern in self.API_PATTERNS:
                url = f"{self.BASE_URL}/api/v1/sport{pattern}{event_id}"
                try:
                    r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code == 200:
                        data = r.json()
                        if self._contains_odds_data(data):
                            return self._parse_captured_json(data)
                except Exception:
                    continue
        return {}

    async def _try_network_capture(self, page, match_url: str) -> dict:
        """Listens for JSON responses whose URL contains any of the API_PATTERNS."""
        captured_data = {"found": False, "json": None}

        async def handle_response(response):
            if captured_data["found"]:
                return
            try:
                if "application/json" not in response.headers.get("content-type", ""):
                    return
                url = response.url
                if not any(pattern in url for pattern in self.API_PATTERNS):
                    return
                body = await response.json()
                if self._contains_odds_data(body):
                    captured_data["found"] = True
                    captured_data["json"] = body
            except Exception:
                pass

        page.on("response", handle_response)
        await page.goto(match_url, wait_until="domcontentloaded")
        for _ in range(30):
            if captured_data["found"]:
                break
            await page.wait_for_timeout(1000)

        if captured_data["json"]:
            return self._parse_captured_json(captured_data["json"])
        return {}

    def _contains_odds_data(self, obj) -> bool:
        """Recursively search a JSON object for 3+ float values in the odds range."""
        floats = []

        def search(node):
            if len(floats) >= 3:
                return
            if isinstance(node, (int, float)):
                if 1.01 <= node <= 1000.0:
                    floats.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    search(v)
            elif isinstance(node, list):
                for v in node:
                    search(v)

        search(obj)
        return len(floats) >= 3

    def _parse_captured_json(self, json_data: dict) -> dict:
        """Parse a generic JSON that contains odds."""
        odds_data = {}
        self._extract_from_dict(json_data, odds_data)
        return odds_data

    def _extract_from_dict(self, obj, result: dict, prefix=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (int, float)) and 1.01 <= value <= 1000.0:
                    pass
                elif isinstance(value, dict):
                    self._extract_market(key, value, result)
                elif isinstance(value, list):
                    for item in value:
                        self._extract_from_dict(item, result, f"{prefix}{key}.")
                else:
                    self._extract_from_dict(value, result, f"{prefix}{key}.")
        elif isinstance(obj, list):
            for item in obj:
                self._extract_from_dict(item, result, prefix)

    def _extract_market(self, market_name: str, odds_dict: dict, result: dict):
        if not isinstance(odds_dict, dict):
            return
        over_val = odds_dict.get("Over") or odds_dict.get("over")
        under_val = odds_dict.get("Under") or odds_dict.get("under")
        if over_val and under_val:
            try:
                over = float(over_val) if not isinstance(over_val, float) else over_val
                under = float(under_val) if not isinstance(under_val, float) else under_val
                try:
                    float(market_name)
                    result[f"over_under_{market_name}"] = {"over": over, "under": under}
                except ValueError:
                    pass
            except (ValueError, TypeError):
                pass

        home_val = odds_dict.get("Home") or odds_dict.get("home")
        away_val = odds_dict.get("Away") or odds_dict.get("away")
        if home_val and away_val:
            try:
                home = float(home_val) if not isinstance(home_val, float) else home_val
                away = float(away_val) if not isinstance(away_val, float) else away_val
                result[f"asian_handicap_{market_name}"] = {"home": home, "away": away}
            except (ValueError, TypeError):
                pass

    async def _scrape_via_text(self, match_url: str, locale="en-US") -> dict:
        """Fallback: force locale and parse visible text."""
        odds_data = {}
        pw, browser, page = await self._get_page_with_locale(match_url, locale)
        try:
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(5000)

            text = await page.evaluate("document.body.innerText")
            over_under_regex = re.compile(
                r"Over\s+(\d+\.?\d*)\s+(\d+\.\d{2})\s+Under\s+\1\s+(\d+\.\d{2})"
            )
            for match in over_under_regex.finditer(text):
                line = match.group(1)
                over = float(match.group(2))
                under = float(match.group(3))
                odds_data[f"over_under_{line}"] = {"over": over, "under": under}

            asian_hcp_regex = re.compile(
                r"(?:Asian\s+)?Handicap\s+([+-]?\d+\.?\d*)\s+(\d+\.\d{2})\s+(\d+\.\d{2})"
            )
            for match in asian_hcp_regex.finditer(text):
                line = match.group(1)
                home = float(match.group(2))
                away = float(match.group(3))
                odds_data[f"asian_handicap_{line}"] = {"home": home, "away": away}
        except Exception as e:
            print(f"1win text fallback error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data

    async def _get_page_with_locale(self, url: str, locale: str):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            locale=locale,
            extra_http_headers={"Accept-Language": f"{locale},en;q=0.9"}
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return pw, browser, page

    async def get_odds(self, match_url: str) -> dict:
        # 1) Try direct API call first – fastest
        odds = await self._try_direct_api(match_url)
        if odds:
            return odds

        # 2) Network interception with URL filtering
        pw = browser = page = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
            )
            page = await context.new_page()
            odds = await self._try_network_capture(page, match_url)
            if odds:
                return odds
        except Exception as e:
            print(f"Network capture error: {e}")
        finally:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()

        # 3) Text fallback
        odds = await self._scrape_via_text(match_url, locale="en-US")
        if odds:
            return odds
        odds = await self._scrape_via_text(match_url, locale="zh-CN")
        return odds


# ─── Betway ─────────────────────────────────────────────────────────────

class BetwayScraper(BaseScraper):
    BASE_URL = "https://betway.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market", timeout=15000)

            ou_sections = await page.query_selector_all(".market:has(.marketTitle:-soup-contains('Total Goals'))")
            for sec in ou_sections:
                selections = await sec.query_selector_all(".selection")
                for sel in selections:
                    line_el = await sel.query_selector(".selectionName")
                    odds_el = await sel.query_selector(".odds")
                    if line_el and odds_el:
                        line = (await line_el.inner_text()).strip()
                        odds = self._parse_odds(await odds_el.inner_text())
                        if odds:
                            direction = "over" if "Over" in line else "under"
                            line_num = line.split()[-1]
                            key = f"over_under_{line_num}"
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = odds

            ah_sections = await page.query_selector_all(".market:has(.marketTitle:-soup-contains('Asian Handicap'))")
            for sec in ah_sections:
                selections = await sec.query_selector_all(".selection")
                for sel in selections:
                    line_el = await sel.query_selector(".selectionName")
                    odds_el = await sel.query_selector(".odds")
                    if line_el and odds_el:
                        line = (await line_el.inner_text()).strip()
                        odds = self._parse_odds(await odds_el.inner_text())
                        if odds:
                            direction = "home" if "Home" in line else "away"
                            key = f"asian_handicap_{line.split()[-1]}"
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = odds

        except Exception as e:
            print(f"Betway error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


# ─── BetWinner ─────────────────────────────────────────────────────────

class BetWinnerScraper(BaseScraper):
    BASE_URL = "https://betwinner.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market-block", timeout=15000)

            ou_blocks = await page.query_selector_all(".market-block:has(.market-name:-soup-contains('Total'))")
            for block in ou_blocks:
                rows = await block.query_selector_all(".market-row")
                for row in rows:
                    cells = await row.query_selector_all(".market-cell")
                    if len(cells) >= 3:
                        line = (await cells[0].inner_text()).strip()
                        over = self._parse_odds(await cells[1].inner_text())
                        under = self._parse_odds(await cells[2].inner_text())
                        if over and under:
                            odds_data[f"over_under_{line}"] = {"over": over, "under": under}

            ah_blocks = await page.query_selector_all(".market-block:has(.market-name:-soup-contains('Handicap'))")
            for block in ah_blocks:
                rows = await block.query_selector_all(".market-row")
                for row in rows:
                    cells = await row.query_selector_all(".market-cell")
                    if len(cells) >= 3:
                        line = (await cells[0].inner_text()).strip()
                        home = self._parse_odds(await cells[1].inner_text())
                        away = self._parse_odds(await cells[2].inner_text())
                        if home and away:
                            odds_data[f"asian_handicap_{line}"] = {"home": home, "away": away}

        except Exception as e:
            print(f"BetWinner error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


# ─── BetPawa ───────────────────────────────────────────────────────────

class BetPawaScraper(BaseScraper):
    BASE_URL = "https://betpawa.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market-section", timeout=15000)

            ou_sections = await page.query_selector_all(".market-section:has(.section-title:-soup-contains('Over/Under'))")
            for sec in ou_sections:
                rows = await sec.query_selector_all(".odds-row")
                for row in rows:
                    cells = await row.query_selector_all(".odds-cell")
                    if len(cells) >= 3:
                        line = (await cells[0].inner_text()).strip()
                        over = self._parse_odds(await cells[1].inner_text())
                        under = self._parse_odds(await cells[2].inner_text())
                        if over and under:
                            odds_data[f"over_under_{line}"] = {"over": over, "under": under}

            ah_sections = await page.query_selector_all(".market-section:has(.section-title:-soup-contains('Asian Handicap'))")
            for sec in ah_sections:
                rows = await sec.query_selector_all(".odds-row")
                for row in rows:
                    cells = await row.query_selector_all(".odds-cell")
                    if len(cells) >= 3:
                        line = (await cells[0].inner_text()).strip()
                        home = self._parse_odds(await cells[1].inner_text())
                        away = self._parse_odds(await cells[2].inner_text())
                        if home and away:
                            odds_data[f"asian_handicap_{line}"] = {"home": home, "away": away}

        except Exception as e:
            print(f"BetPawa error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


# ── Scraper factory ──────────────────────────────────────────────────────

SCRAPER_MAP = {
    "sportybet": SportyBetScraper,
    "1win": OneWinScraper,
    "betway": BetwayScraper,
    "betwinner": BetWinnerScraper,
    "betpawa": BetPawaScraper,
}

def get_scraper(platform: str) -> BaseScraper:
    scraper_class = SCRAPER_MAP.get(platform.lower())
    if not scraper_class:
        raise ValueError(f"No scraper for platform: {platform}")
    return scraper_class()


# ── Debug utility ───────────────────────────────────────────────────────

async def debug_1win_network(match_url: str):
    """Print all JSON responses and their URLs for one minute."""
    from playwright.async_api import async_playwright
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
    await page.wait_for_timeout(60000)  # 1 minute

    print(f"Captured {len(captured)} JSON responses:")
    for url, body in captured[:10]:
        print(f"\n--- {url} ---")
        print(str(body)[:500])
    await browser.close()
    await pw.stop() 
