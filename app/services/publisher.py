import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import SocialPublisher
from app.models import PublishAttempt, Slot, Variant, new_id, utcnow

logger = logging.getLogger(__name__)


class AlreadyPublished(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def publish_slot(session: Session, slot: Slot, variant: Variant, adapter: SocialPublisher) -> PublishAttempt:
    """Publish a slot exactly once. Callable any number of times - safe.

    The dedup guarantee comes from two layers:
      1. A DB-level partial unique index on success rows per idempotency_key.
      2. A pre-check for an existing success on the same key before every publish.
    """
    key = slot.idempotency_key

    existing = (
        session.query(PublishAttempt)
        .filter(PublishAttempt.idempotency_key == key)
        .order_by(PublishAttempt.created_at.desc())
        .first()
    )

    if existing is not None and existing.status == "success":
        logger.info("idempotent hit: key=%s already posted: %s", key, existing.message_url)
        return existing

    attempt = PublishAttempt(
        id=new_id(),
        slot_id=slot.id,
        variant_id=slot.variant_id,
        platform=slot.platform,
        idempotency_key=key,
        status="attempted",
    )
    session.add(attempt)
    session.flush()

    try:
        result = adapter.publish(variant.content, key)
    except Exception as exc:
        attempt.status = "failed"
        attempt.error = f"{type(exc).__name__}: {exc}"
        session.flush()
        logger.warning("publish failed for key=%s: %s", key, attempt.error)
        raise

    attempt.status = "success"
    attempt.result = result.raw
    attempt.external_id = result.external_id
    attempt.message_url = result.message_url
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        row = (
            session.query(PublishAttempt)
            .filter(PublishAttempt.idempotency_key == key)
            .filter(PublishAttempt.status == "success")
            .first()
        )
        raise AlreadyPublished(
            f"concurrent publish detected: key={key} already recorded as success "
            f"(external_id={row.external_id if row else '?'})"
        )

    slot.status = "published"
    logger.info("published key=%s via %s -> %s", key, adapter.platform, result.message_url)
    return attempt


def find_success_for_key(session: Session, key: str) -> PublishAttempt | None:
    return (
        session.query(PublishAttempt)
        .filter(PublishAttempt.idempotency_key == key)
        .filter(PublishAttempt.status == "success")
        .first()
    )