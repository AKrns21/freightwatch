import { Injectable, Logger } from '@nestjs/common';
import { InjectDataSource } from '@nestjs/typeorm';
import { DataSource, QueryRunner, Repository, EntityTarget, ObjectLiteral, DataSourceOptions } from 'typeorm';

/**
 * Database service providing tenant-aware database operations
 * 
 * This service is CRITICAL for Row Level Security (RLS) to work properly.
 * It manages the PostgreSQL session variable 'app.current_tenant' that
 * is used by RLS policies to filter data by tenant.
 */
@Injectable()
export class DatabaseService {
  private readonly logger = new Logger(DatabaseService.name);

  constructor(
    @InjectDataSource()
    private readonly dataSource: DataSource,
  ) {}

  /**
   * Set the tenant context for Row Level Security
   * 
   * CRITICAL: This must be called before any tenant-scoped queries
   * to ensure RLS policies work correctly.
   * 
   * @param tenantId - The UUID of the tenant to set context for
   * @throws Error if tenantId is invalid or setting context fails
   */
  async setTenantContext(tenantId: string): Promise<void> {
    if (!tenantId) {
      throw new Error('Tenant ID cannot be empty');
    }

    // Validate UUID format (relaxed for development)
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRegex.test(tenantId)) {
      throw new Error(`Invalid tenant ID format: ${tenantId}`);
    }

    try {
      // PostgreSQL SET command doesn't support parameter placeholders
      await this.dataSource.query(
        `SET LOCAL app.current_tenant = '${tenantId}'`
      );

      this.logger.debug(`Tenant context set to: ${tenantId}`);
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Failed to set tenant context: ${err.message}`, err.stack);
      throw new Error(`Failed to set tenant context: ${err.message}`);
    }
  }

  /**
   * Reset the tenant context (clear RLS context)
   * 
   * This should be called when switching tenants or at the end
   * of request processing to clean up the session state.
   */
  async resetTenantContext(): Promise<void> {
    try {
      await this.dataSource.query('RESET app.current_tenant');
      this.logger.debug('Tenant context reset');
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Failed to reset tenant context: ${err.message}`, err.stack);
      throw new Error(`Failed to reset tenant context: ${err.message}`);
    }
  }

  /**
   * Get the current tenant context
   * 
   * @returns The current tenant ID or null if not set
   */
  async getCurrentTenantContext(): Promise<string | null> {
    try {
      const result = await this.dataSource.query(
        'SELECT current_setting($1, true) as tenant_id',
        ['app.current_tenant']
      );
      
      const tenantId = result[0]?.tenant_id;
      return tenantId && tenantId !== '' ? tenantId : null;
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Failed to get current tenant context: ${err.message}`);
      return null;
    }
  }

  /**
   * Execute a query with automatic tenant context management
   * 
   * @param tenantId - Tenant ID to set context for
   * @param queryFn - Function that executes the database operations
   * @returns Result from the query function
   */
  async withTenantContext<T>(
    tenantId: string,
    queryFn: (queryRunner: QueryRunner) => Promise<T>
  ): Promise<T> {
    const queryRunner = this.dataSource.createQueryRunner();
    
    try {
      await queryRunner.startTransaction();
      
      // Set tenant context within the transaction
      await queryRunner.query('SET LOCAL app.current_tenant = $1', [tenantId]);
      
      // Execute the query function
      const result = await queryFn(queryRunner);
      
      await queryRunner.commitTransaction();
      return result;
    } catch (error) {
      await queryRunner.rollbackTransaction();
      const err = error as Error;
      this.logger.error(`Transaction with tenant context failed: ${err.message}`, err.stack);
      throw error;
    } finally {
      await queryRunner.release();
    }
  }

  /**
   * Get a repository for the given entity
   * 
   * Note: The repository will use the current tenant context
   * set by setTenantContext()
   */
  getRepository<Entity extends ObjectLiteral>(target: EntityTarget<Entity>): Repository<Entity> {
    return this.dataSource.getRepository(target);
  }

  /**
   * Execute a raw SQL query
   * 
   * @param query - SQL query string
   * @param parameters - Query parameters
   * @returns Query result
   */
  async query(query: string, parameters?: any[]): Promise<any> {
    try {
      return await this.dataSource.query(query, parameters);
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Query execution failed: ${err.message}`, err.stack);
      throw error;
    }
  }

  /**
   * Execute a query and return a single result
   * 
   * @param query - SQL query string
   * @param parameters - Query parameters
   * @returns Single row or null
   */
  async queryOne(query: string, parameters?: any[]): Promise<any> {
    const results = await this.query(query, parameters);
    return results.length > 0 ? results[0] : null;
  }

  /**
   * Create a query runner for transaction management
   * 
   * @returns QueryRunner instance
   */
  createQueryRunner(): QueryRunner {
    return this.dataSource.createQueryRunner();
  }

  /**
   * Get the underlying DataSource
   * 
   * @returns DataSource instance
   */
  getDataSource(): DataSource {
    return this.dataSource;
  }

  /**
   * Check if database connection is healthy
   * 
   * @returns Promise<boolean> indicating health status
   */
  async isHealthy(): Promise<boolean> {
    try {
      await this.dataSource.query('SELECT 1');
      return true;
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Database health check failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Get database connection info for monitoring
   * 
   * @returns Connection info object
   */
  async getConnectionInfo(): Promise<{
    isConnected: boolean;
    database: string;
    host: string;
    port: number;
    currentTenant?: string;
  }> {
    try {
      const currentTenant = await this.getCurrentTenantContext();
      
      const pgOptions = this.dataSource.options as any;
      return {
        isConnected: this.dataSource.isInitialized,
        database: pgOptions.database as string,
        host: pgOptions.host as string,
        port: pgOptions.port as number,
        currentTenant: currentTenant || undefined,
      };
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Failed to get connection info: ${err.message}`);
      return {
        isConnected: false,
        database: 'unknown',
        host: 'unknown',
        port: 0,
      };
    }
  }

  /**
   * Run database migrations programmatically
   * 
   * @returns Promise<void>
   */
  async runMigrations(): Promise<void> {
    try {
      this.logger.log('Running database migrations...');
      await this.dataSource.runMigrations();
      this.logger.log('Database migrations completed successfully');
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Migration failed: ${err.message}`, err.stack);
      throw error;
    }
  }

  /**
   * Revert the last migration
   * 
   * @returns Promise<void>
   */
  async revertLastMigration(): Promise<void> {
    try {
      this.logger.log('Reverting last migration...');
      await this.dataSource.undoLastMigration();
      this.logger.log('Last migration reverted successfully');
    } catch (error) {
      const err = error as Error;
      this.logger.error(`Migration revert failed: ${err.message}`, err.stack);
      throw error;
    }
  }
}