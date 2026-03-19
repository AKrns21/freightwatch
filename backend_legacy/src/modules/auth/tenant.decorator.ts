import { createParamDecorator, ExecutionContext } from '@nestjs/common';
import { TenantRequest } from './tenant.interceptor';

/**
 * @TenantId() Parameter Decorator
 *
 * Extracts the tenantId from the request object that was set by TenantInterceptor.
 * This provides a clean way to access the current tenant ID in controllers.
 *
 * Usage:
 * ```typescript
 * @Get('shipments')
 * async getShipments(@TenantId() tenantId: string) {
 *   // tenantId is automatically extracted from JWT
 *   return this.shipmentsService.findByTenant(tenantId);
 * }
 * ```
 *
 * Security Note:
 * This decorator assumes that TenantInterceptor has already:
 * 1. Validated the JWT token
 * 2. Extracted and validated the tenantId
 * 3. Set the database tenant context
 * 4. Attached tenantId to the request object
 */
export const TenantId = createParamDecorator((_data: unknown, ctx: ExecutionContext): string => {
  const request = ctx.switchToHttp().getRequest<TenantRequest>();

  if (!request.tenantId) {
    throw new Error(
      'TenantId not found in request. Ensure TenantInterceptor is properly configured.'
    );
  }

  return request.tenantId;
});

/**
 * @UserId() Parameter Decorator
 *
 * Extracts the userId from the request object that was set by TenantInterceptor.
 *
 * Usage:
 * ```typescript
 * @Post('shipments')
 * async createShipment(
 *   @TenantId() tenantId: string,
 *   @UserId() userId: string,
 *   @Body() createShipmentDto: CreateShipmentDto
 * ) {
 *   return this.shipmentsService.create(tenantId, userId, createShipmentDto);
 * }
 * ```
 */
export const UserId = createParamDecorator((_data: unknown, ctx: ExecutionContext): string => {
  const request = ctx.switchToHttp().getRequest<TenantRequest>();

  if (!request.userId) {
    throw new Error(
      'UserId not found in request. Ensure TenantInterceptor is properly configured.'
    );
  }

  return request.userId;
});

/**
 * @CurrentUser() Parameter Decorator
 *
 * Extracts the full user object from JWT payload that was set by TenantInterceptor.
 *
 * Usage:
 * ```typescript
 * @Get('profile')
 * async getProfile(@CurrentUser() user: JwtPayload) {
 *   return {
 *     id: user.sub,
 *     email: user.email,
 *     tenantId: user.tenantId,
 *     roles: user.roles
 *   };
 * }
 * ```
 */
export const CurrentUser = createParamDecorator((_data: unknown, ctx: ExecutionContext) => {
  const request = ctx.switchToHttp().getRequest<TenantRequest>();

  if (!request.user) {
    throw new Error('User not found in request. Ensure TenantInterceptor is properly configured.');
  }

  return request.user;
});

/**
 * @TenantContext() Parameter Decorator
 *
 * Extracts both tenantId and userId in a single object for convenience.
 *
 * Usage:
 * ```typescript
 * @Get('dashboard')
 * async getDashboard(@TenantContext() context: { tenantId: string; userId: string }) {
 *   return this.dashboardService.getDashboard(context.tenantId, context.userId);
 * }
 * ```
 */
export const TenantContext = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): { tenantId: string; userId: string } => {
    const request = ctx.switchToHttp().getRequest<TenantRequest>();

    if (!request.tenantId || !request.userId) {
      throw new Error(
        'Tenant context not found in request. Ensure TenantInterceptor is properly configured.'
      );
    }

    return {
      tenantId: request.tenantId,
      userId: request.userId,
    };
  }
);
