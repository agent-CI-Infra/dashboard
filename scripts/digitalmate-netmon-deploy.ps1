param([Parameter(Mandatory = $true)][string]$Machine)
# Install the DigitalMateNetMonPush task: every 15 minutes it summarizes
# C:\ProgramData\dsh\netmon.csv and publishes the JSON to the dashboard
# data branch (agent-CI-Infra/dashboard, branch digitalmate) using the
# stored git credential. Run from an elevated shell once per machine.
$ErrorActionPreference = 'Stop'

$src = 'C:\Users\Administrator\AppData\Local\Temp\digitalmate-netmon-push.ps1'
$dst = 'C:\ProgramData\dsh\digitalmate-netmon-push.ps1'
New-Item -ItemType Directory -Path 'C:\ProgramData\dsh' -Force | Out-Null
Copy-Item $src $dst -Force
Write-Output "[1] installed $dst"

Unregister-ScheduledTask -TaskName 'DigitalMateNetMonPush' -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $dst -Machine $Machine"
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Highest
$once = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
$logon = New-ScheduledTaskTrigger -AtLogOn -User 'Administrator'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName 'DigitalMateNetMonPush' -Action $action -Principal $principal -Trigger @($once, $logon) -Settings $settings -Force | Out-Null
Write-Output "[2] registered DigitalMateNetMonPush (15 min repetition + logon trigger, no time limit)"

Start-ScheduledTask -TaskName 'DigitalMateNetMonPush'
Write-Output "[3] first run started; waiting for it to finish..."
Start-Sleep -Seconds 75
$t = Get-ScheduledTask -TaskName 'DigitalMateNetMonPush'
Write-Output ("[4] task State={0}" -f $t.State)
if (Test-Path 'C:\ProgramData\dsh\netmon-push.log') {
  Get-Content 'C:\ProgramData\dsh\netmon-push.log' -Tail 5
} else {
  Write-Output '[4] netmon-push.log not created yet'
}
Write-Output '=== DONE ==='
