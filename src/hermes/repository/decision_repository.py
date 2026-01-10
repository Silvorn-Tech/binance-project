from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from hermes.persistence.models import DecisionLog, DecisionType


class DecisionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_decision(
        self,
        *,
        asset_id: int,
        profile_id: int,
        decision_type: DecisionType,
        regime_detected: str,
        confidence_score: float,
        reason: str,
        timestamp: datetime | None = None,
    ) -> DecisionLog:
        decision = DecisionLog(
            asset_id=asset_id,
            profile_id=profile_id,
            decision_type=decision_type,
            regime_detected=regime_detected,
            confidence_score=confidence_score,
            reason=reason,
            timestamp=timestamp or datetime.utcnow(),
        )

        self.session.add(decision)
        self.session.commit()
        self.session.refresh(decision)

        return decision
