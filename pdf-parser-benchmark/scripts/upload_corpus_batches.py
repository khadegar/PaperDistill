from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_checked(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {command[0]}\n{completed.stdout}\n{completed.stderr}"
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload hashed PDF batches to the Windows A100 host with resume.")
    parser.add_argument("--root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--host", default="zzx@10.201.29.159")
    parser.add_argument("--remote-root", default="C:/Users/zzx/PaperDistillGPU/benchmark-v1")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--max-batches", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Hashed transfer manifest; defaults to ROOT/transfer-manifest.jsonl when present.",
    )
    args = parser.parse_args()
    if args.batch_size < 1 or args.max_batches < 0:
        parser.error("batch-size must be positive and max-batches non-negative")

    root = args.root.resolve()
    pdf_dir = root / "pdfs"
    transfer_dir = root / "transfer"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    transfer_manifest = args.manifest.resolve() if args.manifest else root / "transfer-manifest.jsonl"
    source_rows = read_jsonl(transfer_manifest) if transfer_manifest.is_file() else read_jsonl(root / "download-state.jsonl")
    upload_state_path = root / "upload-state.jsonl"
    uploaded = {
        str(row["pmcid"]): row
        for row in read_jsonl(upload_state_path)
        if row.get("status") == "verified" and row.get("pmcid")
    }
    candidates: list[dict[str, Any]] = []
    for row in source_rows:
        pmcid = str(row.get("pmcid") or "")
        path = Path(str(row.get("local_path") or pdf_dir / f"{pmcid}.pdf"))
        if not path.is_absolute():
            path = (root / path).resolve()
        if not transfer_manifest.is_file() and row.get("status") not in {"downloaded", "cached"}:
            continue
        if not path.is_file():
            continue
        if path.stat().st_size < 10240:
            continue
        if uploaded.get(pmcid, {}).get("pdf_sha256") == row.get("pdf_sha256"):
            continue
        candidates.append({**row, "_local_path": str(path)})

    batch_count = 0
    for start in range(0, len(candidates), args.batch_size):
        if args.max_batches and batch_count >= args.max_batches:
            break
        batch = candidates[start : start + args.batch_size]
        batch_id = hashlib.sha256(
            "\n".join(f"{row['pmcid']}:{row['pdf_sha256']}" for row in batch).encode("utf-8")
        ).hexdigest()[:16]
        archive = transfer_dir / f"pdf-batch-{batch_id}.tar"
        manifest = transfer_dir / f"pdf-batch-{batch_id}.jsonl"
        manifest_rows = [
            {
                "pmcid": str(row["pmcid"]),
                "filename": f"{row['pmcid']}.pdf",
                "pdf_sha256": str(row["pdf_sha256"]),
                "pdf_bytes": int(row["pdf_bytes"]),
                "_local_path": str(row["_local_path"]),
            }
            for row in batch
        ]
        write_jsonl(manifest, manifest_rows)
        with tarfile.open(archive, "w") as handle:
            for row in manifest_rows:
                handle.add(Path(row["_local_path"]), arcname=row["filename"], recursive=False)
        for row in manifest_rows:
            row.pop("_local_path", None)

        remote_transfer = args.remote_root.rstrip("/") + "/transfer/incoming"
        remote_inputs = args.remote_root.rstrip("/") + "/inputs/corpus10000"
        prepare = (
            f"$transfer='{remote_transfer}';$inputs='{remote_inputs}';"
            "New-Item -ItemType Directory -Force -Path $transfer,$inputs|Out-Null"
        )
        run_checked(
            ["ssh", "-o", "BatchMode=yes", args.host, "powershell", "-NoProfile", "-EncodedCommand", encoded_powershell(prepare)],
            120,
        )
        remote_archive = f"{remote_transfer}/{archive.name}"
        remote_manifest = f"{remote_transfer}/{manifest.name}"
        run_checked(["scp", str(archive), f"{args.host}:{remote_archive}"], args.timeout)
        run_checked(["scp", str(manifest), f"{args.host}:{remote_manifest}"], args.timeout)
        verify = f"""
$archive='{remote_archive}'
$manifest='{remote_manifest}'
$destination='{remote_inputs}'
tar.exe -xf $archive -C $destination
if ($LASTEXITCODE -ne 0) {{ exit 21 }}
$bad=@()
Get-Content -LiteralPath $manifest | ForEach-Object {{
  $row=$_ | ConvertFrom-Json
  $path=Join-Path $destination $row.filename
  if (-not (Test-Path -LiteralPath $path)) {{ $bad += "$($row.pmcid):missing" }}
  elseif ((Get-Item -LiteralPath $path).Length -ne [int64]$row.pdf_bytes) {{ $bad += "$($row.pmcid):size" }}
  elseif ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.pdf_sha256.ToLowerInvariant()) {{ $bad += "$($row.pmcid):hash" }}
}}
if ($bad.Count -gt 0) {{ $bad | ConvertTo-Json; exit 22 }}
Remove-Item -LiteralPath $archive,$manifest -Force
[pscustomobject]@{{status='verified';count={len(batch)};batch_id='{batch_id}'}} | ConvertTo-Json
"""
        completed = run_checked(
            ["ssh", "-o", "BatchMode=yes", args.host, "powershell", "-NoProfile", "-EncodedCommand", encoded_powershell(verify)],
            args.timeout,
        )
        verified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for row in manifest_rows:
            uploaded[row["pmcid"]] = {
                **row,
                "status": "verified",
                "batch_id": batch_id,
                "verified_at": verified_at,
            }
        write_jsonl(upload_state_path, list(uploaded.values()))
        archive.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        batch_count += 1
        print(
            json.dumps(
                {
                    "batch": batch_count,
                    "batch_id": batch_id,
                    "verified": len(batch),
                    "uploaded_total": len(uploaded),
                    "remote": json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else completed.stdout.strip(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
