import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sms_test_"))

os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["PUBLISHERS"] = (
    "x=MockXPublisher,linkedin=MockLinkedInPublisher,instagram=MockInstagramPublisher"
)

import pytest  # noqa: E402

from app.config import reset_settings_cache  # noqa: E402

reset_settings_cache()
from app import main as app_module  # noqa: E402
from app.adapters.registry import build_registry  # noqa: E402

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.models import MockOutbox, Post, PublishAttempt, Slot, Variant
    from app.db import get_session_factory

    Session = get_session_factory(app_module.settings)
    db = Session()
    for table in (MockOutbox, PublishAttempt, Slot, Variant, Post):
        db.query(table).delete()
    db.commit()
    db.close()

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def post(client):
    resp = client.post(
        "/api/posts",
        json={
            "title": "Five Ideas We Are Shipping",
            "content": (
                "Our team has spent three months building a durable publishing "
                "system. The key insight: retries must never double-post, workers "
                "that crash must resume safely, and nothing unapproved may ever go "
                "out. Here is what we learned and what we are shipping next quarter."
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()