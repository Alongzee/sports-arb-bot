"""
core_types.py – Shared canonical enum/type definitions.
"""

from enum import Enum


class Sport(str, Enum):
    FOOTBALL = "football"
    BASKETBALL = "basketball"
    TENNIS = "tennis"
    TABLE_TENNIS = "table_tennis"



class OutcomeType(str, Enum):
    HOME = "home"
    AWAY = "away"
    DRAW = "draw"

    OVER = "over"
    UNDER = "under"

    YES = "yes"
    NO = "no"

    PLAYER1 = "player1"
    PLAYER2 = "player2"

    ODD = "odd"
    EVEN = "even"

    HOME_OR_DRAW = "home_or_draw"
    HOME_OR_AWAY = "home_or_away"
    DRAW_OR_AWAY = "draw_or_away"
