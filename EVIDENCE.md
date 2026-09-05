# EVIDENCE

One proof per Requirements box. Every transcript below was produced by running
this repository, not by editing output.

Run the full live demo yourself:

```bash
python -m venv .venv && .venv/Scripts/activate    # or: pipenv install
pip install -r requirements.txt
PYTHONIOENCODING=utf-8 .venv/Scripts/python tests/live_demo.py
.venv/Scripts/python -m pytest tests -q
```

---

## 1. Ingestion — post stored once; variants read only the stored post

`transcript_live.txt`, sections 1–3:

```
### 1. Ingest: pasted Markdown
  POST /api/posts -> 201
### 2. Ingest: via URL fetch
  POST /api/posts -> 201
  fetched content length: 288
### 3. Generate one variant per platform (stored post is the source of truth)
  POST /api/posts/1423.../variants -> 201
  [x] 248 chars, draft
  [linkedin] 491 chars, draft
  [telegram] 410 chars, draft
```

`apply_variant_generation` in `app/main.py` reads `db.get(Post, post_id)`
(the stored row) and builds every variant from that content only — it never
re-fetches `source_url`. The generator service (`app/services/generator.py`)
takes a `Post` object.

---

## 2. Constraint profiles enforced by code — bad variant blocked, rule named

Section 4 of the live transcript:

```
### 4. Constraint enforcement: break a rule, get it blocked with the rule named
  POST /api/variants -> 422
  {"detail": ["linkedin: hashtag count 0 below minimum 3"]}
  BLOCKED: linkedin: hashtag count 0 below minimum 3
  POST /api/variants -> 422
  {"detail": ["x: content length 300 exceeds max 280"]}
  BLOCKED: x: content length 300 exceeds max 280
```

Enforcement lives in `app/constraint_profiles/profiles.py`:
`ConstraintProfile.validate()` checks length and hashtag counts and returns
human-readable violation messages that are surfaced as the 422 body. The
`tone` field shapes how the generator writes copy — it is *not* itself
validated. The same validator is used by automatic generation, manual variant
creation, and variant **edit**, so nothing rule-breaking can ever reach review.

Pytest: `test_variant_breaking_length_rule_is_blocked`,
`test_variant_breaking_hashtag_rule_is_blocked`,
`test_edit_that_breaks_rule_is_rejected` (all pass).

---

## 3. Review workflow — only approved variants schedulable

Section 5 of the live transcript:

```
### 5. Review: unapproved variant cannot be scheduled
  POST /api/slots -> 409
  -> 409: variant ... is 'draft', not 'approved'; only approved variants can be scheduled

### 6. Approve + schedule
  POST /api/variants/1e9ad4fda88e4357a29410b3881c9fc7/approve -> 200
  POST /api/slots -> 201
```

`create_slot` in `app/main.py` is the only path that creates slots, and it
returns HTTP 409 with a message naming the actual status when
`variant.status != "approved"`. Statuses are exactly
`draft / approved / rejected / published` (`app/models.py`).

Pytest: `test_unapproved_variant_cannot_be_scheduled`,
`test_approved_variant_can_be_scheduled_and_publishes_once` (pass).

---

## 4. Adapter layer — one interface, one real target, mocks; swap = config

Interface: `app/adapters/base.py`

```python
class SocialPublisher(ABC):
    platform: str
    @abstractmethod
    def publish(self, content: str, idempotency_key: str) -> PublishResult: ...
```

Implementations:
- `TelegramPublisher` — real bot API (`app/adapters/telegram.py`); records the
  returned `message_url`/`external_id`. Requires `.env` token+chat (documented).
- `MockXPublisher`, `MockLinkedInPublisher`, `MockInstagramPublisher` —
  record "what would be posted" into the `mock_outbox` table, deduplicated by
  key (`app/adapters/mocks.py`).

Registry (`app/adapters/registry.py`) builds `platform -> SocialPublisher` from
the `PUBLISHERS` config string. Business logic only ever calls
`registry.get(platform).publish(...)`.

**Proof of config-only swap** — in `tests/live_demo.py` the *same campaign* is
published through two different stacks by changing only one environment
variable. The transcript shows the `telegram` platform key mapped to
`MockLinkedInPublisher` (confirmed: its history url is `mock://linkedin/...`):

```
  [x]      success mock://x/9e3eb93c...
  [linkedin] success mock://linkedin/1d2e2508...
  [telegram] success mock://linkedin/eda6e872...      <- telegram adapter swapped to a mock
```

No code change: only `PUBLISHERS=...` in the environment. The app code touches
the interface, never `MockXPublisher` or `TelegramPublisher` by name.

---

## 5. Idempotent publish — repeated calls produce exactly one post

Excerpt from the live transcript (section 8) — the slot was already published,
then `/api/sweep` was called three more times:

```
  outbox rows before=1 after 3 more sweeps => 1 (must stay 1)
```

And the pytest one-liner:

```
test_repeated_publish_calls_create_exactly_one_post PASSED
test_worker_crash_recovery PASSED
```

The mechanism (`app/services/publisher.py`):
1. Look up an existing `success` attempt for the key → return it, no publish.
2. Adapters deduplicate on their own key as a second net.
3. A **partial unique index** on `publish_attempts(idempotency_key) WHERE
   status = 'success'` (`app/models.py`) makes a second success row for the
   same key *impossible* at the DB layer — `test_unique_success_index_blocks_second_success`
   proves a hand-forced duplicate INSERT is rejected with `IntegrityError`.

---

## 6. Durable scheduling — worker killed mid-batch resumes with zero dups

`tests/live_demo.py` section 9 spawns the real worker as a subprocess,
kills it mid-batch (`SCHEDULER_CRASH_AFTER=1` → `os._exit(1)`), restarts it,
and counts:

```
### 9. Crash-restart: two slots, worker dies after the first, restart -> no dupes
  worker #1 exit code: 1 (expected 1 = crashed)
  ... CRASH SIMULATION: published 1 slot(s) this sweep; exiting now ...
  worker #2 exit code: 0
  total outbox rows now=4, duplicate keys=0
```

The same scenario is a regression test (`tests/test_crash_resume.py`):

```
test_worker_crash_restart_publishes_all_exactly_once PASSED
```

Durability comes from the DB-as-job-store design: successes are committed per
slot; anything not committed is still `scheduled` and picked up by the next
worker. Nothing is held in process memory.

---

## 7. Publish history — every attempt visible and queryable

Section 10:

```
### 10. Publish history is visible and queryable
  total attempts recorded: 4
  [x] success mock://x/9e3eb93c...
  [x] success mock://x/67cf00fa...
  [linkedin] success mock://linkedin/1d2e2508...
  [telegram] success mock://linkedin/eda6e872...
```

Every call to the adapter records an append-only `publish_attempts` row with
status, result, error (n/a), message_url and idempotency key. Served by
`GET /api/history` and `GET /api/history/variant/{id}`. Includes failed
attempts with the error message. Pytest: `test_publish_history_visible`.

---

## 8. Secrets stay secret

- `.gitignore` contains `.env` (first line).
- `.env.example` ships placeholder values for `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, etc.
- `app/config.py` reads via `dotenv` from `.env`; no hardcoded tokens.
- Nothing in the repo (source, transcripts, tests) contains a real token.

Run `git status` at repo root to confirm `.env` is untracked after you create it.

---

## 9. README — stranger can run it with one command

See `README.md`: `docker compose up --build` or the plain-Python route
(`pip install -r requirements.txt` then two commands). A `seed.py` step
creates a sample post + variants for instant inspection.

---

## Test suite summary (all 14 pass)

```
$ pytest tests -q
14 passed, 2 warnings in ~6s
```

The 2 warnings are deprecation notices from third-party test libraries
(starlette/anyio/httpx), not from application code.

Coverage of the scary cases: blocked variant (2), refused schedule, duplicate
publish (repeat + unique-index + crash), adapter seam, publish history,
crash-restart with real worker subprocesses.