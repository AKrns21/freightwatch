-- Test user seed for MVP
-- Password: 'password123' (hashed with bcrypt)
-- Email: test@freightwatch.com

INSERT INTO tenant (id, name, type, is_active, created_at, updated_at) 
VALUES (
  'c7b3d8e6-1234-4567-8901-123456789012',
  'Test Tenant',
  'company',
  true,
  NOW(),
  NOW()
) ON CONFLICT (id) DO NOTHING;

INSERT INTO users (
  id, 
  email, 
  password_hash, 
  first_name, 
  last_name, 
  tenant_id, 
  roles, 
  is_active,
  created_at,
  updated_at
) VALUES (
  'a1b2c3d4-5678-9012-3456-789012345678',
  'test@freightwatch.com',
  '$2b$10$OktO6q7IWvEfp5DiX./lkeKBxcNKwBP4ULi.drn08V8061ZRr81.a',
  'Test',
  'User',
  'c7b3d8e6-1234-4567-8901-123456789012',
  '["admin", "user"]',
  true,
  NOW(),
  NOW()
) ON CONFLICT (email) DO NOTHING;