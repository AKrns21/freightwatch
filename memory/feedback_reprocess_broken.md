---
name: Reprocess endpoint still broken as of session end
description: The reprocess button on UploadDetail page was not working at end of session
type: feedback
---

The reprocess endpoint (POST /api/uploads/{id}/reprocess) and the "Erneut verarbeiten" button on UploadDetail page were still not working correctly at the end of the 2026-03-19 session.

**Why:** The session ended before fully debugging the issue. Multiple iterations were attempted.

**Known symptoms:**
- Frontend shows "Wird verarbeitet..." and hangs
- CORS 500 error in browser console
- Backend logs show TimeoutError (empty string) in the reprocess endpoint

**Root causes identified:**
1. Vision OCR timeout (120s) — FIXED by skipping PDFs in _extract_document
2. DB row lock when background task is running — FIXED with atomic UPDATE WHERE status != 'parsing'
3. But still not working — user ended session

**How to apply:** When a new session starts, check the current state of the reprocess endpoint and test it end-to-end. The fix might require checking if the server properly reloaded the latest code changes.
