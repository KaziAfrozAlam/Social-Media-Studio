import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.adapters.base import SocialPublisher
from app.models import PublishAttempt, Slot, Variant, utcnow
from app.services.publisher import AlreadyPublished, publish_slot

logger = logging.getLogger(__name__)


def slot_idempotency_key(variant_id: str, scheduled_at: datetime) -> str:
    """Deterministic key: same variant + same slot time => same key."""
    scheduled = scheduled_at.replace(tzinfo=None) if scheduled_at.tzinfo else scheduled_at
    return f"slot:{variant_id}:{scheduled.isoformat()}"


def publish_due_slots(
    session: Session,
    registry: dict[str, SocialPublisher],
    now: datetime | None = None,
    max_publishes: int | None = None,
) -> list[str]:
    """Publish every slot whose time has arrived.

    Returns the list of idempotency keys it processed. Safe to call repeatedly:
    each (variant, slot) can only ever produce one success. A worker that dies
    mid-batch simply leaves the remaining slots 'scheduled' - the next worker
    invocation takes over with zero duplicates.
    """
    now = now or utcnow()
    due = (
        session.query(Slot)
        .filter(Slot.scheduled_at <= now)
        .filter(Slot.status.in_(("scheduled", "failed")))
        .order_by(Slot.scheduled_at.asc())
        .all()
    )
    processed: list[str] = []
    for slot in due:
        if max_publishes is not None and len(processed) >= max_publishes:
            break
        variant = session.get(Variant, slot.variant_id)
        if variant is None:
            logger.error("slot %s references missing variant %s", slot.id, slot.variant_id)
            continue
        adapter = registry.get(slot.platform)
        if adapter is None:
            logger.error("no adapter for platform %s (slot %s)", slot.platform, slot.id)
            slot.status = "failed"
            session.flush()
            continue
        try:
            publish_slot(session, slot, variant, adapter)
            processed.append(slot.idempotency_key)
        except AlreadyPublished as exc:
            logger.info("duplicate suppressed mid-flight: %s", exc.message)
            slot.status = "published"
            session.flush()
            processed.append(slot.idempotency_key)
        except Exception as exc:
            logger.warning("slot %s could not be published: %s", slot.id, exc)
            slot.status = "failed"
            session.flush()
    session.commit()
    return processed