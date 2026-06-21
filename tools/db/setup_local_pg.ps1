<#
.SYNOPSIS
Provision the LOCAL development PostgreSQL database for LanShare (dev == prod engine).

.DESCRIPTION
Idempotently (re)creates the local `lanshare` role + database, migrates the
current SQLite schema+data into PostgreSQL via tools/db_postgres_export.py, then
runs init_database() to add runtime-managed tables and validate.

This NEVER touches production — it only talks to the local PostgreSQL service.
The application then runs on PostgreSQL via .env (DB_ENGINE=postgres).

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\db\setup_local_pg.ps1
#>
[CmdletBinding()]
param(
    [string]$SuperUser = "postgres",
    [string]$SuperPassword = $env:PGSUPERPASSWORD,   # postgres 超管口令（安装时设定）
    [string]$AppUser = "lanshare",
    [string]$AppPassword = "lanshare_dev_pwd",
    [string]$AppDb = "lanshare",
    [string]$PgHost = "127.0.0.1",
    [int]$PgPort = 5432,
    [string]$SourceDb = "data/classroom.db",
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $RepoRoot
try {
    if (-not (Test-Path $PsqlPath)) { throw "psql not found at $PsqlPath" }
    if ([string]::IsNullOrWhiteSpace($SuperPassword)) {
        throw "Set the postgres superuser password via -SuperPassword or `$env:PGSUPERPASSWORD."
    }
    $python = Join-Path $RepoRoot "venv\Scripts\python.exe"

    # 1. role + database
    $env:PGPASSWORD = $SuperPassword
    & $PsqlPath -h $PgHost -p $PgPort -U $SuperUser -v ON_ERROR_STOP=on -c @"
DO `$`$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$AppUser') THEN CREATE ROLE $AppUser LOGIN PASSWORD '$AppPassword'; END IF; END `$`$;
"@
    $exists = (& $PsqlPath -h $PgHost -p $PgPort -U $SuperUser -tAc "SELECT 1 FROM pg_database WHERE datname='$AppDb'")
    if ($exists -ne "1") {
        & $PsqlPath -h $PgHost -p $PgPort -U $SuperUser -c "CREATE DATABASE $AppDb OWNER $AppUser;"
    }

    # 2. bring a COPY of the dev sqlite up to the current schema, then export -> PG package
    $devCopy = ".codex-temp\dev_source.db"
    New-Item -ItemType Directory -Force -Path ".codex-temp" | Out-Null
    Copy-Item -Force $SourceDb $devCopy
    $env:DB_ENGINE = "sqlite"; $env:MAIN_DB_PATH = $devCopy
    & $python -c "from classroom_app.db.schema import init_database; init_database()"
    Remove-Item Env:DB_ENGINE; Remove-Item Env:MAIN_DB_PATH
    & $python "tools/db_postgres_export.py" --source-db $devCopy --runtime-root ".codex-temp/db-pg-export"

    # 3. apply package (03 statement-by-statement so a single constraint conflict
    #    doesn't roll back the whole batch)
    $env:PGPASSWORD = $AppPassword
    $pkg = ".codex-temp/db-pg-export/package"
    & $PsqlPath -h $PgHost -p $PgPort -U $AppUser -d $AppDb -v ON_ERROR_STOP=on -q -f "$pkg/01-schema.sql"
    & $PsqlPath -h $PgHost -p $PgPort -U $AppUser -d $AppDb -v ON_ERROR_STOP=on -q -f "$pkg/02-data.sql"
    (Get-Content "$pkg/03-constraints-indexes.sql") | Where-Object { $_ -notmatch '^(BEGIN|COMMIT);' } | Set-Content ".codex-temp/03b.sql"
    & $PsqlPath -h $PgHost -p $PgPort -U $AppUser -d $AppDb -f ".codex-temp/03b.sql"

    # 4. runtime-managed tables + validation (must print 130/130 required tables)
    & $python -c "from classroom_app.db.schema import init_database; r=init_database(); print('required tables:', r.get('present_required_table_count'), '/', r.get('required_table_count'))"

    Write-Output "Local PostgreSQL provisioned: $AppDb on ${PgHost}:${PgPort}"
} finally {
    Pop-Location
}
