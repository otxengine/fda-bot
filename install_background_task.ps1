# Registers "FinResearch Scheduler" as a Windows Task Scheduler task that
# starts run_background_hidden.vbs (-> run_background.bat -> the scheduler,
# in a restart-on-exit loop) automatically at logon, with no visible window.
#
# Run once, from this folder:  powershell -ExecutionPolicy Bypass -File install_background_task.ps1
# Check status:                Get-ScheduledTask -TaskName "FinResearch Scheduler" | Get-ScheduledTaskInfo
# View live logs:               Get-Content data\scheduler.log -Tail 30 -Wait
# Remove:                       powershell -ExecutionPolicy Bypass -File uninstall_background_task.ps1

$ErrorActionPreference = "Stop"
$taskName = "FinResearch Scheduler"
$scriptDir = $PSScriptRoot
$vbsPath = Join-Path $scriptDir "run_background_hidden.vbs"

if (-not (Test-Path $vbsPath)) {
    Write-Error "run_background_hidden.vbs not found next to this script - run from the project folder."
    exit 1
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`"" -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

$taskDescription = "Runs the finresearch scheduler (data fetch) automatically at logon, hidden, so it can catch a new economic report the same day it is released, not just when run.bat is launched manually."

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description $taskDescription `
    -Force | Out-Null

Write-Host "Installed. It will start automatically next time you log in."
Write-Host "To start it right now without logging out/in, run:"
Write-Host "  Start-ScheduledTask -TaskName `"$taskName`""
