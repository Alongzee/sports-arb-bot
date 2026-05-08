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


# ─── 1win (DOM polling – finds numbers, never times out) ───────────────

class OneWinScraper(BaseScraper):
    """
    Scrapes 1win by polling the DOM for any visible numbers that look like odds.
    Works regardless of language, template, or framework.
    """

    BASE_URL = "https://1wgcmt.com"

    async def _get_page_en_us(self, url: str):
        """Create a page with en-US locale to get English labels if present."""
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return pw, browser, page

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw = browser = page = None

        try:
            pw, browser, page = await self._get_page_en_us(match_url)

            # Poll the DOM every 2 seconds for up to 40 seconds
            potential_odds = []
            for _ in range(20):
                await page.wait_for_timeout(2000)

                potential_odds = await page.evaluate("""() => {
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    const results = [];
                    let node;
                    while (node = walker.nextNode()) {
                        const text = node.textContent.trim();
                        const match = text.match(/^(\\d+\\.\\d{2})$/);
                        if (match) {
                            const val = parseFloat(match[1]);
                            if (val >= 1.01 && val <= 1000) {
                                const parentText = node.parentElement?.textContent?.trim() || '';
                                results.push({value: val, context: parentText});
                            }
                        }
                    }
                    return results;
                }""")

                if len(potential_odds) >= 6:  # at least 3 pairs
                    break

            if potential_odds:
                over_under_lines = {}
                handicap_lines = {}

                for item in potential_odds:
                    ctx = item['context'].lower()
                    val = item['value']

                    # Over/Under pattern
                    ou_match = re.search(r'(over|under)\s+(\d+\.?\d*)\s+(\d+\.\d{2})', ctx)
                    if ou_match:
                        direction = ou_match.group(1)
                        line = ou_match.group(2)
                        odd_val = float(ou_match.group(3))
                        if line not in over_under_lines:
                            over_under_lines[line] = {}
                        over_under_lines[line][direction] = odd_val

                    # Handicap pattern
                    hcp_match = re.search(
                        r'(?:asian\s+)?handicap\s+([+-]?\d+\.?\d*)\s+(\d+\.\d{2})\s+(\d+\.\d{2})',
                        ctx
                    )
                    if hcp_match:
                        line = hcp_match.group(1)
                        home = float(hcp_match.group(2))
                        away = float(hcp_match.group(3))
                        handicap_lines[line] = {'home': home, 'away': away}

                for line, sides in over_under_lines.items():
                    if 'over' in sides and 'under' in sides:
                        odds_data[f'over_under_{line}'] = {'over': sides['over'], 'under': sides['under']}

                for line, sides in handicap_lines.items():
                    odds_data[f'asian_handicap_{line}'] = sides

            if not odds_data:
                print(f'1win: Found {len(potential_odds)} potential odds values, but could not parse patterns.')
                if potential_odds:
                    for item in potential_odds[:5]:
                        print(f'  {item["context"][:100]}...')

        except Exception as e:
            print(f'1win error: {e}')
        finally:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()

        return odds_data


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
