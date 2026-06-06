# PostgreSQL Migration Scripts

PostgreSQL migrations are forward-only. Each script must document its purpose,
affected tables, lock expectations, data backfill requirements, dry-run command,
and rollback notes.
