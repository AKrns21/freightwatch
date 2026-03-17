-- Migration 014: tenant.freight_delta_threshold_pct
-- Architecture §5.1 — closes GitHub issue #21
--
-- The delta threshold determines when an invoice line is flagged as over/under-billed
-- relative to the expected tariff price. Configurable per tenant so consultants
-- can tune sensitivity per client (e.g. stricter for large freight volumes).
--
-- Benchmark engine classification:
--   delta_pct < -threshold  → 'unter'    (undercharged)
--   -threshold ≤ delta_pct ≤ threshold  → 'im_markt'  (within tolerance)
--   delta_pct > threshold   → 'drüber'   (overcharged — flag for review)

-- ─────────────────── UP ───────────────────────────────────────────────────

ALTER TABLE tenant
  ADD COLUMN freight_delta_threshold_pct numeric(5,2) NOT NULL DEFAULT 5.0;


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- ALTER TABLE tenant DROP COLUMN freight_delta_threshold_pct;
