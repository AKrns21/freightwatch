-- Migration 006: Rename route_trip → own_tour and route_stop → own_tour_stop
-- Reason: Names align with the architecture document business domain language.
-- Data-only rename — no column changes. See architecture §12 (Naming Changes).

-- ─────────────────── UP ───────────────────────────────────────────────────

-- 1. Rename tables
ALTER TABLE route_stop RENAME TO own_tour_stop;
ALTER TABLE route_trip RENAME TO own_tour;

-- 2. Rename foreign key column reference (cascade-style: rename the FK column name on own_tour_stop)
ALTER TABLE own_tour_stop RENAME COLUMN trip_id TO tour_id;

-- 3. Rename indexes
ALTER INDEX idx_route_trip_tenant_date RENAME TO idx_own_tour_tenant_date;
ALTER INDEX idx_route_trip_vehicle     RENAME TO idx_own_tour_vehicle;
ALTER INDEX idx_route_stop_trip        RENAME TO idx_own_tour_stop_tour;

-- 4. Rename RLS policy on own_tour (policy name carried over from route_trip)
ALTER POLICY tenant_isolation ON own_tour RENAME TO tenant_isolation;
-- Note: policy name stays "tenant_isolation" — no rename needed, table rename is sufficient.
-- The policy itself is re-checked against the new table name automatically.

-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- ALTER TABLE own_tour_stop RENAME COLUMN tour_id TO trip_id;
-- ALTER TABLE own_tour_stop RENAME TO route_stop;
-- ALTER TABLE own_tour RENAME TO route_trip;
-- ALTER INDEX idx_own_tour_tenant_date RENAME TO idx_route_trip_tenant_date;
-- ALTER INDEX idx_own_tour_vehicle     RENAME TO idx_route_trip_vehicle;
-- ALTER INDEX idx_own_tour_stop_tour   RENAME TO idx_route_stop_trip;
