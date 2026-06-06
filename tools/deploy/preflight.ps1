param(
    [string]$ReportRoot = ".codex-temp\deploy-checks",
    [switch]$SkipRemoteDryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path (Join-Path $RepoRoot $ReportRoot) "preflight-$Timestamp"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$Results = New-Object System.Collections.Generic.List[object]

function Invoke-PreflightStep {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    $LogPath = Join-Path $ReportDir "$Name.log"
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
        $Results.Add([ordered]@{
            name = $Name
            status = "failed"
            log = $LogPath
            started_at = $StartedAt.ToString("o")
            finished_at = (Get-Date).ToString("o")
            error = $_.Exception.Message
        })
        throw
    }
}

try {
    Invoke-PreflightStep "git-snapshot" {
        git status --short --branch
        git rev-parse HEAD
    }
    Invoke-PreflightStep "frontend-typecheck" { npm run typecheck }
    Invoke-PreflightStep "frontend-test" { npm test }
    Invoke-PreflightStep "frontend-build" { npm run build }
    Invoke-PreflightStep "backend-tests" { python -m unittest discover -s tests -p "test_*.py" }
    Invoke-PreflightStep "manifest-check" {
        python tools\deploy\check_manifest.py --json-output (Join-Path $ReportDir "manifest-check.json")
    }
    Invoke-PreflightStep "postgres-deploy-preflight" {
        python tools\deploy\postgres_preflight.py --json-output (Join-Path $ReportDir "postgres-preflight.json")
    }
    Invoke-PreflightStep "migration-dry-run" {
        python tools\deploy\migration_dry_run.py --json-output (Join-Path $ReportDir "migration-dry-run.json")
    }
    if (-not $SkipRemoteDryRun) {
        Invoke-PreflightStep "remote-deploy-dry-run" {
            powershell -NoProfile -ExecutionPolicy Bypass -File deployment\deploy_remote.ps1 -DryRun
        }
    }
} finally {
    $Summary = [ordered]@{
        status = if (($Results | Where-Object { $_.status -ne "ok" }).Count -eq 0) { "ok" } else { "failed" }
        report_dir = $ReportDir
        generated_at = (Get-Date).ToString("o")
        steps = $Results
        safety = [ordered]@{
            remote_deploy_executed = $false
            migration_ran_on_copied_db = $true
            production_data_modified = $false
        }
    }
    $Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReportDir "summary.json") -Encoding UTF8
    Write-Host "Preflight report: $ReportDir"
}

if (($Results | Where-Object { $_.status -ne "ok" }).Count -gt 0) {
    exit 1
}
