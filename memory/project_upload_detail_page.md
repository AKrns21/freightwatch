---
name: Upload detail page and new API endpoints
description: New frontend page and backend endpoints added for upload inspection
type: project
---

Added on 2026-03-19:

**New backend endpoints (app/routers/upload.py):**
- GET /api/uploads/{id}/detail — full upload DB record + shipment_count
- GET /api/uploads/{id}/shipments — list of shipments parsed from this upload
- GET /api/uploads/{id}/file — download original file
- POST /api/uploads/{id}/reprocess — reset status and re-trigger pipeline (atomic, rejects if parsing)

**New frontend page:** frontend/src/pages/UploadDetail.tsx
- Route: /uploads/:uploadId/detail
- Shows all DB fields, parsing_issues, llm_analysis, suggested_mappings, meta
- "Originaldatei herunterladen" button (downloads original file)
- "Erneut verarbeiten" button (calls reprocess endpoint, polls for completion)

**ProjectDetail.tsx changes:**
- Added "Detail →" link for every upload (not just needs_review)
- Added needs_manual_review orange badge color
- Added partial_success blue badge color
