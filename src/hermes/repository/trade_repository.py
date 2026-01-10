from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from hermes.persistence.models import RealTrade


class TradeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_real_trade(
        self,
        *,
        profile_id: int,
        asset_id: int,
        entry_time: datetime,
        exit_time: datetime,
        entry_price: float,
        exit_price: float,
        pnl: float,
        fees: float,
        duration_seconds: int,
        exit_reason: str,
    ) -> RealTrade:
        trade = RealTrade(
            profile_id=profile_id,
            asset_id=asset_id,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            fees=fees,
            duration_seconds=duration_seconds,
            exit_reason=exit_reason,
        )

        self.session.add(trade)
        self.session.commit()
        self.session.refresh(trade)

        return trade
