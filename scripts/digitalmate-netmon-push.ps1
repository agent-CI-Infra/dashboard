param([Parameter(Mandatory = $true)][string]$Machine)
# Summarize C:\ProgramData\dsh\netmon.csv (written by DigitalMateNetMon) and
# publish a compact JSON to site/data/netmon/<Machine>.json on the
# agent-CI-Infra/dashboard `digitalmate` data branch via the GitHub contents
# API (api.github.com stays reachable when github.com git transport is not).
# Auth comes from the stored git credential (Credential Manager), so no token
# lives in this script.
#
# Note: native git commands must NOT be piped through 2>&1 while
# $ErrorActionPreference is 'Stop' - git writes progress to stderr and
# PowerShell would turn the first progress line into a terminating error.
$ErrorActionPreference = 'Stop'

# Direct network: the local proxy is slower than direct for GitHub.
$env:HTTP_PROXY = ''; $env:HTTPS_PROXY = ''; $env:ALL_PROXY = ''
$env:http_proxy = ''; $env:https_proxy = ''; $env:all_proxy = ''

$csvPath = 'C:\ProgramData\dsh\netmon.csv'
$logPath = 'C:\ProgramData\dsh\netmon-push.log'

function Log([string]$m) {
  $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Machine, $m
  Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
  if ((Get-Item $logPath -ErrorAction SilentlyContinue).Length -gt 1MB) {
    $tail = Get-Content $logPath -Tail 200
    Set-Content $logPath $tail -Encoding UTF8
  }
}

try {
  # ---------- 1. summarize the netmon samples ----------
  # CSV columns: time,adapter,rx_bps,tx_bps,workers,proxy7897 (per adapter per 5s sample)
  $samples = @{}   # 'yyyy-MM-dd HH:mm:ss' -> @(rx_sum, tx_sum, workers_max, proxy_max)
  Get-Content $csvPath -Tail 3200 | ForEach-Object {
    $p = $_ -split ','
    if ($p.Count -lt 6) { return }
    if ($p[0] -notmatch '^\d{4}-') { return }
    $t = $p[0]
    if (-not $samples.ContainsKey($t)) { $samples[$t] = @(0.0, 0.0, 0, 0) }
    $s = $samples[$t]
    $s[0] += [double]$p[2]; $s[1] += [double]$p[3]
    if ([int]$p[4] -gt $s[2]) { $s[2] = [int]$p[4] }
    if ([int]$p[5] -gt $s[3]) { $s[3] = [int]$p[5] }
  }
  $times = $samples.Keys | Sort-Object
  if (-not $times -or $times.Count -lt 2) { Log 'netmon.csv not ready yet'; exit 0 }

  $cutoff = [datetime]::ParseExact($times[-1], 'yyyy-MM-dd HH:mm:ss', $null).AddMinutes(-5).ToString('yyyy-MM-dd HH:mm:ss')
  $window = @($times | Where-Object { $_ -ge $cutoff })
  $rxWindow = @($window | ForEach-Object { $samples[$_][0] })
  $txWindow = @($window | ForEach-Object { $samples[$_][1] })
  $downAvg = if ($rxWindow.Count) { ($rxWindow | Measure-Object -Average).Average } else { 0 }
  $downPeak = if ($rxWindow.Count) { ($rxWindow | Measure-Object -Maximum).Maximum } else { 0 }
  $upAvg = if ($txWindow.Count) { ($txWindow | Measure-Object -Average).Average } else { 0 }

  # per-minute aggregates for the sparkline (last 60 minutes)
  $minuteData = @{}   # 'yyyy-MM-dd HH:mm' -> @{ downs; ups; workers }
  foreach ($t in $times) {
    $m = $t.Substring(0, 16)
    if (-not $minuteData.ContainsKey($m)) { $minuteData[$m] = @{ downs = @(); ups = @(); workers = 0 } }
    $g = $minuteData[$m]
    $g.downs += $samples[$t][0]
    $g.ups += $samples[$t][1]
    if ($samples[$t][2] -gt $g.workers) { $g.workers = [int]$samples[$t][2] }
  }
  $minuteKeys = $minuteData.Keys | Sort-Object | Select-Object -Last 60
  $minuteRows = @()
  foreach ($m in $minuteKeys) {
    $g = $minuteData[$m]
    $minuteRows += [pscustomobject]@{
      t = $m.Substring(11)
      down = [math]::Round(($g.downs | Measure-Object -Average).Average, 0)
      up = [math]::Round(($g.ups | Measure-Object -Average).Average, 0)
      workers = [int]$g.workers
    }
  }

  $last = $samples[$times[-1]]
  $out = [pscustomobject]@{
    updatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    machine = $Machine
    host = $env:COMPUTERNAME
    downAvg5m = [math]::Round($downAvg, 0)
    downPeak5m = [math]::Round($downPeak, 0)
    upAvg5m = [math]::Round($upAvg, 0)
    workersNow = [int]$last[2]
    proxyConns = [int]$last[3]
    minutes = $minuteRows
  }

  # ---------- 2. publish via the GitHub contents API ----------
  # api.github.com stays directly reachable (unlike github.com git transport
  # during the evening GFW window) and the payload is one small JSON, so the
  # contents API beats cloning the 12MB data branch through git.
  $env:GIT_TERMINAL_PROMPT = '0'
  $env:GCM_INTERACTIVE = 'never'
  $cred = ("protocol=https`nhost=github.com`n`n" | git credential fill 2>$null)
  $pat = (($cred | Where-Object { $_ -like 'password=*' }) -replace '^password=', '')
  if (-not $pat) { Log 'no git credential available'; exit 1 }

  $apiHeaders = @{
    Authorization          = "Bearer $pat"
    Accept                 = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
  }
  $jsonText = $out | ConvertTo-Json -Depth 4
  $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($jsonText))
  $apiPath = "repos/agent-CI-Infra/dashboard/contents/site/data/netmon/$Machine.json"

  $published = $false
  for ($attempt = 1; $attempt -le 3 -and -not $published; $attempt++) {
    try {
      $body = @{
        message = "chore(digitalmate): netmon summary $Machine"
        branch  = 'digitalmate'
        content = $b64
      }
      try {
        $existing = Invoke-RestMethod -Uri "https://api.github.com/$apiPath`?ref=digitalmate" -Headers $apiHeaders -TimeoutSec 30
        if ($existing -and $existing.sha) { $body.sha = $existing.sha }
      } catch {
        # 404 on first publish: the file does not exist yet, no sha needed.
      }
      Invoke-RestMethod -Uri "https://api.github.com/$apiPath" -Method PUT -Headers $apiHeaders -ContentType 'application/json' -Body ($body | ConvertTo-Json) -TimeoutSec 30 | Out-Null
      $published = $true
      Log 'published (api)'
    } catch {
      Log "api attempt $attempt failed: $($_.Exception.Message)"
      Start-Sleep -Seconds (3 * $attempt)
    }
  }
  if (-not $published) {
    try {
      $body = @{
        message = "chore(digitalmate): netmon summary $Machine"
        branch  = 'digitalmate'
        content = $b64
      }
      try {
        $existing = Invoke-RestMethod -Uri "https://api.github.com/$apiPath`?ref=digitalmate" -Headers $apiHeaders -Proxy 'http://127.0.0.1:7897' -TimeoutSec 60
        if ($existing -and $existing.sha) { $body.sha = $existing.sha }
      } catch {}
      Invoke-RestMethod -Uri "https://api.github.com/$apiPath" -Method PUT -Headers $apiHeaders -ContentType 'application/json' -Body ($body | ConvertTo-Json) -Proxy 'http://127.0.0.1:7897' -TimeoutSec 60 | Out-Null
      $published = $true
      Log 'published (api via local proxy)'
    } catch { Log ('api via proxy failed: ' + $_.Exception.Message) }
  }
  if (-not $published) { Log 'publish failed'; exit 1 }
} catch {
  Log ('unexpected error: ' + $_.Exception.Message)
  exit 1
}
