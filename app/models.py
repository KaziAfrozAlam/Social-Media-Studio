import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)

from app.db import Base


def utcnow() -> datetime:
    """Naive UTC wall-clock time. All storage/comparison is naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, default=new_id)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    source_url = Column(String, nullable=True)
    markdown_input = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint("post_id", "platform", name="uq_variant_post_platform"),
    )

    id = Column(String, primary_key=True, default=new_id)
    post_id = Column(String, ForeignKey("posts.id"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="draft")
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Slot(Base):
    __tablename__ = "slots"

    id = Column(String, primary_key=True, default=new_id)
    variant_id = Column(String, ForeignKey("variants.id"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(String, nullable=False, default="scheduled")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    idempotency_key = Column(String, nullable=False, unique=True, index=True)


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"

    id = Column(String, primary_key=True, default=new_id)
    slot_id = Column(String, ForeignKey("slots.id"), nullable=False, index=True)
    variant_id = Column(String, ForeignKey("variants.id"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="attempted")
    result = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    message_url = Column(Text, nullable=True)
    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


Index(
    "uq_attempt_success_once",
    PublishAttempt.idempotency_key,
    unique=True,
    sqlite_where=PublishAttempt.status == "success",
)


class MockOutbox(Base):
    __tablename__ = "mock_outbox"

    id = Column(String, primary_key=True, default=new_id)
    platform = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    published_at = Column(DateTime, default=utcnow, nullable=False)