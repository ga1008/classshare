param(
    [string]$BaseUrl = "https://guardianangel.net.cn",
    [string]$ReportRoot = ".codex-temp\deploy-checks",
    [string]$Remote = "root@106.53.153.171",
    [string]$RemotePath = "/lanshare",
    [string]$SshKeyPath = "$env:USERPROFILE\.ssh\lanshare_deploy_rsa",
    [switch]$SkipSsh,
    [switch]$CheckPostgres,
    [ValidateSet("", "sqlite", "postgres")]
    [string]$ExpectedDbEngine = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path (Join-Path $RepoRoot $ReportRoot) "postflight-$Timestamp"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
if ($CheckPostgres -and [string]::IsNullOrWhiteSpace($ExpectedDbEngine)) {
    $ExpectedDbEngine = "postgres"
}
if ($CheckPostgres -and $ExpectedDbEngine -ne "postgres") {
    throw "-CheckPostgres requires -ExpectedDbEngine postgres."
}

$Results = New-Object System.Collections.Generic.List[object]

function Add-Result {
    param(
        [string]$Name,
        [string]$Status,
        [string]$LogPath,
        [string]$ErrorMessage = ""
    )
    $Results.Add([ordered]@{
        name = $Name
        status = $Status
        log = $LogPath
        error = $ErrorMessage
        checked_at = (Get-Date).ToString("o")
    })
}

function Invoke-HttpSmoke {
    param(
        [string]$Name,
        [string]$Path
    )
    $Uri = ($BaseUrl.TrimEnd("/") + $Path)
    $LogPath = Join-Path $ReportDir "$Name.json"
    try {
        $Response = Invoke-WebRequest -Uri $Uri -Method GET -UseBasicParsing -TimeoutSec 30
        $BodyPreview = $Response.Content.Substring(0, [Math]::Min(2000, $Response.Content.Length))
        $BodyPreview = $BodyPreview -replace '(?i)postgres(?:ql)?://[^@\s]+@', 'postgresql://***:***@'
        [ordered]@{
            uri = $Uri
            status_code = [int]$Response.StatusCode
            content_type = $Response.Headers["Content-Type"]
            body_preview = $BodyPreview
        } | ConvertTo-Json -Depth 5 | Set-Content -Path $LogPath -Encoding UTF8
        if ([int]$Response.StatusCode -lt 200 -or [int]$Response.StatusCode -ge 400) {
            throw "unexpected status code $($Response.StatusCode)"
        }
        Add-Result $Name "ok" $LogPath
        return [ordered]@{
            name = $Name
            uri = $Uri
            status_code = [int]$Response.StatusCode
            content = [string]$Response.Content
            log = $LogPath
        }
    } catch {
        Add-Result $Name "failed" $LogPath $_.Exception.Message
        throw
    }
}

$HealthSmoke = Invoke-HttpSmoke "health" "/api/internal/health"
if (-not [string]::IsNullOrWhiteSpace($ExpectedDbEngine)) {
    $HealthBackendLog = Join-Path $ReportDir "health-database-backend.json"
    try {
        $HealthSmoke.content | & python (Join-Path $RepoRoot "tools\deploy\health_backend_check.py") --expected-engine $ExpectedDbEngine --output $HealthBackendLog
        if ($global:LASTEXITCODE -ne 0) {
            throw "health database backend check failed with exit code $global:LASTEXITCODE"
        }
        Add-Result "health-database-backend" "ok" $HealthBackendLog
    } catch {
        Add-Result "health-database-backend" "failed" $HealthBackendLog $_.Exception.Message
        throw
    }
}
Invoke-HttpSmoke "background-tasks" "/api/internal/background-tasks"
Invoke-HttpSmoke "teacher-login-page" "/teacher/login"
Invoke-HttpSmoke "student-login-page" "/student/login"
Invoke-HttpSmoke "vite-manifest" "/static/dist/manifest.json"

if (-not $SkipSsh) {
    $LogPath = Join-Path $ReportDir "remote-docker-ps.log"
    $SshArgs = @()
    $SshArgs += @("-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
    if ($SshKeyPath) {
        $SshArgs += @("-i", $SshKeyPath)
    }
    $SshArgs += @($Remote, "cd $RemotePath; docker compose ps; docker compose exec -T app true")
    try {
        $PreviousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & ssh @SshArgs 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.Exception.Message
            } else {
                $_
            }
        } | Tee-Object -FilePath $LogPath
        $ErrorActionPreference = $PreviousErrorActionPreference
        if ($global:LASTEXITCODE -ne 0) {
            throw "remote docker smoke failed with exit code $global:LASTEXITCODE"
        }
        Add-Result "remote-docker-smoke" "ok" $LogPath
    } catch {
        Add-Result "remote-docker-smoke" "failed" $LogPath $_.Exception.Message
        throw
    } finally {
        if ($PreviousErrorActionPreference) {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }
    }

    $DbLogPath = Join-Path $ReportDir "remote-database-backend.json"
    $DbExpectedEngine = $ExpectedDbEngine
    $DbExpectedEnginePython = $DbExpectedEngine.Replace("\", "\\").Replace("'", "\'")
    $DbCommand = @"
cd $RemotePath
docker compose exec -T app python - <<'PY'
import json
import sys
from dataclasses import asdict
from classroom_app.db.connection import database_backend_state

state = asdict(database_backend_state())
print(json.dumps(state, ensure_ascii=False))
expected_engine = '$DbExpectedEnginePython'
if expected_engine and state.get('engine') != expected_engine:
    sys.exit(2)
if expected_engine and not state.get('configured'):
    sys.exit(3)
PY
"@
    $DbCommand = $DbCommand -replace "`r`n", "`n"
    $DbCommand = $DbCommand -replace "`r", "`n"
    $DbSshArgs = @()
    $DbSshArgs += @("-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
    if ($SshKeyPath) {
        $DbSshArgs += @("-i", $SshKeyPath)
    }
    $DbSshArgs += @($Remote, $DbCommand)
    try {
        $PreviousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & ssh @DbSshArgs 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.Exception.Message
            } else {
                $_
            }
        } | Tee-Object -FilePath $DbLogPath
        $ErrorActionPreference = $PreviousErrorActionPreference
        if ($global:LASTEXITCODE -ne 0) {
            throw "remote database backend check failed with exit code $global:LASTEXITCODE"
        }
        Add-Result "remote-database-backend" "ok" $DbLogPath
    } catch {
        Add-Result "remote-database-backend" "failed" $DbLogPath $_.Exception.Message
        throw
    } finally {
        if ($PreviousErrorActionPreference) {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }
    }
} elseif ($CheckPostgres) {
    $SkippedLog = Join-Path $ReportDir "remote-database-backend-skipped.txt"
    "Skipped because -SkipSsh was provided; -CheckPostgres requires SSH access." | Set-Content -Path $SkippedLog -Encoding UTF8
    Add-Result "remote-database-backend" "failed" $SkippedLog "Cannot verify PostgreSQL cutover with -SkipSsh."
}

$Summary = [ordered]@{
    status = if (($Results | Where-Object { $_.status -ne "ok" }).Count -eq 0) { "ok" } else { "failed" }
    base_url = $BaseUrl
    report_dir = $ReportDir
    generated_at = (Get-Date).ToString("o")
    steps = $Results
    safety = [ordered]@{
        remote_write_operations = $false
        production_data_modified = $false
    }
}
$Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReportDir "summary.json") -Encoding UTF8
Write-Host "Postflight report: $ReportDir"

if (($Results | Where-Object { $_.status -ne "ok" }).Count -gt 0) {
    exit 1
}
