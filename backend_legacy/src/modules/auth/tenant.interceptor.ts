import {
  Injectable,
  NestInterceptor,
  ExecutionContext,
  CallHandler,
  UnauthorizedException,
  Logger,
} from '@nestjs/common';
import { Observable } from 'rxjs';
import { tap, finalize } from 'rxjs/operators';
import { Request } from 'express';
import * as jwt from 'jsonwebtoken';
import { DatabaseService } from '@/database/database.service';

/**
 * JWT Payload interface for tenant-aware authentication
 */
interface JwtPayload {
  sub: string; // user ID
  tenantId: string; // tenant ID (CRITICAL for RLS)
  email?: string;
  roles?: string[];
  iat?: number;
  exp?: number;
}

/**
 * Request interface extended with tenant information
 */
export interface TenantRequest extends Request {
  tenantId: string;
  userId: string;
  user?: JwtPayload;
}

/**
 * CRITICAL: Tenant Isolation Interceptor
 *
 * This interceptor is ESSENTIAL for Row Level Security (RLS) to work properly.
 * It extracts the tenantId from the JWT token and sets the PostgreSQL session
 * variable 'app.current_tenant' which is used by RLS policies to filter data.
 *
 * Security Flow:
 * 1. Extract JWT from Authorization header
 * 2. Decode and validate JWT payload
 * 3. Extract tenantId from payload
 * 4. Set PostgreSQL session: SET LOCAL app.current_tenant = tenantId
 * 5. Process request (all DB queries now tenant-scoped)
 * 6. Reset context: RESET app.current_tenant
 *
 * WITHOUT THIS INTERCEPTOR, TENANT DATA ISOLATION WILL NOT WORK!
 */
@Injectable()
export class TenantInterceptor implements NestInterceptor {
  private readonly logger = new Logger(TenantInterceptor.name);

  constructor(private readonly databaseService: DatabaseService) {}

  async intercept(context: ExecutionContext, next: CallHandler): Promise<Observable<unknown>> {
    const request = context.switchToHttp().getRequest<TenantRequest>();
    const startTime = Date.now();

    try {
      // TEMPORARY DEV MODE: Skip auth completely for MVP development
      // Use whitelist approach: only allow bypass in explicitly defined development mode
      const isDevelopment = process.env.NODE_ENV === 'development';
      const authHeader = request.headers.authorization;

      let tenantId: string;
      let userId: string;
      let user: JwtPayload | undefined;

      if (isDevelopment && !authHeader) {
        // Development mode: use hardcoded test tenant (valid UUID format)
        tenantId = '00000000-0000-0000-0000-000000000001';
        userId = '00000000-0000-0000-0000-000000000002';
        user = undefined;

        this.logger.debug('[DEV MODE] No Authorization header - using test tenant');
      } else {
        // Extract and validate JWT token
        const jwtData = await this.extractTenantFromJWT(request);
        tenantId = jwtData.tenantId;
        userId = jwtData.userId;
        user = jwtData.user;
      }

      // CRITICAL: Set tenant context for RLS
      await this.databaseService.setTenantContext(tenantId);

      // Attach tenant info to request for use in controllers
      request.tenantId = tenantId;
      request.userId = userId;
      request.user = user;

      this.logger.debug(
        `Tenant context set: ${tenantId} for user: ${userId} (${Date.now() - startTime}ms)`
      );

      return next.handle().pipe(
        tap(() => {
          this.logger.debug(
            `Request completed for tenant: ${tenantId} (${Date.now() - startTime}ms)`
          );
        }),
        finalize(async () => {
          try {
            // CRITICAL: Always reset tenant context after request
            await this.databaseService.resetTenantContext();
            this.logger.debug(
              `Tenant context reset for: ${tenantId} (${Date.now() - startTime}ms)`
            );
          } catch (error) {
            this.logger.error(
              `Failed to reset tenant context for: ${tenantId}`,
              (error as Error).stack
            );
          }
        })
      );
    } catch (error) {
      // Reset context on error to prevent context leakage
      try {
        await this.databaseService.resetTenantContext();
      } catch (resetError) {
        this.logger.error('Failed to reset tenant context on error', (resetError as Error).stack);
      }

      this.logger.error(
        `Tenant interceptor error: ${(error as Error).message}`,
        (error as Error).stack
      );
      throw error;
    }
  }
  /**
   * Extract tenant ID from JWT token
   *
   * @param request - Express request object
   * @returns Tenant and user information from JWT
   * @throws UnauthorizedException if token is invalid or tenantId missing
   */
  private async extractTenantFromJWT(
    request: Request
  ): Promise<{ tenantId: string; userId: string; user: JwtPayload }> {
    // Extract Authorization header
    const authHeader = request.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      throw new UnauthorizedException('Missing or invalid Authorization header');
    }

    // Extract token
    const token = authHeader.substring(7); // Remove 'Bearer ' prefix
    if (!token) {
      throw new UnauthorizedException('JWT token is required');
    }

    try {
      // Verify JWT signature (CRITICAL: prevents forged tokens)
      const secret = process.env.JWT_SECRET;
      if (!secret) {
        this.logger.error('JWT_SECRET environment variable is not set - cannot verify tokens!');
        throw new UnauthorizedException('Server authentication configuration error');
      }

      const decoded = jwt.verify(token, secret) as JwtPayload;

      if (!decoded) {
        throw new UnauthorizedException('Invalid JWT token format');
      }

      // Validate required fields
      if (!decoded.sub) {
        throw new UnauthorizedException('JWT token missing user ID (sub)');
      }

      if (!decoded.tenantId) {
        throw new UnauthorizedException('JWT token missing tenantId - tenant isolation required');
      }

      // Validate tenantId format (UUID)
      const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
      if (!uuidRegex.test(decoded.tenantId)) {
        throw new UnauthorizedException(`Invalid tenantId format in JWT: ${decoded.tenantId}`);
      }

      // Check token expiration (if present)
      if (decoded.exp && decoded.exp < Math.floor(Date.now() / 1000)) {
        throw new UnauthorizedException('JWT token has expired');
      }

      return {
        tenantId: decoded.tenantId,
        userId: decoded.sub,
        user: decoded,
      };
    } catch (error) {
      if (error instanceof UnauthorizedException) {
        throw error;
      }

      // Handle JWT decode errors
      this.logger.error(`JWT decode error: ${(error as Error).message}`);
      throw new UnauthorizedException('Invalid JWT token');
    }
  }
}
