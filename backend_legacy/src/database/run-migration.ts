#!/usr/bin/env ts-node
/* eslint-disable no-console */
/**
 * Manual migration runner for SQL files
 * Usage: ts-node src/database/run-migration.ts <migration-file>
 */

import { DataSource } from 'typeorm';
import * as fs from 'fs';
import * as path from 'path';

async function runMigration(migrationFile: string): Promise<void> {
  // Database configuration
  const dataSource = new DataSource({
    type: 'postgres',
    host: process.env.DB_HOST || 'localhost',
    port: parseInt(process.env.DB_PORT || '5432'),
    username: process.env.DB_USERNAME || 'postgres',
    password: process.env.DB_PASSWORD || 'postgres',
    database: process.env.DB_DATABASE || 'freightwatch_test',
  });

  try {
    console.log('Connecting to database...');
    await dataSource.initialize();
    console.log('Connected successfully');

    // Read migration file
    const migrationPath = path.resolve(__dirname, 'migrations', migrationFile);
    console.log(`Reading migration: ${migrationPath}`);

    const sqlContent = fs.readFileSync(migrationPath, 'utf-8');

    // Remove all single-line comments (-- ...)
    const noComments = sqlContent.replace(/--[^\n]*/g, '');

    // Split SQL into individual statements, respecting DO $$ ... END $$ blocks
    const statements: string[] = [];
    let currentStatement = '';
    let inDollarQuote = false;

    const lines = noComments.split(';');
    for (const line of lines) {
      currentStatement += line;

      // Check if we enter or exit a dollar-quoted block
      const dollarMatches = line.match(/\$\$/g);
      if (dollarMatches) {
        for (const _match of dollarMatches) {
          inDollarQuote = !inDollarQuote;
        }
      }

      // If not in dollar quote and we hit semicolon, that's a statement boundary
      if (!inDollarQuote) {
        const trimmed = currentStatement.trim();
        if (trimmed.length > 0 && !trimmed.match(/^[\s\n]*$/)) {
          statements.push(trimmed);
        }
        currentStatement = '';
      } else {
        // Still in dollar quote, add semicolon back
        currentStatement += ';';
      }
    }

    // Add remaining statement if any
    const trimmed = currentStatement.trim();
    if (trimmed.length > 0 && !trimmed.match(/^[\s\n]*$/)) {
      statements.push(trimmed);
    }

    console.log(`Executing ${statements.length} SQL statements...`);

    let executed = 0;
    for (const statement of statements) {
      try {
        await dataSource.query(statement);
        executed++;
        if (executed % 10 === 0) {
          console.log(`  Executed ${executed}/${statements.length} statements`);
        }
      } catch (error: unknown) {
        console.error(`\nFailed at statement ${executed + 1}:`);
        console.error(statement.substring(0, 200) + '...');
        throw error;
      }
    }

    console.log(`✅ Migration executed successfully (${executed} statements)`);

    // Verify new tables
    console.log('\nVerifying new tables...');
    const tables = await dataSource.query(`
      SELECT table_name,
             (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = t.table_name) as column_count,
             obj_description((quote_ident(table_schema)||'.'||quote_ident(table_name))::regclass, 'pg_class') as comment
      FROM information_schema.tables t
      WHERE table_schema = 'public'
        AND table_name IN ('project', 'consultant_note', 'parsing_template', 'manual_mapping', 'report')
      ORDER BY table_name;
    `);

    console.log('New tables:');
    console.table(tables);

    // Verify RLS policies
    console.log('\nVerifying RLS policies...');
    const policies = await dataSource.query(`
      SELECT schemaname, tablename, policyname,
             CASE
               WHEN cmd = 'r' THEN 'SELECT'
               WHEN cmd = 'a' THEN 'INSERT'
               WHEN cmd = 'w' THEN 'UPDATE'
               WHEN cmd = 'd' THEN 'DELETE'
               WHEN cmd = '*' THEN 'ALL'
               ELSE cmd
             END as command,
             qual as using_expression
      FROM pg_policies
      WHERE tablename IN ('project', 'consultant_note', 'parsing_template', 'manual_mapping', 'report')
      ORDER BY tablename, policyname;
    `);

    console.log('RLS policies:');
    console.table(policies);

    // Verify dropped tables
    console.log('\nVerifying dropped tables...');
    const droppedCheck = await dataSource.query(`
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN ('service_catalog', 'service_alias', 'surcharge_catalog', 'tariff_rule');
    `);

    if (droppedCheck.length === 0) {
      console.log('✅ All legacy tables successfully dropped');
    } else {
      console.log('⚠️ Some legacy tables still exist:');
      console.table(droppedCheck);
    }

    // Verify new columns on existing tables
    console.log('\nVerifying new columns on upload table...');
    const uploadColumns = await dataSource.query(`
      SELECT column_name, data_type, is_nullable
      FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'upload'
        AND column_name IN ('project_id', 'parse_method', 'confidence', 'suggested_mappings', 'llm_analysis', 'reviewed_by', 'reviewed_at', 'parsing_issues')
      ORDER BY ordinal_position;
    `);

    console.log('Upload table new columns:');
    console.table(uploadColumns);

    console.log('\nVerifying new columns on shipment table...');
    const shipmentColumns = await dataSource.query(`
      SELECT column_name, data_type, is_nullable
      FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'shipment'
        AND column_name IN ('project_id', 'completeness_score', 'missing_fields', 'data_quality_issues', 'consultant_notes', 'manual_override')
      ORDER BY ordinal_position;
    `);

    console.log('Shipment table new columns:');
    console.table(shipmentColumns);

    console.log('\n✅ Migration completed successfully!');
  } catch (error: unknown) {
    console.error('Migration failed:', error);
    throw error;
  } finally {
    await dataSource.destroy();
  }
}

// Main execution
const migrationFile = process.argv[2] || '003_refactor_to_project_workflow.sql';

runMigration(migrationFile)
  .then(() => {
    console.log('\n✅ Done!');
    process.exit(0);
  })
  .catch((error) => {
    console.error('\n❌ Migration failed:', error.message);
    process.exit(1);
  });
