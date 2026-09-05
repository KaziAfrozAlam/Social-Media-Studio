import os

from app.config import get_settings
from app.models import Post, new_id


def seed_sample():
    settings = get_settings()
    from app.db import get_session_factory, init_db

    init_db(settings)
    Session = get_session_factory(settings)
    db = Session()
    try:
        existing = db.query(Post).count()
        if existing:
            print(f"sample post already present ({existing}), skipping")
            return
        post = Post(
            id=new_id(),
            title="How We Built a Crash-Safe Social Publisher",
            content=(
                "After three months of building, our team shipped a social publishing "
                "system where retries can never double-post. The trick is an "
                "idempotency key per variant plus a database-level guarantee that "
                "only one success row can ever exist for that key. Workers that "
                "crash mid-batch resume from the job store and simply skip "
                "everything already recorded as success."
            ),
        )
        db.add(post)

        from app.services.generator import create_variant

        for platform in ["x", "linkedin", "instagram", "telegram"]:
            try:
                variant = create_variant(post, platform)
            except Exception:
                continue
            db.add(variant)
        db.commit()
        print(f"seeded post {post.id}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_sample()