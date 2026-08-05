"""Trade valuation: what a deal does to the lineup you can actually field."""

from fantasyedge.trade.evaluate import (
    TradeVerdict,
    evaluate,
    simulate_lineup,
)
from fantasyedge.trade import market, suggest

__all__ = ["TradeVerdict", "evaluate", "simulate_lineup", "market", "suggest"]
