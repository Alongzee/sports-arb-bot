"""Configuration constants for sports-arb-bot."""

import os

# ── Bookmakers ───────────────────────────────────────────────────────────
TIER1 = ["1win", "sportybet", "betway", "betwinner", "betpawa"]
TIER2 = ["msport", "1xbet", "melbet"]

# ── Active Hours (GMT) ──────────────────────────────────────────────────
ACTIVE_START = 6   # 6 AM
ACTIVE_END   = 18  # 6 PM

# ── Markets to scan ─────────────────────────────────────────────────────
MARKETS = [
    "asian_handicap",
    "asian_handicap_1st_half",
    "over_under_10min",
    "over_under_1st_half",
]

# ── Database ─────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/sports-arb-bot/arb_opportunities.db")

# ── Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Scanning ─────────────────────────────────────────────────────────────
SCAN_INTERVAL = 2  # seconds between scans
