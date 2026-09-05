import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

from app.adapters.registry import build_registry
from app.config import get_settings
from app.db import init_db, get_session_factory
from app.models import PublishAttempt
from app.services.scheduler import publish_due_slots

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s WORKER %(message)s"
)
logger = logging.getLogger("worker")

_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True
    logger.info("signal %s received, shutting down after current batch", signum)


def recover_stuck_attempts(session, cutoff_seconds: int = 30):
    """Slots whose attempts are stuck 'attempted' (worker died mid-publish)
    get reset so the scheduler can safely retry them. Success-once is still
    guaranteed by the <idempotency_key, success> unique index."""
    cutoff = datetime.utcnow() - timedelta(seconds=cutoff_seconds)
    stuck = (
        session.query(PublishAttempt)
        .filter(PublishAttempt.status == "attempted")
        .filter(PublishAttempt.created_at < cutoff)
        .all()
    )
    for a in stuck:
        logger.info(
            "recovering stuck attempt %s key=%s (old, status='attempted')",
            a.id,
            a.idempotency_key,
        )
        session.delete(a)


def run_once(session, crash_after: int = 0, recovery_seconds: int = 30):
    settings = get_settings()
    registry = build_registry(settings.publishers, session)
    recover_stuck_attempts(session, recovery_seconds)
    session.commit()
    processed = publish_due_slots(
        session, registry, max_publishes=crash_after if crash_after else None
    )
    if crash_after and len(processed) >= crash_after:
        logger.info(
            "CRASH SIMULATION: published %d slot(s) this sweep; exiting now "
            "(SCHEDULER_CRASH_AFTER=%d) to prove resume-without-duplicates",
            len(processed),
            crash_after,
        )
        sys.stdout.flush()
        os._exit(1)
    return processed


def main():
    once = "--once" in sys.argv
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    settings = get_settings()
    init_db(settings)
    SessionLocal = get_session_factory(settings)
    logger.info("worker starting; poll every %ss (once=%s)", settings.poll_seconds, once)

    while not _stop:
        try:
            session = SessionLocal()
            try:
                processed = run_once(session, settings.crash_after, settings.crash_recovery_seconds)
                if processed:
                    logger.info("published %d slot(s): %s", len(processed), processed)
                else:
                    logger.info("no due slots")
            finally:
                session.close()
        except Exception:
            logger.exception("worker sweep failed; will retry next cycle")
        if once:
            break
        time.sleep(settings.poll_seconds)

    logger.info("worker stopped cleanly")


if __name__ == "__main__":
    main()