"""
config.py – Central configuration for the sports arbitrage bot.
All constants, thresholds, and runtime settings.
"""

import os
import random
from typing import List

# ── Telegram ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")   # Group ID for alerts

# ── Bookmakers ────────────────────────────────────────────────────────────
BOOKMAKER_YOU    = "1win"
BOOKMAKER_FRIEND = "sportybet"

# ── Active Hours (UTC) ────────────────────────────────────────────────────
ACTIVE_START   = 0    # 6 AM
ACTIVE_END     = 24   # 6 PM

# ── Bankroll & Staking ────────────────────────────────────────────────────
TOTAL_BANKROLL = 100.0          # GHS – split across both accounts
ACCOUNT_FLOOR  = 5.0            # Minimum balance an account can hold before arb is skipped

# ── Non‑Arb Bets ──────────────────────────────────────────────────────────
NON_ARB_BASE_PCT    = 1.0      # base percentage of current balance
NON_ARB_VARIANCE_PCT = 0.5     # ± random variance → final pct = base ± variance
NON_ARB_ROUND_TO     = 0.5     # round stake to nearest 0.5 GHS

# ── Cool‑Off Calendar ─────────────────────────────────────────────────────
def generate_cool_off_days() -> List[int]:
    """
    Returns two random, non‑consecutive days (0=Monday … 6=Sunday).
    Never returns the same two days as last week.
    """
    days = list(range(7))
    random.shuffle(days)
    first = days[0]
    excluded = {first, (first + 1) % 7, (first - 1) % 7}
    remaining = [d for d in days if d not in excluded]
    second = remaining[0] if remaining else (first + 3) % 7
    return [first, second]

# Store current week's cool‑off days (runtime, reset weekly)
COOL_OFF_DAYS: List[int] = generate_cool_off_days()

# ── Golden Arb Override (exceptional arb during cool‑off) ─────────────────
GOLDEN_ARB_MIN_MARGIN      = 5.0    # percent
GOLDEN_ARB_MAX_SETTLE_MIN  = 45     # minutes
GOLDEN_ARB_MAX_PER_DAY     = 1

# ── Arbitration Thresholds ────────────────────────────────────────────────
MIN_MARGIN_PCT    = 1.5      # skip arbs below this
DUPLICATE_WINDOW  = 30       # seconds – same arb re‑appearing within this window is ignored

# ── Execution Timeout ─────────────────────────────────────────────────────
EXECUTION_TIMEOUT = 120      # seconds – if both sides haven't replied /done by then, abort

# ── Priority Scoring Weights ──────────────────────────────────────────────
WEIGHT_MARGIN   = 0.5
WEIGHT_SPEED    = 0.4
WEIGHT_STABILITY = 0.1

# ── Settlement Speed Scores (used inside market_ai.py) ────────────────────
# These default values can be overridden per market in market_ai.py
SPEED_FAST   = 1.0   # ~10‑45 min
SPEED_MEDIUM = 0.6   # ~45‑90 min
SPEED_SLOW   = 0.2   # >90 min

# ── Stability Scores (used inside market_ai.py) ───────────────────────────
STABILITY_HIGH   = 1.0   # pre‑match 10‑30 min to kickoff
STABILITY_MEDIUM = 0.6   # live, early minutes / pre‑match 30‑60 min
STABILITY_LOW    = 0.2   # live, late game

# ── Database ──────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/sports-arb-bot/arb_opportunities.db")

# ── Passive Mode (overnight scanning) ────────────────────────────────────
PASSIVE_MODE_START = 18   # 6 PM UTC
PASSIVE_MODE_END   = 6    # 6 AM UTC

# ── Scan Interval ─────────────────────────────────────────────────────────
SCAN_INTERVAL = 2  # seconds between full scan cycles
