from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = (
    "mysql+pymysql://hermes_user:hermes_password@localhost:3306/hermes"
)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def init_db() -> None:
    # Import models to register them with SQLAlchemy metadata.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
