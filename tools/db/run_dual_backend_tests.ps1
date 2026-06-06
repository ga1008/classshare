<#
.SYNOPSIS
Run local dual-backend safety tests for the PostgreSQL migration work.

.DESCRIPTION
Runs focused unit tests for SQLite default mode, PostgreSQL fail-closed
configuration, SQL dialect helpers, migration readiness, concurrency planning,
and file integrity. Optional API smoke checks can target an already running
local app by passing -BaseUrl; this script does not start the production app
against real data.
#>

[CmdletBinding()]
param(
    [string]$LabRoot = ".codex-temp\pg-migration-lab",
    [string]$BaseUrl = "",
    [switch]$SkipApiSmoke,
    [ValidateSet("", "sqlite", "postgres")]
    [string]$ExpectedDbEngine = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ResolvedLabRoot = if ([System.IO.Path]::IsPathRooted($LabRoot)) { $LabRoot } else { Join-Path $RepoRoot $LabRoot }
$ReportDir = Join-Path $ResolvedLabRoot "reports"
$LogDir = Join-Path $ResolvedLabRoot "logs"
New-Item -ItemType Directory -Force -Path $ReportDir, $LogDir | Out-Null

$Results = New-Object System.Collections.Generic.List[object]
$HadFailure = $false

function Invoke-TestStep {
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

Invoke-TestStep "database-unit-tests" {
    python -m unittest tests.test_db_file_integrity tests.test_db_concurrency_plan tests.test_db_migration_readiness tests.test_db_sql_helpers tests.test_db_schema_plan tests.test_database_inventory_tools tests.test_database_split_idempotency tests.test_deploy_check_tools
}

Invoke-TestStep "syntax-checks" {
    python -m py_compile tools\db_pg_lab.py tools\db_file_integrity.py tools\db_concurrency_plan.py tools\db_migration_readiness.py tools\db_schema_plan.py tools\db_inventory.py tools\deploy\health_backend_check.py classroom_app\db\sql.py classroom_app\db\connection.py classroom_app\db\errors.py classroom_app\db\migration_registry.py classroom_app\db\row.py
}

if (-not $SkipApiSmoke -and -not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    Invoke-TestStep "api-health-smoke" {
        $HealthUrl = $BaseUrl.TrimEnd("/") + "/api/internal/health"
        $Response = Invoke-WebRequest -Uri $HealthUrl -Method GET -UseBasicParsing -TimeoutSec 15
        $BodyPreview = $Response.Content.Substring(0, [Math]::Min(1000, $Response.Content.Length))
        $BodyPreview = $BodyPreview -replace '(?i)postgres(?:ql)?://[^@\s]+@', 'postgresql://***:***@'
        [ordered]@{
            uri = $HealthUrl
            status_code = [int]$Response.StatusCode
            content_type = $Response.Headers["Content-Type"]
            body_preview = $BodyPreview
        } | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $ReportDir "api-health-smoke.json") -Encoding UTF8
        if ([int]$Response.StatusCode -lt 200 -or [int]$Response.StatusCode -ge 400) {
            throw "unexpected API health status code $($Response.StatusCode)"
        }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedDbEngine)) {
            $BackendReportPath = Join-Path $ReportDir "api-health-database-backend.json"
            $Response.Content | & python tools\deploy\health_backend_check.py --expected-engine $ExpectedDbEngine --output $BackendReportPath
            if ($global:LASTEXITCODE -ne 0) {
                throw "api health database backend check failed with exit code $global:LASTEXITCODE"
            }
        }
    }
} else {
    $Results.Add([ordered]@{
        name = "api-health-smoke"
        status = "skipped"
        log = ""
        started_at = (Get-Date).ToString("o")
        finished_at = (Get-Date).ToString("o")
        reason = "Pass -BaseUrl to smoke-test an already running temp app. The script does not start the app against real data."
    })
}

$Summary = [ordered]@{
    status = if ($HadFailure) { "failed" } else { "ok" }
    generated_at = (Get-Date).ToString("o")
    lab_root = $ResolvedLabRoot
    reports_dir = $ReportDir
    steps = $Results
    safety = [ordered]@{
        production_data_modified = $false
        remote_data_modified = $false
        real_data_app_started = $false
    }
}
$Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReportDir "dual-backend-tests-summary.json") -Encoding UTF8
Write-Host "Dual-backend test report: $ReportDir"

if ($HadFailure) {
    exit 1
}
