from __future__ import annotations

from dataclasses import dataclass
import enum


class MarketRegime(str, enum.Enum):
    TREND_FRIENDLY = "TREND_FRIENDLY"
    CHOPPY = "CHOPPY"
    NO_EDGE = "NO_EDGE"


class ProfileRecommendation(str, enum.Enum):
    NORMAL = "NORMAL"
    CAUTIOUS = "CAUTIOUS"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class ProfileAssessment:
    confidence_score: float
    recommendation: ProfileRecommendation


@dataclass(frozen=True)
class ConvictionAssessment:
    conviction_score: float
    trailing_adjustment: float | None = None
    timeout_adjustment_seconds: float | None = None
