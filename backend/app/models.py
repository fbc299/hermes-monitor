"""SQLAlchemy ORM models.

Three tables cover the Langfuse-style data model at a personal scale:

  * ``traces``      - one logical conversation/turn group (optional, the
                      SDK uses it to correlate multi-turn Hermes sessions).
  * ``generations`` - one concrete LLM call (prompt -> completion), the core
                      record written by both the proxy and the SDK.
  * ``sessions``    - lightweight aggregation keyed by Hermes session id.

We store the raw prompt/completion bodies as JSON TEXT rather than
normalizing every message: personal-scale queries rarely need to join on
message content, and this keeps the schema simple and the writes fast.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    DateTime,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all models."""


def _utcnow() -> datetime:
    """Timezone-aware UTC 'now', used as column default."""
    return datetime.now(timezone.utc)


class GenSource(str, enum.Enum):
    """Where the generation record came from."""

    PROXY = "proxy"  # captured transparently by the reverse proxy
    SDK = "sdk"      # reported explicitly by the Python SDK


class GenStatus(str, enum.Enum):
    """Outcome of a single LLM call."""

    SUCCESS = "success"
    ERROR = "error"


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    generations: Mapped[list["Generation"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="SET NULL"), index=True, nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    source: Mapped[GenSource] = mapped_column(Enum(GenSource), default=GenSource.PROXY)
    status: Mapped[GenStatus] = mapped_column(Enum(GenStatus), default=GenStatus.SUCCESS, index=True)

    # Raw bodies (messages array / response object), stored as JSON text.
    prompt_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    completion_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Token accounting.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Performance.
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # time-to-first-token

    # Money.
    cost: Mapped[float] = mapped_column(Float, default=0.0)

    # Provenance / error.
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    trace: Mapped[Trace | None] = relationship(back_populates="generations")

    __table_args__ = (
        # The dashboard's most common query shape is
        # "ORDER BY created_at DESC" optionally filtered by model/status.
        Index("ix_generations_created", "created_at"),
    )


class Session(Base):
    """Aggregated stats per Hermes session, kept up to date on write."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)


class AppConfig(Base):
    """Key-value store for runtime configuration.

    Values set through the settings UI take precedence over environment
    variables, so users never need to edit config files manually.
    """
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


def utcnow() -> datetime:
    """Public accessor for the UTC 'now' helper (used by services)."""
    return func.now()
