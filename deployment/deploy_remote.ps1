<#
.SYNOPSIS
Deploy local Lanshare code to the remote Docker Compose server.

.DESCRIPTION
This script packages local code/configuration files, uploads them to the remote
/lanshare directory, backs up remote code, optionally backs up the SQLite
database to /tmp, rebuilds Docker Compose, and starts services in the
background.

It intentionally never uploads or replaces production runtime data such as
lanshare/data, logs, submissions, storage, .env, or docker.env.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$RemoteHost = "106.53.153.171",
    [string]$RemoteUser = "root",
    [string]$RemotePath = "/lanshare",
    [string]$SshKey = "$env:USERPROFILE\.ssh\lanshare_deploy_rsa",
    [int]$KeepBackups = 3,
    [string]$BackupDir = "/tmp/lanshare-deploy-backups",
    [switch]$DryRun,
    [switch]$SkipDatabaseBackup,
    [switch]$SkipHealthCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Normalize-ArchivePath {
    param([string]$Path)
    return ($Path -replace "\\", "/").Trim()
}

function Test-ProtectedDeployPath {
    param([string]$Path)

    $p = Normalize-ArchivePath $Path
    $p = $p.TrimStart("./")
    if ([string]::IsNullOrWhiteSpace($p)) {
        return $true
    }
    if ($p.StartsWith("/") -or $p -match "(^|/)\.\.(/|$)") {
        return $true
    }
    if ($p -cmatch "[^\x00-\x7F]") {
        return $true
    }

    $protectedPrefixes = @(
        ".git/",
        ".idea/",
        ".sync_state/",
        ".deploy_tmp/",
        ".codex_tmp/",
        ".tmp_loadtest_run",
        ".tmp_loadtest_run2",
        "__pycache__/",
        ".pytest_cache/",
        "attendance/",
        "chat_logs/",
        "data/",
        "dist/",
        "build/",
        "homework_submissions/",
        "logs/",
        "node_modules/",
        "rosters/",
        "shared_files/",
        "storage/",
        "test-results/",
        "venv/",
        ".venv/",
        "env/",
        "ENV/",
        "tools/guardianangel.net.cn_nginx/"
    )

    foreach ($prefix in $protectedPrefixes) {
        if ($p.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    $fileName = Split-Path -Leaf $p
    if ($fileName -in @(".env", "docker.env", "_.env", "settings.zip")) {
        return $true
    }
    if ($fileName -match "\.env$") {
        return $true
    }
    if ($fileName -match "\.log$") {
        return $true
    }
    if ($fileName -match "\.pyc$") {
        return $true
    }
    if ($fileName -match "\.zip$") {
        return $true
    }

    return $false
}

function Get-DeployFileList {
    param([string]$RepoRoot)

    Push-Location $RepoRoot
    try {
        $insideGit = $false
        try {
            $insideGit = ((& git rev-parse --is-inside-work-tree 2>$null) -eq "true")
        } catch {
            $insideGit = $false
        }

        if ($insideGit) {
            $rawFiles = & git -c core.quotePath=false ls-files -co --exclude-standard
            if ($LASTEXITCODE -ne 0) {
                throw "git ls-files failed."
            }
        } else {
            Write-Warning "Not inside a Git worktree; falling back to filesystem enumeration."
            $rawFiles = Get-ChildItem -Path $RepoRoot -Recurse -File -Force |
                ForEach-Object {
                    $_.FullName.Substring($RepoRoot.Length).TrimStart("\", "/")
                }
        }

        $files = @()
        foreach ($item in $rawFiles) {
            $path = Normalize-ArchivePath $item
            if (-not [string]::IsNullOrWhiteSpace($path) -and -not (Test-ProtectedDeployPath $path)) {
                $files += $path
            }
        }

        $files = $files | Sort-Object -Unique
        if (-not $files -or $files.Count -eq 0) {
            throw "No deployable files were found."
        }
        return $files
    } finally {
        Pop-Location
    }
}

function Write-Utf8NoBomFile {
    param(
        [string]$Path,
        [string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

if ($KeepBackups -lt 1 -or $KeepBackups -gt 20) {
    throw "KeepBackups must be between 1 and 20."
}
if ($RemotePath -eq "/" -or [string]::IsNullOrWhiteSpace($RemotePath)) {
    throw "RemotePath '$RemotePath' is unsafe."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployId = Get-Date -Format "yyyyMMdd-HHmmss"
$localWorkDir = Join-Path ([System.IO.Path]::GetTempPath()) "lanshare-deploy-$deployId"
$archiveName = "lanshare-code-$deployId.tgz"
$archivePath = Join-Path $localWorkDir $archiveName
$manifestPath = Join-Path $localWorkDir "deploy-files.txt"
$remoteScriptPath = Join-Path $localWorkDir "remote-deploy.sh"
$remoteArchivePath = "/tmp/$archiveName"
$remoteScriptUploadPath = "/tmp/lanshare-remote-deploy-$deployId.sh"
$remote = "$RemoteUser@$RemoteHost"

New-Item -ItemType Directory -Force -Path $localWorkDir | Out-Null

try {
    Write-Step "Checking local tools"
    Require-Command git
    Require-Command tar
    Require-Command ssh
    Require-Command scp
    Require-Command python
    if (-not (Test-Path -LiteralPath $SshKey)) {
        throw "SSH key not found: $SshKey"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "docker-compose.yml"))) {
        throw "docker-compose.yml was not found under $repoRoot."
    }

    Write-Step "Checking PostgreSQL deployment gates"
    Push-Location $repoRoot
    try {
        & python tools\deploy\postgres_preflight.py --json-output (Join-Path $localWorkDir "postgres-preflight.json")
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL deployment preflight failed. See $(Join-Path $localWorkDir "postgres-preflight.json")."
        }
    } finally {
        Pop-Location
    }

    Write-Step "Building deploy manifest"
    $files = Get-DeployFileList -RepoRoot $repoRoot
    $badFiles = $files | Where-Object { Test-ProtectedDeployPath $_ }
    if ($badFiles) {
        throw "Protected paths unexpectedly entered the deploy manifest:`n$($badFiles -join "`n")"
    }
    $missingFiles = $files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $repoRoot ($_ -replace "/", [System.IO.Path]::DirectorySeparatorChar))) }
    if ($missingFiles) {
        throw "Deploy manifest contains paths that do not exist locally:`n$($missingFiles -join "`n")"
    }
    Write-Utf8NoBomFile -Path $manifestPath -Content (($files -join "`n") + "`n")
    Write-Host "Deployable files: $($files.Count)"

    Write-Step "Creating archive"
    Push-Location $repoRoot
    try {
        & tar -czf $archivePath -T $manifestPath
        if ($LASTEXITCODE -ne 0) {
            throw "tar failed while creating $archivePath."
        }
    } finally {
        Pop-Location
    }

    $archiveList = & tar -tzf $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed while reading back $archivePath."
    }
    $archiveBad = $archiveList | Where-Object { Test-ProtectedDeployPath $_ }
    if ($archiveBad) {
        throw "Archive contains protected paths and will not be deployed:`n$($archiveBad -join "`n")"
    }

    $archiveInfo = Get-Item -LiteralPath $archivePath
    Write-Host ("Archive: {0} ({1:N2} MB)" -f $archivePath, ($archiveInfo.Length / 1MB))

    if ($DryRun) {
        Write-Step "Dry run complete"
        Write-Host "No files were uploaded and Docker Compose was not touched."
        Write-Host "First 30 deploy files:"
        $files | Select-Object -First 30 | ForEach-Object { Write-Host "  $_" }
        return
    }

    $remoteScript = @'
#!/usr/bin/env bash
set -euo pipefail

remote_path="${1:?remote path required}"
archive="${2:?archive path required}"
keep_backups="${3:?backup retention required}"
backup_dir="${4:?backup directory required}"
skip_database_backup="${5:-False}"
skip_health_check="${6:-False}"

if [ "$remote_path" = "/" ] || [ -z "$remote_path" ]; then
  echo "Unsafe remote path: $remote_path" >&2
  exit 2
fi
if [ ! -d "$remote_path" ]; then
  echo "Remote path does not exist: $remote_path" >&2
  exit 2
fi
if ! [[ "$keep_backups" =~ ^[0-9]+$ ]] || [ "$keep_backups" -lt 1 ] || [ "$keep_backups" -gt 20 ]; then
  echo "Invalid backup retention: $keep_backups" >&2
  exit 2
fi

mkdir -p "$backup_dir"
list="/tmp/lanshare-deploy-files-$$.txt"
tar -tzf "$archive" > "$list"

if awk '($0 ~ /^\// || $0 ~ /(^|\/)\.\.(\/|$)/) {print; bad=1} END{exit bad}' "$list"; then
  :
else
  echo "Archive contains unsafe absolute or parent paths." >&2
  exit 2
fi

if grep -E '(^|/)(data|attendance|chat_logs|homework_submissions|logs|rosters|shared_files|storage|node_modules|venv|\.venv|\.git|tools/guardianangel\.net\.cn_nginx)(/|$)|(^|/)(\.env|docker\.env|[^/]*\.env)$' "$list"; then
  echo "Archive contains protected production/runtime paths. Refusing to deploy." >&2
  exit 2
fi

cd "$remote_path"
ts="$(date +%Y%m%d-%H%M%S)"

configure_compose_cmd() {
  compose_cmd=(docker compose)
  compose_files_description="docker-compose.yml"
  local db_engine=""
  if [ -f "$remote_path/docker.env" ]; then
    db_engine="$(awk -F= '$1 == "DB_ENGINE" {print $2; exit}' "$remote_path/docker.env" | tr -d ' "\r')"
  fi
  if [ "$db_engine" = "postgres" ] && [ -f "$remote_path/docker-compose.postgres.yml" ]; then
    compose_cmd=(docker compose -f docker-compose.yml -f docker-compose.postgres.yml)
    compose_files_description="docker-compose.yml + docker-compose.postgres.yml"
  fi
  echo "COMPOSE_FILES=$compose_files_description"
}

configure_compose_cmd

echo "Backing up remote code to $backup_dir"
code_backup="$backup_dir/code-$ts.tgz"
tar --ignore-failed-read \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./*.env' \
  --exclude='./docker.env' \
  --exclude='./attendance' \
  --exclude='./chat_logs' \
  --exclude='./data' \
  --exclude='./dist' \
  --exclude='./build' \
  --exclude='./homework_submissions' \
  --exclude='./logs' \
  --exclude='./node_modules' \
  --exclude='./rosters' \
  --exclude='./shared_files' \
  --exclude='./storage' \
  --exclude='./test-results' \
  --exclude='./venv' \
  --exclude='./.venv' \
  --exclude='./tools/guardianangel.net.cn_nginx' \
  -czf "$code_backup" -C "$remote_path" .
echo "CODE_BACKUP=$code_backup"

if [ "$skip_database_backup" != "True" ]; then
  db_path=""
  if command -v docker >/dev/null 2>&1; then
    container_db="$("${compose_cmd[@]}" exec -T app python -c 'from classroom_app.config import DB_PATH; print(DB_PATH)' 2>/dev/null | tr -d '\r' || true)"
    case "$container_db" in
      /app/data/*) db_path="$remote_path/data/${container_db#/app/data/}" ;;
    esac
  fi
  if [ -z "$db_path" ]; then
    if [ -f "$remote_path/data/classroom.db" ]; then
      db_path="$remote_path/data/classroom.db"
    elif [ -f "$remote_path/data/db/classroom.db" ]; then
      db_path="$remote_path/data/db/classroom.db"
    fi
  fi
  if [ -n "$db_path" ] && [ -f "$db_path" ]; then
    db_backup="$backup_dir/db-$ts.db"
    if command -v python3 >/dev/null 2>&1; then
      python3 - "$db_path" "$db_backup" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
      echo "DB_BACKUP=$db_backup"
    else
      echo "WARNING: python3 not found; SQLite online backup skipped." >&2
    fi
  else
    echo "WARNING: SQLite database was not found; database backup skipped." >&2
  fi
else
  echo "Database backup skipped by flag."
fi

echo "Pruning old backups; keeping latest $keep_backups of each type."
ls -1t "$backup_dir"/code-*.tgz 2>/dev/null | tail -n +"$((keep_backups + 1))" | xargs -r rm -f
ls -1t "$backup_dir"/db-*.db 2>/dev/null | tail -n +"$((keep_backups + 1))" | xargs -r rm -f

echo "Extracting code archive into $remote_path"
tar -xzf "$archive" -C "$remote_path"
if [ -f "$remote_path/deployment/docker/entrypoint.sh" ]; then
  chmod +x "$remote_path/deployment/docker/entrypoint.sh"
fi
configure_compose_cmd

echo "Preparing Agent runtime data directories"
mkdir -p "$remote_path/data/agent_tasks/deepseek_home"
chown -R 1000:1000 "$remote_path/data/agent_tasks/deepseek_home"
chmod -R u+rwX,go+rX "$remote_path/data/agent_tasks/deepseek_home"

docker_env="$remote_path/docker.env"
if [ -f "$docker_env" ]; then
  echo "Ensuring Agent runtime environment defaults"
  get_env_value() {
    local key="$1"
    sed -n "s/^${key}=//p" "$docker_env" | tail -n 1
  }
  upsert_env_value() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$docker_env"; then
      python - "$docker_env" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
updated = False
out = []
for line in lines:
    if line.startswith(f"{key}="):
        if not updated:
            out.append(f"{key}={value}")
            updated = True
        continue
    out.append(line)
if not updated:
    out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
    else
      printf '\n%s=%s\n' "$key" "$value" >> "$docker_env"
    fi
  }
  ensure_env_value() {
    local key="$1"
    local value="$2"
    local current
    current="$(get_env_value "$key")"
    if [ -z "$current" ]; then
      upsert_env_value "$key" "$value"
    fi
  }

  runtime_token="$(get_env_value AGENT_TASK_RUNTIME_TOKEN)"
  if [ -z "$runtime_token" ] || [ "$runtime_token" = "replace-with-agent-runtime-token" ]; then
    runtime_token="dst_$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    upsert_env_value AGENT_TASK_RUNTIME_TOKEN "$runtime_token"
  fi
  deepseek_runtime_token="$(get_env_value DEEPSEEK_RUNTIME_TOKEN)"
  if [ -z "$deepseek_runtime_token" ] || [ "$deepseek_runtime_token" = "replace-with-agent-runtime-token" ]; then
    upsert_env_value DEEPSEEK_RUNTIME_TOKEN "$runtime_token"
  fi
  ensure_env_value AGENT_TASKS_ENABLED true
  ensure_env_value AGENT_TASK_RUNTIME_URL http://deepseek-runtime:7878
  ensure_env_value AGENT_TASK_RUNTIME_MODEL deepseek-v4-pro
  ensure_env_value AGENT_TASK_RUNTIME_WORKSPACE_PREFIX /workspace/tasks
  ensure_env_value AGENT_TASK_WORKER_ID agent-worker-compose
  ensure_env_value AGENT_TASK_WORKER_POLL_SECONDS 5
  ensure_env_value AGENT_TASK_RUNTIME_POLL_SECONDS 5
  ensure_env_value AGENT_TASK_MAX_RUNTIME_SECONDS 1800
  ensure_env_value AGENT_TASK_DEEPSEEK_AUTO_APPROVE false
  ensure_env_value AGENT_TASK_ALLOW_RUNTIME_SHELL false
  ensure_env_value DEEPSEEK_TUI_TAG latest
fi

echo "Checking Docker Compose configuration"
"${compose_cmd[@]}" config --quiet

echo "Rebuilding and starting Docker Compose in the background"
"${compose_cmd[@]}" up -d --build
echo "Restarting nginx to refresh upstream service addresses"
"${compose_cmd[@]}" restart nginx
"${compose_cmd[@]}" ps

if [ "$skip_health_check" != "True" ]; then
  echo "Checking health endpoints"
  curl -fsS http://127.0.0.1/api/internal/health
  echo
  "${compose_cmd[@]}" exec -T app python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8000/api/internal/health", timeout=8).read().decode())
PY
  "${compose_cmd[@]}" exec -T ai python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8001/api/internal/health", timeout=8).read().decode())
PY
  if "${compose_cmd[@]}" logs --tail=120 app ai mailer | grep -Ei 'traceback|exception|critical|failed|error'; then
    echo "WARNING: recent logs contain error-like lines; inspect the output above." >&2
  else
    echo "NO_RECENT_ERROR_LOGS"
  fi
else
  echo "Health checks skipped by flag."
fi

if [ -d "$remote_path/data" ]; then
  echo "DATA_DIR_SIZE=$(du -sh "$remote_path/data" | cut -f1)"
fi

rm -f "$archive" "$list" "$0"
echo "DEPLOY_DONE"
'@

    Write-Utf8NoBomFile -Path $remoteScriptPath -Content ($remoteScript.Replace("`r`n", "`n"))

    $sshBaseArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($SshKey)) {
        $sshBaseArgs += @("-i", $SshKey)
    }
    $sshBaseArgs += @("-o", "BatchMode=yes", "-o", "ConnectTimeout=20")

    Write-Step "Uploading archive and remote script"
    & scp @sshBaseArgs $archivePath "${remote}:$remoteArchivePath"
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed while uploading archive."
    }
    & scp @sshBaseArgs $remoteScriptPath "${remote}:$remoteScriptUploadPath"
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed while uploading remote script."
    }

    Write-Step "Running remote deployment"
    $skipDb = if ($SkipDatabaseBackup) { "True" } else { "False" }
    $skipHealth = if ($SkipHealthCheck) { "True" } else { "False" }
    & ssh @sshBaseArgs $remote "bash" $remoteScriptUploadPath $RemotePath $remoteArchivePath $KeepBackups $BackupDir $skipDb $skipHealth
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deployment failed with exit code $LASTEXITCODE."
    }

    Write-Step "Deployment finished"
} finally {
    if (Test-Path -LiteralPath $localWorkDir) {
        Remove-Item -LiteralPath $localWorkDir -Recurse -Force
    }
}
