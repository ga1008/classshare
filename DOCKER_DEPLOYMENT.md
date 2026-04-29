# Docker Deployment

## Prerequisites

- Docker Engine with Compose V2
- The base image `lanshare_base` already exists locally
- A populated `docker.env` file

If `docker.env` does not exist yet, copy `docker.env.example` and fill in the real secrets and AI provider keys.

## Start

Build and start the full stack:

```powershell
docker compose up -d --build
```

The stack contains:

- `nginx`: only public entry point
- `app`: main FastAPI service running `main.py`
- `ai`: AI assistant service running `ai_assistant.py`

By default only port `80` is published. To change it:

```powershell
$env:LANSHARE_HTTP_PORT = "8080"
docker compose up -d --build
```

## Runtime model

- `app` talks to `ai` through `AI_ASSISTANT_URL=http://ai:8001`
- `ai` sends grading callbacks to `http://app:8000/api/internal/grading-complete`
- Both services expose `/api/internal/health`
- `nginx` forwards WebSocket traffic and keeps streaming chat responses unbuffered

## Persistent data

LanShare now treats `data/` as the canonical runtime data root. New deployments should keep
SQLite, uploaded media, submissions, imports, and runtime logs under this one host directory:

- `data/`

Older deployments may still have mutable content in these legacy directories:

- `attendance/`
- `chat_logs/`
- `homework_submissions/`
- `logs/`
- `rosters/`
- `shared_files/`
- `storage/`

The application keeps compatibility with those locations while you migrate, so existing uploads
and submissions continue to resolve during an upgrade.

## Data layout migration

Preview the migration plan:

```powershell
python tools/migrate_data_layout.py --verify
```

Apply the copy into the new `data/` layout:

```powershell
python tools/migrate_data_layout.py --apply --verify
```

The tool copies data non-destructively. Keep the legacy folders until verification passes and
you have a backup. After migration, the app will prefer populated paths such as:

- `data/db/classroom.db`
- `data/media/blobs/sha256/`
- `data/files/submissions/`
- `data/files/legacy_shared/`
- `data/imports/rosters/`
- `data/imports/attendance/`
- `data/logs/chat_logs/`
- `data/tmp/chunked_uploads/`

## Upgrade flow

1. Pull or copy the new project code.
2. Review `docker.env` for any new variables.
3. Rebuild the application image.
4. Recreate the containers.

```powershell
docker compose build --pull
docker compose up -d --remove-orphans
```

If you only changed Python or application code, the persistent directories are reused automatically.

## Operations

Check status:

```powershell
docker compose ps
```

Tail logs:

```powershell
docker compose logs -f nginx app ai
```

Stop the stack:

```powershell
docker compose down
```

Validate the final merged compose file:

```powershell
docker compose config
```

## Notes

- `requirements.lock.txt` is now the single locked dependency source for local and Docker installs.
- `AI_WORKER_CONCURRENCY` is still supported, but new Docker deployments should use `GLOBAL_AI_CONCURRENCY`.
- The image entrypoint is unified, so `app` and `ai` use the same image and only differ by the startup argument.
