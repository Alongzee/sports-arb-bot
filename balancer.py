"""
balancer.py – Multi-bookmaker bankroll tracker for N-way arbitrage.

Tracks balance per bookmaker, calculates balanced stakes for any N-way arb,
manages capital lock, and enforces minimum account floors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from config import (
    TOTAL_BANKROLL,
    ACCOUNT_FLOOR,
    BOOKMAKERS,
)
from market_ai import ULTRA_FAST, FAST, MEDIUM, SLOW

log = logging.getLogger("balancer")


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class Account:
    """Single bookmaker account."""
    platform: str
    balance: float = 0.0        # current balance
    locked: float = 0.0          # capital in active arbs

    @property
    def available(self) -> float:
        """Usable balance (balance - locked)."""
        return max(0.0, self.balance - self.locked)


# ── Balancer ───────────────────────────────────────────────────────────────

class Balancer:
    """
    Manages multi-bookmaker accounts for N-way arbitrage.
    
    - Tracks balance per platform
    - Calculates balanced stakes for any two platforms
    - Enforces account floors
    - Manages capital lock for active arbs
    """

    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        
        # Initialize accounts for all bookmakers
        per_bookie = TOTAL_BANKROLL / len(BOOKMAKERS) if BOOKMAKERS else 0
        for bookie in BOOKMAKERS:
            self.accounts[bookie] = Account(platform=bookie, balance=per_bookie)
        
        log.info(f"Balancer initialized: {len(BOOKMAKERS)} bookmakers, GHS {TOTAL_BANKROLL} bankroll")

    # ── Balance queries ────────────────────────────────────────────────────
    def get_balance(self, platform: str = "all") -> float:
        """Get balance for one platform or all combined."""
        if platform == "all":
            return sum(acc.balance for acc in self.accounts.values())
        return self.accounts.get(platform, Account(platform)).balance

    def get_available(self, platform: str = "all") -> float:
        """Get available (unlocked) balance."""
        if platform == "all":
            return sum(acc.available for acc in self.accounts.values())
        return self.accounts.get(platform, Account(platform)).available

    def get_total_available(self) -> float:
        """Total available balance across all accounts."""
        return self.get_available("all")

    def get_min_margin(self) -> float:
        """Minimum margin % required for an arb to be placed."""
        # Could be dynamic based on account balances, but static for now
        from config import MIN_MARGIN_PCT
        return MIN_MARGIN_PCT

    def get_rebalance_bonus(self) -> float:
        """Bonus multiplier if accounts are imbalanced (encourages rebalancing)."""
        if not self.accounts:
            return 1.0
        
        balances = [acc.balance for acc in self.accounts.values()]
        avg_balance = sum(balances) / len(balances)
        
        # If any account is far below average, boost priority on arbs that favor it
        min_bal = min(balances)
        if min_bal < avg_balance * 0.7:
            return 1.2  # 20% priority boost
        return 1.0

    # ── Stake calculation ──────────────────────────────────────────────────
    def calculate_balanced_stakes(
        self,
        odds_a: float,
        odds_b: float,
        total_stake: float,
    ) -> Tuple[float, float]:
        """
        Calculate balanced stakes for a 2-way arb.
        
        If there's 100 GHS and odds are 1.8 vs 2.0:
        - stake_a should win 100 GHS
        - stake_b should also win 100 GHS
        
        Returns: (stake_a, stake_b)
        """
        if odds_a <= 1.0 or odds_b <= 1.0 or total_stake <= 0:
            return 0.0, 0.0
        
        # Target win = total_stake / 2 (both sides win equally)
        target_win = total_stake / 2.0
        
        stake_a = target_win / (odds_a - 1.0)
        stake_b = target_win / (odds_b - 1.0)
        
        # Round to 0.5 GHS increments
        from config import NON_ARB_ROUND_TO
        stake_a = round(stake_a / NON_ARB_ROUND_TO) * NON_ARB_ROUND_TO
        stake_b = round(stake_b / NON_ARB_ROUND_TO) * NON_ARB_ROUND_TO
        
        return stake_a, stake_b

    # ── Execution validation ───────────────────────────────────────────────
    def can_execute(
        self,
        stake_a: float,
        stake_b: float,
        min_margin: float,
        actual_margin: float,
    ) -> Tuple[bool, str]:
        """
        Check if an arb can be placed given current balances.
        
        Returns: (can_place, reason_if_not)
        """
        # Margin check
        if actual_margin < min_margin:
            return False, f"Margin {actual_margin:.2f}% below minimum {min_margin:.2f}%"
        
        # We don't know which platforms yet (that's in ArbEngine),
        # so we just check if there's enough total available
        total_needed = stake_a + stake_b
        total_available = self.get_total_available()
        
        if total_needed > total_available:
            return False, f"Need GHS {total_needed:.2f}, only GHS {total_available:.2f} available"
        
        # Rough floor check (more detailed check happens in lock_arb)
        if total_available - total_needed < ACCOUNT_FLOOR * len(self.accounts):
            return False, "Would violate account floors"
        
        return True, ""

    # ── Capital management ─────────────────────────────────────────────────
    def lock_arb(
        self,
        platform_a: str,
        stake_a: float,
        platform_b: str,
        stake_b: float,
    ) -> bool:
        """
        Lock capital for an active arb.
        Returns False if not enough balance on either platform.
        """
        acc_a = self.accounts.get(platform_a)
        acc_b = self.accounts.get(platform_b)
        
        if not acc_a or not acc_b:
            log.error(f"Platform not found: {platform_a} or {platform_b}")
            return False
        
        if acc_a.available < stake_a:
            log.error(f"{platform_a}: insufficient funds (need {stake_a}, have {acc_a.available})")
            return False
        
        if acc_b.available < stake_b:
            log.error(f"{platform_b}: insufficient funds (need {stake_b}, have {acc_b.available})")
            return False
        
        if (acc_a.balance - stake_a) < ACCOUNT_FLOOR:
            log.error(f"{platform_a}: would violate account floor")
            return False
        
        if (acc_b.balance - stake_b) < ACCOUNT_FLOOR:
            log.error(f"{platform_b}: would violate account floor")
            return False
        
        acc_a.locked += stake_a
        acc_b.locked += stake_b
        log.info(f"Locked: {platform_a} GHS {stake_a:.2f}, {platform_b} GHS {stake_b:.2f}")
        return True

    def unlock_arb(self, platform_a: str, stake_a: float, platform_b: str, stake_b: float):
        """Unlock capital (arb cancelled or result determined)."""
        self.accounts[platform_a].locked -= stake_a
        self.accounts[platform_b].locked -= stake_b
        log.info(f"Unlocked: {platform_a} GHS {stake_a:.2f}, {platform_b} GHS {stake_b:.2f}")

    def settle_arb_win(self, platform_a: str, stake_a: float, odds_a: float, platform_b: str, stake_b: float):
        """
        Settle a won arb.
        Side A won: gets profit from stake_a * odds_a.
        Side B lost: loses stake_b.
        """
        acc_a = self.accounts[platform_a]
        acc_b = self.accounts[platform_b]
        
        # Unlock
        acc_a.locked -= stake_a
        acc_b.locked -= stake_b
        
        # Payout
        profit_a = stake_a * (odds_a - 1.0)
        acc_a.balance += profit_a
        acc_b.balance -= stake_b
        
        log.info(f"Arb settled: {platform_a} +GHS {profit_a:.2f}, {platform_b} -GHS {stake_b:.2f}")

    def settle_arb_loss(self, platform_a: str, stake_a: float, platform_b: str, stake_b: float, odds_b: float):
        """
        Settle when Side B wins instead.
        Side B won: gets profit from stake_b * odds_b.
        Side A lost: loses stake_a.
        """
        acc_a = self.accounts[platform_a]
        acc_b = self.accounts[platform_b]
        
        # Unlock
        acc_a.locked -= stake_a
        acc_b.locked -= stake_b
        
        # Payout
        profit_b = stake_b * (odds_b - 1.0)
        acc_a.balance -= stake_a
        acc_b.balance += profit_b
        
        log.info(f"Arb settled: {platform_a} -GHS {stake_a:.2f}, {platform_b} +GHS {profit_b:.2f}")

    # ── Status ─────────────────────────────────────────────────────────────
    def get_status(self) -> str:
        """Return a formatted status string."""
        lines = ["Balancer Status:", "─" * 40]
        for bookie, acc in self.accounts.items():
            lines.append(f"{bookie:15} Balance: GHS {acc.balance:8.2f}  Locked: GHS {acc.locked:8.2f}  Available: GHS {acc.available:8.2f}")
        lines.append("─" * 40)
        lines.append(f"{'TOTAL':15} Balance: GHS {self.get_balance():8.2f}  Locked: GHS {sum(a.locked for a in self.accounts.values()):8.2f}  Available: GHS {self.get_total_available():8.2f}")
        return "\n".join(lines)
