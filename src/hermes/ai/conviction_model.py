from __future__ import annotations

from hermes.ai.types import ConvictionAssessment


class EntryConvictionModel:
    def assess(self, *, conviction_score: float) -> ConvictionAssessment:
        return ConvictionAssessment(conviction_score=conviction_score)
