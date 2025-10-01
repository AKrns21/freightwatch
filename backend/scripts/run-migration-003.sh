#!/bin/bash
# Run migration 003 manually
# This script executes the project workflow refactoring migration

set -e

# Load environment variables
if [ -f .env ]; then
  export $(cat .env | grep -v '^#' | xargs)
fi

# Database connection
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_USERNAME=${DB_USERNAME:-postgres}
DB_DATABASE=${DB_DATABASE:-freightwatch}

echo "================================================"
echo "Running Migration 003: Project Workflow Refactor"
echo "================================================"
echo ""
echo "Database: $DB_DATABASE@$DB_HOST:$DB_PORT"
echo ""

# Check if psql is available
if ! command -v psql &> /dev/null; then
    echo "Error: psql command not found. Please install PostgreSQL client."
    echo ""
    echo "On macOS: brew install postgresql"
    echo "On Ubuntu: sudo apt-get install postgresql-client"
    exit 1
fi

# Backup database first
echo "Creating backup..."
BACKUP_FILE="backup_before_migration_003_$(date +%Y%m%d_%H%M%S).sql"
pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE > $BACKUP_FILE
echo "Backup created: $BACKUP_FILE"
echo ""

# Execute migration
echo "Executing migration 003..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -f src/database/migrations/003_refactor_to_project_workflow.sql

echo ""
echo "================================================"
echo "Migration 003 completed successfully!"
echo "================================================"
echo ""
echo "Verify new tables exist:"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt project"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt consultant_note"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt parsing_template"
echo ""
echo "Verify old tables are gone:"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt service_alias" || echo "✓ service_alias dropped"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt service_catalog" || echo "✓ service_catalog dropped"
psql -h $DB_HOST -p $DB_PORT -U $DB_USERNAME -d $DB_DATABASE -c "\dt tariff_rule" || echo "✓ tariff_rule dropped"
echo ""
echo "Next steps:"
echo "1. Delete old entity files: service-catalog.entity.ts, service-alias.entity.ts, tariff-rule.entity.ts"
echo "2. Clean up imports in ParsingModule"
echo "3. Run tests to verify everything works"
