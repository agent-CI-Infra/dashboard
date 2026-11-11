param(
    [Parameter(Mandatory = $true)][string]$Kubeconfig,
    [Parameter(Mandatory = $true)][string]$TokenFile,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Kubectl,
    [Parameter(Mandatory = $true)][string]$GitDirectory,
    [Parameter(Mandatory = $true)][string]$GhDirectory
)
$ErrorActionPreference = 'Stop'
$tunnel = $null
Start-Transcript -Path (Join-Path $PSScriptRoot 'collector.log') -Append | Out-Null
$env:KUBECONFIG = $Kubeconfig
$env:KUBECTL = $Kubectl
$env:PATH = "$GitDirectory;$GhDirectory;$env:PATH"
$env:ADX_BASE = 'https://onlineswarm.openjiuwen.com:31472'
try {
    $secure = (Get-Content -LiteralPath $TokenFile -Raw).Trim() | ConvertTo-SecureString
    $env:ADX_TOKEN = (New-Object System.Net.NetworkCredential('', $secure)).Password
    if (-not $env:ADX_TOKEN) { throw 'Management credential is unavailable' }
    if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18769 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Collector proxy port is already in use'
    }
    $tunnel = Start-Process -FilePath 'ssh.exe' -WindowStyle Hidden -PassThru -ArgumentList @(
        '-N', '-D', '127.0.0.1:18769', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=15', 'hk-leihui'
    )
    $connected = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Seconds 1
        if ($tunnel.HasExited) { throw 'Hong Kong proxy exited before connecting' }
        & curl.exe --silent --output NUL --max-time 3 --socks5-hostname 127.0.0.1:18769 --noproxy '127.0.0.2' https://api.github.com
        if ($LASTEXITCODE -eq 0) { $connected = $true; break }
    }
    if (-not $connected) { throw 'Hong Kong proxy could not be verified' }
    & $Python -B (Join-Path $PSScriptRoot 'publish-local-kube-metrics.py')
    if ($LASTEXITCODE -ne 0) { throw 'Kubernetes collection or publication failed' }
} finally {
    Remove-Item Env:ADX_TOKEN -ErrorAction SilentlyContinue
    if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue }
    Stop-Transcript | Out-Null
}
