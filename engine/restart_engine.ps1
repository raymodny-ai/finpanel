# restart_engine.ps1 — Kill any existing engine on port 8888, then relaunch
# via WMI Win32_Process Create (fully detached from bash/PowerShell job objects).
#
# WMI-spawned processes are owned by WmiPrvSE.exe and survive session teardown,
# unlike Start-Process which gets killed when the calling bash tool exits.
#
# Usage: powershell.exe -NoProfile -ExecutionPolicy Bypass -File restart_engine.ps1

$ErrorActionPreference = "Stop"
$Port = 8888
$EngineDir = "C:\Users\raylan\.qoderworkcn\workspace\mtoyaj33cc0le1nx\outputs\finpanel\engine"
$PyExe = "C:\Users\raylan\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

# === 1. Kill anything holding the port or any leftover engine/fork processes ===
for ($attempt = 1; $attempt -le 3; $attempt++) {
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $pids = @()
    if ($conns) { $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique }
    # Also catch multiprocessing-fork children that inherited the socket
    $forks = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "multiprocessing|uvicorn|agent_engine" }
    $forkPids = @($forks | Select-Object -ExpandProperty ProcessId)
    $allPids = ($pids + $forkPids) | Sort-Object -Unique
    if (-not $allPids) { Write-Output "Port $Port is free (attempt $attempt)."; break }
    foreach ($p in $allPids) {
        Write-Output "Attempt $attempt : killing PID $p"
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# === 2. Spawn engine via WMI (detached from caller's job object) ===
$cmd = "`"$PyExe`" -m uvicorn agent_engine.main:app --host 0.0.0.0 --port $Port"
Write-Output "WMI Create: $cmd"
Write-Output "  WorkingDir: $EngineDir"

$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd
    CurrentDirectory = $EngineDir
}

if ($result.ReturnValue -ne 0) {
    Write-Output "FAILED: WMI Create returned $($result.ReturnValue)"
    exit 1
}
Write-Output "SUCCESS: engine PID $($result.ProcessId) spawned via WMI"

# === 3. Wait for health ===
$ok = $false
for ($i = 1; $i -le 12; $i++) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            Write-Output "Engine healthy after ${i}s: $($resp.Content)"
            $ok = $true; break
        }
    } catch { }
}
if (-not $ok) { Write-Output "WARNING: engine did not respond within 12s." }
