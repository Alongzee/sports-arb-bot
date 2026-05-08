"""
balancer.py – Dual‑account bankroll tracker, capital lock, stake rounding,
              hedge correction, and flexible minimum margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import (
    ACCOUNT_FLOOR,
    TOTAL_BANKROLL,
    BOOKMAKER_YOU,
    BOOKMAKER_FRIEND,
)
from market_ai import ULTRA_FAST, FAST, MEDIUM, SLOW, get_settlement_speed


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class Account:
    platform: str
    balance: float = 0.0
    locked: float = 0.0          # capital currently in active arbs

    @property
    def available(self) -> float:
        return max(0.0, self.balance - self.locked)


@dataclass
class ActiveArb:
    arb_id: int
    sport: str
    market_key: str
    stake_you: float
    stake_friend: float
    locked: bool = True           # True until settled
    settlement_speed: int = FAST


# ── Balancer engine ───────────────────────────────────────────────────────

class Balancer:
    """
    Tracks two accounts, manages locked capital, and provides
    stake‑rounding with automatic hedge correction.
    """

    def __init__(self) -> None:
        self.accounts: Dict[str, Account] = {
            BOOKMAKER_YOU:    Account(BOOKMAKER_YOU,    TOTAL_BANKROLL / 2),
            BOOKMAKER_FRIEND: Account(BOOKMAKER_FRIEND, TOTAL_BANKROLL / 2),
        }
        self.active_arbs: Dict[int, ActiveArb] = {}
        self._next_arb_id = 1

    # ── Balance sync ──────────────────────────────────────────────────

    def set_balances(self, you: float, friend: float) -> None:
        """Manual override – call from /balance command."""
        self.accounts[BOOKMAKER_YOU].balance = you
        self.accounts[BOOKMAKER_FRIEND].balance = friend

    def get_available(self, platform: str) -> float:
        return self.accounts[platform].available

    def get_balance(self, platform: str) -> float:
        return self.accounts[platform].balance

    def get_total_available(self) -> float:
        return sum(a.available for a in self.accounts.values())

    # ── Flexible minimum margin ────────────────────────────────────────

    def get_min_margin(self) -> float:
        """
        Returns the minimum margin that should be required right now.
        Drops from 1.5 % → 1.2 % → 0.8 % as imbalance grows.
        """
        bal_you = self.accounts[BOOKMAKER_YOU].balance
        bal_friend = self.accounts[BOOKMAKER_FRIEND].balance
        total = bal_you + bal_friend
        if total == 0:
            return 1.5

        pct_you = (bal_you / total) * 100
        imbalance = abs(pct_you - 50)

        if imbalance < 10:        # 40‑60 % → balanced
            return 1.5
        elif imbalance < 20:      # 30‑70 % → moderate
            return 1.2
        else:                     # >70 % → severe
            return 0.8

    # ── Arb viability check ────────────────────────────────────────────

    def can_execute(
        self,
        stake_you: float,
        stake_friend: float,
        min_margin: float = 1.5,
        margin_pct: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Returns (True, "") if both accounts have enough available funds
        AND the arb meets the current minimum margin.
        """
        if margin_pct < min_margin:
            return False, f"Margin {margin_pct:.1f} % < min {min_margin:.1f} %"

        if stake_you > self.accounts[BOOKMAKER_YOU].available:
            return False, f"Insufficient funds on {BOOKMAKER_YOU}"
        if stake_friend > self.accounts[BOOKMAKER_FRIEND].available:
            return False, f"Insufficient funds on {BOOKMAKER_FRIEND}"

        # Ensure neither account drops below floor after stake
        if (self.accounts[BOOKMAKER_YOU].balance - stake_you) < ACCOUNT_FLOOR:
            return False, f"{BOOKMAKER_YOU} would drop below floor"
        if (self.accounts[BOOKMAKER_FRIEND].balance - stake_friend) < ACCOUNT_FLOOR:
            return False, f"{BOOKMAKER_FRIEND} would drop below floor"

        return True, ""

    # ── Stake rounding with hedge correction ───────────────────────────

    @staticmethod
    def round_stake(amount: float) -> float:
        """
        Round stake according to our security rules.
        <10   → nearest 0.50
        10‑50 → nearest 1.00
        >50   → nearest 5.00
        """
        if amount < 10:
            return round(amount * 2) / 2    # nearest 0.50
        elif amount < 50:
            return round(amount)             # nearest 1
        else:
            return round(amount / 5) * 5     # nearest 5

    def calculate_balanced_stakes(
        self,
        odds_you: float,
        odds_friend: float,
        total_capital: float | None = None,
    ) -> Tuple[float, float]:
        """
        Given two odds, compute exact stakes, round the first one,
        then recalculate the second stake so the hedge remains intact.
        Returns (rounded_stake_you, corrected_stake_friend).
        """
        if total_capital is None:
            total_capital = self.get_total_available()

        # Implied probabilities
        imp_you = 1.0 / odds_you
        imp_friend = 1.0 / odds_friend
        total_imp = imp_you + imp_friend

        # Exact stakes
        exact_you = total_capital * (imp_you / total_imp)
        exact_friend = total_capital * (imp_friend / total_imp)

        # Round your stake, correct friend's
        rounded_you = self.round_stake(exact_you)
        # Recalculate friend's stake to maintain the same payout ratio
        corrected_friend = rounded_you * (odds_you / odds_friend)
        # Round friend's stake
        rounded_friend = self.round_stake(corrected_friend)

        return rounded_you, rounded_friend

    # ── Capital lock / release ─────────────────────────────────────────

    def lock_capital(
        self,
        sport: str,
        market_key: str,
        stake_you: float,
        stake_friend: float,
    ) -> int:
        """Lock capital for a new arb; returns arb_id."""
        arb_id = self._next_arb_id
        self._next_arb_id += 1

        speed = get_settlement_speed(sport, market_key)
        self.active_arbs[arb_id] = ActiveArb(
            arb_id=arb_id,
            sport=sport,
            market_key=market_key,
            stake_you=stake_you,
            stake_friend=stake_friend,
            settlement_speed=speed,
        )

        self.accounts[BOOKMAKER_YOU].locked += stake_you
        self.accounts[BOOKMAKER_FRIEND].locked += stake_friend
        return arb_id

    def release_capital(self, arb_id: int, winner: str) -> None:
        """
        Release capital after settlement.
        winner = 'you' | 'friend' (which platform won)
        """
        arb = self.active_arbs.pop(arb_id, None)
        if arb is None:
            return

        self.accounts[BOOKMAKER_YOU].locked -= arb.stake_you
        self.accounts[BOOKMAKER_FRIEND].locked -= arb.stake_friend

        # Payout: the winning side gets the total profit added
        payout_you = arb.stake_you * (1 + arb.stake_friend / arb.stake_you)  # approx
        # Simpler: just add the profit to the winner, remove stake from loser
        if winner == "you":
            self.accounts[BOOKMAKER_YOU].balance += arb.stake_friend  # profit
        else:
            self.accounts[BOOKMAKER_FRIEND].balance += arb.stake_you

    def get_locked_arbs(self) -> List[ActiveArb]:
        return list(self.active_arbs.values())

    # ── Rebalance priority ─────────────────────────────────────────────

    def get_rebalance_bonus(self) -> float:
        """
        Returns a multiplier (1.0‑1.5) that boosts arbs favouring
        the weaker account. Used by arb engine when ranking opportunities.
        """
        bal_you = self.accounts[BOOKMAKER_YOU].balance
        bal_friend = self.accounts[BOOKMAKER_FRIEND].balance
        total = bal_you + bal_friend
        if total == 0:
            return 1.0

        pct_you = (bal_you / total) * 100
        imbalance = abs(pct_you - 50)

        if imbalance < 15:
            return 1.0
        elif imbalance < 30:
            return 1.2
        else:
            return 1.5
