"""
market_ai.py – Cross‑market relationship mapping, settlement speed,
               stability scoring, and three‑factor priority scoring.
Covers Football, Basketball, Tennis, Table Tennis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ── Settlement speed constants (used for scoring) ─────────────────────────
ULTRA_FAST = 0   # ~1‑10 min  (point, game, 10‑min interval)
FAST       = 1   # ~12‑45 min (quarter, 1st half, set, next goal)
MEDIUM     = 2   # ~45‑90 min (2nd half, full match without OT)
SLOW       = 3   # >90 min    (full match with OT, full tennis match)

SPEED_SCORES = {
    ULTRA_FAST: 1.0,
    FAST:       0.8,
    MEDIUM:     0.4,
    SLOW:       0.1,
}

# ── Stability scores ──────────────────────────────────────────────────────
STABILITY_HIGH   = 1.0   # pre‑match 10‑30 min to kickoff
STABILITY_MEDIUM = 0.6   # pre‑match 30‑60 min, live early minutes
STABILITY_LOW    = 0.2   # live late game

# ── Three‑factor scoring weights (mirrors config.py) ─────────────────────
WEIGHT_MARGIN    = 0.5
WEIGHT_SPEED     = 0.4
WEIGHT_STABILITY = 0.1


# ═══════════════════════════════════════════════════════════════════════════
#  MARKET DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MarketDef:
    """
    Describes a single two‑way market.
    """
    sport: str
    market_key: str              # standard key used everywhere (e.g. "over_under")
    display_name: str            # human‑readable
    settlement_speed: int        # one of ULTRA_FAST/FAST/MEDIUM/SLOW
    # The labels that different platforms might use – already handled by
    # matcher.normalise_market().  We list them here as documentation only.
    sportybet_labels: List[str] = None
    onewin_labels: List[str] = None

    def __post_init__(self):
        if self.sportybet_labels is None:
            self.sportybet_labels = []
        if self.onewin_labels is None:
            self.onewin_labels = []


# ── All two‑way markets per sport ──────────────────────────────────────────

FOOTBALL_MARKETS: List[MarketDef] = [
    # Full match
    MarketDef("football", "over_under",          "Over/Under Goals",          SLOW),
    MarketDef("football", "asian_handicap",      "Asian Handicap",            SLOW),
    MarketDef("football", "double_chance",       "Double Chance",             SLOW),
    MarketDef("football", "both_to_score",       "Both Teams to Score",       SLOW),
    MarketDef("football", "odd_even",            "Odd/Even Goals",            SLOW),
    MarketDef("football", "draw_no_bet",         "Draw No Bet",               SLOW),
    MarketDef("football", "handicap_3way",       "Handicap (3‑Way)",          SLOW),
    MarketDef("football", "winner",              "Match Winner",              SLOW),
    # Fast / Ultra‑fast
    MarketDef("football", "over_under_10min",    "10‑Min Over/Under",         ULTRA_FAST),
    MarketDef("football", "over_under_1st_half", "1st Half Over/Under",       FAST),
    MarketDef("football", "asian_handicap_1st_half", "1st Half Asian Handicap", FAST),
    MarketDef("football", "both_to_score_1st_half", "1st Half Both to Score", FAST),
    MarketDef("football", "next_goal_2way",      "Next Goal (2‑Way)",         ULTRA_FAST),
    MarketDef("football", "over_under_2nd_half", "2nd Half Over/Under",       MEDIUM),
    # Corners
    MarketDef("football", "corners_over_under",  "Corners Over/Under",        SLOW),
    MarketDef("football", "corners_asian_handicap", "Corners Asian Handicap", SLOW),
    # 5/10/15 min intervals
    MarketDef("football", "interval_over_under",  "Interval Over/Under",      ULTRA_FAST),
]

BASKETBALL_MARKETS: List[MarketDef] = [
    MarketDef("basketball", "winner_full",        "Winner (no OT)",           SLOW),
    MarketDef("basketball", "over_under_full",    "Over/Under (no OT)",       SLOW),
    MarketDef("basketball", "handicap_full",      "Handicap (no OT)",         SLOW),
    MarketDef("basketball", "winner_incl_ot",     "Winner (incl. OT)",        SLOW),
    MarketDef("basketball", "over_under_incl_ot", "Over/Under (incl. OT)",    SLOW),
    MarketDef("basketball", "handicap_incl_ot",   "Handicap (incl. OT)",      SLOW),
    MarketDef("basketball", "odd_even_incl_ot",   "Odd/Even (incl. OT)",      SLOW),
    MarketDef("basketball", "quarter_over_under", "Quarter Over/Under",       FAST),
    MarketDef("basketball", "quarter_handicap",   "Quarter Handicap",         FAST),
    MarketDef("basketball", "quarter_winner",     "Quarter Winner",           FAST),
    MarketDef("basketball", "half_over_under",    "Half Over/Under",          MEDIUM),
    MarketDef("basketball", "half_handicap",      "Half Handicap",            MEDIUM),
    MarketDef("basketball", "player_points",      "Player Points Over/Under", SLOW),
]

TENNIS_MARKETS: List[MarketDef] = [
    MarketDef("tennis", "winner",                "Match Winner",             SLOW),
    MarketDef("tennis", "game_handicap",         "Game Handicap",            SLOW),
    MarketDef("tennis", "total_games",           "Total Games Over/Under",   SLOW),
    MarketDef("tennis", "odd_even_games",        "Odd/Even Games",           SLOW),
    MarketDef("tennis", "set_winner",            "Set Winner",               FAST),
    MarketDef("tennis", "set_total_games",       "Set Total Games Over/Under", FAST),
    MarketDef("tennis", "set_game_handicap",     "Set Game Handicap",        FAST),
    MarketDef("tennis", "tiebreak_in_match",     "Tiebreak in Match?",       SLOW),
    MarketDef("tennis", "tiebreak_in_set",       "Tiebreak in Set?",         FAST),
    MarketDef("tennis", "game_winner",           "Game Winner",              ULTRA_FAST),
    MarketDef("tennis", "game_to_deuce",         "Game to Deuce?",           ULTRA_FAST),
    MarketDef("tennis", "player_to_win_a_set",   "Player to Win a Set",      SLOW),
]

TABLE_TENNIS_MARKETS: List[MarketDef] = [
    MarketDef("table_tennis", "winner",           "Match Winner",            FAST),
    MarketDef("table_tennis", "point_handicap",   "Point Handicap",          FAST),
    MarketDef("table_tennis", "total_points",     "Total Points Over/Under", FAST),
    MarketDef("table_tennis", "game_odd_even",    "Game Odd/Even",          ULTRA_FAST),
    MarketDef("table_tennis", "race_to_points",   "Race to X Points",       ULTRA_FAST),
]

ALL_MARKETS: Dict[str, List[MarketDef]] = {
    "football":      FOOTBALL_MARKETS,
    "basketball":    BASKETBALL_MARKETS,
    "tennis":        TENNIS_MARKETS,
    "table_tennis":  TABLE_TENNIS_MARKETS,
}

# ═══════════════════════════════════════════════════════════════════════════
#  CROSS‑MARKET PAIRS
# ═══════════════════════════════════════════════════════════════════════════

# Format: (sport, market_key_A, platform_A_outcome, market_key_B, platform_B_outcome)
# "outcome" is either "home"/"over"/"yes" or "away"/"under"/"no"
CROSS_MARKET_PAIRS: List[Tuple[str, str, str, str, str]] = [
    # Football
    ("football", "double_chance", "home_or_draw", "winner", "away"),
    ("football", "double_chance", "home_or_away", "winner", "draw"),
    ("football", "double_chance", "draw_or_away", "winner", "home"),
    # Tennis
    ("tennis", "tiebreak_in_match", "yes", "tiebreak_in_match", "no"),
    ("tennis", "tiebreak_in_set", "yes", "tiebreak_in_set", "no"),
    ("tennis", "game_to_deuce", "yes", "game_to_deuce", "no"),
    # Basketball
    ("basketball", "odd_even_incl_ot", "odd", "odd_even_incl_ot", "even"),
    # Table Tennis
    ("table_tennis", "game_odd_even", "odd", "game_odd_even", "even"),
]


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def get_market_def(sport: str, market_key: str) -> Optional[MarketDef]:
    """Return the MarketDef for a given sport and standardised market key."""
    for m in ALL_MARKETS.get(sport, []):
        if m.market_key == market_key:
            return m
    return None


def get_settlement_speed(sport: str, market_key: str) -> int:
    """Return the settlement speed constant for a market (default SLOW)."""
    m = get_market_def(sport, market_key)
    return m.settlement_speed if m else SLOW


def get_speed_score(sport: str, market_key: str) -> float:
    speed = get_settlement_speed(sport, market_key)
    return SPEED_SCORES.get(speed, 0.1)


def get_stability_score(is_live: bool, minutes_to_kickoff: float = 0) -> float:
    """
    Return stability score based on match state.
    - Pre‑match, 10‑30 min to kickoff → HIGH
    - Pre‑match, 30‑60 min → MEDIUM
    - Live, early (first quarter / 1st half / first set) → MEDIUM
    - Live, late → LOW
    """
    if not is_live:
        if 10 <= minutes_to_kickoff <= 30:
            return STABILITY_HIGH
        elif 30 < minutes_to_kickoff <= 60:
            return STABILITY_MEDIUM
        else:
            return STABILITY_LOW   # >60 min out = low urgency
    else:
        # For live matches we'll assume the caller passes an appropriate flag;
        # here we return MEDIUM as a safe default for early live.
        # The caller should use the more specific is_early_live / is_late_live
        # from the match context.
        return STABILITY_MEDIUM


def find_cross_market_opposite(
    sport: str,
    market_a: str,
    outcome_a: str
) -> Optional[Tuple[str, str]]:
    """
    Given a market and the side you would bet on Platform A,
    return the (market_key, outcome) that is its logical opposite on Platform B.
    """
    for s, mk_a, out_a, mk_b, out_b in CROSS_MARKET_PAIRS:
        if s == sport and mk_a == market_a and out_a == outcome_a.lower():
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
    """
    Calculate the three‑factor priority score for an arb.

    Returns (score, breakdown_dict)
    """
    speed = get_speed_score(sport, market_key)

    if is_live:
        stability = STABILITY_MEDIUM if is_early_live else STABILITY_LOW
    else:
        stability = get_stability_score(is_live, minutes_to_kickoff)

    # Normalise margin to 0‑100 scale for scoring (cap at 10 %)
    margin_score = min(margin_pct / 10.0, 1.0)

    score = (
        (margin_score * WEIGHT_MARGIN)
        + (speed * WEIGHT_SPEED)
        + (stability * WEIGHT_STABILITY)
    )

    breakdown = {
        "margin_pct": margin_pct,
        "margin_score": round(margin_score, 3),
        "speed": speed,
        "stability": stability,
        "final_score": round(score, 3),
    }

    return score, breakdown
