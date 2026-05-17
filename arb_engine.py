"""
arb_engine.py – Arbitrage math engine with three‑factor priority scoring,
                same‑match bonus detection, and golden arb override.
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
    BOOKMAKER_YOU,
    BOOKMAKER_FRIEND,
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
    platform_over: str           # which platform has the best Over / Home / Yes
    platform_under: str          # which platform has the best Under / Away / No
    odds_over: float
    odds_under: float
    stake_over: float
    stake_under: float
    margin_pct: float
    priority_score: float
    speed: int
    stability: float
    is_live: bool
    minutes_to_kickoff: float
    is_golden_override: bool = False
    score_breakdown: dict = field(default_factory=dict)
    same_match_bonus: bool = False


@dataclass
class ArbResult:
    opportunity: Optional[ArbOpportunity] = None
    skipped_reason: str = ""


# ── Engine ────────────────────────────────────────────────────────────────

class ArbEngine:
    """
    Evaluates every two‑way market pair and cross‑market pair,
    ranks arbs, and returns the single best opportunity per scan cycle.
    """

    def __init__(self, balancer: Balancer) -> None:
        self.balancer = balancer
        self._callbacks: List[Callable] = []
        self._next_id = 1
        self._last_alerted: Dict[str, float] = {}   # market_key → timestamp
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

    # ── Core evaluation ────────────────────────────────────────────────
    def evaluate(
        self,
        sport: str,
        match_name: str,
        market_key: str,
        odds_a: Tuple[str, float],   # (platform, odds) for side A
        odds_b: Tuple[str, float],   # (platform, odds) for side B
        is_live: bool,
        minutes_to_kickoff: float,
        market_display: str = "",
    ) -> Optional[ArbOpportunity]:
        """
        Calculate whether an arb exists and score it.
        odds_a and odds_b must be from DIFFERENT platforms.
        """
        plat_a, odd_a = odds_a
        plat_b, odd_b = odds_b

        if plat_a == plat_b:
            return None  # same platform = no arb

        # Implied probability
        imp_a = 1.0 / odd_a
        imp_b = 1.0 / odd_b
        combined = imp_a + imp_b

        if combined >= 1.0:
            return None  # no arb

        margin_pct = (1.0 - combined) * 100

        # ── Minimum margin (flexible, from balancer) ─────────────────
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

        # ── Priority scoring ─────────────────────────────────────────
        speed = self._get_speed(sport, market_key)
        stability = get_stability_score(is_live, minutes_to_kickoff)
        score, breakdown = calculate_priority_score(
            margin_pct, sport, market_key, is_live, minutes_to_kickoff,
        )

        # ── Rebalance bonus ──────────────────────────────────────────
        rebalance_bonus = self.balancer.get_rebalance_bonus()
        final_score = score * rebalance_bonus

        # ── Stakes ───────────────────────────────────────────────────
        stake_over, stake_under = self.balancer.calculate_balanced_stakes(
            odd_a, odd_b,
            self.balancer.get_total_available(),
        )

        # ── Viability ────────────────────────────────────────────────
        viable, reason = self.balancer.can_execute(
            stake_over, stake_under, min_margin, margin_pct,
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
            platform_over=plat_a,
            platform_under=plat_b,
            odds_over=odd_a,
            odds_under=odd_b,
            stake_over=stake_over,
            stake_under=stake_under,
            margin_pct=margin_pct,
            priority_score=final_score,
            speed=speed,
            stability=stability,
            is_live=is_live,
            minutes_to_kickoff=minutes_to_kickoff,
            is_golden_override=golden,
            score_breakdown=breakdown,
        )

    # ── Batch scan ─────────────────────────────────────────────────────
    def scan_all(
        self,
        sport: str,
        match_name: str,
        markets: Dict[str, Dict[str, Tuple[str, float]]],
        is_live: bool,
        minutes_to_kickoff: float,
    ) -> ArbResult:
        """
        markets format:
        {
            "over_under_2.5": {
                "over":  ("sportybet", 1.72),
                "under": ("1win", 2.01),
            },
            ...
        }
        Returns the single best arb for this match, or an empty result.
        """
        candidates: List[ArbOpportunity] = []

        for market_key, sides in markets.items():
            if "over" in sides and "under" in sides:
                side_a = sides["over"]
                side_b = sides["under"]
            elif "home" in sides and "away" in sides:
                side_a = sides["home"]
                side_b = sides["away"]
            else:
                continue

            if not side_a or not side_b:
                continue

            # Skip duplicates
            if self._is_duplicate(match_name, market_key):
                continue

            arb = self.evaluate(
                sport, match_name, market_key,
                side_a, side_b,
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

    # ── Helpers ──────────────────────────────────────────────────────────
    def _get_speed(self, sport: str, market_key: str) -> int:
        from market_ai import get_settlement_speed
        return get_settlement_speed(sport, market_key) 
