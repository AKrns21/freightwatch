import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { BullModule } from '@nestjs/bull';
import { APP_INTERCEPTOR } from '@nestjs/core';
import { DatabaseModule } from './database/database.module';
import { TenantInterceptor } from './modules/auth/tenant.interceptor';

@Module({
  imports: [
    // Global configuration module
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),
    
    // Database module with TypeORM and RLS support
    DatabaseModule,
    
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
  ],
})
export class AppModule {}