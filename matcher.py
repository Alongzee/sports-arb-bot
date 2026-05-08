"""
matcher.py – Fuzzy team matching + market label normalisation.
Solves the "CDP Junior FC" vs "CDP Junior" problem and the
"Over/Under" vs "Total Goals" vs "Total" problem.
"""

from __future__ import annotations

import re
from typing import Dict, List

# ── Market label aliases ──────────────────────────────────────────────────
# Every scraped market name gets mapped to a standard key.
# Built from official SportyBet rules + confirmed 1win scraper output.
MARKET_ALIASES: Dict[str, List[str]] = {
    "over_under": [
        "over/under", "total goals", "total", "over/under goals",
        "goals over/under", "over / under", "totals", "total goals over/under",
        "goal line", "goals",
    ],
    "asian_handicap": [
        "asian handicap", "handicap (asian)", "ah", "asian handicap 1",
        "asian handicap 2", "asian hcp", "handicap 2-way",
    ],
    "handicap_3way": [
        "handicap", "european handicap", "3-way handicap", "handicap 1",
        "handicap (3-way)", "hcp",
    ],
    "double_chance": [
        "double chance", "dc", "double chance 1x2",
    ],
    "both_to_score": [
        "both teams to score", "btts", "gg/ng", "both to score",
        "goal/goal no goal",
    ],
    "next_goal": [
        "next goal", "next team to score", "next goal 3-way",
    ],
    "next_goal_2way": [
        "next goal (2-way)", "next goal 2-way",
    ],
    "corners_over_under": [
        "corners over/under", "corners - over/under", "corner over/under",
    ],
    "corners_asian_handicap": [
        "corners handicap", "corner handicap", "asian corners",
        "corners asian handicap", "corner asian handicap",
    ],
    "winner": [
        "winner", "match winner", "full time result", "1x2", "moneyline",
        "winner (incl. overtime)", "winner (incl. ot)",
    ],
    "over_under_10min": [
        "10-minute over/under", "goals from 1 to 10 min",
        "total goals from 1 to 10 minute", "total goals from 1 to 10 min",
        "total goals over/under from 1 to 10 minute",
    ],
    "over_under_1st_half": [
        "1st half over/under", "first half total", "1st half goals",
        "1st half – over/under", "1st half - over/under",
    ],
    "asian_handicap_1st_half": [
        "1st half asian handicap", "1st half handicap",
        "first half asian handicap", "1st half – asian handicap",
    ],
    "both_to_score_1st_half": [
        "1st half both teams to score", "1st half gg/ng",
        "1st half – gg/ng", "1st half - gg/ng",
    ],
    "over_under_2nd_half": [
        "2nd half over/under", "second half total", "2nd half goals",
        "2nd half – over/under", "2nd half - over/under",
    ],
    "interval_over_under": [
        "5/10/15 minutes – over/under from a to b",
        "5/10/15 minutes - over/under from a to b",
        "5/10/15 minutes – over/under",
        "interval over/under",
    ],
    "draw_no_bet": [
        "draw no bet", "dnb", "draw no bet (dnb)",
    ],
    "odd_even": [
        "odd/even", "odd / even", "odd or even",
    ],
    "booking_over_under": [
        "total bookings", "bookings over/under", "total booking points",
    ],
    "corners_1x2": [
        "corners 1x2", "corners - 1x2",
    ],
    # Basketball markets
    "winner_full": [
        "winner (no ot)", "1x2",
    ],
    "over_under_full": [
        "over/under", "total points",
    ],
    "handicap_full": [
        "handicap", "spread",
    ],
    "winner_incl_ot": [
        "winner (incl. ot)", "winner (incl. overtime)",
    ],
    "over_under_incl_ot": [
        "over/under (incl. ot)", "over/under (incl. overtime)",
    ],
    "handicap_incl_ot": [
        "handicap (incl. ot)", "handicap (incl. overtime)",
    ],
    "odd_even_incl_ot": [
        "odd/even (incl. ot)", "odd/even (incl. overtime)",
    ],
    "quarter_over_under": [
        "xth quarter – total", "quarter total", "quarter over/under",
        "1st quarter – total", "2nd quarter – total",
        "3rd quarter – total", "4th quarter – total",
    ],
    "quarter_handicap": [
        "xth quarter – handicap", "quarter handicap",
        "1st quarter – handicap", "2nd quarter – handicap",
    ],
    "quarter_winner": [
        "xth quarter – 1x2", "quarter winner",
        "1st quarter – 1x2", "2nd quarter – 1x2",
    ],
    "half_over_under": [
        "1st half – total", "half over/under",
    ],
    "half_handicap": [
        "1st half – handicap", "half handicap",
    ],
    "player_points": [
        "player points", "player points over/under",
    ],
    "player_assists": [
        "player assists", "player assists over/under",
    ],
    "player_rebounds": [
        "player rebounds", "player rebounds over/under",
    ],
    "player_three_pointers": [
        "player 3-point field goals", "player 3-pointers",
        "player three pointers",
    ],
    "player_blocks": [
        "player blocks", "player blocks over/under",
    ],
    "player_steals": [
        "player steals", "player steals over/under",
    ],
    # Tennis markets
    "game_handicap": [
        "game handicap",
    ],
    "total_games": [
        "total games", "total games over/under",
    ],
    "odd_even_games": [
        "odd/even games",
    ],
    "set_winner": [
        "xth set - winner", "set winner",
    ],
    "set_total_games": [
        "xth set - total games", "set total games",
    ],
    "set_game_handicap": [
        "xth set - game handicap", "set game handicap",
    ],
    "tiebreak_in_match": [
        "will there be a tiebreak", "tiebreak in match",
    ],
    "tiebreak_in_set": [
        "xth set - will there be a tiebreak", "tiebreak in set",
    ],
    "game_winner": [
        "xth set game x - winner", "game winner",
    ],
    "game_to_deuce": [
        "xth set game x - to deuce", "game to deuce",
    ],
    "player_to_win_a_set": [
        "competitor1 to win a set", "competitor2 to win a set",
        "player to win a set",
    ],
    # Table Tennis markets
    "point_handicap": [
        "point handicap (spread)", "point handicap",
    ],
    "total_points": [
        "total points (spread)", "total points",
    ],
    "game_odd_even": [
        "game - odd/even", "game odd/even",
    ],
    "race_to_points": [
        "race to x points", "race to points",
    ],
}

# ── Team name aliases ─────────────────────────────────────────────────────
# Maps standardised team names to all known variations across platforms.
# Build this as you encounter mismatches during execution.
TEAM_ALIASES: Dict[str, List[str]] = {
    "CDP Junior FC": ["cdp junior", "cdp junior fc", "junior fc", "cdp", "junior"],
    "Cerro Porteño": ["cerro porteno", "cerro porteño", "cerro port.", "cerro"],
    "Crystal Palace": ["crystal palace", "crystal", "palace", "crystal palace fc"],
    "Shakhtar Donetsk": ["shakhtar", "shakhtar donetsk", "fc shakhtar donetsk", "shakhtar d"],
    "Bayern München": ["bayern", "bayern munich", "fc bayern", "bayern münchen", "fc bayern münchen"],
    "Paris Saint Germain": ["psg", "paris", "paris saint germain", "paris sg"],
    "Oklahoma City Thunder": ["oklahoma city", "thunder", "oklahoma", "okc"],
    "Los Angeles Lakers": ["la lakers", "lakers", "los angeles", "l.a. lakers"],
    "Levante UD": ["levante", "levante ud", "levante ud."],
    "CA Osasuna": ["osasuna", "ca osasuna", "osasuna ca"],
    "Aston Villa": ["aston villa", "villa", "aston villa fc"],
    "Nottingham Forest": ["nottingham forest", "forest", "nottingham", "nottm forest"],
    "KFR": ["kfr", "kfr fc"],
    "Ellidi": ["ellidi", "ellidi fc"],
    "Haras El Hodood": ["haras el hodood", "haras", "el hodood"],
    "Zed FC": ["zed fc", "zed", "zed football club"],
    "CA Paulistano SP": ["ca paulistano", "paulistano", "ca paulistano sp"],
    "EC Pinheiros SP": ["ec pinheiros", "pinheiros", "ec pinheiros sp"],
    "Oklahoma City Thunder": ["oklahoma city thunder", "thunder", "oklahoma", "okc"],
    "Los Angeles Lakers": ["los angeles lakers", "lakers", "la lakers", "l.a. lakers"],
}

# ── Public API ────────────────────────────────────────────────────────────

def normalise_market(market_name: str) -> str:
    """
    Convert a platform-specific market name to a standard key.
    e.g. "Total Goals" → "over_under"
         "AH" → "asian_handicap"
         "Total Goals from 1 to X min" → "over_under_10min"
    """
    name_lower = market_name.strip().lower()
    for standard, aliases in MARKET_ALIASES.items():
        for alias in aliases:
            if alias in name_lower:
                return standard
    # If no alias matches, return the original lowercased and underscored
    return re.sub(r'\s+', '_', name_lower)


def normalise_team(name: str) -> str:
    """
    Convert a platform-specific team name to a standard form.
    First checks the alias table, then falls back to basic cleaning.
    """
    name_lower = name.strip().lower()
    # Check alias table
    for standard, aliases in TEAM_ALIASES.items():
        if name_lower in [a.lower() for a in aliases]:
            return standard
        # Fuzzy fallback — if the name is very similar to an alias
        for alias in aliases:
            if _similarity(name_lower, alias.lower()) >= 85:
                return standard
    # Return cleaned version if no alias found
    return re.sub(r'\s+', ' ', name_lower).strip()


def match_teams(name1: str, name2: str) -> bool:
    """
    Return True if two team names likely refer to the same team.
    Uses alias table + fuzzy string matching.
    """
    # Both normalise to same standard name? → match
    if normalise_team(name1) == normalise_team(name2):
        return True
    # Fuzzy fallback
    return _similarity(name1.lower(), name2.lower()) >= 85


# ── Internal helpers ──────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    """Return 0–100 similarity score between two strings."""
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(a, b)
    except ImportError:
        return _simple_similarity(a, b)


def _simple_similarity(a: str, b: str) -> float:
    """Basic similarity if rapidfuzz isn't installed."""
    stopwords = {"fc", "cf", "sc", "ac", "as", "cd", "ca", "the", "de", "la", "el"}
    def clean(s):
        return " ".join(w for w in s.split() if w not in stopwords)
    a_clean = clean(a)
    b_clean = clean(b)
    if a_clean == b_clean:
        return 100.0
    if a_clean in b_clean or b_clean in a_clean:
        return 90.0
    a_words = set(a_clean.split())
    b_words = set(b_clean.split())
    if not a_words or not b_words:
        return 0.0
    shared = a_words & b_words
    return (len(shared) / max(len(a_words), len(b_words))) * 100
