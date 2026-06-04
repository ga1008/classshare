param(
    [int]$Port = $(if ($env:P03_PORT) { [int]$env:P03_PORT } else { 8023 }),
    [int]$AiPort = $(if ($env:P03_AI_PORT) { [int]$env:P03_AI_PORT } else { 8024 })
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$RuntimeRoot = if ($env:P03_RUNTIME_ROOT) {
    $env:P03_RUNTIME_ROOT
} else {
    Join-Path $RepoRoot ".codex-temp\p03-runtime"
}

$pythonCandidates = @(
    (Join-Path $RepoRoot "venv\Scripts\python.exe"),
    (Join-Path $RepoRoot ".venv\Scripts\python.exe")
)
$Python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
    $Python = "python"
}

$env:P03_RUNTIME_ROOT = $RuntimeRoot
$env:LANSHARE_DATA_ROOT = $RuntimeRoot
$env:MAIN_DATA_DIR = $RuntimeRoot
$env:MAIN_DB_PATH = (Join-Path $RuntimeRoot "db\classroom.db")
$env:AI_HOST = "127.0.0.1"
$env:AI_PORT = [string]$AiPort
$env:AI_ASSISTANT_URL = "http://127.0.0.1:$AiPort"
$env:MAIN_APP_CALLBACK_URL = "http://127.0.0.1:$Port/api/internal/grading-complete"
$env:MOCK_AI_GRADING_DELAY_MS = if ($env:LANSHARE_P03_MOCK_AI_DELAY_MS) { $env:LANSHARE_P03_MOCK_AI_DELAY_MS } else { "1500" }
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

& $Python (Join-Path $RepoRoot "tests\e2e\scripts\prepare_p03_runtime.py") --runtime-root $RuntimeRoot | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "P03 runtime preparation failed with exit code $LASTEXITCODE"
}

$mockArgs = @("tools\mock_ai_assistant.py")
$mockProcess = Start-Process -FilePath $Python -ArgumentList $mockArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru

function Wait-ForUrl {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    throw "Timed out waiting for $Url"
}

try {
    Wait-ForUrl -Url "http://127.0.0.1:$AiPort/api/internal/health" -TimeoutSeconds 30
    & $Python -m uvicorn classroom_app.app:app --host 127.0.0.1 --port $Port --log-level warning
} finally {
    if ($mockProcess -and -not $mockProcess.HasExited) {
        Stop-Process -Id $mockProcess.Id -Force
    }
}
