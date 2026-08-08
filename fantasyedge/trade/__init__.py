"""Trade valuation: what a deal does to the lineup you can actually field."""

from fantasyedge.trade.evaluate import (
    TradeVerdict,
    compare_packages,
    evaluate,
    simulate_lineup,
)
from fantasyedge.trade import explain, market, suggest

__all__ = ["TradeVerdict", "evaluate", "compare_packages", "simulate_lineup", "explain", "market", "suggest"]
