-- Seed data for service catalog and aliases
-- This migration populates the service normalization system

-- Insert service catalog entries
INSERT INTO service_catalog (code, description, category) VALUES
  ('STANDARD', 'Standard Delivery', 'standard'),
  ('EXPRESS', 'Express/Next Day', 'premium'),
  ('ECONOMY', 'Economy/Slow', 'economy'),
  ('PREMIUM', 'Premium Service', 'premium'),
  ('SAME_DAY', 'Same Day Delivery', 'premium'),
  ('OVERNIGHT', 'Overnight Delivery', 'premium')
ON CONFLICT (code) DO NOTHING;

-- Global service aliases (tenant_id = NULL, carrier_id = NULL)
INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
  -- Express variants
  (NULL, NULL, 'express', 'EXPRESS'),
  (NULL, NULL, '24h', 'EXPRESS'),
  (NULL, NULL, 'next day', 'EXPRESS'),
  (NULL, NULL, 'nextday', 'EXPRESS'),
  (NULL, NULL, 'overnight', 'OVERNIGHT'),
  (NULL, NULL, 'same day', 'SAME_DAY'),
  (NULL, NULL, 'sameday', 'SAME_DAY'),
  
  -- Standard variants
  (NULL, NULL, 'standard', 'STANDARD'),
  (NULL, NULL, 'normal', 'STANDARD'),
  (NULL, NULL, 'regular', 'STANDARD'),
  (NULL, NULL, 'default', 'STANDARD'),
  
  -- Economy variants
  (NULL, NULL, 'economy', 'ECONOMY'),
  (NULL, NULL, 'eco', 'ECONOMY'),
  (NULL, NULL, 'slow', 'ECONOMY'),
  (NULL, NULL, 'spar', 'ECONOMY'),
  (NULL, NULL, 'günstig', 'ECONOMY'),
  (NULL, NULL, 'cheap', 'ECONOMY'),
  
  -- Premium variants
  (NULL, NULL, 'premium', 'PREMIUM'),
  (NULL, NULL, 'priority', 'PREMIUM'),
  (NULL, NULL, 'first class', 'PREMIUM'),
  (NULL, NULL, 'firstclass', 'PREMIUM'),
  
  -- German specific aliases
  (NULL, NULL, 'eilsendung', 'EXPRESS'),
  (NULL, NULL, 'schnell', 'EXPRESS'),
  (NULL, NULL, 'standardversand', 'STANDARD'),
  (NULL, NULL, 'normalversand', 'STANDARD'),
  (NULL, NULL, 'sparversand', 'ECONOMY'),
  (NULL, NULL, 'langsam', 'ECONOMY')
ON CONFLICT (COALESCE(tenant_id::text, 'global'), COALESCE(carrier_id::text, 'all'), alias_text) DO NOTHING;

-- Common carrier-specific aliases examples
-- These would be populated based on actual carrier service names

-- DHL examples (if we had carrier UUIDs)
-- INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
--   (NULL, 'dhl-carrier-uuid', 'DHL Express', 'EXPRESS'),
--   (NULL, 'dhl-carrier-uuid', 'DHL Standard', 'STANDARD'),
--   (NULL, 'dhl-carrier-uuid', 'DHL Economy', 'ECONOMY');

-- UPS examples
-- INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES  
--   (NULL, 'ups-carrier-uuid', 'UPS Next Day', 'EXPRESS'),
--   (NULL, 'ups-carrier-uuid', 'UPS Ground', 'STANDARD'),
--   (NULL, 'ups-carrier-uuid', 'UPS SurePost', 'ECONOMY');

-- FedEx examples
-- INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
--   (NULL, 'fedex-carrier-uuid', 'FedEx Overnight', 'OVERNIGHT'),
--   (NULL, 'fedex-carrier-uuid', 'FedEx Express', 'EXPRESS'),
--   (NULL, 'fedex-carrier-uuid', 'FedEx Ground', 'STANDARD');