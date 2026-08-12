from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PAGE_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE | re.IGNORECASE)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
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


def inspect_pdf(row: dict[str, Any], corpus_root: Path, pdfinfo: Path, verify_hash: bool) -> dict[str, Any]:
    pmcid = str(row.get("pmcid") or "").strip()
    source = corpus_root / str(row.get("pdf_relpath") or f"pdfs/{pmcid}.pdf")
    result = {"pmcid": pmcid, "source_pdf": str(source), "status": "ok"}
    if not pmcid or not source.is_file():
        return {**result, "status": "error", "error": "pdf_missing"}
    expected_hash = str(row.get("pdf_sha256") or "")
    actual_hash = sha256_file(source) if verify_hash or not expected_hash else expected_hash
    if expected_hash and actual_hash != expected_hash:
        return {**result, "status": "error", "error": "pdf_sha256_mismatch"}
    completed = subprocess.run(
        [str(pdfinfo), str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    match = PAGE_RE.search(completed.stdout)
    if completed.returncode != 0 or not match:
        return {
            **result,
            "status": "error",
            "error": "pdfinfo_failed",
            "pdfinfo_stderr": completed.stderr[-1000:],
        }
    page_count = int(match.group(1))
    if page_count < 1:
        return {**result, "status": "error", "error": "invalid_page_count"}
    return {
        **result,
        "pdf_sha256": actual_hash,
        "pdf_bytes": source.stat().st_size,
        "page_count": page_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify downloaded PDFs and build the deterministic A100 corpus run manifest."
    )
    parser.add_argument("--config", type=Path, default=Path("config/benchmark.json"))
    parser.add_argument("--corpus-root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--source-manifest", type=Path, default=Path("corpus-10000/manifest.jsonl"))
    parser.add_argument(
        "--download-state",
        type=Path,
        default=None,
        help="Optional resumable downloader state to overlay before PDF verification.",
    )
    parser.add_argument(
        "--source-overrides",
        type=Path,
        default=Path("corpus-10000/source-overrides.jsonl"),
        help="Optional traceable input overrides, for example a JATS-rendered PDF when a withdrawn PDF is unavailable.",
    )
    parser.add_argument("--output", type=Path, default=Path("data/corpus-10000-manifest.jsonl"))
    parser.add_argument("--transfer-manifest", type=Path, default=Path("corpus-10000/transfer-manifest.jsonl"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Verify only the first N ready rows in source-manifest order (for staged smoke runs).",
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--skip-hash-verification", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")

    config = read_json(args.config)
    pdfinfo = Path(str(config["pdf_tools"]["pdfinfo"]))
    if not pdfinfo.is_file():
        raise FileNotFoundError(pdfinfo)
    corpus_root = args.corpus_root.resolve()
    source_rows = read_jsonl(args.source_manifest)
    state_rows = read_jsonl(args.download_state) if args.download_state and args.download_state.is_file() else []
    download_state = {str(row.get("pmcid") or ""): row for row in state_rows}
    if len(download_state) != len(state_rows) or "" in download_state:
        raise ValueError(f"Invalid or duplicate PMCID in download state: {args.download_state}")
    override_rows = read_jsonl(args.source_overrides) if args.source_overrides.is_file() else []
    overrides = {str(row.get("pmcid") or ""): row for row in override_rows}
    if len(overrides) != len(override_rows) or "" in overrides:
        raise ValueError(f"Invalid or duplicate PMCID in source overrides: {args.source_overrides}")
    normalized_rows: list[dict[str, Any]] = []
    for original in source_rows:
        row = dict(original)
        state_row = download_state.get(str(row.get("pmcid") or ""))
        if state_row:
            row["pdf_status"] = state_row.get("status", "pending")
            row["pdf_sha256"] = state_row.get("pdf_sha256", "")
            state_relpath = str(state_row.get("pdf_relpath") or "")
            if state_relpath:
                row["pdf_relpath"] = (
                    state_relpath if state_relpath.startswith("pdfs/") else f"pdfs/{state_relpath}"
                )
            else:
                row["pdf_relpath"] = ""
            row["pdf_bytes"] = state_row.get("pdf_bytes", 0)
            row["pdf_error"] = state_row.get("error", "")
            row["pdf_source_url"] = state_row.get("source_url", "")
            row["pdf_acquisition_fallback"] = state_row.get("acquisition_fallback", "")
        override = overrides.get(str(row.get("pmcid") or ""))
        if override:
            row["pdf_status"] = "cached"
            row["pdf_sha256"] = str(override.get("pdf_sha256") or "")
            pdf_relpath = str(override.get("pdf_relpath") or "")
            prefix = f"{args.corpus_root.as_posix().rstrip('/')}/"
            if pdf_relpath.startswith(prefix):
                pdf_relpath = pdf_relpath[len(prefix) :]
            row["pdf_relpath"] = pdf_relpath or f"pdfs/{row.get('pmcid')}.pdf"
            row["source_override"] = override
        normalized_rows.append(row)
    source_rows = normalized_rows
    ready = [
        row
        for row in source_rows
        if row.get("pdf_status") in {"downloaded", "cached"} and row.get("pdf_sha256")
    ]
    if args.limit is not None:
        ready = ready[: args.limit]
    if not args.allow_partial and len(ready) != len(source_rows):
        raise RuntimeError(f"PDF corpus is incomplete: ready={len(ready)}, source={len(source_rows)}")

    inspections: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                inspect_pdf,
                row,
                corpus_root,
                pdfinfo,
                not args.skip_hash_verification,
            ): row
            for row in ready
        }
        for future in as_completed(futures):
            source_row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "pmcid": str(source_row.get("pmcid") or ""),
                    "source_pdf": str(corpus_root / str(source_row.get("pdf_relpath") or "")),
                    "status": "error",
                    "error": f"inspect_exception:{type(exc).__name__}:{exc}",
                }
            inspections[str(result.get("pmcid") or "")] = result

    errors = [row for row in inspections.values() if row.get("status") != "ok"]
    if errors and not args.allow_partial:
        raise RuntimeError(f"PDF verification failed for {len(errors)} files; first={errors[0]}")

    run_rows: list[dict[str, Any]] = []
    transfer_rows: list[dict[str, Any]] = []
    for source in source_rows:
        pmcid = str(source.get("pmcid") or "")
        checked = inspections.get(pmcid)
        if not checked or checked.get("status") != "ok":
            continue
        run_rows.append(
            {
                "sample_id": pmcid,
                "paper_id": str(source.get("paper_id") or pmcid),
                "pmcid": pmcid,
                "doi": str(source.get("doi") or ""),
                "title": str(source.get("title") or ""),
                "benchmark_stratum": "corpus10000",
                "discovery_strata": source.get("discovery_strata") or [],
                "page_count": checked["page_count"],
                "pdf_bytes": checked["pdf_bytes"],
                "pdf_sha256": checked["pdf_sha256"],
                "staged_pdf_relpath": f"inputs/corpus10000/{pmcid}.pdf",
                "input_kind": str((source.get("source_override") or {}).get("input_kind") or "publisher_pdf"),
                "publication_status": str((source.get("source_override") or {}).get("status") or "published"),
                "source_override": source.get("source_override"),
            }
        )
        transfer_rows.append(
            {
                "pmcid": pmcid,
                "local_path": checked["source_pdf"],
                "remote_relpath": f"inputs/corpus10000/{pmcid}.pdf",
                "pdf_sha256": checked["pdf_sha256"],
                "pdf_bytes": checked["pdf_bytes"],
                "input_kind": str((source.get("source_override") or {}).get("input_kind") or "publisher_pdf"),
            }
        )

    write_jsonl(args.output, run_rows)
    write_jsonl(args.transfer_manifest, transfer_rows)
    summary = {
        "status": "PASS" if not errors and len(run_rows) == len(source_rows) else "PARTIAL",
        "source_rows": len(source_rows),
        "ready_rows": len(ready),
        "run_rows": len(run_rows),
        "errors": errors,
        "run_manifest": str(args.output.resolve()),
        "run_manifest_sha256": sha256_file(args.output),
        "transfer_manifest": str(args.transfer_manifest.resolve()),
        "transfer_manifest_sha256": sha256_file(args.transfer_manifest),
        "pdf_bytes": sum(int(row["pdf_bytes"]) for row in run_rows),
        "page_count": sum(int(row["page_count"]) for row in run_rows),
        "source_overrides": len(override_rows),
        "download_state_rows": len(download_state),
        "requested_limit": args.limit,
    }
    write_json(args.transfer_manifest.with_name("prepare-summary.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS" or args.allow_partial else 2


if __name__ == "__main__":
    raise SystemExit(main())
