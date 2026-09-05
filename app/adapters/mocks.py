import uuid

from sqlalchemy.orm import Session

from app.adapters.base import SocialPublisher, PublishResult
from app.models import MockOutbox, new_id


class MockPublisherBase(SocialPublisher):
    """Records what it would have posted into the mock_outbox table.

    Idempotent at the adapter level: re-publishing with the same idempotency_key
    returns the same external id without a second row.
    """

    def __init__(self, session: Session | None = None):
        self.session = session

    def _platform_label(self) -> str:
        return self.platform

    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        session = self.session
        if session is None:
            raise RuntimeError("mock adapter needs a database session")

        existing = (
            session.query(MockOutbox)
            .filter(MockOutbox.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return PublishResult(
                external_id=f"{existing.platform}-{existing.id}",
                message_url=f"mock://{existing.platform}/{existing.id}",
                raw=f"duplicate_suppressed key={idempotency_key}",
            )

        record = MockOutbox(
            id=new_id(),
            platform=self._platform_label(),
            content=content,
            idempotency_key=idempotency_key,
        )
        session.add(record)
        session.flush()
        return PublishResult(
            external_id=f"{record.platform}-{record.id}",
            message_url=f"mock://{record.platform}/{record.id}",
            raw=f"recorded id={record.id} platform={record.platform}",
        )


class MockXPublisher(MockPublisherBase):
    platform = "x"


class MockLinkedInPublisher(MockPublisherBase):
    platform = "linkedin"


class MockInstagramPublisher(MockPublisherBase):
    platform = "instagram"