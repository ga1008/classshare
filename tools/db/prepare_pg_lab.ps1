<#
.SYNOPSIS
Prepare the local Win11 PostgreSQL migration lab under .codex-temp.

.DESCRIPTION
Creates .codex-temp/pg-migration-lab folders and copies the SQLite source
database with SQLite backup API. The real data/classroom.db is never used as a
write target.

When -StartDockerPostgres is used, the script starts a local PostgreSQL
container only if the requested image already exists locally. It never pulls
from the network.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$LabRoot = ".codex-temp\pg-migration-lab",
    [string]$SourceDb = "",
    [switch]$Clean,
    [switch]$StartDockerPostgres,
    [string]$PostgresContainerName = "lanshare-pg-migration-lab",
    [string]$PostgresImage = "postgres:16-alpine",
    [int]$PostgresPort = 55432
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Join-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $RepoRoot $Path
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-PythonPrepare {
    $ResolvedLabRoot = Join-RepoPath $LabRoot
    $ReportDir = Join-Path $ResolvedLabRoot "reports"
    New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
    $PrepareJson = Join-Path $ReportDir "prepare.json"

    $Args = @("tools\db_pg_lab.py", "prepare", "--lab-root", $LabRoot, "--json-output", $PrepareJson)
    if (-not [string]::IsNullOrWhiteSpace($SourceDb)) {
        $Args += @("--source-db", $SourceDb)
    }
    if ($Clean) {
        $Args += "--clean"
    }
    & python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "prepare lab failed with exit code $LASTEXITCODE"
    }
}

function Start-LocalPostgres {
    Require-Command docker
    docker image inspect $PostgresImage *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image '$PostgresImage' is not available locally. Load the offline image first; this script will not pull from the network."
    }

    $Existing = docker ps -a --filter "name=^/$PostgresContainerName$" --format "{{.Names}}"
    if ($Existing -eq $PostgresContainerName) {
        docker start $PostgresContainerName | Out-Null
        return
    }

    $ResolvedLabRoot = Join-RepoPath $LabRoot
    $PgData = Join-Path $ResolvedLabRoot "postgres-data"
    $PgEnv = Join-Path $ResolvedLabRoot "postgres.env"
    $PasswordBytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24)
    $GeneratedPassword = [Convert]::ToHexString($PasswordBytes).ToLowerInvariant()
    New-Item -ItemType Directory -Force -Path $PgData | Out-Null
    @(
        "POSTGRES_USER=lanshare_lab",
        "POSTGRES_PASSWORD=$GeneratedPassword",
        "POSTGRES_DB=lanshare_lab"
    ) | Set-Content -LiteralPath $PgEnv -Encoding UTF8
    docker run `
        --name $PostgresContainerName `
        --env-file $PgEnv `
        -p "127.0.0.1:${PostgresPort}:5432" `
        -v "${PgData}:/var/lib/postgresql/data" `
        -d $PostgresImage | Out-Null
}

Push-Location $RepoRoot
try {
    Require-Command python
    if ($PSCmdlet.ShouldProcess((Join-RepoPath $LabRoot), "prepare PostgreSQL migration lab")) {
        Invoke-PythonPrepare
        if ($StartDockerPostgres) {
            Start-LocalPostgres
        }
    }
} finally {
    Pop-Location
}
