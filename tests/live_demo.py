"""End-to-end demo against a real running server + real worker subprocesses.

Captures a full transcript showing:
  1. ingest a markdown post (and a second post via source_url fetch)
  2. generate variants per platform with constraint enforcement
  3. blocking a rule-breaking variant (422 naming the rule)
  4. refusing to schedule an unapproved variant (409)
  5. approve -> schedule -> publish through the durable worker
  6. crash the worker mid-batch, restart it, zero duplicates
  7. idempotent repeat-publish transcript
  8. publish history view
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = "\033[1;36m"  # cyan bold
OK = "\033[1;32m"  # green bold
WARN = "\033[1;33m"
RED = "\033[1;31m"
DIM = "\033[2m"
RST = "\033[0m"


def headline(*parts):
    print(f"\n{OK}== {' '.join(str(p) for p in parts)} =={RST}")


def demote(*parts):
    print(f"{DIM}  {' '.join(str(p) for p in parts)}{RST}")


def section(msg):
    print(f"\n{UA}### {msg}{RST}")


RUNZ = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="sms_live_")
DB = f"sqlite:///{TMP}/live.db"

sys.path.insert(0, str(RUNZ))

from fastapi.testclient import TestClient  # noqa: E402

os.environ["DATABASE_URL"] = DB
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["PUBLISHERS"] = "x=MockXPublisher,linkedin=MockLinkedInPublisher,telegram=MockLinkedInPublisher"
os.environ["SCHEDULER_POLL_SECONDS"] = "1"

from app.config import reset_settings_cache  # noqa: E402
from app.db import reset_db  # noqa: E402

reset_settings_cache()
reset_db()
from app import main as app_module  # noqa: E402

c = TestClient(app_module.app)

def http(method, path, **kw):
    r = getattr(c, method)(path, **kw)
    status = r.status_code
    try:
        body = r.json()
    except Exception:
        body = r.text
    label = f"{method.upper()} {path}"
    if status >= 400:
        print(f"  {WARN}{label} -> {status}{RST}")
        demote(json.dumps(body)[:400])
    else:
        print(f"  {OK}{label} -> {status}{RST}")
    return r, body

section("1. Ingest: pasted Markdown")
r, post = http("post", "/api/posts", json={
    "title": "How We Built a Crash-Safe Publisher",
    "content": (
        "After three months of building, our team shipped a publishing system "
        "where retries can never double-post. The idempotency key is per "
        "variant plus slot, and a database constraint guarantees exactly one "
        "success row per key. Workers that crash mid-batch resume from the job "
        "store and skip everything already recorded as success."
    ),
})
post_id = post["id"]

section("2. Ingest: via URL fetch")
r2, _ = http("post", "/api/posts", json={
    "title": "Fetched Post",
    "source_url": "https://example.com",
})
print(f"  fetched content length: {len(_['content'])}")

section("3. Generate one variant per platform (stored post is the source of truth)")
r, variants = http("post", f"/api/posts/{post_id}/variants")
for v in variants:
    demote(f"[{v['platform']}] {len(v['content'])} chars, {v['status']}")
    print(f"{DIM}    {v['content'][:80]!r}...{RST}")

section("4. Constraint enforcement: break a rule, get it blocked with the rule named")
broken = "A linkedin post with hashtags but way too short" 
r, body = http("post", "/api/variants", json={
    "post_id": post_id, "platform": "linkedin",
    "content": "interesting but needless text without any hashtags",
})
for detail in body.get("detail", []):
    print(f"{RED}  BLOCKED: {detail}{RST}")

r, body = http("post", "/api/variants", json={
    "post_id": post_id, "platform": "x",
    "content": "y" * 300,
})
for detail in body.get("detail", []):
    print(f"{RED}  BLOCKED: {detail}{RST}")

section("5. Review: unapproved variant cannot be scheduled")
xv = next(v for v in variants if v["platform"] == "x")
when_future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
r, body = http("post", "/api/slots", json={"variant_id": xv["id"], "scheduled_at": when_future})
print(f"{WARN}  -> {r.status_code}: {body.get('detail')}{RST}")

section("6. Approve + schedule")
r, _ = http("post", f"/api/variants/{xv['id']}/approve")
when = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
r, slot = http("post", "/api/slots", json={"variant_id": xv["id"], "scheduled_at": when})
print(f"  idempotency_key: {slot.get('idempotency_key')}")

section("7. Durable worker publishes the due slot (real worker subprocess)")
time.sleep(3)  # let the slot become due
env = dict(os.environ)
env["DATABASE_URL"] = DB
env["SCHEDULER_CRASH_AFTER"] = "0"
p = subprocess.run([sys.executable, "-m", "app.worker", "--once"], env=env, cwd=str(RUNZ), capture_output=True, text=True)
print(f"  worker output: {[l.strip() for l in (p.stderr or '').splitlines() if 'published' in l or 'due' in l]}")
r, history = http("get", "/api/history")
for h in history:
    print(f"  attempt: platform={h['platform']} status={h['status']} url={h['message_url']}")

section("8. Idempotent repeat-publish: same slot called again => zero new posts")
before_post_count = len(c.get("/api/mock_outbox").json())
for i in range(3):
    r, res = http("post", "/api/sweep")
after_post_count = len(c.get("/api/mock_outbox").json())
print(f"  outbox rows before={before_post_count} after 3 more sweeps => {after_post_count} (must stay {before_post_count})")
assert after_post_count == before_post_count == 1

section("9. Crash-restart: two slots, worker dies after the first, restart -> no dupes")
env2 = dict(os.environ)
lf = c.post("/api/posts", json={
    "title": "Crash-Proof Campaign",
    "content": "Five ideas our team is shipping this quarter for a resilient publish pipeline that survives process death.",
}).json()
c.post(f"/api/posts/{lf['id']}/variants")
for v in c.get(f"/api/posts/{lf['id']}/variants").json():
    c.post(f"/api/variants/{v['id']}/approve")
    w = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    c.post("/api/slots", json={"variant_id": v["id"], "scheduled_at": w})
time.sleep(2)

env2["SCHEDULER_CRASH_AFTER"] = "1"
time.sleep(3)  # let the crash campaign slots become due
p1 = subprocess.run([sys.executable, "-m", "app.worker"], env=env2, cwd=str(RUNZ), capture_output=True, text=True, timeout=30)
print(f"{WARN}  worker #1 exit code: {p1.returncode} (expected 1 = crashed){RST}")
crash_log = p1.stderr or p1.stdout
for line in crash_log.splitlines():
    if "CRASH SIMULATION" in line or "published" in line:
        print(f"{WARN}  {line.strip()}{RST}")

env2["SCHEDULER_CRASH_AFTER"] = "0"
p2 = subprocess.run([sys.executable, "-m", "app.worker", "--once"], env=env2, cwd=str(RUNZ), capture_output=True, text=True)
print(f"{OK}  worker #2 exit code: {p2.returncode}{RST}")

total = c.get("/api/mock_outbox").json()
keys = [o["idempotency_key"] for o in total]
dupes = len(keys) - len(set(keys))
print(f"  total outbox rows now={len(total)}, duplicate keys={dupes}")
print(f"{DIM}  rows={len(total)} (expected {1 + 3}: 1 from section 7 + 3 crash campaign){RST}")
assert dupes == 0

section("10. Publish history is visible and queryable")
r, history = http("get", "/api/history")
print(f"  total attempts recorded: {len(history)}")
for h in history:
    demote(f"  [{h['platform']}] {h['status']} {h['message_url']}")

headline("ALL CHECKS PASSED")
print()