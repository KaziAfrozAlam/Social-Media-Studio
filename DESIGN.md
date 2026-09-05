# Social Media Studio — Design (Phase 1 gate)

**Problem.** A marketer writes one blog post and wants it on several platforms
with platform-specific tone and length. That is easy with an API. The hard
part is operational: publishes must be *exactly once* even when a network call
times out, a worker is killed mid-batch, or an operator double-clicks a
retry. An unapproved variant must never go out. A new platform must be a
config change, never a rewrite.

**Data model (single source of truth: the stored post).**
- `posts` — title + content; everything downstream reads only from here.
- `variants` — one row per (post, platform); status is one of
  `draft | approved | rejected | published`.
- `slots` — a scheduled publish: one variant at one future time. Carries a
  unique `idempotency_key` derived from `variant_id + scheduled_at`.
- `publish_attempts` — append-only log of every attempt (visible history).
  A **partial unique index** `(idempotency_key) WHERE status = 'success'`
  makes the exactly-once guarantee a *database constraint*, not a hope.
- `mock_outbox` — what the mock adapters "would have posted".

**Constraint profiles.** Each platform has a `ConstraintProfile` with
`max_length`, `tone`, `min/max_hashtags`. `validate()` returns a list of
failing rule names. Generation runs the same validator that review runs, so a
rule-breaking variant can never reach review.

**API surface.**
```
POST /api/posts {title, content | markdown_input | source_url}
POST /api/posts/{id}/variants        generate one variant per configured platform
POST /api/variants {post_id, platform, content}   -> 422 on rule break, rule named
PATCH /api/variants/{id} {content}   re-validated on edit
POST /api/variants/{id}/approve | /reject | /redraft
POST /api/slots {variant_id, scheduled_at}  -> 409 unless variant is approved
POST /api/sweep                      manual durable-scheduler sweep (demos/tests)
GET  /api/slots | /api/history | /api/mock_outbox | /api/variants/{id}
```

**The adapter seam.** `SocialPublisher.publish(content, idempotency_key)` is
the only contract. A registry maps `platform -> AdapterClass` from the
`PUBLISHERS` environment string. Swapping
`telegram=TelegramPublisher` to `telegram=MockXPublisher` is a config string
edit; zero business logic changes. Telegram hits the real Bot API; mock
adapters deduplicate on their own key too, so even a crash between adapter
call and DB commit cannot double-record.

**Exactly-once publish sequence.**
1. Look up any existing `success` attempt for the key → if present, return it
   without calling the adapter (idempotent retry).
2. Insert `publish_attempts` row as `attempted`.
3. Call the adapter. On adapter failure, mark the attempt `failed`; the slot
   stays `scheduled` for retry.
4. On success, mark the attempt `success`. The partial unique index rejects a
   second success row for the same key (including concurrent workers), and the
   publisher turns that into an `AlreadyPublished` outcome instead of a dup.

**Durable scheduling.** The SQLite DB *is* the job store. The worker
(`app/worker.py`) queries due slots, publishes each, commits. If it is killed,
the committed successes survive; the uncommitted remain `scheduled`, so a
restarted worker resumes with zero duplicates. Slots with attempts stuck
`attempted` for >30s are reaped. No Redis, no in-memory queue — resumability
falls out of the DB.

**Non-goal:** no real Instagram/X/LinkedIn clients, no image gen, no
engagement analytics. Real-world publishing is limited to one owned free
target (Telegram) plus two mock adapters that prove the seam.