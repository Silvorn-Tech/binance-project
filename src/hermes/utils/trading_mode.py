from enum import Enum


class TradingMode(str, Enum):
    SIMULATION = "simulation"
    ARMED = "armed"
    LIVE = "live"
    AI = "ai"
