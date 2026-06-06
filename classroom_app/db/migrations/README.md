# LanShare Database Migrations

This directory is the versioned migration home for the PostgreSQL transition.

Runtime production migrations must be explicit commands with reports. Application
startup may verify the required version, but it must not run large production
schema changes implicitly.

Subdirectories:

- `common/`: migration metadata and shared notes.
- `sqlite/`: SQLite compatibility records and source-baseline notes.
- `postgres/`: PostgreSQL forward-only migration scripts.
