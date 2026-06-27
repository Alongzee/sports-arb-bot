"""
arb_engine.py – Arbitrage math engine with N-way support.
Finds best-odds pairs across any number of bookmakers (2-way, 3-way, etc).
"""

from __future__ import annotations

import logging
from datetime import timezone
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from market_ai import (
    get_speed_score, get_stability_score, calculate_priority_score,
    find_cross_market_opposite,
    ULTRA_FAST, FAST,
)
from balancer import Balancer
from matcher import normalise_market, match_teams
from config import (
    MIN_MARGIN_PCT,
    GOLDEN_ARB_MIN_MARGIN,
    GOLDEN_ARB_MAX_SETTLE_MIN,
    GOLDEN_ARB_MAX_PER_DAY,
    DUPLICATE_WINDOW,
    COOL_OFF_DAYS,
    BOOKMAKERS,
)

log = logging.getLogger("arb_engine")


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class ArbOpportunity:
    arb_id: int
    match_name: str
    sport: str
    market_display: str
    market_key: str
    sides: Dict[str, Tuple[str, float]]  # side → (platform, odds)  e.g. {"over": ("sportybet", 1.92), "under": ("betway", 2.05)}
    margin_pct: float
    priority_score: float
    speed: int
    stability: float
    is_live: bool
    minutes_to_kickoff: float
    is_golden_override: bool = False
    score_breakdown: dict = field(default_factory=dict)
    num_platforms: int = 2  # 2-way, 3-way, etc


@dataclass
class ArbResult:
    opportunity: Optional[ArbOpportunity] = None
    skipped_reason: str = ""


# ── Engine ────────────────────────────────────────────────────────────────

class ArbEngine:
    """
    Evaluates N-way markets and ranks arbs.
    Returns the single best opportunity per scan cycle.
    """

    def __init__(self, balancer: Balancer) -> None:
        self.balancer = balancer
        self._callbacks: List[Callable] = []
        self._next_id = 1
        self._last_alerted: Dict[str, float] = {}
        self._golden_arbs_today = 0
        self._golden_reset_day = -1

    def on_opportunity(self, cb: Callable) -> None:
        self._callbacks.append(cb)

    # ── Duplicate detection ────────────────────────────────────────────
    def _is_duplicate(self, match_name: str, market_key: str) -> bool:
        key = f"{match_name}:{market_key}"
        last = self._last_alerted.get(key, 0)
        import time
        return (time.time() - last) < DUPLICATE_WINDOW

    def _mark_alerted(self, match_name: str, market_key: str) -> None:
        import time
        self._last_alerted[f"{match_name}:{market_key}"] = time.time()

    # ── Cool‑off / golden arb logic ────────────────────────────────────
    def _is_cool_off(self) -> bool:
        import datetime
        return datetime.datetime.now(timezone.utc).weekday() in COOL_OFF_DAYS

    def _can_golden_override(self, margin_pct: float, speed: int) -> bool:
        import datetime
        now = datetime.datetime.now(timezone.utc)
        if now.weekday() != self._golden_reset_day:
            self._golden_arbs_today = 0
            self._golden_reset_day = now.weekday()
        if self._golden_arbs_today >= GOLDEN_ARB_MAX_PER_DAY:
            return False
        return (
            margin_pct >= GOLDEN_ARB_MIN_MARGIN
            and speed in (ULTRA_FAST, FAST)
        )

    # ── N-way arbitrage evaluation ─────────────────────────────────────
    def evaluate_nway(
        self,
        sport: str,
        match_name: str,
        market_key: str,
        sides_odds: Dict[str, List[Tuple[str, float]]],
        is_live: bool,
        minutes_to_kickoff: float,
        market_display: str = "",
    ) -> Optional[ArbOpportunity]:
        """
        Evaluate N-way arbitrage for a single market.
        
        sides_odds format:
        {
            "over": [("sportybet", 1.72), ("betway", 1.88), ...],
            "under": [("1win", 2.01), ("betway", 1.95), ...],
        }
        
        Picks the single best odds for each side and checks if arb exists.
        """
        
        # Pick best odds per side
        best_sides = {}
        for side, bookies in sides_odds.items():
            if not bookies:
                return None
            # Sort by odds descending, pick the best (highest)
            best_platform, best_odds = max(bookies, key=lambda x: x[1])
            best_sides[side] = (best_platform, best_odds)

        # Need at least 2 sides for an arb
        if len(best_sides) < 2:
            return None

        # Check if sides are from DIFFERENT platforms (basic arb requirement)
        platforms = {p for p, _ in best_sides.values()}
        if len(platforms) < 2:
            return None  # all sides from same platform = no arb

        # Calculate implied probability
        implied = sum(1.0 / odds for _, odds in best_sides.values())
        
        if implied >= 1.0:
            return None  # no arb

        margin_pct = (1.0 - implied) * 100

        # ── Minimum margin check ────────────────────────────────────
        min_margin = self.balancer.get_min_margin()
        is_cool = self._is_cool_off()

        if is_cool:
            if not self._can_golden_override(margin_pct, self._get_speed(sport, market_key)):
                return None
            golden = True
            self._golden_arbs_today += 1
        else:
            golden = False
            if margin_pct < min_margin:
                return None

        # ── Priority scoring ────────────────────────────────────────
        speed = self._get_speed(sport, market_key)
        stability = get_stability_score(is_live, minutes_to_kickoff)
        score, breakdown = calculate_priority_score(
            margin_pct, sport, market_key, is_live, minutes_to_kickoff,
        )

        # ── Rebalance bonus ────────────────────────────────────────
        rebalance_bonus = self.balancer.get_rebalance_bonus()
        final_score = score * rebalance_bonus

        # ── Stakes (calculate for just the first 2 sides, order doesn't matter) ──
        sides_list = list(best_sides.items())
        _, (_, odds_a) = sides_list[0]
        _, (_, odds_b) = sides_list[1]
        
        stake_a, stake_b = self.balancer.calculate_balanced_stakes(
            odds_a, odds_b,
            self.balancer.get_total_available(),
        )

        # ── Viability ────────────────────────────────────────────────
        viable, reason = self.balancer.can_execute(
            stake_a, stake_b, min_margin, margin_pct,
        )
        if not viable:
            log.info(f"Arb skipped: {reason}")
            return None

        return ArbOpportunity(
            arb_id=self._next_id,
            match_name=match_name,
            sport=sport,
            market_display=market_display or market_key,
            market_key=market_key,
            sides=best_sides,
            margin_pct=margin_pct,
            priority_score=final_score,
            speed=speed,
            stability=stability,
            is_live=is_live,
            minutes_to_kickoff=minutes_to_kickoff,
            is_golden_override=golden,
            score_breakdown=breakdown,
            num_platforms=len(platforms),
        )

    # ── Batch scan ─────────────────────────────────────────────────────
    def scan_all(
        self,
        sport: str,
        match_name: str,
        markets: Dict[str, Dict[str, List[Tuple[str, float]]]],
        is_live: bool,
        minutes_to_kickoff: float,
    ) -> ArbResult:
        """
        Scan all markets for a match and return the single best arb.
        
        markets format:
        {
            "over_under_2.5": {
                "over": [("sportybet", 1.72), ("betway", 1.88)],
                "under": [("1win", 2.01), ("betway", 1.95)],
            },
            ...
        }
        """
        candidates: List[ArbOpportunity] = []

        for market_key, sides_odds in markets.items():
            # Skip duplicates
            if self._is_duplicate(match_name, market_key):
                continue

            arb = self.evaluate_nway(
                sport, match_name, market_key,
                sides_odds,
                is_live, minutes_to_kickoff,
            )
            if arb:
                candidates.append(arb)
                self._mark_alerted(match_name, market_key)

        if not candidates:
            return ArbResult(skipped_reason="No viable arb")

        # Sort by priority score (higher = better)
        candidates.sort(key=lambda a: a.priority_score, reverse=True)
        best = candidates[0]

        # Assign arb ID
        best.arb_id = self._next_id
        self._next_id += 1

        return ArbResult(opportunity=best)

    # ── Helpers ────────────────────────────────────────────────────────
    def _get_speed(self, sport: str, market_key: str) -> int:
        from market_ai import get_settlement_speed
        return get_settlement_speed(sport, market_key)
