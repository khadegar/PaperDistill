param(
    [string]$ProjectRoot = "D:\CodeX\PaperDistill\pdf-parser-benchmark"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectRoot
$python = "D:\Users\zzx\anaconda3\python.exe"
$log = Join-Path $ProjectRoot "logs\corpus10000-error-recovery.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-RecoveryLog([string]$Message) {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date).ToString("o"), $Message)
}

function Main-DownloaderActive {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "fetch_corpus_pdfs_ncbi\.py" }
    ).Count -gt 0
}

function Repair-WatcherActive {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "powershell.exe" -and $_.CommandLine -match "post_download_repair\.ps1" }
    ).Count -gt 0
}

function Sync-Active {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "powershell.exe" -and $_.CommandLine -match "sync_corpus10000\.ps1" }
    ).Count -gt 0
}

try {
    Write-RecoveryLog "recovery_watcher_start"
    while ((Main-DownloaderActive) -or (Repair-WatcherActive) -or (Sync-Active)) {
        Write-RecoveryLog ("waiting_for_prior_jobs downloader={0} repair={1} sync={2}" -f (Main-DownloaderActive), (Repair-WatcherActive), (Sync-Active))
        Start-Sleep -Seconds 60
    }

    $retryArgs = @(
        "scripts\fetch_corpus_pdfs_ncbi.py",
        "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
        "--root", "corpus-10000",
        "--workers", "1", "--batch-size", "10",
        "--timeout", "120", "--retries", "2",
        "--max-package-mib", "128",
        "--fallback-europepmc"
    )
    Write-RecoveryLog "retry_all_error_rows"
    & $python @retryArgs 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
    }

    $renderArgs = @(
        "scripts\render_jats_pdf_surrogates.py",
        "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
        "--download-state", "corpus-10000\download-state.jsonl",
        "--jats-root", "..\biomechanics-corpus\raw\jats",
        "--corpus-root", "corpus-10000",
        "--overrides", "corpus-10000\source-overrides.jsonl",
        "--overwrite"
    )
    Write-RecoveryLog "render_remaining_jats_candidates"
    & $python @renderArgs 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
    }

    Write-RecoveryLog "triggering_incremental_sync"
    Start-ScheduledTask -TaskName PaperDistillCorpus10000Sync
    Write-RecoveryLog "recovery_watcher_complete"
}
catch {
    Write-RecoveryLog ("recovery_error=" + $_.Exception.Message)
    exit 1
}
