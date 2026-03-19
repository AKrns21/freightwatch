---
name: Upload pipeline state (2026-03-19)
description: Current state of the upload processing pipeline, known issues, and what was changed
type: project
---

The upload pipeline was significantly reworked on 2026-03-19. Here is the current state:

**Pipeline stages (upload_processor_service.py):**
1. Load upload record
2. Set status → 'parsing'
2.5 DocumentService.process() — but ONLY for CSV/XLSX (PDFs are skipped to avoid Vision OCR timeout)
2.6 DocumentTypeDetector.detect() — classifies doc type, saves to upload.doc_type
3. TemplateService.find_match() — receives extracted column headers as file_content
4. Parse CSV/Excel with template
5–10. Validate, save shipments, benchmarks, update status

**Known working:**
- CSV/XLSX files with matching template headers will auto-parse
- 4 global templates seeded (seed with: cd backend && python -m app.scripts.seed_templates)
- Logging to file: backend/logs/freightwatch.log

**Known issue — PDF reprocessing:**
- PDF uploads land on needs_manual_review (correct — PDFs can't be auto-parsed by template system)
- The "Erneut verarbeiten" (reprocess) button on UploadDetail page is broken
- Root cause: two problems:
  1. The old Vision OCR was timing out after 120s (fixed by skipping PDFs in _extract_document)
  2. The reprocess endpoint was blocking on a DB row lock when a background task was already running
  3. The frontend polling for completion was added but the overall flow still doesn't work
- The reprocess endpoint now uses atomic UPDATE WHERE status != 'parsing' RETURNING ...
- The specific upload being tested: id=41277ea6-91f6-49ee-9c49-cb9cecc250f7, file="as 02-2023 Dirk Beese.pdf"

**Why:** PDFs require a future invoice-parsing-via-LLM flow. The template system only works for CSV/XLSX.

**How to apply:** When working on the upload pipeline or reprocess endpoint, be aware that PDFs should immediately go to needs_manual_review without Vision OCR. The reprocess button is the next thing to fix.
