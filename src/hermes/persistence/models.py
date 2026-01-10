from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# =========================
# ENUMS
# =========================

class ExecutionMode(str, enum.Enum):
    SIMULATION = "SIMULATION"
    LIVE = "LIVE"


class DecisionType(str, enum.Enum):
    ENTER = "ENTER"
    EXIT = "EXIT"
    SKIP = "SKIP"
    NO_TRADE = "NO_TRADE"


# =========================
# CORE TABLES
# =========================

class Asset(Base):
    __tablename__ = "asset"

    asset_id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    base_asset: Mapped[str] = mapped_column(String(10))
    quote_asset: Mapped[str] = mapped_column(String(10))


class StrategyProfile(Base):
    __tablename__ = "strategy_profile"

    profile_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    risk_level: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshot"

    snapshot_id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.asset_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    timeframe: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    volatility: Mapped[float] = mapped_column(Float)
    range_pct: Mapped[float] = mapped_column(Float)

    sma_fast: Mapped[float] = mapped_column(Float)
    sma_slow: Mapped[float] = mapped_column(Float)
    trend_strength: Mapped[float] = mapped_column(Float)

    asset = relationship("Asset")


class SimulationRun(Base):
    __tablename__ = "simulation_run"

    simulation_id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profile.profile_id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.asset_id"))

    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    execution_mode: Mapped[ExecutionMode] = mapped_column(Enum(ExecutionMode))
    parameter_hash: Mapped[str] = mapped_column(String(64))

    profile = relationship("StrategyProfile")
    asset = relationship("Asset")


class SimulatedTrade(Base):
    __tablename__ = "simulated_trade"

    sim_trade_id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulation_run.simulation_id"))

    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime] = mapped_column(DateTime)

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)

    pnl: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[int] = mapped_column(Integer)

    exit_reason: Mapped[str] = mapped_column(String(50))

    simulation = relationship("SimulationRun")


class RealTrade(Base):
    __tablename__ = "real_trade"

    trade_id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profile.profile_id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.asset_id"))

    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime] = mapped_column(DateTime)

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)

    pnl: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float)

    duration_seconds: Mapped[int] = mapped_column(Integer)
    exit_reason: Mapped[str] = mapped_column(String(50))

    profile = relationship("StrategyProfile")
    asset = relationship("Asset")


class DecisionLog(Base):
    __tablename__ = "decision_log"

    decision_id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.asset_id"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profile.profile_id"))

    decision_type: Mapped[DecisionType] = mapped_column(Enum(DecisionType))
    regime_detected: Mapped[str] = mapped_column(String(30))
    confidence_score: Mapped[float] = mapped_column(Float)

    reason: Mapped[str] = mapped_column(Text)

    asset = relationship("Asset")
    profile = relationship("StrategyProfile")


class PerformanceWindow(Base):
    __tablename__ = "performance_window"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "asset_id", "window_start", "window_end",
            name="uq_performance_window",
        ),
    )

    window_id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profile.profile_id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.asset_id"))

    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)

    trades_count: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    avg_pnl: Mapped[float] = mapped_column(Float)
    pnl_slope: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)

    profile = relationship("StrategyProfile")
    asset = relationship("Asset")
