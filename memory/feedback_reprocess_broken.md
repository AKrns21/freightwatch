---
name: Reprocess race condition — root cause and fix
description: The reprocess endpoint was failing due to a FastAPI background task vs session commit race condition
type: feedback
---

**RESOLVED in commit a2f3fac.**

**Root cause:** FastAPI runs `BackgroundTasks` inside `response.__call__()`, which happens BEFORE the `Depends` generator's cleanup code (i.e., `session.commit()`). So the background task's first `UPDATE upload SET status='parsing'` was blocked by the HTTP handler's uncommitted row lock on the same upload row.

**Why:** This is a fundamental FastAPI/Starlette ordering issue — not obvious from the docs.

**How to apply:** Any time an endpoint does a DB write AND starts a background task that also writes the same row, add `await db.commit()` explicitly before `background_tasks.add_task(...)`. The dependency generator will call commit again as a no-op.

**Additional fixes shipped at the same time:**
- `lock_timeout='5s'` in `_update_status` and `lock_timeout='3s'` in reprocess endpoint for fast-fail on external lock contention
- `statement_timeout=20s` + `idle_in_transaction_session_timeout=30s` in `server_settings` to auto-kill orphan Postgres backends
- `command_timeout` reduced from 120s to 25s
