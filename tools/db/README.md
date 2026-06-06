# Local Database Migration Lab

This folder contains Win11 PowerShell entrypoints for the SQLite to PostgreSQL
migration work.

All scripts keep scratch data under `.codex-temp/pg-migration-lab` by default.
They must not modify `data/classroom.db`, remote `/lanshare/data`, or any
production Docker volume.

## Commands

```powershell
powershell -ExecutionPolicy Bypass -File tools\db\prepare_pg_lab.ps1
powershell -ExecutionPolicy Bypass -File tools\db\run_pg_migration_dry_run.ps1
powershell -ExecutionPolicy Bypass -File tools\db\run_dual_backend_tests.ps1
powershell -ExecutionPolicy Bypass -File tools\db\cleanup_pg_lab.ps1 -WhatIf
```

Use `prepare_pg_lab.ps1 -StartDockerPostgres` only when the configured
PostgreSQL image already exists locally. The script intentionally does not pull
images from the network, because the final Linux Docker Compose target may be
offline.

`run_pg_migration_dry_run.ps1` currently performs copied-SQLite readiness,
schema, concurrency, and file-integrity checks. It explicitly reports that real
PostgreSQL data loading has not run until the later P01 migration gates are
implemented and accepted.
