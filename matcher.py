"""
matcher.py – Fuzzy team matching + bookmaker-aware market normalisation.
Handles:
- Team aliases + fuzzy matching
- Cross-bookmaker market label normalisation
- Event fingerprint generation
"""

from __future__ import annotations

import re
from typing import Dict, List


# ── Market label aliases ────────────────────────────────────────────────

MARKET_ALIASES: Dict[str, List[str]] = {
    "over_under": [
        "over/under", "total goals", "total", "over/under goals",
        "goals over/under", "over / under", "totals",
        "total goals over/under", "goal line", "goals", "o/u",
    ],
    "asian_handicap": [
        "asian handicap", "handicap (asian)", "ah",
        "asian handicap 1", "asian handicap 2",
        "asian hcp", "handicap 2-way",
    ],
    "winner": [
        "winner", "match winner", "full time result",
        "1x2", "moneyline",
        "winner (incl. overtime)", "winner (incl. ot)",
    ],
    "both_to_score": [
        "both teams to score", "btts",
        "gg/ng", "both to score",
    ],
    "double_chance": [
        "double chance", "dc",
    ],
    "draw_no_bet": [
        "draw no bet", "dnb",
    ],
    "odd_even": [
        "odd/even", "odd / even",
    ],
}


# ── Team aliases ────────────────────────────────────────────────────────

TEAM_ALIASES: Dict[str, List[str]] = {
    "CDP Junior FC": [
        "cdp junior",
        "cdp junior fc",
        "junior fc",
        "cdp",
        "junior",
    ],
    "Cerro Porteño": [
        "cerro porteno",
        "cerro porteño",
        "cerro",
    ],
    "Crystal Palace": [
        "crystal palace",
        "palace",
        "crystal",
    ],
    "Shakhtar Donetsk": [
        "shakhtar",
        "shakhtar donetsk",
    ],
    "Bayern München": [
        "bayern",
        "bayern munich",
        "bayern münchen",
    ],
    "Paris Saint Germain": [
        "psg",
        "paris sg",
        "paris saint germain",
    ],
    "Oklahoma City Thunder": [
        "oklahoma city thunder",
        "thunder",
        "okc",
    ],
    "Los Angeles Lakers": [
        "los angeles lakers",
        "la lakers",
        "lakers",
    ],
}


# ── Bookmaker-specific fixes ────────────────────────────────────────────

BOOKMAKER_MARKET_FIXES = {
    "sportybet": {
        "o/u": "over/under",
    },
    "1win": {
        "total": "over/under",
    },
    "bet365": {
        "goals over/under": "over/under",
    },
    "melbet": {
        "o/u": "over/under",
    },
}


# ── Precomputed lookup tables ───────────────────────────────────────────

_MARKET_LOOKUP: Dict[str, str] = {}

for standard, aliases in MARKET_ALIASES.items():
    for alias in aliases:
        _MARKET_LOOKUP[alias.lower()] = standard


_TEAM_LOOKUP: Dict[str, str] = {}

for standard, aliases in TEAM_ALIASES.items():
    for alias in aliases:
        _TEAM_LOOKUP[alias.lower()] = standard


# ── Market preprocessing ────────────────────────────────────────────────

def preprocess_market_name(bookmaker: str, market_name: str) -> str:
    """
    Apply bookmaker-specific cleanup before normalisation.
    """

    name = market_name.strip().lower()

    fixes = BOOKMAKER_MARKET_FIXES.get(bookmaker.lower(), {})

    for src, dst in fixes.items():
        name = name.replace(src, dst)

    return name


# ── Market normalisation ────────────────────────────────────────────────

def normalise_market(
    market_name: str,
    bookmaker: str | None = None,
) -> str:
    """
    Convert bookmaker-specific market names
    into canonical internal market keys.
    """

    if bookmaker:
        name_lower = preprocess_market_name(bookmaker, market_name)
    else:
        name_lower = market_name.strip().lower()

    # Exact alias lookup
    if name_lower in _MARKET_LOOKUP:
        return _MARKET_LOOKUP[name_lower]

    # Partial alias lookup
    for alias, standard in _MARKET_LOOKUP.items():
        if alias in name_lower:
            return standard

    # Fallback
    return re.sub(r"\s+", "_", name_lower)


# ── Team normalisation ──────────────────────────────────────────────────

def normalise_team(name: str) -> str:
    """
    Convert bookmaker team names into canonical names.
    """

    name_lower = re.sub(
        r"\s+",
        " ",
        name.strip().lower(),
    )

    # Exact alias lookup
    if name_lower in _TEAM_LOOKUP:
        return _TEAM_LOOKUP[name_lower]

    # Fuzzy fallback
    for alias, standard in _TEAM_LOOKUP.items():
        if _similarity(name_lower, alias) >= 85:
            return standard

    return name_lower


# ── Team matching ───────────────────────────────────────────────────────

def match_teams(name1: str, name2: str) -> bool:
    """
    Return True if two team names refer
    to the same team.
    """

    if normalise_team(name1) == normalise_team(name2):
        return True

    return _similarity(name1.lower(), name2.lower()) >= 85


# ── Event fingerprinting ────────────────────────────────────────────────

def build_event_key(
    home_team: str,
    away_team: str,
    sport: str,
) -> str:
    """
    Build deterministic cross-bookmaker event key.
    """

    home = normalise_team(home_team)
    away = normalise_team(away_team)

    ordered = sorted([home, away])

    return f"{sport}:{ordered[0]}:{ordered[1]}"


# ── Similarity helpers ──────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    """
    Return similarity score from 0–100.
    """

    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(a, b)

    except ImportError:
        return _simple_similarity(a, b)


def _simple_similarity(a: str, b: str) -> float:
    """
    Lightweight similarity fallback.
    """

    stopwords = {
        "fc", "cf", "sc", "ac",
        "as", "cd", "ca",
        "the", "de", "la", "el",
    }

    def clean(s: str) -> str:
        return " ".join(
            w for w in s.split()
            if w not in stopwords
        )

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

    return (
        len(shared) / max(len(a_words), len(b_words))
    ) * 100
