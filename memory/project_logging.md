---
name: Logging setup
description: How logging is configured — writes to both stdout and rotating file
type: project
---

Logging was set up on 2026-03-19. Logs write to both stdout and a rotating file.

**Log file location:** backend/logs/freightwatch.log (gitignored)
**Rotation:** 10 MB per file, 7 backups kept (~70 MB total)
**Format:** JSON in production, console in development (controlled by LOG_FORMAT env var)

**Key env vars (backend/.env):**
- LOG_LEVEL=INFO
- LOG_FORMAT=json
- LOG_FILE=logs/freightwatch.log
- LOG_FILE_MAX_MB=10
- LOG_FILE_BACKUP_COUNT=7

**Implementation:** app/utils/logger.py — SafePrintLogger writes to both stdout and RotatingFileHandler. setup_logging() called at app startup.
