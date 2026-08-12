param(
    [string]$ProjectRoot = "D:\CodeX\PaperDistill\pdf-parser-benchmark",
    [string]$RemoteHost = "zzx@10.201.29.159",
    [string]$RemoteRoot = "C:\Users\zzx\PaperDistillGPU\benchmark-v1",
    [int]$IntervalSeconds = 300,
    [int]$MaxRetryRounds = 2
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectRoot
$ssh = "C:\Windows\System32\OpenSSH\ssh.exe"
$scp = "C:\Windows\System32\OpenSSH\scp.exe"
$archive = Join-Path $ProjectRoot "transfer\corpus10000-prod-export.tar"
$incrementArchive = Join-Path $ProjectRoot "transfer\corpus10000-prod-increment.tar"
$incrementList = Join-Path $ProjectRoot "transfer\corpus10000-prod-increment.lst"
$remoteIndexLocal = Join-Path $ProjectRoot "transfer\corpus10000-prod-remote-index.jsonl"
$log = Join-Path $ProjectRoot "logs\corpus10000-markdown-watcher.log"
$exportRoot = Join-Path $ProjectRoot "exports\corpus10000-prod\mineru"
New-Item -ItemType Directory -Force -Path (Split-Path $archive), (Split-Path $log), $exportRoot | Out-Null

function Write-WatchLog([string]$Message) {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date).ToString("o"), $Message)
}

function Collect-RemoteMarkdown {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(@"
Set-Location -LiteralPath '$RemoteRoot'
& '.\envs\eval\Scripts\python.exe' '.\scripts\collect_corpus_markdown.py' --project-root . --manifest '.\data\corpus-10000-manifest.jsonl' --tool mineru --run-label corpus10000-prod --output '.\exports\corpus10000-prod\mineru'
`$collectorExit = `$LASTEXITCODE
Write-Output ('collector_exit=' + `$collectorExit)
if (`$collectorExit -ne 0 -and `$collectorExit -ne 2) { exit `$collectorExit }
exit 0
"@))
    & $ssh -o BatchMode=yes $RemoteHost powershell -NoProfile -EncodedCommand $encoded 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw "remote collect failed ($LASTEXITCODE)" }
    & $scp -o BatchMode=yes "${RemoteHost}:$($RemoteRoot -replace '\\','/')/exports/corpus10000-prod/mineru/index.jsonl" $remoteIndexLocal 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw "remote index download failed ($LASTEXITCODE)" }

    $localRows = @{}
    $localIndex = Join-Path $exportRoot "index.jsonl"
    if (Test-Path -LiteralPath $localIndex) {
        foreach ($line in Get-Content -LiteralPath $localIndex -Encoding UTF8) {
            if (-not $line.Trim()) { continue }
            try { $row = $line | ConvertFrom-Json; $localRows[[string]$row.pmcid] = $row } catch { }
        }
    }
    $remoteRows = @(Get-Content -LiteralPath $remoteIndexLocal -Encoding UTF8 |
        Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    $transferPaths = New-Object System.Collections.Generic.List[string]
    foreach ($row in $remoteRows) {
        $localRow = $localRows[[string]$row.pmcid]
        foreach ($property in @('primary_markdown_relpath','fallback_markdown_relpath','preferred_markdown_relpath')) {
            $rel = [string]$row.$property
            if (-not $rel -or $rel.Contains('..') -or [IO.Path]::IsPathRooted($rel)) { continue }
            $hashProperty = $property -replace '_relpath','_sha256'
            $target = Join-Path $exportRoot ($rel -replace '/','\')
            $same = $false
            if ($localRow -and (Test-Path -LiteralPath $target) -and [string]$localRow.$hashProperty -eq [string]$row.$hashProperty) {
                $same = $true
            }
            if (-not $same) { [void]$transferPaths.Add($rel) }
        }
    }

    $transferPaths = @($transferPaths | Sort-Object -Unique)
    $batchSize = 200
    for ($offset = 0; $offset -lt $transferPaths.Count; $offset += $batchSize) {
        $batch = @($transferPaths | Select-Object -Skip $offset -First $batchSize)
        $batch | Set-Content -LiteralPath $incrementList -Encoding UTF8
        $remoteList = "$(($RemoteRoot -replace '\\','/'))/transfer/corpus10000-prod-increment.lst"
        & $scp -o BatchMode=yes $incrementList "${RemoteHost}:$remoteList" 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "increment list upload failed ($LASTEXITCODE)" }
        $remoteScript = @"
Set-Location -LiteralPath '$RemoteRoot'
`$paths = Get-Content -LiteralPath '$remoteList' | Where-Object { `$_ -and `$_ -notmatch '\.\.' }
& tar.exe -cf '.\transfer\corpus10000-prod-increment.tar' -C '.\exports\corpus10000-prod\mineru' `$paths
if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }
"@
        $encodedBatch = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))
        & $ssh -o BatchMode=yes $RemoteHost powershell -NoProfile -EncodedCommand $encodedBatch 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "remote increment archive failed ($LASTEXITCODE)" }
        & $scp -o BatchMode=yes "${RemoteHost}:$($RemoteRoot -replace '\\','/')/transfer/corpus10000-prod-increment.tar" $incrementArchive 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "increment archive download failed ($LASTEXITCODE)" }
        & tar.exe -xf $incrementArchive -C $exportRoot 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "increment archive extraction failed ($LASTEXITCODE)" }
    }
    Copy-Item -LiteralPath $remoteIndexLocal -Destination (Join-Path $exportRoot 'index.jsonl') -Force
    $remoteSummary = Join-Path $ProjectRoot "transfer\corpus10000-prod-remote-summary.json"
    & $scp -o BatchMode=yes "${RemoteHost}:$($RemoteRoot -replace '\\','/')/exports/corpus10000-prod/mineru/summary.json" $remoteSummary 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
    if ($LASTEXITCODE -eq 0) { Copy-Item -LiteralPath $remoteSummary -Destination (Join-Path $exportRoot 'summary.json') -Force }
    Write-WatchLog ("increment_sync_paths={0}" -f $transferPaths.Count)
}

function Get-MissingMarkdownIds {
    $index = Join-Path $exportRoot "index.jsonl"
    if (-not (Test-Path -LiteralPath $index)) { return @() }
    $missing = @()
    foreach ($line in Get-Content -LiteralPath $index -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $row = $line | ConvertFrom-Json
        if (-not [string]$row.preferred_markdown_relpath) {
            $missing += [string]$row.pmcid
        }
    }
    return @($missing | Where-Object { $_ } | Sort-Object -Unique)
}

function Start-TargetedRetry([string[]]$Ids, [int]$Round) {
    if ($Ids.Count -eq 0) { return }
    $retryFile = Join-Path $ProjectRoot ("data\corpus10000-retry-{0}.txt" -f $Round)
    $Ids | Set-Content -LiteralPath $retryFile -Encoding UTF8
    $remoteRetry = "$RemoteRoot/data/corpus10000-retry-$Round.txt"
    & $scp -o BatchMode=yes $retryFile "${RemoteHost}:$($remoteRetry -replace '\\','/')" 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw "retry list upload failed ($LASTEXITCODE)" }
    $retryScript = @"
Set-Location -LiteralPath '$RemoteRoot'
`$ids = Get-Content -LiteralPath '$remoteRetry' | Where-Object { `$_ -and `$_ -notmatch '^#' }
`$args = @('--config','config/benchmark.json','--run-label','corpus10000-prod','--no-resume')
foreach (`$id in `$ids) { `$args += @('--sample-id',[string]`$id) }
`$out = 'logs/corpus10000-retry-$Round.stdout.log'
`$err = 'logs/corpus10000-retry-$Round.stderr.log'
`$p = Start-Process -FilePath 'envs/eval/Scripts/python.exe' -ArgumentList `$args -WorkingDirectory '$RemoteRoot' -RedirectStandardOutput `$out -RedirectStandardError `$err -WindowStyle Hidden -Wait -PassThru
Write-Output ('retry_exit=' + `$p.ExitCode + ';retry_count=' + `$ids.Count)
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($retryScript))
    Write-WatchLog ("retry_start_round={0};count={1}" -f $Round, $Ids.Count)
    & $ssh -o BatchMode=yes $RemoteHost powershell -NoProfile -EncodedCommand $encoded 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw "targeted retry failed ($LASTEXITCODE)" }
}

$retryRound = 0
while ($true) {
    try {
        Collect-RemoteMarkdown
        $progressText = & $ssh -o BatchMode=yes $RemoteHost "cmd /c type $RemoteRoot\runs\batch-corpus10000-prod-corpus10000-service.json" 2>&1
        $progressText | ForEach-Object { Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_) }
        $progress = $null
        try { $progress = ($progressText -join "`n" | ConvertFrom-Json) } catch { }
        if ($progress -and $progress.status -eq "COMPLETE") {
            $missing = @(Get-MissingMarkdownIds)
            if ($missing.Count -eq 0) {
                Write-WatchLog "remote_complete_all_markdown"
                break
            }
            if ($retryRound -ge $MaxRetryRounds) {
                Write-WatchLog ("remote_complete_with_missing={0}" -f $missing.Count)
                break
            }
            $retryRound++
            Start-TargetedRetry -Ids $missing -Round $retryRound
            continue
        }
        Start-Sleep -Seconds ([Math]::Max(30, $IntervalSeconds))
    }
    catch {
        # A transient SSH/SCP disconnect must not permanently stop a long
        # corpus transfer. Preserve local state and retry the next iteration.
        Write-WatchLog ("watcher_iteration_error=" + $_.Exception.Message)
        Start-Sleep -Seconds ([Math]::Max(30, [Math]::Min(60, $IntervalSeconds)))
    }
}
