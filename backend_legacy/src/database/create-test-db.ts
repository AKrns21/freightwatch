#!/usr/bin/env ts-node
/* eslint-disable no-console */
/**
 * Create test database
 */

import { DataSource } from 'typeorm';

async function createTestDatabase(): Promise<void> {
  // Connect to postgres database first
  const dataSource = new DataSource({
    type: 'postgres',
    host: process.env.DB_HOST || 'localhost',
    port: parseInt(process.env.DB_PORT || '5432'),
    username: process.env.DB_USERNAME || 'postgres',
    password: process.env.DB_PASSWORD || 'postgres',
    database: 'postgres', // Connect to default postgres database
  });

  try {
    console.log('Connecting to postgres database...');
    await dataSource.initialize();
    console.log('Connected');

    // Check if freightwatch_test exists
    const result = await dataSource.query(
      `SELECT 1 FROM pg_database WHERE datname = 'freightwatch_test'`
    );

    if (result.length > 0) {
      console.log('Database freightwatch_test already exists');

      // Drop it first
      console.log('Dropping existing database...');
      await dataSource.query(`DROP DATABASE freightwatch_test`);
      console.log('Dropped');
    }

    // Create database
    console.log('Creating freightwatch_test database...');
    await dataSource.query(`CREATE DATABASE freightwatch_test`);
    console.log('✅ Database created successfully');
  } catch (error: unknown) {
    console.error('Failed to create database:', error);
    throw error;
  } finally {
    await dataSource.destroy();
  }
}

createTestDatabase()
  .then(() => {
    console.log('\n✅ Done!');
    process.exit(0);
  })
  .catch((error) => {
    console.error('\n❌ Failed:', error.message);
    process.exit(1);
  });
