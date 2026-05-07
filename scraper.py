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
    BASE_URL = "https://sportybet.com/gh/m/spc"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".market-group", timeout=15000)

            # Over/Under
            blocks = await page.query_selector_all(".market-group:has(.market-name:-soup-contains('Over/Under'))")
            for block in blocks:
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
            ah_blocks = await page.query_selector_all(".market-group:has(.market-name:-soup-contains('Asian Handicap'))")
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
            print(f"SportyBet error: {e}")
        finally:
            await browser.close()
            await pw.stop()
        return odds_data


class OneWinScraper(BaseScraper):
    BASE_URL = "https://1win.com/gh"

    async def get_odds(self, match_url: str) -> dict:
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)
        try:
            await page.wait_for_selector(".betting-table", timeout=15000)

            # Over/Under
            rows = await page.query_selector_all(".betting-table__row")
            for row in rows:
                title_el = await row.query_selector(".betting-table__title")
                if not title_el:
                    continue
                title = (await title_el.inner_text()).lower()
                cells = await row.query_selector_all(".betting-table__cell")
                if len(cells) >= 3:
                    over = self._parse_odds(await cells[1].inner_text())
                    under = self._parse_odds(await cells[2].inner_text())
                    if over and under:
                        if "total" in title:
                            line = title.split()[-1]
                            odds_data[f"over_under_{line}"] = {"over": over, "under": under}
                        elif "handicap" in title:
                            odds_data[f"asian_handicap_{title}"] = {"home": over, "away": under}

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
