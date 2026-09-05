from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PostCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str | None = None
    source_url: str | None = None
    markdown_input: str | None = None


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content: str
    source_url: str | None = None
    markdown_input: str | None = None
    created_at: datetime


class VariantCreate(BaseModel):
    post_id: str
    platform: str
    content: str


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    post_id: str
    platform: str
    content: str
    status: str
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class VariantEdit(BaseModel):
    content: str


class VariantReject(BaseModel):
    reason: str = Field(min_length=1)


class SlotCreate(BaseModel):
    variant_id: str
    scheduled_at: datetime


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    variant_id: str
    platform: str
    scheduled_at: datetime
    status: str
    idempotency_key: str


class PublishAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slot_id: str
    variant_id: str
    platform: str
    idempotency_key: str
    status: str
    result: str | None = None
    error: str | None = None
    message_url: str | None = None
    external_id: str | None = None
    created_at: datetime