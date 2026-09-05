from sqlalchemy.orm import Session

from app.adapters.base import SocialPublisher
from app.adapters.mocks import MockInstagramPublisher, MockLinkedInPublisher, MockXPublisher
from app.adapters.telegram import TelegramPublisher

ADAPTER_CLASSES = {
    "TelegramPublisher": TelegramPublisher,
    "MockXPublisher": MockXPublisher,
    "MockLinkedInPublisher": MockLinkedInPublisher,
    "MockInstagramPublisher": MockInstagramPublisher,
}


def build_registry(publishers_config: str, session: Session) -> dict[str, SocialPublisher]:
    """Build a platform -> adapter map from a config string, e.g.

    "telegram=TelegramPublisher,mock_x=MockXPublisher,mock_linkedin=MockLinkedInPublisher"
    """
    registry: dict[str, SocialPublisher] = {}
    if not publishers_config.strip():
        raise ValueError("PUBLISHERS config is empty")
    for chunk in publishers_config.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            raise ValueError(f"invalid PUBLISHERS entry: {chunk!r}")
        platform, class_name = chunk.split("=", 1)
        platform = platform.strip()
        class_name = class_name.strip()
        if class_name not in ADAPTER_CLASSES:
            raise ValueError(f"unknown adapter class: {class_name}")
        klass = ADAPTER_CLASSES[class_name]
        if class_name == "TelegramPublisher":
            registry[platform] = klass()
        else:
            registry[platform] = klass(session=session)
    return registry