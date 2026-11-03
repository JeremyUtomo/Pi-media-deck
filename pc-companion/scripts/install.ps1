# Publishes pc-companion to %LOCALAPPDATA%\Programs\PiMediaDeck, turns on "Start with Windows" (logon task) and starts it.
# Re-run to update. No admin needed; add the firewall rule from the tray menu ("Allow through firewall…").
$ErrorActionPreference = 'Stop'

$target = Join-Path $env:LOCALAPPDATA 'Programs\PiMediaDeck'
$exe = Join-Path $target 'pc-companion.exe'

# Stop the running app. The copy started by the logon task is stopped through the task, which also works when this
# script runs somewhere that can't see that process directly (like a sandboxed shell).
if (Get-ScheduledTask -TaskName 'PiMediaDeck' -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName 'PiMediaDeck' }
Get-Process pc-companion -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

dotnet publish (Join-Path $PSScriptRoot '..\PcCompanion.csproj') -c Release -o $target --nologo -v q
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed ($LASTEXITCODE)" }

# Start with Windows: a per-user logon task, the same one the tray's "Start with Windows" toggle creates
# (Tray/Autostart.cs). The HKCU Run key was skipped at sign-in on this Windows 11 build, so remove any leftover.
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'PiMediaDeck' -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run' -Name 'PiMediaDeck' -ErrorAction SilentlyContinue
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT5S'
Register-ScheduledTask -TaskName 'PiMediaDeck' -Force `
	-Description 'Pi Media Deck companion: WebSocket server for the touchscreen deck.' `
	-Action (New-ScheduledTaskAction -Execute $exe) -Trigger $trigger `
	-Principal (New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited) `
	-Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Priority 5 -MultipleInstances IgnoreNew) | Out-Null

# Start it through the task, so it runs in the real desktop session (not inside whatever shell ran this script).
Start-ScheduledTask -TaskName 'PiMediaDeck'
Write-Host "Installed to $target and started. Log: $env:LOCALAPPDATA\PiMediaDeck\pc-companion.log"
