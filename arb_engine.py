"""
arb_engine.py — Pure arbitrage math for two-way markets.
No prioritisation. No filtering. Raw calculation only.
Returns every arb detected, regardless of margin.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ArbOpportunity:
    """A single arbitrage opportunity across two platforms."""
    match_name: str
    market: str
    best_over_platform: str
    best_over_odds: float
    best_under_platform: str
    best_under_odds: float
    combined_imp: float
    margin_pct: float
    stake_over: float        # Stake on the Over/Home side (GHS)
    stake_under: float       # Stake on the Under/Away side (GHS)
    total_stake: float
    payout: float
    profit: float
    mode: str = "active"     # "active" or "passive"


def find_best_pair(odds_list: List[dict]) -> Optional[Tuple]:
    """
    Given a list of odds dictionaries from multiple platforms,
    find the best Over and best Under across all of them.

    Each dict format: {
        "platform": "sportybet",
        "odds": {"over": 1.72, "under": 2.01}
    }

    Returns (best_over_platform, best_over_odds, best_under_platform, best_under_odds)
    or None if fewer than 2 platforms have data.
    """
    if len(odds_list) < 2:
        return None

    best_over = None
    best_over_plat = None
    best_under = None
    best_under_plat = None

    for entry in odds_list:
        plat = entry["platform"]
        odds = entry.get("odds", {})

        over = odds.get("over")
        under = odds.get("under")

        if over and (best_over is None or over > best_over):
            best_over = over
            best_over_plat = plat

        if under and (best_under is None or under > best_under):
            best_under = under
            best_under_plat = plat

    if best_over and best_under and best_over_plat != best_under_plat:
        return (best_over_plat, best_over, best_under_plat, best_under)

    return None


def calculate_arb(
    best_over_plat: str,
    best_over_odds: float,
    best_under_plat: str,
    best_under_odds: float,
    total_bankroll: float,
    match_name: str = "",
    market: str = "",
    mode: str = "active",
) -> Optional[ArbOpportunity]:
    """
    Calculate whether an arbitrage exists and compute exact stakes.

    Arbitrage exists if: (1 / best_over_odds) + (1 / best_under_odds) < 1.0

    Returns ArbOpportunity if profitable, None otherwise.
    """
    # Implied probabilities
    imp_over = 1.0 / best_over_odds
    imp_under = 1.0 / best_under_odds
    combined = imp_over + imp_under

    # No arb if combined >= 1.0
    if combined >= 1.0:
        return None

    # Margin percentage
    margin_pct = (1.0 - combined) * 100

    # Stake calculation — proportioned by implied probability
    stake_over = total_bankroll * (imp_over / combined)
    stake_under = total_bankroll * (imp_under / combined)

    # Payout is the same regardless of which side wins
    payout = stake_over * best_over_odds
    profit = payout - total_bankroll

    return ArbOpportunity(
        match_name=match_name,
        market=market,
        best_over_platform=best_over_plat,
        best_over_odds=best_over_odds,
        best_under_platform=best_under_plat,
        best_under_odds=best_under_odds,
        combined_imp=combined,
        margin_pct=round(margin_pct, 4),
        stake_over=round(stake_over, 2),
        stake_under=round(stake_under, 2),
        total_stake=total_bankroll,
        payout=round(payout, 2),
        profit=round(profit, 2),
        mode=mode,
    )


def evaluate_all_markets(
    market_data: Dict[str, List[dict]],
    total_bankroll: float,
    match_name: str = "",
    mode: str = "active",
) -> List[ArbOpportunity]:
    """
    Takes all scraped market data for one match and evaluates every
    two-way market for arbitrage opportunities.

    market_data format:
    {
        "over_under_2.5": [
            {"platform": "sportybet", "odds": {"over": 1.72, "under": 2.01}},
            {"platform": "1win", "odds": {"over": 1.80, "under": 1.95}},
        ],
        "asian_handicap_-2.5": [
            {"platform": "sportybet", "odds": {"over": 4.80, "under": 1.19}},
            {"platform": "1win", "odds": {"over": 5.90, "under": 1.25}},
        ],
    }

    Returns list of ArbOpportunity objects (empty if none found).
    """
    results = []

    for market, odds_list in market_data.items():
        pair = find_best_pair(odds_list)
        if pair is None:
            continue

        arb = calculate_arb(
            best_over_plat=pair[0],
            best_over_odds=pair[1],
            best_under_plat=pair[2],
            best_under_odds=pair[3],
            total_bankroll=total_bankroll,
            match_name=match_name,
            market=market,
            mode=mode,
        )

        if arb:
            results.append(arb)

    return results
