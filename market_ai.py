"""
market_ai.py – Canonical market intelligence layer.

Responsibilities:
- Settlement speed classification
- Stability scoring
- Priority scoring
- Cross-market relationship mapping
- Canonical market metadata registry

This file MUST remain pure logic only.
No scraper logic.
No bookmaker logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════════
# SPEED CONSTANTS
# ════════════════════════════════════════════════════════════════════════

ULTRA_FAST = 0
FAST = 1
MEDIUM = 2
SLOW = 3


SPEED_SCORES = {
    ULTRA_FAST: 1.0,
    FAST: 0.8,
    MEDIUM: 0.4,
    SLOW: 0.1,
}


# ════════════════════════════════════════════════════════════════════════
# STABILITY
# ════════════════════════════════════════════════════════════════════════

STABILITY_HIGH = 1.0
STABILITY_MEDIUM = 0.6
STABILITY_LOW = 0.2


# ════════════════════════════════════════════════════════════════════════
# PRIORITY WEIGHTS
# ════════════════════════════════════════════════════════════════════════

WEIGHT_MARGIN = 0.5
WEIGHT_SPEED = 0.4
WEIGHT_STABILITY = 0.1


# ════════════════════════════════════════════════════════════════════════
# MARKET DEFINITIONS
# ════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketDef:
    sport: str
    market_key: str
    display_name: str
    settlement_speed: int
    category: str
    supported_outcomes: Tuple[str, ...]


# ════════════════════════════════════════════════════════════════════════
# FOOTBALL
# ════════════════════════════════════════════════════════════════════════

FOOTBALL_MARKETS: List[MarketDef] = [

    MarketDef(
        sport="football",
        market_key="winner",
        display_name="Match Winner",
        settlement_speed=SLOW,
        category="moneyline",
        supported_outcomes=("home", "draw", "away"),
    ),

    MarketDef(
        sport="football",
        market_key="over_under",
        display_name="Over/Under Goals",
        settlement_speed=SLOW,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="football",
        market_key="asian_handicap",
        display_name="Asian Handicap",
        settlement_speed=SLOW,
        category="spread",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="football",
        market_key="both_to_score",
        display_name="Both Teams To Score",
        settlement_speed=SLOW,
        category="prop",
        supported_outcomes=("yes", "no"),
    ),

    MarketDef(
        sport="football",
        market_key="double_chance",
        display_name="Double Chance",
        settlement_speed=SLOW,
        category="combo",
        supported_outcomes=(
            "home_or_draw",
            "home_or_away",
            "draw_or_away",
        ),
    ),

    MarketDef(
        sport="football",
        market_key="draw_no_bet",
        display_name="Draw No Bet",
        settlement_speed=SLOW,
        category="moneyline",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="football",
        market_key="odd_even",
        display_name="Odd/Even Goals",
        settlement_speed=SLOW,
        category="prop",
        supported_outcomes=("odd", "even"),
    ),

    MarketDef(
        sport="football",
        market_key="over_under_1st_half",
        display_name="1st Half Over/Under",
        settlement_speed=FAST,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="football",
        market_key="asian_handicap_1st_half",
        display_name="1st Half Asian Handicap",
        settlement_speed=FAST,
        category="spread",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="football",
        market_key="next_goal_2way",
        display_name="Next Goal",
        settlement_speed=ULTRA_FAST,
        category="prop",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="football",
        market_key="interval_over_under",
        display_name="Interval Over/Under",
        settlement_speed=ULTRA_FAST,
        category="total",
        supported_outcomes=("over", "under"),
    ),
]


# ════════════════════════════════════════════════════════════════════════
# BASKETBALL
# ════════════════════════════════════════════════════════════════════════

BASKETBALL_MARKETS: List[MarketDef] = [

    MarketDef(
        sport="basketball",
        market_key="winner_full",
        display_name="Winner",
        settlement_speed=SLOW,
        category="moneyline",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="basketball",
        market_key="over_under_full",
        display_name="Total Points",
        settlement_speed=SLOW,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="basketball",
        market_key="handicap_full",
        display_name="Spread",
        settlement_speed=SLOW,
        category="spread",
        supported_outcomes=("home", "away"),
    ),

    MarketDef(
        sport="basketball",
        market_key="quarter_over_under",
        display_name="Quarter Total",
        settlement_speed=FAST,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="basketball",
        market_key="quarter_handicap",
        display_name="Quarter Handicap",
        settlement_speed=FAST,
        category="spread",
        supported_outcomes=("home", "away"),
    ),
]


# ════════════════════════════════════════════════════════════════════════
# TENNIS
# ════════════════════════════════════════════════════════════════════════

TENNIS_MARKETS: List[MarketDef] = [

    MarketDef(
        sport="tennis",
        market_key="winner",
        display_name="Match Winner",
        settlement_speed=SLOW,
        category="moneyline",
        supported_outcomes=("player1", "player2"),
    ),

    MarketDef(
        sport="tennis",
        market_key="total_games",
        display_name="Total Games",
        settlement_speed=SLOW,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="tennis",
        market_key="game_handicap",
        display_name="Game Handicap",
        settlement_speed=SLOW,
        category="spread",
        supported_outcomes=("player1", "player2"),
    ),

    MarketDef(
        sport="tennis",
        market_key="set_winner",
        display_name="Set Winner",
        settlement_speed=FAST,
        category="moneyline",
        supported_outcomes=("player1", "player2"),
    ),

    MarketDef(
        sport="tennis",
        market_key="game_winner",
        display_name="Game Winner",
        settlement_speed=ULTRA_FAST,
        category="moneyline",
        supported_outcomes=("player1", "player2"),
    ),
]


# ════════════════════════════════════════════════════════════════════════
# TABLE TENNIS
# ════════════════════════════════════════════════════════════════════════

TABLE_TENNIS_MARKETS: List[MarketDef] = [

    MarketDef(
        sport="table_tennis",
        market_key="winner",
        display_name="Winner",
        settlement_speed=FAST,
        category="moneyline",
        supported_outcomes=("player1", "player2"),
    ),

    MarketDef(
        sport="table_tennis",
        market_key="total_points",
        display_name="Total Points",
        settlement_speed=FAST,
        category="total",
        supported_outcomes=("over", "under"),
    ),

    MarketDef(
        sport="table_tennis",
        market_key="point_handicap",
        display_name="Point Handicap",
        settlement_speed=FAST,
        category="spread",
        supported_outcomes=("player1", "player2"),
    ),
]


# ════════════════════════════════════════════════════════════════════════
# REGISTRY
# ════════════════════════════════════════════════════════════════════════

ALL_MARKETS: Dict[str, List[MarketDef]] = {
    "football": FOOTBALL_MARKETS,
    "basketball": BASKETBALL_MARKETS,
    "tennis": TENNIS_MARKETS,
    "table_tennis": TABLE_TENNIS_MARKETS,
}


# Fast lookup cache
_MARKET_CACHE: Dict[Tuple[str, str], MarketDef] = {}

for sport, defs in ALL_MARKETS.items():
    for m in defs:
        _MARKET_CACHE[(sport, m.market_key)] = m


# ════════════════════════════════════════════════════════════════════════
# CROSS-MARKET RELATIONSHIPS
# ════════════════════════════════════════════════════════════════════════

CROSS_MARKET_PAIRS: List[
    Tuple[str, str, str, str, str]
] = [

    (
        "football",
        "double_chance",
        "home_or_draw",
        "winner",
        "away",
    ),

    (
        "football",
        "double_chance",
        "draw_or_away",
        "winner",
        "home",
    ),

    (
        "football",
        "double_chance",
        "home_or_away",
        "winner",
        "draw",
    ),

    (
        "football",
        "both_to_score",
        "yes",
        "both_to_score",
        "no",
    ),
]


# ════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════════

def get_market_def(
    sport: str,
    market_key: str,
) -> Optional[MarketDef]:

    return _MARKET_CACHE.get((sport, market_key))


def get_settlement_speed(
    sport: str,
    market_key: str,
) -> int:

    market = get_market_def(sport, market_key)

    if not market:
        return SLOW

    return market.settlement_speed


def get_speed_score(
    sport: str,
    market_key: str,
) -> float:

    speed = get_settlement_speed(sport, market_key)

    return SPEED_SCORES.get(speed, 0.1)


def get_stability_score(
    is_live: bool,
    minutes_to_kickoff: float = 0,
) -> float:

    if is_live:
        return STABILITY_MEDIUM

    if 10 <= minutes_to_kickoff <= 30:
        return STABILITY_HIGH

    if 30 < minutes_to_kickoff <= 60:
        return STABILITY_MEDIUM

    return STABILITY_LOW


def find_cross_market_opposite(
    sport: str,
    market_a: str,
    outcome_a: str,
) -> Optional[Tuple[str, str]]:

    for (
        s,
        mk_a,
        out_a,
        mk_b,
        out_b,
    ) in CROSS_MARKET_PAIRS:

        if (
            s == sport
            and mk_a == market_a
            and out_a == outcome_a.lower()
        ):
            return (mk_b, out_b)

    return None


def calculate_priority_score(
    margin_pct: float,
    sport: str,
    market_key: str,
    is_live: bool,
    minutes_to_kickoff: float = 0,
    is_early_live: bool = False,
) -> Tuple[float, dict]:

    speed = get_speed_score(
        sport,
        market_key,
    )

    if is_live:
        stability = (
            STABILITY_MEDIUM
            if is_early_live
            else STABILITY_LOW
        )
    else:
        stability = get_stability_score(
            is_live,
            minutes_to_kickoff,
        )

    margin_score = min(
        margin_pct / 10.0,
        1.0,
    )

    score = (
        (margin_score * WEIGHT_MARGIN)
        + (speed * WEIGHT_SPEED)
        + (stability * WEIGHT_STABILITY)
    )

    breakdown = {
        "margin_pct": round(margin_pct, 3),
        "margin_score": round(margin_score, 3),
        "speed": round(speed, 3),
        "stability": round(stability, 3),
        "final_score": round(score, 3),
    }

    return score, breakdown
