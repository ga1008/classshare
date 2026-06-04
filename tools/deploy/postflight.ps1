param(
    [string]$BaseUrl = "https://guardianangel.net.cn",
    [string]$ReportRoot = ".codex-temp\deploy-checks",
    [string]$Remote = "root@106.53.153.171",
    [string]$RemotePath = "/lanshare",
    [string]$SshKeyPath = "",
    [switch]$SkipSsh
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path (Join-Path $RepoRoot $ReportRoot) "postflight-$Timestamp"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

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
        [ordered]@{
            uri = $Uri
            status_code = [int]$Response.StatusCode
            content_type = $Response.Headers["Content-Type"]
            body_preview = $Response.Content.Substring(0, [Math]::Min(2000, $Response.Content.Length))
        } | ConvertTo-Json -Depth 5 | Set-Content -Path $LogPath -Encoding UTF8
        if ([int]$Response.StatusCode -lt 200 -or [int]$Response.StatusCode -ge 400) {
            throw "unexpected status code $($Response.StatusCode)"
        }
        Add-Result $Name "ok" $LogPath
    } catch {
        Add-Result $Name "failed" $LogPath $_.Exception.Message
        throw
    }
}

Invoke-HttpSmoke "health" "/api/internal/health"
Invoke-HttpSmoke "background-tasks" "/api/internal/background-tasks"
Invoke-HttpSmoke "teacher-login-page" "/teacher/login"
Invoke-HttpSmoke "student-login-page" "/student/login"
Invoke-HttpSmoke "vite-manifest" "/static/dist/manifest.json"

if (-not $SkipSsh) {
    $LogPath = Join-Path $ReportDir "remote-docker-ps.log"
    $SshArgs = @()
    if ($SshKeyPath) {
        $SshArgs += @("-i", $SshKeyPath)
    }
    $SshArgs += @($Remote, "cd $RemotePath; docker compose ps; docker compose exec -T app python -c ""import json, urllib.request; print('app container reachable')""")
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
