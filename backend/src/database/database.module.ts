import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { DataSource } from 'typeorm';
import { DatabaseService } from './database.service';

@Module({
  imports: [
    ConfigModule,
    TypeOrmModule.forRootAsync({
      imports: [ConfigModule],
      useFactory: (configService: ConfigService) => ({
        type: 'postgres',
        host: configService.get<string>('DB_HOST', 'localhost'),
        port: configService.get<number>('DB_PORT', 5432),
        username: configService.get<string>('DB_USERNAME', 'postgres'),
        password: configService.get<string>('DB_PASSWORD', 'postgres'),
        database: configService.get<string>('DB_DATABASE', 'freightwatch'),
        
        // Entity auto-loading
        autoLoadEntities: true,
        entities: [__dirname + '/../**/*.entity{.ts,.js}'],
        
        // Connection pool configuration
        extra: {
          connectionLimit: configService.get<number>('DB_CONNECTION_LIMIT', 10),
          min: configService.get<number>('DB_MIN_CONNECTIONS', 2),
          max: configService.get<number>('DB_MAX_CONNECTIONS', 10),
          acquireTimeoutMillis: 60000,
          createTimeoutMillis: 30000,
          destroyTimeoutMillis: 5000,
          idleTimeoutMillis: 30000,
          reapIntervalMillis: 1000,
          createRetryIntervalMillis: 100,
        },
        
        // Connection pool options for node-postgres
        poolSize: configService.get<number>('DB_MAX_CONNECTIONS', 10),
        
        // Schema synchronization - DISABLED due to entity conflicts
        synchronize: false,
        
        // Migrations
        migrations: [__dirname + '/migrations/*{.ts,.js}'],
        migrationsRun: configService.get<string>('NODE_ENV') === 'production',
        
        // Logging configuration
        logging: configService.get<string>('NODE_ENV') === 'development' 
          ? ['query', 'error', 'warn', 'info', 'log'] 
          : ['error', 'warn'],
        logger: 'advanced-console',
        
        // Connection options
        retryAttempts: 5,
        retryDelay: 3000,
        
        // SSL configuration for production
        ssl: configService.get<string>('NODE_ENV') === 'production' 
          ? { rejectUnauthorized: false } 
          : false,
        
        // Application name for PostgreSQL connection tracking
        applicationName: 'FreightWatch-API',
        
        // Connection timeout
        connectTimeoutMS: 10000,
        
        // Statement timeout
        statement_timeout: 30000,
      }),
      inject: [ConfigService],
    }),
  ],
  providers: [DatabaseService],
  exports: [DatabaseService, TypeOrmModule],
})
export class DatabaseModule {
  constructor(private dataSource: DataSource) {}

  /**
   * Get the underlying DataSource for advanced operations
   */
  getDataSource(): DataSource {
    return this.dataSource;
  }

  /**
   * Health check for database connection
   */
  async isHealthy(): Promise<boolean> {
    try {
      await this.dataSource.query('SELECT 1');
      return true;
    } catch (error) {
      return false;
    }
  }
}