import logging
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.adapters.registry import build_registry
from app.config import Settings, get_settings
from app.db import get_session_factory, init_db
from app.schemas import (
    PostCreate,
    PostOut,
    PublishAttemptOut,
    SlotCreate,
    SlotOut,
    VariantCreate,
    VariantEdit,
    VariantOut,
    VariantReject,
)
from app.services.generator import VariantValidationError, create_variant
from app.services.scheduler import publish_due_slots, slot_idempotency_key

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

settings = get_settings()
init_db(settings)
SessionLocal = get_session_factory(settings)

app = FastAPI(
    title="Social Media Studio",
    version="1.0.0",
    description="Turn one blog post into a full multi-platform social campaign "
    "with human review and durable, idempotent publishing.",
)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_registry(db: Session = Depends(get_db)):
    return build_registry(settings.publishers, db)


def configured_platforms() -> list[str]:
    return [
        chunk.split("=")[0].strip()
        for chunk in settings.publishers.split(",")
        if "=" in chunk and chunk.strip()
    ]


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post("/api/posts", response_model=PostOut, status_code=201, tags=["posts"])
def create_post(payload: PostCreate, db: Session = Depends(get_db)):
    content = payload.content or payload.markdown_input
    if payload.source_url and (not content):
        import re

        import requests

        try:
            resp = requests.get(payload.source_url, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(422, f"could not fetch source_url: {type(exc).__name__}: {exc}")
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text)
        content = text.strip()

    if not content:
        raise HTTPException(422, "one of content, markdown_input, or a fetchable source_url is required")

    post = models.Post(
        title=payload.title,
        content=content,
        source_url=payload.source_url,
        markdown_input=payload.markdown_input,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@app.get("/api/posts/{post_id}", response_model=PostOut, tags=["posts"])
def get_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    return post


@app.post("/api/posts/{post_id}/variants", status_code=201, tags=["variants"])
def generate_variants(post_id: str, db: Session = Depends(get_db)):
    """Generate one variant per registered platform from the STORED post only."""
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(404, "post not found")

    created: list[models.Variant] = []
    for platform in configured_platforms():
        existing = (
            db.query(models.Variant)
            .filter(models.Variant.post_id == post_id)
            .filter(models.Variant.platform == platform)
            .first()
        )
        if existing:
            created.append(existing)
            continue
        try:
            variant = create_variant(post, platform)
        except VariantValidationError as exc:
            raise HTTPException(
                422, f"generated variant for {platform} failed validation: {'; '.join(exc.violations)}"
            )
        db.add(variant)
        created.append(variant)
    db.commit()
    for v in created:
        db.refresh(v)
    return [VariantOut.model_validate(v) for v in created]


@app.post("/api/variants", response_model=VariantOut, status_code=201, tags=["variants"])
def create_variant_endpoint(payload: VariantCreate, db: Session = Depends(get_db)):
    post = db.get(models.Post, payload.post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    try:
        variant = create_variant(post, payload.platform, content=payload.content)
    except VariantValidationError as exc:
        raise HTTPException(422, exc.violations)
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@app.get("/api/variants/{variant_id}", response_model=VariantOut, tags=["variants"])
def get_variant(variant_id: str, db: Session = Depends(get_db)):
    variant = db.get(models.Variant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    return variant


@app.get("/api/posts/{post_id}/variants", response_model=list[VariantOut], tags=["variants"])
def list_variants_for_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    return (
        db.query(models.Variant)
        .filter(models.Variant.post_id == post_id)
        .order_by(models.Variant.created_at)
        .all()
    )


@app.patch("/api/variants/{variant_id}", response_model=VariantOut, tags=["variants"])
def edit_variant(variant_id: str, payload: VariantEdit, db: Session = Depends(get_db)):
    """Edit a variant's content. Re-validates against the platform constraint profile."""
    variant = db.get(models.Variant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    if variant.status == "published":
        raise HTTPException(409, "published variants cannot be edited")

    from app.constraint_profiles.profiles import get_profile
    from app.services.generator import VariantValidationError

    violations = get_profile(variant.platform).validate(payload.content)
    if violations:
        raise HTTPException(422, violations)
    variant.content = payload.content
    db.commit()
    db.refresh(variant)
    return variant


@app.post("/api/variants/{variant_id}/approve", response_model=VariantOut, tags=["review"])
def approve_variant(variant_id: str, db: Session = Depends(get_db)):
    variant = db.get(models.Variant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    if variant.status == "rejected":
        raise HTTPException(409, "a rejected variant must be re-drafted before approval")
    variant.status = "approved"
    db.commit()
    db.refresh(variant)
    return variant


@app.post("/api/variants/{variant_id}/reject", response_model=VariantOut, tags=["review"])
def reject_variant(variant_id: str, payload: VariantReject, db: Session = Depends(get_db)):
    variant = db.get(models.Variant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    if variant.status == "published":
        raise HTTPException(409, "a published variant cannot be rejected")
    variant.status = "rejected"
    variant.rejection_reason = payload.reason
    db.commit()
    db.refresh(variant)
    return variant


@app.post("/api/variants/{variant_id}/redraft", response_model=VariantOut, tags=["review"])
def redraft_variant(variant_id: str, db: Session = Depends(get_db)):
    variant = db.get(models.Variant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    variant.status = "draft"
    variant.rejection_reason = None
    db.commit()
    db.refresh(variant)
    return variant


@app.post("/api/slots", response_model=SlotOut, status_code=201, tags=["scheduler"])
def create_slot(payload: SlotCreate, db: Session = Depends(get_db)):
    """Schedule an APPROVED variant at a time slot. Refuses anything else."""
    variant = db.get(models.Variant, payload.variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    if variant.status != "approved":
        raise HTTPException(
            409,
            f"variant {variant.id} is '{variant.status}', not 'approved'; "
            "only approved variants can be scheduled",
        )

    scheduled_at = payload.scheduled_at
    if scheduled_at.tzinfo is not None:
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    if scheduled_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(422, "scheduled_at must be in the future")

    key = slot_idempotency_key(variant.id, scheduled_at)
    existing = (
        db.query(models.Slot)
        .filter(models.Slot.variant_id == variant.id)
        .filter(models.Slot.scheduled_at == scheduled_at)
        .first()
    )
    if existing:
        db.refresh(existing)
        return existing

    slot = models.Slot(
        variant_id=variant.id,
        platform=variant.platform,
        scheduled_at=scheduled_at,
        status="scheduled",
        idempotency_key=key,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


@app.get("/api/slots", response_model=list[SlotOut], tags=["scheduler"])
def list_slots(db: Session = Depends(get_db)):
    return db.query(models.Slot).order_by(models.Slot.scheduled_at).all()


@app.post("/api/sweep", tags=["scheduler"])
def sweep_once(db: Session = Depends(get_db)):
    """Manually trigger the durable scheduler sweep. Used for demos and tests."""
    registry = build_registry(settings.publishers, db)
    processed = publish_due_slots(db, registry)
    return {"processed": processed, "now": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


@app.get("/api/history", response_model=list[PublishAttemptOut], tags=["history"])
def publish_history(db: Session = Depends(get_db)):
    return (
        db.query(models.PublishAttempt)
        .order_by(models.PublishAttempt.created_at.asc())
        .all()
    )


@app.get("/api/history/variant/{variant_id}", response_model=list[PublishAttemptOut], tags=["history"])
def history_for_variant(variant_id: str, db: Session = Depends(get_db)):
    return (
        db.query(models.PublishAttempt)
        .filter(models.PublishAttempt.variant_id == variant_id)
        .order_by(models.PublishAttempt.created_at.asc())
        .all()
    )


@app.get("/api/mock_outbox", tags=["mocks"])
def mock_outbox(db: Session = Depends(get_db)):
    rows = db.query(models.MockOutbox).order_by(models.MockOutbox.published_at.asc()).all()
    return [
        {
            "id": r.id,
            "platform": r.platform,
            "content": r.content,
            "idempotency_key": r.idempotency_key,
            "published_at": r.published_at,
            "published": True,
        }
        for r in rows
    ]


@app.get("/api/platforms", tags=["meta"])
def platforms():
    registry = build_registry(settings.publishers, None)
    return {
        platform: adapter.describe()
        for platform, adapter in registry.items()
    }