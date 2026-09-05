from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PublishResult:
    external_id: str
    message_url: str | None = None
    raw: str | None = None


class SocialPublisher(ABC):
    """The seam every adapter implements. The app never knows the platform."""

    platform: str

    @abstractmethod
    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        """Publish content once for idempotency_key. Must be safe to call repeatedly."""
        raise NotImplementedError

    def describe(self) -> str:
        return self.platform