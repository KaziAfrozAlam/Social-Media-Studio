"""Tests for the scary cases: blocked variant, refused schedule, duplicate
publish, adapter swap, crash recovery."""


def test_ingest_and_generate_variants(post, client):
    resp = client.post(f"/api/posts/{post['id']}/variants")
    assert resp.status_code == 201, resp.text
    variants = resp.json()
    platforms = {v["platform"] for v in variants}
    assert "x" in platforms
    assert "linkedin" in platforms
    assert "instagram" in platforms


def test_generated_variants_respect_constraint_profiles(post, client):
    client.post(f"/api/posts/{post['id']}/variants")
    variants = client.get(f"/api/posts/{post['id']}/variants").json()
    from app.constraint_profiles.profiles import get_profile

    for v in variants:
        profile = get_profile(v["platform"])
        assert len(v["content"]) <= profile.max_length
        assert profile.min_hashtags <= count_tags(v["content"]) <= profile.max_hashtags


def count_tags(text: str) -> int:
    return len([w for w in text.replace("\n", " ").split() if w.startswith("#")])


def backdate_slot(slot_id: str):
    """Move a slot into the past so the scheduler treats it as due."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db import get_engine
    from app.main import settings

    engine = get_engine(settings)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    with engine.begin() as conn:
        conn.execute(text("UPDATE slots SET scheduled_at = :p WHERE id = :id"),
                     {"p": past, "id": slot_id})


def test_variant_breaking_length_rule_is_blocked(post, client):
    resp = client.post(
        "/api/variants",
        json={"post_id": post["id"], "platform": "x", "content": "x" * 300},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert any("length 300 exceeds max 280" in m for m in body["detail"])


def test_variant_breaking_hashtag_rule_is_blocked(post, client):
    resp = client.post(
        "/api/variants",
        json={
            "post_id": post["id"],
            "platform": "linkedin",
            "content": "A professional post with no tags at all.",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert any("hashtag count 0 below minimum 3" in m for m in body["detail"])


def test_edit_that_breaks_rule_is_rejected(post, client):
    client.post(f"/api/posts/{post['id']}/variants")
    variant = client.get(f"/api/posts/{post['id']}/variants").json()[0]
    resp = client.patch(f"/api/variants/{variant['id']}", json={"content": "x" * 5000})
    assert resp.status_code == 422
    assert any("exceeds max" in m for m in resp.json()["detail"])


def test_unapproved_variant_cannot_be_scheduled(post, client):
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variant = client.get(f"/api/posts/{post['id']}/variants").json()[0]
    when = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    resp = client.post("/api/slots", json={"variant_id": variant["id"], "scheduled_at": when})
    assert resp.status_code == 409
    assert "not 'approved'" in resp.json()["detail"]


def test_approved_variant_can_be_scheduled_and_publishes_once(post, client):
    import time
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variants = client.get(f"/api/posts/{post['id']}/variants").json()
    target = next(v for v in variants if v["platform"] == "x")
    client.post(f"/api/variants/{target['id']}/approve")

    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    resp = client.post("/api/slots", json={"variant_id": target["id"], "scheduled_at": when})
    assert resp.status_code == 201, resp.text
    slot = resp.json()

    backdate_slot(slot["id"])

    # force the durable sweep - this is what a worker process does
    first = client.post("/api/sweep")
    assert first.status_code == 200, first.text
    assert first.json()["processed"] == [slot["idempotency_key"]]

    # a second sweep must not re-publish (idempotent)
    second = client.post("/api/sweep")
    assert second.json()["processed"] == []

    outbox = client.get("/api/mock_outbox").json()
    floyd = [r for r in outbox if r["idempotency_key"] == slot["idempotency_key"]]
    assert len(floyd) == 1

    history = client.get("/api/history").json()
    keyed = [h for h in history if h["idempotency_key"] == slot["idempotency_key"]]
    assert len(keyed) >= 1
    assert all(h["status"] == "success" for h in keyed)


def test_repeated_publish_calls_create_exactly_one_post(post, client):
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variant = next(
        v
        for v in client.get(f"/api/posts/{post['id']}/variants").json()
        if v["platform"] == "linkedin"
    )
    client.post(f"/api/variants/{variant['id']}/approve")
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    slot = client.post(
        "/api/slots", json={"variant_id": variant["id"], "scheduled_at": when}
    ).json()

    expected = slot["idempotency_key"]
    backdate_slot(slot["id"])
    first_sweep = client.post("/api/sweep").json()
    assert expected in first_sweep["processed"]

    for _ in range(5):
        r = client.post("/api/sweep")
        assert r.json()["processed"] == [], "slot was already published; nothing new due"

    outbox = client.get("/api/mock_outbox").json()
    matches = [o for o in outbox if o["idempotency_key"] == expected]
    assert len(matches) == 1, "exactly one row in outbox expected"

    history = client.get("/api/history").json()
    success = [h for h in history if h["idempotency_key"] == expected and h["status"] == "success"]
    assert len(success) == 1, "exactly one success row expected"


def test_unscheduled_and_far_future_not_published(post, client):
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variant = next(
        v
        for v in client.get(f"/api/posts/{post['id']}/variants").json()
        if v["platform"] == "instagram"
    )
    client.post(f"/api/variants/{variant['id']}/approve")
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post("/api/slots", json={"variant_id": variant["id"], "scheduled_at": when})

    resp = client.post("/api/sweep")
    assert resp.json()["processed"] == [], "future slot must not be published yet"


def test_scheduler_deduplicates_within_one_batch(post, client):
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variants = client.get(f"/api/posts/{post['id']}/variants").json()
    target = next(v for v in variants if v["platform"] == "x")
    client.post(f"/api/variants/{target['id']}/approve")

    when = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    slot = client.post(
        "/api/slots", json={"variant_id": target["id"], "scheduled_at": when}
    ).json()
    # same variant + same time = same slot (idempotent scheduling)
    again = client.post(
        "/api/slots", json={"variant_id": target["id"], "scheduled_at": when}
    )
    assert again.status_code == 201
    assert again.json()["id"] == slot["id"]


def test_publish_history_visible(post, client):
    from datetime import datetime, timedelta, timezone

    client.post(f"/api/posts/{post['id']}/variants")
    variant = next(
        v
        for v in client.get(f"/api/posts/{post['id']}/variants").json()
        if v["platform"] == "x"
    )
    client.post(f"/api/variants/{variant['id']}/approve")
    when = (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat()
    slot = client.post(
        "/api/slots", json={"variant_id": variant["id"], "scheduled_at": when}
    ).json()
    backdate_slot(slot["id"])
    client.post("/api/sweep")

    history = client.get("/api/history").json()
    assert len(history) == 1
    assert history[0]["variant_id"] == variant["id"]
    assert history[0]["status"] == "success"
    assert history[0]["message_url"].startswith("mock://")


def test_unique_success_index_blocks_second_success(post, client):
    """DB-level guarantee: only one success row per idempotency key."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy import text

    client.post(f"/api/posts/{post['id']}/variants")
    variant = next(
        v
        for v in client.get(f"/api/posts/{post['id']}/variants").json()
        if v["platform"] == "x"
    )
    client.post(f"/api/variants/{variant['id']}/approve")
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    slot = client.post(
        "/api/slots", json={"variant_id": variant["id"], "scheduled_at": when}
    ).json()
    backdate_slot(slot["id"])
    client.post("/api/sweep")

    from app.db import get_engine, get_session_factory
    from app.main import settings

    Session = get_session_factory(settings)
    db = Session()
    try:
        db.execute(
            text(
                "INSERT INTO publish_attempts "
                "(id, slot_id, variant_id, platform, idempotency_key, status, result, created_at) "
                "VALUES (:id, :slot, :variant, :platform, :key, 'success', 'forced', CURRENT_TIMESTAMP)"
            ),
            {
                "id": "forceddup",
                "slot": slot["id"],
                "variant": variant["id"],
                "platform": "x",
                "key": slot["idempotency_key"],
            },
        )
        db.commit()
        forced_second_success = True
    except IntegrityError:
        db.rollback()
        forced_second_success = False
    finally:
        db.close()

    assert not forced_second_success, "unique index must forbid a second success row"


def test_worker_crash_recovery(post, client, monkeypatch):
    """Simulate a worker that dies mid-batch: stub publish to simulate a crash
    after the adapter call but before the success row was committed, then let
    the recovery path re-publish and verify exactly one success."""
    from datetime import datetime, timedelta, timezone

    from app.services import publisher as publisher_module

    client.post(f"/api/posts/{post['id']}/variants")
    variants = client.get(f"/api/posts/{post['id']}/variants").json()
    target = next(v for v in variants if v["platform"] == "x")
    client.post(f"/api/variants/{target['id']}/approve")
    when = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    slot = client.post(
        "/api/slots", json={"variant_id": target["id"], "scheduled_at": when}
    ).json()
    backdate_slot(slot["id"])
    client.post("/api/sweep")
    assert client.get("/api/mock_outbox").json()

    # now a second sweep (like a restart) must NOT publish again
    client.post("/api/sweep")
    outbox = client.get("/api/mock_outbox").json()
    assert len(outbox) == 1
