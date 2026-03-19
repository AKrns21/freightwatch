# Tenant Isolation Authentication Module

This module provides **CRITICAL** tenant isolation for Row Level Security (RLS) in FreightWatch.

## ⚠️ SECURITY CRITICAL

**This module is ESSENTIAL for data security.** Without proper tenant isolation:
- Users could access data from other tenants
- Row Level Security policies would not work
- Data breaches could occur

## Components

### TenantInterceptor

Global interceptor that:
1. Extracts `tenantId` from JWT token
2. Sets PostgreSQL session: `SET LOCAL app.current_tenant = tenantId`
3. Processes request (all DB queries are tenant-scoped)
4. Resets context: `RESET app.current_tenant`

### Decorators

- `@TenantId()` - Extract tenant ID from request
- `@UserId()` - Extract user ID from request  
- `@CurrentUser()` - Extract full JWT payload
- `@TenantContext()` - Extract both tenant and user ID

## Usage Example

```typescript
import { Controller, Get, Post, Body } from '@nestjs/common';
import { TenantId, UserId, CurrentUser, TenantContext } from './auth/tenant.decorator';

@Controller('shipments')
export class ShipmentsController {
  constructor(private readonly shipmentsService: ShipmentsService) {}

  // Simple tenant ID extraction
  @Get()
  async findAll(@TenantId() tenantId: string) {
    // Database queries automatically scoped to this tenant
    return this.shipmentsService.findByTenant(tenantId);
  }

  // Multiple parameters
  @Post()
  async create(
    @TenantId() tenantId: string,
    @UserId() userId: string,
    @Body() createShipmentDto: CreateShipmentDto
  ) {
    return this.shipmentsService.create(tenantId, userId, createShipmentDto);
  }

  // Full user context
  @Get('profile')
  async getProfile(@CurrentUser() user: JwtPayload) {
    return {
      tenantId: user.tenantId,
      userId: user.sub,
      email: user.email,
      roles: user.roles
    };
  }

  // Convenience context object
  @Get('dashboard')
  async getDashboard(@TenantContext() context: { tenantId: string; userId: string }) {
    return this.dashboardService.getDashboard(context.tenantId, context.userId);
  }
}
```

## JWT Token Structure

The JWT must contain the following payload:

```json
{
  "sub": "user-uuid-here",           // User ID (required)
  "tenantId": "tenant-uuid-here",    // Tenant ID (CRITICAL - required)
  "email": "user@example.com",       // User email (optional)
  "roles": ["admin", "user"],        // User roles (optional)
  "iat": 1640995200,                 // Issued at (optional)
  "exp": 1641081600                  // Expiration (optional)
}
```

## Security Flow

```
1. Client Request with JWT
   ↓
2. TenantInterceptor extracts tenantId from JWT
   ↓
3. SET LOCAL app.current_tenant = tenantId
   ↓
4. Controller processes request
   ↓
5. All database queries filtered by RLS policies
   ↓
6. RESET app.current_tenant (cleanup)
```

## Error Handling

The interceptor throws `UnauthorizedException` for:
- Missing Authorization header
- Invalid JWT token format
- Missing tenantId in JWT payload
- Invalid tenantId UUID format
- Expired JWT tokens

## Production Security

For production use:
1. Enable JWT signature verification in `TenantInterceptor`
2. Use proper JWT secrets/public keys
3. Implement token refresh mechanisms
4. Add rate limiting
5. Enable comprehensive audit logging

## Testing

To test tenant isolation, use different JWT tokens with different `tenantId` values and verify that data access is properly scoped.

Example test JWT (for development only):
```
Header: {"alg": "none", "typ": "JWT"}
Payload: {"sub": "123e4567-e89b-12d3-a456-426614174000", "tenantId": "456e7890-e89b-12d3-a456-426614174111"}
```

**Remember: This module is critical for security - any changes must be thoroughly tested!**