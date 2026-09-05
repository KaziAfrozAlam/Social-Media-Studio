# Social Media Studio

Turn **one blog post** into a full multi-platform social campaign with a
human review gate and an **idempotent, crash-safe scheduler**:

```
[blog post: URL or pasted Markdown]
        |
        v
 ingest + store (DB is the single source of truth)
        |
        v
 variant generator -> constraint validation (blocked rule = named 422)
        |
        v
 review workflow: draft -> approved | rejected
        |
        v
 scheduler (durable, resumable — the DB is the job store)
        |
        v
 SocialPublisher interface
   +-- TelegramPublisher   (REAL free target you own)
   +-- MockX / MockLinkedIn / MockInstagram  (record what they'd post)
        |
        v
 publish history: one slot = one post, always
```

The point is not API calls. It is a publishing system that survives the real
world: retries never double-post, a worker killed mid-batch resumes with zero
duplicates, and an unapproved variant can never go out.

## The hard parts and how they're solved

| Hard part | Solution |
|---|---|
| **Idempotency** | Every slot carries `idempotency_key = slot:<variant_id>:<scheduled_at>`. A **partial unique index** `(idempotency_key) WHERE status='success'` on `publish_attempts` makes two successful posts for the same slot a DB-level impossibility. Retries first check for an existing success and return it. |
| **Constraint profiles** | Each platform has a `ConstraintProfile` (max length, tone, min/max hashtags). One validator runs on generation, manual create, *and* edit. A broken rule → `422` naming the rule. |
| **Durable scheduling** | The SQLite DB *is* the job store. The worker queries due slots, publishes, commits per slot. A killed worker loses nothing committed; the rest stay `scheduled` and resume. No Redis, no in-memory queue. |
| **Adapter seam** | The app depends only on `SocialPublisher.publish(content, idempotency_key)`. A config string `PUBLISHERS=x=MockXPublisher,...` maps platforms to adapters. Swapping = edit the env var, not the code. |

## Quickstart — fastest path (Docker)

Requires Docker. No credit card, nothing to configure to see it work
(mocks only — see below to add the real Telegram target).

```bash
cp .env.example .env
docker compose up --build
```

The API comes up at http://localhost:8000 (docs at `/docs`), the durable
worker runs in a second container, and a sample post + variants are seeded.

## Quickstart — plain Python

```bash
python -m venv .venv
.venv/Scripts/activate                # mac/linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

.venv/Scripts/python -m app.seed      # seed a sample post + variants
.venv/Scripts/uvicorn app.main:app --port 8000      # terminal 1: API
.venv/Scripts/python -m app.worker                   # terminal 2: scheduler
```

## Adding the real (free) Telegram target

1. Create a bot with [@BotFather](https://t.me/BotFather) → get a token.
2. Add the bot to a channel/server you own (bots allowed).
3. Put values in `.env` (never commit this file):
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAA...
   TELEGRAM_CHAT_ID=@my_test_channel
   ```
   Telegram is already the platform key `telegram` in the default
   `PUBLISHERS`, so nothing else changes.

## Try it end to end (the acceptance probes)

```bash
BASE=http://127.0.0.1:8000

# 1. ingest + generate
curl -s -X POST $BASE/api/posts -H 'content-type: application/json' \
  -d '{"title":"Crash-safe publishing","content":"Retries must never double-post..."}'
# take the returned post id
curl -s -X POST $BASE/api/posts/<POST_ID>/variants

# 2. a rule-breaking variant is blocked, rule named
curl -s -X POST $BASE/api/variants -H 'content-type: application/json' \
  -d '{"post_id":"<POST_ID>","platform":"linkedin","content":"no hashtags here"}'
# -> 422  ["linkedin: hashtag count 0 below minimum 3"]

# 3. schedule before approval → refused
curl -s -X POST $BASE/api/slots -H 'content-type: application/json' \
  -d '{"variant_id":"<VARIANT_ID>","scheduled_at":"2026-09-06T12:00:00Z"}'
# -> 409  "variant ... is 'draft', not 'approved'; only approved variants can be scheduled"

# 4. approve, schedule ~2 min out, let the worker fire, check history
curl -s -X POST $BASE/api/variants/<VARIANT_ID>/approve
curl -s -X POST $BASE/api/slots -H 'content-type: application/json' \
  -d '{"variant_id":"<VARIANT_ID>","scheduled_at":"<NOW+2MIN ISO>"}'
curl -s $BASE/api/history

# 5. crash-restart proof (no duplicates)
#    stop the worker (Ctrl+C / kill), restart it: the DB is the job store,
#    completed slots are skipped, interrupted ones resume.
export SCHEDULER_CRASH_AFTER=1        # for a scripted demo: worker exits after 1 publish
.venv/Scripts/python -m app.worker
#    restart without SCHEDULER_CRASH_AFTER -> finishes the batch, zero dupes
```

## Verifying the claims

```bash
.venv/Scripts/python -m pytest tests -q        # 14 tests: blocked variant, refused
# schedule, repeated-publish dedup, DB unique-index proof, crash-restart with
# real subprocesses, adapter seam, history
PYTHONIOENCODING=utf-8 .venv/Scripts/python tests/live_demo.py   # full transcript
```

Every proof (with transcript excerpts) is in **[EVIDENCE.md](EVIDENCE.md)**.
Honest AI usage is logged in **[BUILDLOG.md](BUILDLOG.md)**.

## Project layout

```
app/
  main.py                 FastAPI app: ingest, variants, review, slots, history
  models.py               posts / variants / slots / publish_attempts / mock_outbox
  config.py               .env-driven settings
  db.py                   SQLite engine + session factory
  constraint_profiles/    per-platform profiles + validate()
  services/
    generator.py          template variant generation (reads stored post only)
    publisher.py          the exactly-once publish orchestration
    scheduler.py          due-slot sweep + idempotency key derivation
  adapters/
    base.py               SocialPublisher interface (the seam)
    telegram.py           REAL adapter (Telegram Bot API)
    mocks.py              MockX / MockLinkedIn / MockInstagram (record to DB)
    registry.py           PUBLISHERS config string -> adapter instances
  worker.py               durable worker subprocess (crash-resumable)
  seed.py                 sample post + variants
tests/                    pytest suite + live demo transcript generator
```

## Known limitations

- **Exactly-once is DB-enforced; the *real* Telegram call is at-most-once plus
  a dedup check.** If the process dies between the successful Telegram send and
  the DB commit, a retry may send again. Mock adapters close this gap fully
  (they deduplicate on their own key); this is the standard
  at-least-once/at-most-once trade-off and is documented in
  `app/services/publisher.py`.
- Variant generation uses hand-written templates (AI optional per spec); there
  is no AI cost.
- Telegram should point at a channel/server you own; never publish real X or
  LinkedIn accounts per the capstone's safety rules — the mocks exist for that.
- Single-node SQLite (fine for a demo; swap `DATABASE_URL` for PostgreSQL
  without code changes).