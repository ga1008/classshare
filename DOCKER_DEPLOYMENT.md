# Docker Deployment

## Prerequisites

- Docker Engine with Compose V2
- Python 3.10+ on the deployment host (standard library only)
- Dependency images prepared with `python3 tools/deploy/dependency_images.py ensure`
- A populated `docker.env` file

If `docker.env` does not exist yet, copy `docker.env.example` and fill in the real secrets and AI provider keys.

## Start

Build the shared application image once, then start the stack. On the Linux
deployment host, the build wrapper prepares dependency images only when needed:

```bash
bash deployment/docker/build_app.sh
docker compose up -d --no-build --pull never
```

Pull the sidecar images specified by Compose explicitly when provisioning a new
host. Normal releases reuse the installed images and do not refresh mutable tags.

## Reusable dependency images

- `DockerfileBase` produces `lanshare-runtime-deps:<fingerprint>` with locked Python
  packages, LibreOffice, PDF/document tools, fonts and system libraries.
- `deployment/docker/Dockerfile.frontend-deps` produces
  `lanshare-frontend-deps:<fingerprint>` with Node and all locked npm dependencies.
  Node and `node_modules` stay in the build stage, outside the application runtime.
- Upstream Python/Node images are pinned by digest. The fingerprint includes the
  relevant recipe, lockfiles and validation script, with Windows/Linux line endings
  normalized. Business code and templates do not change dependency versions.
- `ensure` reuses matching tagged images even if Docker's build cache was pruned.
  It builds missing versions serially from a minimal allowlisted context and checks
  their labels. `:current` aliases update only after both versions are ready.
- Application builds use `network: none`; they only verify dependency inputs,
  compile frontend assets and copy code. Stale dependency images fail the build
  instead of silently shipping a mismatched environment.

Inspect the expected versions without building or contacting Docker:

```bash
python3 tools/deploy/dependency_images.py plan
```

The existing local `deployment/deploy_remote.ps1` now calls the tracked
`deployment/docker/build_app.sh` before recreating services. This wrapper serializes
builds with `flock`, selects fingerprinted images and reports
`DEPENDENCY_REUSED` / `APPLICATION_BUILD_SECONDS`. If using another deployment
entrypoint, call the same wrapper rather than running dependency installation in
the application Dockerfile.

Dependency changes: update the appropriate lockfile or base recipe, review and
test it, then deploy as usual. Only the affected base is rebuilt. For OS/security
updates, update the pinned upstream digest or add a dated recipe change so the
new base receives its own fingerprint; dependency updates remain an explicit
maintenance action. npm vulnerability review is separate from production builds;
the base installation uses `npm ci --no-audit --no-fund` to avoid waiting on the
audit service during release.

Keep the current and previous dependency tags for rollback. The deployment's
`docker image prune -f` does not remove tagged bases; do not replace it with
`docker image prune -a`. Remove older dependency tags explicitly after verifying
they are not needed by retained application versions. Runtime data and secrets
never belong in dependency images.

## Disk space and backup retention

The canonical `deployment/deploy_remote.ps1` keeps the latest **two code archives**
and **two database backups** in `/tmp/lanshare-deploy-backups`. Its `-KeepBackups`
parameter can override this when explicitly needed. PostgreSQL backups are full
logical dumps; validate compressed backup integrity before removing older copies.
Historical migration dumps of the same database also count toward retention.

After a successful deployment, unused Docker build cache is pruned with a 2 GB
retention budget, under the same build lock. Tagged runtime/frontend dependency
images remain available even when their build cache is removed. Prune build cache
through Docker; never remove files inside Docker/containerd storage directly.
Do not use volume pruning or delete live database directories to reclaim space.

## Services

The stack contains:

- `nginx`: only public entry point
- `app`: main FastAPI service running `main.py`
- `ai`: AI assistant service running `ai_assistant.py`

By default only port `80` is published. To change it:

```powershell
$env:LANSHARE_HTTP_PORT = "8080"
docker compose up -d --no-build --pull never
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
3. Prepare/reuse dependency images and rebuild the application image once.
4. Recreate the containers.

```bash
bash deployment/docker/build_app.sh
docker compose up -d --no-build --pull never
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

## Optional PostgreSQL Overlay

SQLite remains the default production backend until every migration gate passes.
The PostgreSQL service is provided as an explicit overlay:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml config
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d postgres
```

The overlay keeps PostgreSQL on the internal Compose network and stores data
under `data/postgres/`. Do not publish PostgreSQL ports on the public server.

For an existing PostgreSQL deployment, pass the same Compose files to the build wrapper:

```bash
bash deployment/docker/build_app.sh docker compose -f docker-compose.yml -f docker-compose.postgres.yml
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --no-build --pull never
```

Before enabling `DB_ENGINE=postgres` in the real remote `docker.env`, complete
all of the following:

1. Back up `/lanshare/data` and the current SQLite database.
2. Load or confirm the PostgreSQL image locally on the remote host; do not rely
   on internet access during production deployment.
3. Run the SQLite to PostgreSQL migration against a backup copy and save the
   validation report.
4. Run `python tools/deploy/postgres_preflight.py` and resolve every blocker.
5. Set `DATABASE_URL`, `POSTGRES_PASSWORD`, and `POSTGRES_BACKEND_READY=true`
   only after the migration report and rollback plan are accepted.
6. Restart app and worker containers together so they read the same database
   configuration.
7. Run `tools/deploy/postflight.ps1 -CheckPostgres` and keep the report.

Never commit the real PostgreSQL password or a production `DATABASE_URL`.

## Notes

- `requirements.lock.txt` is now the single locked dependency source for local and Docker installs.
- `AI_WORKER_CONCURRENCY` is still supported, but new Docker deployments should use `GLOBAL_AI_CONCURRENCY`.
- The image entrypoint is unified, so `app` and `ai` use the same image and only differ by the startup argument.
