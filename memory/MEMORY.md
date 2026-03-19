# FreightWatch Memory Index

This directory contains persistent notes across Claude Code sessions. Each file documents a specific area of the codebase, a design decision, or unresolved issue.

## Files

| File | Type | Summary |
|------|------|---------|
| [project_upload_pipeline.md](./project_upload_pipeline.md) | project | Current state of the upload processing pipeline — pipeline stages, what works, PDF handling, known issues with reprocess |
| [project_parsing_templates.md](./project_parsing_templates.md) | project | Global parsing template design — why templates are generic (not carrier-specific), the 4 seeded templates, matching logic, how to re-seed |
| [project_logging.md](./project_logging.md) | project | Logging setup — rotating file at backend/logs/freightwatch.log, env vars, implementation in app/utils/logger.py |
| [project_upload_detail_page.md](./project_upload_detail_page.md) | project | New UploadDetail frontend page and backend endpoints added 2026-03-19 (detail, shipments, file download, reprocess) |
| [feedback_reprocess_broken.md](./feedback_reprocess_broken.md) | feedback | Reprocess endpoint still broken at session end — symptoms, root causes investigated, what was and was not fixed |

## How to use

When starting a new session, read the relevant file(s) above before touching the related code. Each file has a **How to apply** section where applicable.

To add a new memory file: create the `.md` file in this directory and add a row to the table above.
