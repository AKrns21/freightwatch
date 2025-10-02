import { NestFactory } from '@nestjs/core';
import { Logger, ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const logger = new Logger('Bootstrap');

  // CRITICAL: Enforce JWT_SECRET in non-development environments
  const isDevelopment = process.env.NODE_ENV === 'development';
  if (!isDevelopment && !process.env.JWT_SECRET) {
    logger.error('FATAL: JWT_SECRET environment variable is required in production mode');
    logger.error('Set JWT_SECRET in your environment or .env file before starting the server');
    process.exit(1);
  }

  // Warn if using default/weak JWT secret in production
  if (!isDevelopment && process.env.JWT_SECRET && process.env.JWT_SECRET.length < 32) {
    logger.warn('WARNING: JWT_SECRET is too short (<32 characters) - use a strong secret in production!');
  }

  // Create NestJS application
  const app = await NestFactory.create(AppModule);

  // CRITICAL: Enable global validation for DTOs
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true, // Strip properties not in DTO
      forbidNonWhitelisted: true, // Throw error on unknown properties
      transform: true, // Auto-transform payloads to DTO types
      transformOptions: {
        enableImplicitConversion: true,
      },
    }),
  );

  // Global API prefix
  app.setGlobalPrefix('api');

  // Enable CORS (TODO: restrict in production)
  app.enableCors();

  // Start server
  const port = process.env.PORT || 3000;
  await app.listen(port);

  logger.log(`FreightWatch API is running on port ${port}`);
  logger.log(`Environment: ${process.env.NODE_ENV || 'not set'}`);
  logger.log(`Database: ${process.env.DB_HOST || 'localhost'}:${process.env.DB_PORT || 5432}`);

  if (isDevelopment) {
    logger.warn('DEVELOPMENT MODE: Authentication bypass is enabled');
  }
}

bootstrap();