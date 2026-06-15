"""SQLAlchemy ORM models."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, BigInteger, Integer, DateTime, ForeignKey, LargeBinary, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Key(Base):
    """Authentication key - represents an agent's identity.

    No traditional user account. key_hash stores SHA-256 of the raw key.
    recovery_hash stores bcrypt of the recovery code (for key reset).
    """
    __tablename__ = "keys"

    key_id = Column(String, primary_key=True, default=_uuid)
    key_hash = Column(LargeBinary, nullable=False, unique=True, index=True)
    recovery_hash = Column(LargeBinary, nullable=False)
    label = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    events = relationship("Event", back_populates="owner", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="owner", cascade="all, delete-orphan")


class Event(Base):
    """Append-only event log entry.

    Events represent changes to the agent's memory/state.
    Memory objects are a query view over this log.
    """
    __tablename__ = "events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String, ForeignKey("keys.key_id"), nullable=False, index=True)
    type = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    client_ts = Column(DateTime(timezone=True), nullable=True)
    server_ts = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    client_event_id = Column(String, nullable=True, index=True)  # client-side dedup key

    owner = relationship("Key", back_populates="events")

    __table_args__ = (
        Index("ix_events_key_server", "key_id", "server_ts"),
        Index("ix_events_key_client", "key_id", "client_event_id"),
    )


class Asset(Base):
    """Asset metadata. Binary content stored on disk/S3 separately."""
    __tablename__ = "assets"

    asset_id = Column(String, primary_key=True, default=_uuid)
    key_id = Column(String, ForeignKey("keys.key_id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    mime = Column(String, nullable=False)
    size = Column(BigInteger, nullable=False)
    storage_path = Column(String, nullable=False)
    meta = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    owner = relationship("Key", back_populates="assets")


class Share(Base):
    """Sub-key / share token. Allows sharing memory with others.

    v0.3 - exposed via /v1/share.
    """
    __tablename__ = "shares"

    share_id = Column(String, primary_key=True, default=_uuid)
    parent_key_id = Column(String, ForeignKey("keys.key_id"), nullable=False, index=True)
    token_hash = Column(LargeBinary, nullable=False, unique=True)
    permissions = Column(String, nullable=False, default="read")  # read | read_memory | full
    label = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)