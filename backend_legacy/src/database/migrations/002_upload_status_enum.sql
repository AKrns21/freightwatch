-- Migration 002: Add CHECK constraint on upload.status
-- Canonical values aligned with UploadStatus TypeScript enum

ALTER TABLE upload
  ADD CONSTRAINT upload_status_check CHECK (
    status IN (
      'pending',
      'processing',
      'parsed',
      'partial_success',
      'needs_review',
      'needs_manual_review',
      'failed',
      'error',
      'reviewed',
      'unmatched'
    )
  );
