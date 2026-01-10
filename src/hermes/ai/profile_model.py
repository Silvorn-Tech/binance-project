from __future__ import annotations

from hermes.ai.types import ProfileAssessment, ProfileRecommendation


class ProfileConfidenceModel:
    def evaluate(self, *, confidence_score: float) -> ProfileAssessment:
        if confidence_score < 0.3:
            return ProfileAssessment(
                confidence_score=confidence_score,
                recommendation=ProfileRecommendation.NO_TRADE,
            )
        if confidence_score < 0.6:
            return ProfileAssessment(
                confidence_score=confidence_score,
                recommendation=ProfileRecommendation.CAUTIOUS,
            )
        return ProfileAssessment(
            confidence_score=confidence_score,
            recommendation=ProfileRecommendation.NORMAL,
        )
