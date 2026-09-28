# Removes the "FinResearch Scheduler" Task Scheduler task installed by
# install_background_task.ps1, and stops the currently-running background
# scheduler process if one is active (matched via its PID file, same check
# scheduler/main.py itself uses to avoid a duplicate writer).

$ErrorActionPreference = "Stop"
$taskName = "FinResearch Scheduler"
$scriptDir = $PSScriptRoot
$pidFile = Join-Path $scriptDir "data\scheduler.pid"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    # Stop first (kills the wscript.exe -> cmd.exe -> python.exe tree Task
    # Scheduler is tracking), THEN unregister - Unregister alone only
    # removes the task definition, it does not stop an already-running instance.
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Stopped and removed the scheduled task - it will no longer auto-start at logon."
} else {
    Write-Host "No scheduled task named '$taskName' found - nothing to remove."
}

if (Test-Path $pidFile) {
    $procId = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $procId -Force
        Write-Host "Stopped the currently-running background scheduler (PID $procId)."
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Also kill run_background.bat's own retry-loop cmd.exe (Stop-ScheduledTask
# above does NOT reliably stop it - confirmed in practice: the .vbs launches
# it detached via WshShell.Run, outside what Task Scheduler tracks/kills, so
# killing only the python.exe PID above would leave the loop alive to
# silently restart a new scheduler ~30s later).
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run_background.bat*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped the run_background.bat retry loop (PID $($_.ProcessId))."
    }
