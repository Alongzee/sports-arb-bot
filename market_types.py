from enum import Enum

class MarketType(str, Enum):
MONEYLINE = "moneyline"
TOTAL = "total"
SPREAD = "spread"
PROP = "prop"
COMBO = "combo"
