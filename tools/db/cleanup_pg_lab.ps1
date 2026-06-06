<#
.SYNOPSIS
Clean the local PostgreSQL migration lab.

.DESCRIPTION
Deletes only the configured lab root when it resolves under .codex-temp. Use
-WhatIf to preview and -Confirm to require confirmation. It never deletes the
real data directory or any remote Docker volume.
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [string]$LabRoot = ".codex-temp\pg-migration-lab",
    [switch]$StopDockerPostgres,
    [string]$PostgresContainerName = "lanshare-pg-migration-lab"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$TempRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot ".codex-temp"))
$ResolvedLabRoot = if ([System.IO.Path]::IsPathRooted($LabRoot)) { $LabRoot } else { Join-Path $RepoRoot $LabRoot }
$ResolvedLabRoot = [System.IO.Path]::GetFullPath($ResolvedLabRoot)

function Assert-SafeLabRoot {
    param([string]$Path)
    $TempFull = [System.IO.Path]::GetFullPath($TempRoot).TrimEnd("\", "/")
    $PathFull = [System.IO.Path]::GetFullPath($Path).TrimEnd("\", "/")
    if ($PathFull -eq $TempFull) {
        throw "Refusing to delete the whole .codex-temp root."
    }
    if (-not $PathFull.StartsWith($TempFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Lab root must stay under $TempFull; got $PathFull"
    }
    if ($PathFull -match "(^|[\\/])data([\\/]|$)") {
        throw "Refusing to delete a path that looks like a runtime data directory: $PathFull"
    }
}

function Stop-LocalPostgres {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker was not found in PATH."
    }
    $Existing = docker ps -a --filter "name=^/$PostgresContainerName$" --format "{{.Names}}"
    if ($Existing -eq $PostgresContainerName) {
        docker stop $PostgresContainerName | Out-Null
        docker rm $PostgresContainerName | Out-Null
    }
}

Assert-SafeLabRoot $ResolvedLabRoot

if (-not $WhatIfPreference) {
    Push-Location $RepoRoot
    try {
        $ReportDir = Join-Path $ResolvedLabRoot "reports"
        New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
        python tools\db_pg_lab.py cleanup-plan --lab-root $ResolvedLabRoot --json-output (Join-Path $ReportDir "cleanup-plan.json")
        if ($LASTEXITCODE -ne 0) {
            throw "cleanup safety plan failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "What if: generate cleanup safety plan for $ResolvedLabRoot"
}

if ($StopDockerPostgres) {
    if ($PSCmdlet.ShouldProcess($PostgresContainerName, "stop and remove local lab PostgreSQL container")) {
        Stop-LocalPostgres
    }
}

if (Test-Path -LiteralPath $ResolvedLabRoot) {
    if ($PSCmdlet.ShouldProcess($ResolvedLabRoot, "delete local PostgreSQL migration lab")) {
        Remove-Item -LiteralPath $ResolvedLabRoot -Recurse -Force
    }
}
