param(
    [Parameter(Mandatory = $true)][string]$Runtime,
    [int]$Seconds = 3600,
    [int]$Interval = 5
)
$ErrorActionPreference = 'Stop'
$capacityRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$capacityTemporary = [IO.Path]::GetFullPath((Join-Path $capacityRoot '.codex-temp')) + '\'
$capacityRuntime = [IO.Path]::GetFullPath($Runtime)
if (-not $capacityRuntime.StartsWith($capacityTemporary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Owned temporary runtime required' }
$capacityFixture = Get-Content -LiteralPath (Join-Path $capacityRuntime 'fixture.json') -Raw | ConvertFrom-Json
if ($capacityFixture.uiV3Synthetic -ne $true) { throw 'Explicit synthetic fixture required' }
$capacityBudget = Get-Content -LiteralPath (Join-Path $capacityRuntime 'resource-budget.json') -Raw | ConvertFrom-Json
$capacityMaster = Get-CimInstance Win32_Process -Filter "ProcessId = $($capacityBudget.postgresMaster)"
if ($capacityMaster.CommandLine -notlike '*platform-capacity-20261105-pg*') { throw 'Owned PostgreSQL cluster required' }
if ($Seconds -lt 1 -or $Seconds -gt 10800 -or $Interval -lt 1) { throw 'Bounded observation duration required' }
$capacityOutput = Join-Path $capacityRuntime 'resource-observations.jsonl'
$capacityUntil = [DateTime]::UtcNow.AddSeconds($Seconds)
$capacityStop = Join-Path $capacityRuntime 'stop-capacity-resource'
while ([DateTime]::UtcNow -lt $capacityUntil -and -not (Test-Path -LiteralPath $capacityStop)) {
    $capacityBudget = Get-Content -LiteralPath (Join-Path $capacityRuntime 'resource-budget.json') -Raw | ConvertFrom-Json
    $capacityRows = Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -eq $capacityBudget.appPid -or
        $_.ProcessId -eq $capacityBudget.backgroundPid -or
        $_.ProcessId -eq $capacityBudget.postgresMaster -or $_.ParentProcessId -eq $capacityBudget.postgresMaster -or
        $_.ProcessId -eq $capacityBudget.nginxMaster -or $_.ParentProcessId -eq $capacityBudget.nginxMaster
    }
    $capacityProcesses = foreach ($capacityRow in $capacityRows) {
        try {
            $capacityProc = Get-Process -Id $capacityRow.ProcessId
            [pscustomobject]@{
                pid=$capacityProc.Id; parent=$capacityRow.ParentProcessId; name=$capacityProc.ProcessName
                cpuSeconds=$capacityProc.CPU; privateBytes=$capacityProc.PrivateMemorySize64
                workingSetBytes=$capacityProc.WorkingSet64; affinity=$capacityProc.ProcessorAffinity.ToInt64()
                threads=$capacityProc.Threads.Count; handles=$capacityProc.HandleCount
            }
        } catch { }
    }
    [pscustomobject]@{ at=[DateTime]::UtcNow.ToString('o'); logicalCpuBudget=$capacityBudget.logicalCpuBudget; processes=@($capacityProcesses) } |
        ConvertTo-Json -Depth 4 -Compress | Add-Content -LiteralPath $capacityOutput -Encoding utf8
    Start-Sleep -Seconds $Interval
}
Write-Output $capacityOutput
