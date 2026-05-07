"""
scraper.py – Playwright-based odds scrapers for Tier 1 bookmakers.
All five platforms implemented. Returns standardised odds dictionaries.
"""

import asyncio
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


class SportyBetScraper(BaseScraper):
    """
    Real HTML structure (verified from live page):
      .code-selection-item  — each selection row
      .outcome              — outcome name e.g. "Over 2.5", "Home -1.5"
      .market               — market type e.g. "Over/Under", "Asian Handicap"
      .odds                 — odds value e.g. "@1.72" (strip the @ prefix)

    Note: SportyBet match URLs must use the desktop format:
      https://www.sportybet.com/gh/sport/football/.../sr:match:XXXXXXXX
    The /m/ mobile URL returns a booking-code share page, not match odds.
    """
    BASE_URL = "https://www.sportybet.com/gh/sport"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".code-selection-item", timeout=15000)
            items = await page.query_selector_all(".code-selection-item")

            for item in items:
                outcome_el = await item.query_selector(".outcome")
                market_el  = await item.query_selector(".market")
                odds_el    = await item.query_selector(".odds")

                if not (outcome_el and market_el and odds_el):
                    continue

                outcome = (await outcome_el.inner_text()).strip()
                market  = (await market_el.inner_text()).strip()
                odds_raw = (await odds_el.inner_text()).strip().lstrip("@")
                odds_val = self._parse_odds(odds_raw)

                if not odds_val:
                    continue

                if "Over/Under" in market:
                    # outcome = "Over 2.5" or "Under 2.5"
                    parts = outcome.split()
                    if len(parts) == 2:
                        direction = parts[0].lower()   # "over" or "under"
                        line      = parts[1]            # "2.5"
                        key = f"over_under_{line}"
                        if key not in odds_data:
                            odds_data[key] = {}
                        odds_data[key][direction] = odds_val

                elif "Asian Handicap" in market:
                    # outcome = "Home -1.5" or "Away +1.5"
                    parts = outcome.split()
                    if len(parts) == 2:
                        side = parts[0].lower()   # "home" or "away"
                        line = parts[1]            # "-1.5"
                        key = f"asian_handicap_{line}"
                        if key not in odds_data:
                            odds_data[key] = {}
                        odds_data[key][side] = odds_val

        except Exception as e:
            print(f"SportyBet error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


class OneWinScraper(BaseScraper):
    """
    Real HTML structure (verified from live page with en-US locale):
      ._title_8ulje_6   — market group title e.g. "Total", "Handicap"
      ._name_1hbh7_36   — outcome name e.g. "Over 2.5", "Under 2.5"
      ._cf_17if8_2      — odds value e.g. "1.77" (plain float, no prefix)

    CSS module hashes are stable per deploy but may change on updates.
    If scraper breaks, re-run inspect_pages.py and grep for the new hashes.

    Must use en-US locale — default locale resolves to Japanese on Tokyo VPS.
    """
    BASE_URL = "https://1wgcmt.com"

    async def _get_page(self, url: str):
        """Override to force en-US locale."""
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        return pw, browser, page

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector("._title_8ulje_6", timeout=15000)

            # Get all market group sections
            sections = await page.query_selector_all("._root_m2ytg_2")

            for section in sections:
                title_el = await section.query_selector("._title_8ulje_6")
                if not title_el:
                    continue
                title = (await title_el.inner_text()).strip().lower()

                # Only process Total (Over/Under) and Handicap markets
                if "total" not in title and "handicap" not in title:
                    continue

                # Get all outcome buttons in this section
                buttons = await section.query_selector_all("._root_1hbh7_2")
                for btn in buttons:
                    name_el = await btn.query_selector("._name_1hbh7_36")
                    odds_el = await btn.query_selector("._cf_17if8_2")
                    if not (name_el and odds_el):
                        continue

                    name     = (await name_el.inner_text()).strip()
                    odds_val = self._parse_odds(await odds_el.inner_text())
                    if not odds_val:
                        continue

                    if "total" in title:
                        # name = "Over 2.5" or "Under 2.5"
                        parts = name.split()
                        if len(parts) == 2 and parts[0].lower() in ("over", "under"):
                            direction = parts[0].lower()
                            line      = parts[1]
                            key = f"over_under_{line}"
                            if key not in odds_data:
                                odds_data[key] = {}
                            odds_data[key][direction] = odds_val

                    elif "handicap" in title:
                        # name = "Crystal Palace -1.5" or "FC Shakhtar +1.5"
                        # Last token is the handicap line, second-to-last may be sign
                        parts = name.rsplit(None, 1)
                        if len(parts) == 2:
                            line = parts[1]  # e.g. "-1.5"
                            # Determine home/away by position (first btn = home)
                            key = f"asian_handicap_{line}"
                            if key not in odds_data:
                                odds_data[key] = {"home": odds_val}
                            elif "away" not in odds_data[key]:
                                odds_data[key]["away"] = odds_val

        except Exception as e:
            print(f"1win error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


class BetwayScraper(BaseScraper):
    BASE_URL = "https://betway.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market", timeout=15000)

            # Over/Under
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

            # Asian Handicap
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


class BetWinnerScraper(BaseScraper):
    BASE_URL = "https://betwinner.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market-block", timeout=15000)

            # Over/Under
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

            # Asian Handicap
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


class BetPawaScraper(BaseScraper):
    BASE_URL = "https://betpawa.com.gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market-section", timeout=15000)

            # Over/Under
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

            # Asian Handicap
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
