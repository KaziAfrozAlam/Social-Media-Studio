"""Durable scheduling proof: a worker that dies mid-batch resumes without
duplicates. We run the real worker.py as a subprocess against a real SQLite
DB; SCHEDULER_CRASH_AFTER kills it after publishing 1 slot, then we restart
it and verify both slots are published exactly once and only one success row
exists per idempotency key."""

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_RUNZ = Path(__file__).resolve().parent.parent
_SAVED_ENV = {}


@pytest.fixture(autouse=True, scope="module")
def _save_restore_env():
    """test_crash_resume re-points env+reloads app.main; restore after so the
    rest of the suite keeps the pristine conftest configuration."""
    _SAVED_ENV.update(os.environ)
    yield
    os.environ.clear()
    os.environ.update(_SAVED_ENV)
    from app.config import reset_settings_cache
    from app.db import reset_db

    reset_settings_cache()
    reset_db()
    import importlib

    import app.main as app_module

    importlib.reload(app_module)


@pytest.fixture()
def crash_env():
    tmp = tempfile.mkdtemp(prefix="sms_crash_")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_RUNZ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DATABASE_URL"] = f"sqlite:///{tmp}/crash.db"
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""
    env["PUBLISHERS"] = "x=MockXPublisher,linkedin=MockLinkedInPublisher"
    env["SCHEDULER_POLL_SECONDS"] = "1"
    return env


def _run_python(env, module, args=None, timeout=60):
    cmd = [sys.executable, "-m", module] + (args or [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, cwd=str(_RUNZ))
        return out
    except subprocess.TimeoutExpired as exc:
        print("=== TIMEOUT STDERR ===")
        print(exc.stderr[:4000] if exc.stderr else "(none)")
        print("=== TIMEOUT STDOUT ===")
        print(exc.stdout[:4000] if exc.stdout else "(none)")
        raise


def test_worker_crash_restart_publishes_all_exactly_once(crash_env, client_seed):
    post_id, slot_ids = client_seed

    # worker run 1: dies after the first slot
    env = dict(crash_env)
    env["SCHEDULER_CRASH_AFTER"] = "1"
    r1 = _run_python(env, "app.worker", timeout=90)
    assert r1.returncode == 1 or "published 1 slot(s)" in r1.stdout or "published 1 slot(s)" in r1.stderr

    # worker run 2: full restart, must publish the remaining slot and skip #1
    env2 = dict(crash_env)
    env2["SCHEDULER_CRASH_AFTER"] = "0"
    r2 = _run_python(env2, "app.worker", args=["--once"], timeout=90)

    # verify through the API
    from fastapi.testclient import TestClient

    from app.config import reset_settings_cache
    from app.db import reset_db
    from app import main as app_module

    os.environ["DATABASE_URL"] = env["DATABASE_URL"]
    os.environ["PUBLISHERS"] = env["PUBLISHERS"]
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["TELEGRAM_CHAT_ID"] = ""
    reset_settings_cache()
    reset_db()
    import importlib
    importlib.reload(app_module)

    c = TestClient(app_module.app)
    history = c.get("/api/history").json()
    outbox = c.get("/api/mock_outbox").json()

    assert len(outbox) == 2, "exactly one post per slot"
    assert len(history) == 2, "exactly one success row per slot"
    keys = [h["idempotency_key"] for h in history]
    assert len(keys) == len(set(keys)), "no duplicate idempotency keys"
    assert all(h["status"] == "success" for h in history)
    assert sorted(h["slot_id"] for h in history) == sorted(slot_ids)


@pytest.fixture()
def client_seed(crash_env):
    from fastapi.testclient import TestClient

    from app.config import reset_settings_cache
    from app.db import reset_db
    from app import main as app_module

    os.environ["DATABASE_URL"] = crash_env["DATABASE_URL"]
    os.environ["PUBLISHERS"] = crash_env["PUBLISHERS"]
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["TELEGRAM_CHAT_ID"] = ""
    os.environ["SCHEDULER_POLL_SECONDS"] = "1"
    reset_settings_cache()
    reset_db()
    import importlib

    importlib.reload(app_module)

    c = TestClient(app_module.app)
    post = c.post(
        "/api/posts",
        json={
            "title": "Crash Test Post",
            "content": "A post that will be published by two different X and LinkedIn variants.",
        },
    ).json()
    post_id = post["id"]
    variants = c.post(f"/api/posts/{post_id}/variants").json()
    slot_ids = []
    when = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
    for v in variants:
        c.post(f"/api/variants/{v['id']}/approve")
        slot = c.post("/api/slots", json={"variant_id": v["id"], "scheduled_at": when}).json()
        slot_ids.append(slot["id"])
    return post_id, slot_ids