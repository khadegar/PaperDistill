param(
    [string]$ProjectRoot = "D:\CodeX\PaperDistill\pdf-parser-benchmark"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectRoot
$python = "D:\Users\zzx\anaconda3\python.exe"
$pdfinfo = "E:\tool\texlive\2022\bin\win32\pdfinfo.exe"
$log = Join-Path $ProjectRoot "logs\corpus10000-post-repair.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-RepairLog([string]$Message) {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date).ToString("o"), $Message)
}

function Fetch-Active {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "fetch_corpus_pdfs_ncbi\.py" }
    ).Count -gt 0
}

try {
    Write-RepairLog "watcher_start"
    while (Fetch-Active) {
        Write-RepairLog "waiting_for_main_downloader"
        Start-Sleep -Seconds 60
    }

    $retryArgs = @(
        "scripts\fetch_corpus_pdfs_ncbi.py",
        "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
        "--root", "corpus-10000",
        "--workers", "1", "--batch-size", "3",
        "--timeout", "120", "--retries", "2",
        "--max-package-mib", "128",
        "--only-pmcid", "PMC5736202",
        "--only-pmcid", "PMC5216739",
        "--only-pmcid", "PMC11630650"
    )
    Write-RepairLog "retry_corrupt_pdf_candidates"
    & $python @retryArgs 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
    }

    $bad = @()
    foreach ($pmcid in @("PMC5736202", "PMC5216739", "PMC11630650")) {
        $pdf = Join-Path $ProjectRoot "corpus-10000\pdfs\$pmcid.pdf"
        # pdfinfo writes parser errors to stderr for malformed PDFs. With
        # ErrorActionPreference=Stop, a native stderr record can abort the
        # watcher before the JATS surrogate fallback is reached. Treat every
        # probe failure as a repair candidate and continue through the loop.
        $probe = @()
        $probeExit = 1
        try {
            $probe = @(& $pdfinfo $pdf 2>&1)
            $probeExit = $LASTEXITCODE
        }
        catch {
            $probeExit = 1
            $probe = @($_.Exception.Message)
        }
        if (($probeExit -ne 0) -or (-not ($probe -match "^Pages:\s*\d+"))) {
            $bad += $pmcid
        }
    }
    if ($bad.Count -gt 0) {
        $renderArgs = @(
            "scripts\render_jats_pdf_surrogates.py",
            "--source-manifest", "..\biomechanics-corpus\manifest.jsonl",
            "--download-state", "corpus-10000\download-state.jsonl",
            "--jats-root", "..\biomechanics-corpus\raw\jats",
            "--corpus-root", "corpus-10000",
            "--overrides", "corpus-10000\source-overrides.jsonl",
            "--overwrite"
        )
        foreach ($pmcid in $bad) { $renderArgs += @("--pmcid", $pmcid) }
        Write-RepairLog ("jats_repair=" + ($bad -join ","))
        & $python @renderArgs 2>&1 | ForEach-Object {
            Add-Content -LiteralPath $log -Encoding UTF8 -Value ([string]$_)
        }
    } else {
        Write-RepairLog "all_corrupt_candidates_repaired_by_redownload"
    }

    Write-RepairLog "triggering_incremental_sync"
    Start-ScheduledTask -TaskName PaperDistillCorpus10000Sync
    Write-RepairLog "watcher_complete"
}
catch {
    Write-RepairLog ("repair_error=" + $_.Exception.Message)
    exit 1
}
