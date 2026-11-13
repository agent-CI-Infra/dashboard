param([Parameter(Mandatory = $true)][string]$Machine)
# Summarize C:\ProgramData\dsh\netmon.csv (written by DigitalMateNetMon) and
# publish a compact JSON to site/data/netmon/<Machine>.json on the
# agent-CI-Infra/dashboard `digitalmate` branch. Pushes use the stored git
# credential (Credential Manager) so no token lives in this script.
$ErrorActionPreference = 'Stop'

# Direct network: the local proxy is slower than direct for GitHub.
$env:HTTP_PROXY = ''; $env:HTTPS_PROXY = ''; $env:ALL_PROXY = ''
$env:http_proxy = ''; $env:https_proxy = ''; $env:all_proxy = ''
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

$csvPath = 'C:\ProgramData\dsh\netmon.csv'
$work = 'C:\ProgramData\dsh\dashboard-data'
$repo = 'https://github.com/agent-CI-Infra/dashboard.git'
$logPath = 'C:\ProgramData\dsh\netmon-push.log'

function Log([string]$m) {
  $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Machine, $m
  Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
  if ((Get-Item $logPath -ErrorAction SilentlyContinue).Length -gt 1MB) {
    $tail = Get-Content $logPath -Tail 200
    Set-Content $logPath $tail -Encoding UTF8
  }
}

# ---------- 1. summarize the netmon samples ----------
# CSV columns: time,adapter,rx_bps,tx_bps,workers,proxy7897 (one row per adapter per 5s sample)
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

# ---------- 2. publish to the dashboard branch ----------
if (-not (Test-Path (Join-Path $work '.git'))) {
  git clone --depth 1 -b digitalmate $repo $work 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Log 'clone failed'; exit 1 }
  git -C $work config user.name 'digitalmate-netmon'
  git -C $work config user.email 'digitalmate-netmon@users.noreply.github.com'
}

$rel = "site/data/netmon/$Machine.json"
$pushed = $false
for ($attempt = 1; $attempt -le 3 -and -not $pushed; $attempt++) {
  git -C $work fetch --depth 1 origin digitalmate 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Log "fetch failed (attempt $attempt)"; continue }
  git -C $work reset --hard FETCH_HEAD 2>&1 | Out-Null
  New-Item -ItemType Directory -Path (Split-Path (Join-Path $work $rel)) -Force | Out-Null
  $out | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $work $rel) -Encoding ascii
  git -C $work add $rel
  git -C $work diff --cached --quiet -- $rel
  $hasChanges = ($LASTEXITCODE -ne 0)
  if (-not $hasChanges) { $pushed = $true; Log 'no changes'; continue }
  git -C $work commit -m "chore(digitalmate): netmon summary $Machine" 2>&1 | Out-Null
  git -C $work push origin HEAD:digitalmate 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) { $pushed = $true; Log 'published' }
  else { Log "push failed (attempt $attempt), retrying with fresh state" }
}
if (-not $pushed) { Log 'push failed after 3 attempts'; exit 1 }
