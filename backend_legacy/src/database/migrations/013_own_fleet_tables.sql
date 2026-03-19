-- Migration 013: own fleet tables (fleet_vehicle, fleet_driver + own_tour / own_tour_stop columns)
-- Architecture §4.2 / §5.2 — closes GitHub issue #20
--
-- Context:
--   - own_tour / own_tour_stop tables already exist (renamed in migration 006 from route_trip/route_stop)
--   - The existing `vehicle` table is telemetry-focused (plate number, type)
--   - fleet_vehicle is the NEW benchmark cost model table (fixed daily cost, variable km cost)
--   - fleet_driver is NEW (hourly rate for own-tour cost calculation)
--
-- Own vs. Carrier Benchmark cost model (§5.2):
--   fixed_cost  = fleet_vehicle.fixed_cost_per_day
--   fuel_cost   = own_tour.distance_km × fleet_vehicle.variable_cost_per_km
--   driver_cost = own_tour.duration_hours × fleet_driver.hourly_rate
--   total       = fixed_cost + fuel_cost + driver_cost
--   per_stop    = total / own_tour.stop_count

-- ─────────────────── UP ───────────────────────────────────────────────────

-- 1. fleet_vehicle — cost-model master data for own delivery vehicles
CREATE TABLE fleet_vehicle (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid NOT NULL,
  license_plate         text NOT NULL,
  vehicle_type          text,             -- 'sprinter' | '7.5t' | '12t' | '40t'
  payload_kg            int,
  fixed_cost_per_day    numeric(10,2),    -- EUR/day regardless of usage
  variable_cost_per_km  numeric(8,4),     -- EUR/km
  currency              char(3) DEFAULT 'EUR',
  active                boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, license_plate)
);

ALTER TABLE fleet_vehicle ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fleet_vehicle
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_fleet_vehicle_tenant ON fleet_vehicle(tenant_id);


-- 2. fleet_driver — driver hourly rate for own-tour cost model
CREATE TABLE fleet_driver (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL,
  name        text NOT NULL,
  hourly_rate numeric(8,2),   -- EUR/hour (driving + idle combined)
  currency    char(3) DEFAULT 'EUR',
  active      boolean NOT NULL DEFAULT true
);

ALTER TABLE fleet_driver ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fleet_driver
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_fleet_driver_tenant ON fleet_driver(tenant_id);


-- 3. own_tour — add columns required by the benchmark cost model
--    (existing columns from route_trip rename are preserved)
ALTER TABLE own_tour
  -- External tour identifier from dispatcher/TMS (e.g. "24475")
  ADD COLUMN tour_id         text,
  -- Link to the benchmark cost model driver (separate from telemetry vehicle FK)
  ADD COLUMN driver_id       uuid REFERENCES fleet_driver(id),
  -- Departure ZIP (depot address, used for benchmark region grouping)
  ADD COLUMN depot_zip       char(5),
  -- Total delivered weight across all stops (for weight-class analysis)
  ADD COLUMN total_weight_kg numeric(10,2),
  -- Full original row from dispatcher CSV (preserved for re-import)
  ADD COLUMN raw_data        jsonb;

CREATE INDEX idx_own_tour_driver ON own_tour(driver_id);


-- 4. own_tour_stop — add columns for shipment matching and delivery metrics
--    (existing columns: stop_sequence, arrival_zip, arrival_locality, etc.)
ALTER TABLE own_tour_stop
  -- Soft FK to shipment.shipment_reference — enables carrier cost lookup per stop
  ADD COLUMN shipment_ref text,
  -- Delivered weight at this stop
  ADD COLUMN weight_kg    numeric(10,2),
  -- Number of packages delivered at this stop
  ADD COLUMN packages     int;

CREATE INDEX idx_own_tour_stop_shipment_ref ON own_tour_stop(shipment_ref);


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- ALTER TABLE own_tour_stop DROP COLUMN packages, DROP COLUMN weight_kg, DROP COLUMN shipment_ref;
-- ALTER TABLE own_tour DROP COLUMN raw_data, DROP COLUMN total_weight_kg,
--   DROP COLUMN depot_zip, DROP COLUMN driver_id, DROP COLUMN tour_id;
-- DROP TABLE fleet_driver;
-- DROP TABLE fleet_vehicle;
