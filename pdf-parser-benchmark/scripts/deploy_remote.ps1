[CmdletBinding()]
param(
    [string]$HostName = '10.201.29.159',
    [string]$RemoteUser = 'zzx',
    [string]$RemoteRoot = 'C:/Users/zzx/PaperDistillGPU/benchmark-v1'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Config = Join-Path $ProjectRoot 'config\benchmark.json'
python (Join-Path $ProjectRoot 'scripts\pdfbench.py') bundle-manifest --config $Config
$RemoteRootWindows = $RemoteRoot.Replace('/','\')
$CreateRootCommand = "powershell -NoProfile -Command `"[void][System.IO.Directory]::CreateDirectory('$RemoteRootWindows')`""
& ssh "$RemoteUser@$HostName" $CreateRootCommand
if ($LASTEXITCODE -ne 0) { throw "Remote project directory creation failed ($LASTEXITCODE)" }
$Target = "${RemoteUser}@${HostName}:$RemoteRoot/"
scp -r (Join-Path $ProjectRoot 'README.md') (Join-Path $ProjectRoot 'config') (Join-Path $ProjectRoot 'scripts') (Join-Path $ProjectRoot 'server') (Join-Path $ProjectRoot 'src') (Join-Path $ProjectRoot 'tests') (Join-Path $ProjectRoot 'data') (Join-Path $ProjectRoot 'inputs') (Join-Path $ProjectRoot 'ground-truth') $Target
if ($LASTEXITCODE -ne 0) { throw "Project transfer failed ($LASTEXITCODE)" }
$RemoteOffline = "$RemoteRoot/offline"
$RemoteOfflineWindows = $RemoteOffline.Replace('/','\')
$CreateOfflineCommand = "powershell -NoProfile -Command `"[void][System.IO.Directory]::CreateDirectory('$RemoteOfflineWindows')`""
& ssh "$RemoteUser@$HostName" $CreateOfflineCommand
if ($LASTEXITCODE -ne 0) { throw "Remote offline directory creation failed ($LASTEXITCODE)" }
$OfflineTarget = "${RemoteUser}@${HostName}:$RemoteOffline/"
scp -r (Join-Path $ProjectRoot 'offline\wheels') (Join-Path $ProjectRoot 'offline\models') (Join-Path $ProjectRoot 'offline\locks') (Join-Path $ProjectRoot 'offline\python') (Join-Path $ProjectRoot 'offline\bundle-manifest.jsonl') (Join-Path $ProjectRoot 'offline\bundle-summary.json') $OfflineTarget
if ($LASTEXITCODE -ne 0) { throw "Offline bundle transfer failed ($LASTEXITCODE)" }
