<#
.SYNOPSIS
Run the local database migration lab checks against a copied SQLite database.

.DESCRIPTION
This script prepares .codex-temp/pg-migration-lab, then runs read-only or copied
database checks: migration dry-run, inventory, schema plan, migration readiness,
concurrency plan, and file integrity. PostgreSQL data loading is intentionally
reported as not executed until the P01 PostgreSQL adapter and loader gates pass.
#>

[CmdletBinding()]
param(
    [string]$LabRoot = ".codex-temp\pg-migration-lab",
    [string]$SourceDb = "",
    [string]$DatabaseUrl = "",
    [switch]$Clean,
    [switch]$SkipPrepare
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ResolvedLabRoot = if ([System.IO.Path]::IsPathRooted($LabRoot)) { $LabRoot } else { Join-Path $RepoRoot $LabRoot }
$ReportDir = Join-Path $ResolvedLabRoot "reports"
$LogDir = Join-Path $ResolvedLabRoot "logs"
New-Item -ItemType Directory -Force -Path $ReportDir, $LogDir | Out-Null

$Results = New-Object System.Collections.Generic.List[object]
$HadFailure = $false

function Invoke-LabStep {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    $LogPath = Join-Path $LogDir "$Name.log"
    $StartedAt = Get-Date
    $global:LASTEXITCODE = 0
    try {
        Push-Location $RepoRoot
        try {
            $PreviousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $Command 2>&1 | ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    $_
                }
            } | Tee-Object -FilePath $LogPath
            $ErrorActionPreference = $PreviousErrorActionPreference
            if ($global:LASTEXITCODE -ne 0) {
                throw "$Name failed with exit code $global:LASTEXITCODE"
            }
        } finally {
            if ($PreviousErrorActionPreference) {
                $ErrorActionPreference = $PreviousErrorActionPreference
            }
            Pop-Location
        }
        $Results.Add([ordered]@{
            name = $Name
            status = "ok"
            log = $LogPath
            started_at = $StartedAt.ToString("o")
            finished_at = (Get-Date).ToString("o")
        })
    } catch {
        $script:HadFailure = $true
        $Results.Add([ordered]@{
            name = $Name
            status = "failed"
            log = $LogPath
            started_at = $StartedAt.ToString("o")
            finished_at = (Get-Date).ToString("o")
            error = $_.Exception.Message
        })
        Write-Warning $_.Exception.Message
    }
}

try {
    if (-not $SkipPrepare) {
        Invoke-LabStep "prepare" {
            $Args = @("tools\db_pg_lab.py", "prepare", "--lab-root", $LabRoot, "--json-output", (Join-Path $ReportDir "prepare.json"))
            if (-not [string]::IsNullOrWhiteSpace($SourceDb)) {
                $Args += @("--source-db", $SourceDb)
            }
            if ($Clean) {
                $Args += "--clean"
            }
            python @Args
        }
    }

    $CopiedDb = Join-Path $ResolvedLabRoot "db\classroom.db"
    Invoke-LabStep "environment" {
        $Args = @("tools\db_pg_lab.py", "environment", "--lab-root", $LabRoot, "--json-output", (Join-Path $ReportDir "environment.json"))
        if (-not [string]::IsNullOrWhiteSpace($DatabaseUrl)) {
            $Args += @("--database-url", $DatabaseUrl)
        }
        python @Args
    }
    Invoke-LabStep "migration-dry-run" {
        python tools\deploy\migration_dry_run.py --runtime-root (Join-Path $ResolvedLabRoot "migration-dry-run") --source-db $CopiedDb --json-output (Join-Path $ReportDir "migration-dry-run.json")
    }
    Invoke-LabStep "database-inventory" {
        python tools\db_inventory.py --runtime-root (Join-Path $ResolvedLabRoot "inventory") --source-db $CopiedDb --json-output (Join-Path $ReportDir "database-inventory.json") --markdown-output (Join-Path $ReportDir "database-inventory.md") --table-map-output (Join-Path $ReportDir "database-table-map.md") --risk-register-output (Join-Path $ReportDir "database-risk-register.md")
    }
    Invoke-LabStep "schema-plan" {
        python tools\db_schema_plan.py --runtime-root (Join-Path $ResolvedLabRoot "schema-plan") --source-db $CopiedDb --json-output (Join-Path $ReportDir "schema-baseline-plan.json") --markdown-output (Join-Path $ReportDir "schema-baseline-plan.md")
    }
    Invoke-LabStep "migration-readiness" {
        python tools\db_migration_readiness.py --runtime-root (Join-Path $ResolvedLabRoot "migration-readiness") --source-db $CopiedDb --json-output (Join-Path $ReportDir "migration-readiness.json") --markdown-output (Join-Path $ReportDir "migration-readiness.md")
    }
    Invoke-LabStep "concurrency-plan" {
        python tools\db_concurrency_plan.py --runtime-root (Join-Path $ResolvedLabRoot "concurrency-plan") --source-db $CopiedDb --json-output (Join-Path $ReportDir "concurrency-plan.json") --markdown-output (Join-Path $ReportDir "concurrency-plan.md")
    }
    Invoke-LabStep "file-integrity" {
        python tools\db_file_integrity.py --runtime-root (Join-Path $ResolvedLabRoot "file-integrity") --source-db $CopiedDb --data-root (Join-Path $RepoRoot "data") --repo-root $RepoRoot --json-output (Join-Path $ReportDir "file-integrity.json") --markdown-output (Join-Path $ReportDir "file-integrity.md")
    }
} finally {
    $Summary = [ordered]@{
        status = if ($HadFailure) { "failed" } else { "ok" }
        generated_at = (Get-Date).ToString("o")
        lab_root = $ResolvedLabRoot
        reports_dir = $ReportDir
        copied_db = (Join-Path $ResolvedLabRoot "db\classroom.db")
        steps = $Results
        postgres_target = [ordered]@{
            database_url_configured = -not [string]::IsNullOrWhiteSpace($DatabaseUrl)
            database_url_redacted = if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) { "" } else { "***redacted***" }
            actual_postgres_data_load_executed = $false
            reason = "PostgreSQL data loading is gated until P01 PostgreSQL adapter and loader targets pass."
        }
        safety = [ordered]@{
            production_data_modified = $false
            remote_data_modified = $false
            copied_sqlite_db_only = $true
        }
    }
    $Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReportDir "run-pg-migration-dry-run-summary.json") -Encoding UTF8
    Push-Location $RepoRoot
    try {
        python tools\db_pg_lab.py summarize --lab-root $LabRoot --json-output (Join-Path $ReportDir "lab-summary.json") --markdown-output (Join-Path $ReportDir "lab-summary.md")
    } finally {
        Pop-Location
    }
}

if ($HadFailure) {
    exit 1
}
