from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional, Tuple

from core_types import Sport
from market_types import MarketType


@dataclass(frozen=True, slots=True)
class CanonicalOutcome:
    """
    One betting outcome.

    Examples:
      home @ 2.10
      over @ 1.95
      yes @ 1.80
    """
    label: str
    odds: Decimal


@dataclass(frozen=True, slots=True)
class CanonicalMarket:
    """
    A normalized bookmaker market.
    """
    market_type: MarketType

    # Example:
    # over 2.5
    # handicap -1.0
    line: Optional[Decimal]

    outcomes: Tuple[CanonicalOutcome, ...]


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    """
    Fully normalized sporting event.
    """
    event_id: str

    bookmaker: str

    sport: Sport

    home_team: str
    away_team: str

    start_time: datetime

    markets: Tuple[CanonicalMarket, ...]
