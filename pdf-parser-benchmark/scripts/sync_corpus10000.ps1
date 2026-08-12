param(
    [string]$ProjectRoot = "D:\CodeX\PaperDistill\pdf-parser-benchmark",
    [string]$RemoteHost = "zzx@10.201.29.159",
    [string]$RemoteRoot = "C:\Users\zzx\PaperDistillGPU\benchmark-v1"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectRoot
$python = "D:\Users\zzx\anaconda3\python.exe"
$ssh = "C:\Windows\System32\OpenSSH\ssh.exe"
$scp = "C:\Windows\System32\OpenSSH\scp.exe"
$log = Join-Path $ProjectRoot "logs\corpus10000-sync.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log), (Join-Path $ProjectRoot "exports\corpus10000-prod") | Out-Null

function Write-SyncLog([string]$Message) {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date).ToString("o"), $Message)
}

function Invoke-NativeLogged([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
    }
    return $LASTEXITCODE
}

function Invoke-RemotePowerShell([string]$ScriptText) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($ScriptText))
    & $ssh -o BatchMode=yes $RemoteHost powershell -NoProfile -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) { throw "Remote PowerShell failed with exit code $LASTEXITCODE" }
}

try {
    Write-SyncLog "sync_start"
    $fetchProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "fetch_corpus_pdfs_ncbi\.py" -and $_.ProcessId -ne $PID })
    if ($fetchProcesses.Count -gt 0) {
        Write-SyncLog ("recovery_skipped_fetch_active=" + $fetchProcesses.Count)
    }
    $recovery = @()
    if ($fetchProcesses.Count -eq 0) {
        $recovery = @(Get-Content -LiteralPath "corpus-10000\download-state.jsonl" -ErrorAction SilentlyContinue |
            ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.status -eq "error" -and $_.error -notmatch "package_contains_no_pdf" } |
            Select-Object -First 8)
    }
    if ($recovery.Count -gt 0) {
        $recoveryArgs = @(
            "scripts\fetch_corpus_pdfs_ncbi.py",
            "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
            "--root", "corpus-10000",
            "--workers", "1",
            "--batch-size", "8",
            "--timeout", "120",
            "--retries", "1",
            "--max-package-mib", "128"
        )
        foreach ($row in $recovery) { $recoveryArgs += @("--only-pmcid", [string]$row.pmcid) }
        $recoveryExit = Invoke-NativeLogged $python $recoveryArgs
        if ($recoveryExit -ne 0) { throw "recovery download failed with exit code $recoveryExit" }
        Write-SyncLog ("recovery_attempted=" + $recovery.Count)
    }
    $prepareArgs = @(
        "scripts\prepare_corpus_run.py",
        "--config", "config\benchmark.json",
        "--corpus-root", "corpus-10000",
        "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
        "--download-state", "corpus-10000\download-state.jsonl",
        "--source-overrides", "corpus-10000\source-overrides.jsonl",
        "--output", "data\corpus-10000-manifest.jsonl",
        "--transfer-manifest", "corpus-10000\transfer-manifest.jsonl",
        "--workers", "8", "--allow-partial"
    )
    $prepareExit = Invoke-NativeLogged $python $prepareArgs
    if ($prepareExit -ne 0) { throw "prepare_corpus_run failed with exit code $prepareExit" }

    $uploadArgs = @(
        "scripts\upload_corpus_batches.py",
        "--root", "corpus-10000",
        "--host", $RemoteHost,
        "--remote-root", ($RemoteRoot -replace '\\','/'),
        "--manifest", "corpus-10000\transfer-manifest.jsonl",
        "--batch-size", "250", "--timeout", "7200"
    )
    $uploadExit = Invoke-NativeLogged $python $uploadArgs
    if ($uploadExit -ne 0) { throw "upload_corpus_batches failed with exit code $uploadExit" }

    $manifestExit = Invoke-NativeLogged $scp @(
        "data\corpus-10000-manifest.jsonl",
        "${RemoteHost}:C:/Users/zzx/PaperDistillGPU/benchmark-v1/data/corpus-10000-manifest.jsonl"
    )
    if ($manifestExit -ne 0) { throw "manifest upload failed with exit code $manifestExit" }

    $stateScript = "(Get-ScheduledTask -TaskName 'PaperDistillCorpus10000').State.ToString()"
    $state = (Invoke-RemotePowerShell $stateScript | Where-Object { $_ -match '^(Ready|Running|Disabled|Queued)$' } | Select-Object -First 1).Trim()
    Write-SyncLog "remote_task_state=$state"
    # Collect terminal Markdown records even while the production task is
    # still running. The collector ignores the in-flight document and writes
    # completed records atomically; restart is reserved for the Ready state.
    if ($state -in @("Ready", "Running")) {
        $collectScript = @"
Set-Location -LiteralPath '$RemoteRoot'
& '.\envs\eval\Scripts\python.exe' '.\scripts\collect_corpus_markdown.py' --project-root . --manifest '.\data\corpus-10000-manifest.jsonl' --tool mineru --run-label corpus10000-prod --output '.\exports\corpus10000-prod\mineru'
tar.exe -cf '.\transfer\corpus10000-prod-export.tar' -C . 'exports\corpus10000-prod\mineru'
exit 0
"@
        Invoke-RemotePowerShell $collectScript | ForEach-Object {
            Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
        }
        $archive = Join-Path $ProjectRoot "corpus-10000\transfer\corpus10000-prod-export.tar"
        $returnExit = Invoke-NativeLogged $scp @(
            "${RemoteHost}:C:/Users/zzx/PaperDistillGPU/benchmark-v1/transfer/corpus10000-prod-export.tar",
            $archive
        )
        if ($returnExit -ne 0) { throw "Markdown return transfer failed with exit code $returnExit" }
        $extractExit = Invoke-NativeLogged "tar.exe" @("-xf", $archive, "-C", $ProjectRoot)
        if ($extractExit -ne 0) { throw "Markdown archive extraction failed with exit code $extractExit" }
        if ($state -eq "Ready") {
            $restartExit = Invoke-NativeLogged $ssh @("-o", "BatchMode=yes", $RemoteHost, "schtasks", "/Run", "/TN", "PaperDistillCorpus10000")
            if ($restartExit -ne 0) { throw "Remote production task restart failed with exit code $restartExit" }
            Write-SyncLog "remote_task_restarted"
        } else {
            Write-SyncLog "remote_task_still_running_no_restart"
        }
    }
    Write-SyncLog "sync_complete"
}
catch {
    Write-SyncLog ("sync_error=" + $_.Exception.Message)
    throw
}
