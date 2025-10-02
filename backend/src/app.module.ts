import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { BullModule } from '@nestjs/bull';
import { ThrottlerModule, ThrottlerGuard } from '@nestjs/throttler';
import { APP_INTERCEPTOR, APP_GUARD } from '@nestjs/core';
import { DatabaseModule } from './database/database.module';
import { UploadModule } from './modules/upload/upload.module';
import { ProjectModule } from './modules/project/project.module';
import { ReportModule } from './modules/report/report.module';
import { InvoiceModule } from './modules/invoice/invoice.module';
import { TenantInterceptor } from './modules/auth/tenant.interceptor';

@Module({
  imports: [
    // Global configuration module
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),

    // Global rate limiting module
    // Protects against brute-force attacks and API abuse
    ThrottlerModule.forRoot([{
      ttl: 60000, // Time window in milliseconds (60 seconds)
      limit: 100, // Max requests per TTL window per IP
    }]),

    // Database module with TypeORM and RLS support
    DatabaseModule,

    // Upload processing module
    UploadModule,

    // Project management module (NEW)
    ProjectModule,

    // Report generation module (NEW)
    ReportModule,

    // Invoice processing module (NEW)
    InvoiceModule,

    // Redis/Bull queue module
    BullModule.forRoot({
      redis: {
        host: process.env.REDIS_HOST || 'localhost',
        port: parseInt(process.env.REDIS_PORT || '6379'),
        password: process.env.REDIS_PASSWORD,
      },
    }),
  ],
  controllers: [],
  providers: [
    // CRITICAL: Global tenant isolation interceptor
    // This interceptor MUST be registered globally to ensure ALL requests
    // are tenant-scoped for Row Level Security (RLS) to work properly
    {
      provide: APP_INTERCEPTOR,
      useClass: TenantInterceptor,
    },
    // Global rate limiting guard
    // Protects all endpoints from brute-force and DDoS attacks
    {
      provide: APP_GUARD,
      useClass: ThrottlerGuard,
    },
  ],
})
export class AppModule {}