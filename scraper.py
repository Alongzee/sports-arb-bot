"""
scraper.py – Playwright-based odds scraper for multiple bookmakers.
Each platform gets its own scraper class. All scrapers return a standardised
dictionary of odds for a given match and market.
"""

import asyncio
import re
from playwright.async_api import async_playwright


class BaseScraper:
    """Shared Playwright setup and helper methods."""

    async def _get_page(self, url: str):
        """Launch browser, create page, navigate to URL."""
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        return pw, browser, page


class SportyBetScraper(BaseScraper):
    """Scrapes SportyBet for Asian Handicap and Over/Under odds."""

    BASE_URL = "https://sportybet.com/gh/m/spc"

    async def get_odds(self, match_url: str) -> dict:
        """
        Extract Over/Under and Asian Handicap odds from a SportyBet match page.
        Returns a dict with market names as keys and odds tuples.
        Example output:
        {
            "over_under_2.5": {"over": 1.72, "under": 2.01},
            "asian_handicap_-2.5": {"home": 4.80, "away": 1.19}
        }
        """
        odds_data = {}
        pw, browser, page = await self._get_page(match_url)

        try:
            # Wait for odds tables to load
            await page.wait_for_selector(".market-group", timeout=15000)

            # ── Extract Over/Under lines ─────────────────────────────
            ou_blocks = await page.query_selector_all(".market-group:has(.market-name:-soup-contains('Over/Under'))")
            for block in ou_blocks:
                rows = await block.query_selector_all(".market-row")
                for row in rows:
                    cells = await row.query_selector_all(".market-cell")
                    if len(cells) >= 3:
                        line_text = await cells[0].inner_text()
                        over_odds = await cells[1].inner_text()
                        under_odds = await cells[2].inner_text()
                        # Extract numeric odds
                        over = self._parse_odds(over_odds)
                        under = self._parse_odds(under_odds)
                        if over and under:
                            # Normalise market name, e.g. "over_under_2.5"
                            market_name = f"over_under_{line_text.strip().replace(' Over/Under','')}"
                            odds_data[market_name] = {"over": over, "under": under}

            # ── Extract Asian Handicap lines ─────────────────────────
            ah_blocks = await page.query_selector_all(".market-group:has(.market-name:-soup-contains('Asian Handicap'))")
            for block in ah_blocks:
                rows = await block.query_selector_all(".market-row")
                for row in rows:
                    cells = await row.query_selector_all(".market-cell")
                    if len(cells) >= 3:
                        line_text = await cells[0].inner_text()
                        home_odds = await cells[1].inner_text()
                        away_odds = await cells[2].inner_text()
                        h = self._parse_odds(home_odds)
                        a = self._parse_odds(away_odds)
                        if h and a:
                            market_name = f"asian_handicap_{line_text.strip()}"
                            odds_data[market_name] = {"home": h, "away": a}

        except Exception as e:
            print(f"SportyBet scrape error: {e}")
        finally:
            await browser.close()
            await pw.stop()

        return odds_data

    def _parse_odds(self, text: str) -> float:
        """Convert odds text like '1.72' or '5/4' to float."""
        text = text.strip()
        try:
            return float(text)
        except ValueError:
            # Handle fractional odds if present
            if "/" in text:
                parts = text.split("/")
                if len(parts) == 2:
                    num, den = parts
                    return float(num) / float(den) + 1
        return None


# ── Factory function to get the right scraper ──────────────────────────

SCRAPER_MAP = {
    "sportybet": SportyBetScraper,
    # Add other platforms here — e.g. "1win": OneWinScraper,
    # "betway": BetwayScraper,
}

def get_scraper(platform: str) -> BaseScraper:
    scraper_class = SCRAPER_MAP.get(platform.lower())
    if not scraper_class:
        raise ValueError(f"No scraper for platform: {platform}")
    return scraper_class()
