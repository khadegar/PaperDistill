from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fetch_corpus_pdfs import _default_proxy, _download_one, _read_jsonl, _sha256_and_size, _write_json, _write_jsonl


DEFAULT_FILE_LIST_URL = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_file_list.csv"
DEFAULT_DOWNLOAD_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def opener_for(proxy: str):
    return build_opener(ProxyHandler({"http": proxy, "https": proxy})) if proxy else build_opener()


def materialize_file_list(url: str, target: Path, proxy: str, timeout: int, retries: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = opener_for(proxy)
    head = opener.open(Request(url, method="HEAD"), timeout=timeout)
    total = int(head.headers.get("Content-Length") or 0)
    head.close()
    if total <= 0:
        raise RuntimeError("NCBI OA file list did not provide Content-Length")
    if target.is_file() and target.stat().st_size > total:
        target.unlink()
    failure_count = 0
    while not target.is_file() or target.stat().st_size < total:
        offset = target.stat().st_size if target.is_file() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = Request(url, headers=headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                append = offset > 0 and response.status == 206
                if offset > 0 and not append:
                    offset = 0
                with target.open("ab" if append else "wb") as handle:
                    while True:
                        block = response.read(4 * 1024 * 1024)
                        if not block:
                            break
                        handle.write(block)
            failure_count = 0
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failure_count += 1
            if failure_count > retries:
                raise RuntimeError(
                    f"Failed to materialize NCBI OA file list at {target.stat().st_size if target.exists() else 0}/{total}: {exc}"
                ) from exc
            time.sleep(min(5.0 * failure_count, 30.0))
        current = target.stat().st_size if target.is_file() else 0
        print(json.dumps({"phase": "oa_file_list", "bytes": current, "total": total}), flush=True)
    if target.stat().st_size != total:
        raise RuntimeError(f"NCBI OA file-list size mismatch: {target.stat().st_size} != {total}")


def load_package_map(file_list: Path, target_pmcids: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    with file_list.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pmcid = str(row.get("Accession ID") or "").strip()
            if pmcid in target_pmcids:
                found[pmcid] = str(row.get("File") or "").strip()
                if len(found) == len(target_pmcids):
                    break
    return found


def cached_package_map(root: Path, file_list: Path, target_pmcids: set[str]) -> dict[str, str]:
    cache_path = root / "ncbi-package-map.jsonl"
    metadata_path = root / "ncbi-package-map.json"
    target_digest = hashlib.sha256("\n".join(sorted(target_pmcids)).encode("utf-8")).hexdigest()
    expected = {
        "file_list_bytes": file_list.stat().st_size,
        "file_list_mtime_ns": file_list.stat().st_mtime_ns,
        "target_count": len(target_pmcids),
        "target_sha256": target_digest,
    }
    if cache_path.is_file() and metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if all(metadata.get(key) == value for key, value in expected.items()):
                rows = _read_jsonl(cache_path)
                return {str(row["pmcid"]): str(row["package_relpath"]) for row in rows}
        except Exception:
            pass
    found = load_package_map(file_list, target_pmcids)
    _write_jsonl(
        cache_path,
        [{"pmcid": pmcid, "package_relpath": found[pmcid]} for pmcid in sorted(found)],
    )
    _write_json(metadata_path, {**expected, "found": len(found)})
    return found


def write_corpus_manifest(path: Path, source_rows: list[dict[str, Any]], state: dict[str, dict[str, Any]]) -> None:
    output_rows: list[dict[str, Any]] = []
    for row in source_rows:
        pmcid = str(row.get("pmcid") or "")
        result = state.get(pmcid, {})
        output_rows.append(
            {
                **row,
                "pdf_status": result.get("status", "pending"),
                "pdf_relpath": f"pdfs/{pmcid}.pdf" if result.get("pdf_sha256") else "",
                "pdf_sha256": result.get("pdf_sha256", ""),
                "pdf_bytes": result.get("pdf_bytes", 0),
                "pdf_error": result.get("error", ""),
                "pdf_source_url": result.get("source_url", ""),
                "pdf_acquisition_fallback": result.get("acquisition_fallback", ""),
            }
        )
    _write_jsonl(path, output_rows)


def choose_article_pdf(members: list[tarfile.TarInfo], pmcid: str) -> tarfile.TarInfo | None:
    pdfs = [member for member in members if member.isfile() and member.name.lower().endswith(".pdf")]
    if not pdfs:
        return None

    def rank(member: tarfile.TarInfo) -> tuple[int, int, int]:
        name = Path(member.name).name.lower()
        supplemental = int(any(token in name for token in ("supp", "appendix", "additional", "mmc")))
        contains_pmcid = int(pmcid.lower() in name)
        return (-supplemental, contains_pmcid, member.size)

    return max(pdfs, key=rank)


def download_package_pdf(
    row: dict[str, Any],
    package_relpath: str,
    pdf_dir: Path,
    package_base: str,
    proxy: str,
    timeout: int,
    retries: int,
    user_agent: str,
    max_package_bytes: int,
) -> dict[str, Any]:
    pmcid = str(row.get("pmcid") or "").strip()
    output = pdf_dir / f"{pmcid}.pdf"
    if output.is_file():
        digest, size, prefix = _sha256_and_size(output)
        if prefix.startswith(b"%PDF") and size >= 10240:
            return {"pmcid": pmcid, "status": "cached", "pdf_sha256": digest, "pdf_bytes": size}
        output.unlink(missing_ok=True)
    if not package_relpath:
        return {"pmcid": pmcid, "status": "error", "error": "ncbi_package_not_listed"}

    url = package_base.rstrip("/") + "/" + package_relpath.lstrip("/")
    opener = opener_for(proxy)
    last_error = ""
    for attempt in range(1, retries + 2):
        fd, name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".tar.gz", dir=pdf_dir)
        os.close(fd)
        archive_path = Path(name)
        pdf_fd, pdf_name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".pdf.part", dir=pdf_dir)
        os.close(pdf_fd)
        pdf_temp = Path(pdf_name)
        try:
            started = time.monotonic()
            request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/gzip"})
            with opener.open(request, timeout=timeout) as response, archive_path.open("wb") as handle:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    handle.write(block)
                    if max_package_bytes and handle.tell() > max_package_bytes:
                        raise ValueError(f"package_exceeds_limit:{handle.tell()}>{max_package_bytes}")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(f"package download exceeded total timeout of {timeout}s")
            package_sha256 = sha256_file(archive_path)
            with tarfile.open(archive_path, "r:gz") as archive:
                member = choose_article_pdf(archive.getmembers(), pmcid)
                if member is None:
                    raise ValueError("package_contains_no_pdf")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("package_pdf_not_readable")
                with source, pdf_temp.open("wb") as handle:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        handle.write(block)
            digest, size, prefix = _sha256_and_size(pdf_temp)
            if not prefix.startswith(b"%PDF") or size < 10240:
                raise ValueError(f"invalid_extracted_pdf:{size}")
            os.replace(pdf_temp, output)
            archive_path.unlink(missing_ok=True)
            return {
                "pmcid": pmcid,
                "status": "downloaded",
                "pdf_relpath": output.name,
                "pdf_sha256": digest,
                "pdf_bytes": size,
                "source_url": url,
                "source_package_relpath": package_relpath,
                "source_package_sha256": package_sha256,
                "source_package_member": member.name,
                "attempts": attempt,
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, EOFError, tarfile.TarError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            archive_path.unlink(missing_ok=True)
            pdf_temp.unlink(missing_ok=True)
            if isinstance(exc, ValueError) and str(exc).startswith("package_exceeds_limit:"):
                break
            if attempt > retries:
                break
            delay = min(10.0 * attempt, 60.0) if isinstance(exc, HTTPError) and exc.code == 429 else min(2.0 ** (attempt - 1), 15.0)
            time.sleep(delay)
    return {"pmcid": pmcid, "status": "error", "error": last_error, "source_url": url}


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize corpus PDFs through official NCBI OA packages.")
    parser.add_argument("--source-manifest", type=Path, default=Path("../biomechanics-corpus/manifest.jsonl"))
    parser.add_argument("--root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--file-list-url", default=DEFAULT_FILE_LIST_URL)
    parser.add_argument(
        "--refresh-file-list",
        action="store_true",
        help="Revalidate/redownload the NCBI OA file list instead of trusting the existing local snapshot.",
    )
    parser.add_argument("--package-base", default=DEFAULT_DOWNLOAD_BASE)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument(
        "--max-package-mib",
        type=int,
        default=256,
        help="Abort an oversized OA archive and use the Europe PMC PDF fallback instead (0 disables).",
    )
    parser.add_argument("--proxy", default=_default_proxy())
    parser.add_argument("--fallback-europepmc", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--skip-pmcid",
        action="append",
        default=[],
        help="Temporarily exclude a pathological PMCID without changing the corpus manifest (repeatable).",
    )
    parser.add_argument(
        "--only-pmcid",
        action="append",
        default=[],
        help="Restrict this invocation to selected PMCID values for low-concurrency recovery (repeatable).",
    )
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1 or args.offset < 0 or args.retries < 0:
        parser.error("workers and batch-size must be positive; offset and retries must be non-negative")

    root = args.root.resolve()
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    source_rows = _read_jsonl(args.source_manifest)
    skipped_pmcids = {str(value).strip() for value in args.skip_pmcid if str(value).strip()}
    only_pmcids = {str(value).strip() for value in args.only_pmcid if str(value).strip()}
    selected = [
        row
        for row in source_rows[args.offset : (args.offset + args.limit if args.limit else None)]
        if str(row.get("pmcid") or "") not in skipped_pmcids
        and (not only_pmcids or str(row.get("pmcid") or "") in only_pmcids)
    ]
    targets = {str(row.get("pmcid") or "") for row in selected if row.get("pmcid")}
    file_list = root / "ncbi-oa-file-list.csv"
    if args.refresh_file_list or not file_list.is_file():
        materialize_file_list(args.file_list_url, file_list, args.proxy, args.timeout, args.retries)
    package_map = cached_package_map(root, file_list, targets)
    print(json.dumps({"phase": "package_map", "targets": len(targets), "found": len(package_map)}), flush=True)

    state_path = root / "download-state.jsonl"
    state: dict[str, dict[str, Any]] = {}
    if state_path.is_file():
        for row in _read_jsonl(state_path):
            if row.get("pmcid"):
                state[str(row["pmcid"])] = row
    candidates: list[dict[str, Any]] = []
    for row in selected:
        pmcid = str(row.get("pmcid") or "")
        prior = state.get(pmcid, {})
        if prior.get("status") not in {"downloaded", "cached"} or not (pdf_dir / f"{pmcid}.pdf").is_file():
            candidates.append(row)

    user_agent = "PaperDistill-PDFBenchmark/1.0 (NCBI OA package acquisition)"
    completed = downloaded = cached = errors = fallback_success = 0
    for start in range(0, len(candidates), args.batch_size):
        batch = candidates[start : start + args.batch_size]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            future_rows = {
                pool.submit(
                    download_package_pdf,
                    row,
                    package_map.get(str(row.get("pmcid") or ""), ""),
                    pdf_dir,
                    args.package_base,
                    args.proxy,
                    args.timeout,
                    args.retries,
                    user_agent,
                    args.max_package_mib * 1024 * 1024,
                ): row
                for row in batch
            }
            for future in as_completed(future_rows):
                source = future_rows[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "pmcid": str(source.get("pmcid") or ""),
                        "status": "error",
                        "error": f"worker_exception:{type(exc).__name__}:{exc}",
                    }
                if result.get("status") == "error" and args.fallback_europepmc:
                    result = _download_one(
                        source,
                        pdf_dir,
                        args.timeout,
                        user_agent,
                        args.proxy,
                        args.retries,
                    )
                    if result.get("status") in {"downloaded", "cached"}:
                        fallback_success += 1
                        result["acquisition_fallback"] = "europepmc_render"
                pmcid = str(source.get("pmcid") or "")
                result.update(
                    {
                        "paper_id": source.get("paper_id"),
                        "doi": source.get("doi"),
                        "title": source.get("title"),
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                state[pmcid] = result
                completed += 1
                if result.get("status") == "downloaded":
                    downloaded += 1
                elif result.get("status") == "cached":
                    cached += 1
                else:
                    errors += 1
        _write_jsonl(state_path, list(state.values()))
        write_corpus_manifest(root / "manifest.jsonl", source_rows, state)
        print(
            json.dumps(
                {
                    "phase": "pdfs",
                    "completed": completed,
                    "selected": len(candidates),
                    "downloaded": downloaded,
                    "cached": cached,
                    "errors": errors,
                    "fallback_success": fallback_success,
                }
            ),
            flush=True,
        )

    write_corpus_manifest(root / "manifest.jsonl", source_rows, state)
    summary = {
        "source_rows": len(source_rows),
        "selected_this_run": len(selected),
        "temporarily_skipped_pmcids": sorted(skipped_pmcids),
        "only_pmcids": sorted(only_pmcids),
        "state_rows": len(state),
        "pdfs_present": sum(
            1 for row in state.values() if row.get("status") in {"downloaded", "cached"}
        ),
        "errors": sum(1 for row in state.values() if row.get("status") == "error"),
        "package_map_found": len(package_map),
        "source_manifest_sha256": sha256_file(args.source_manifest),
        "oa_file_list_sha256": sha256_file(file_list),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
