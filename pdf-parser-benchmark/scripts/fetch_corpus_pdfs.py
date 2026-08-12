from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen


DEFAULT_SOURCE_MANIFEST = Path("..") / "biomechanics-corpus" / "manifest.jsonl"


def _default_proxy() -> str:
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        value = os.environ.get(name, "").strip()
        if value and not value.endswith(":9"):
            return value
    # The Codex shell intentionally exposes a closed-loopback proxy at :9;
    # the workstation's system proxy is the reachable :7890 endpoint.
    return "http://127.0.0.1:7890"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            rows.append(value)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _replace_with_retry(temporary, path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    _replace_with_retry(temporary, path)


def _replace_with_retry(temporary: Path, destination: Path, attempts: int = 10) -> None:
    """Atomically publish a state file while tolerating short Windows file locks."""
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(0.25 * (2**attempt), 5.0))
    if last_error is not None:
        raise last_error


def _sha256_and_size(path: Path) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            if len(prefix) < 16:
                prefix += block[: 16 - len(prefix)]
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size, prefix


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_one(
    row: dict[str, Any],
    pdf_dir: Path,
    timeout: int,
    user_agent: str,
    proxy: str,
    retries: int,
) -> dict[str, Any]:
    pmcid = str(row.get("pmcid") or "").strip()
    if not pmcid:
        return {"pmcid": "", "status": "error", "error": "missing_pmcid"}
    output = pdf_dir / f"{pmcid}.pdf"
    if output.is_file():
        sha256, size, prefix = _sha256_and_size(output)
        if prefix.startswith(b"%PDF") and size >= 10240:
            return {
                "pmcid": pmcid,
                "status": "cached",
                "pdf_relpath": output.name,
                "pdf_sha256": sha256,
                "pdf_bytes": size,
            }
        output.unlink(missing_ok=True)

    url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
    last_error = ""
    for attempt in range(1, retries + 2):
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".part", dir=pdf_dir)
        os.close(temporary_fd)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        prefix = b""
        try:
            started = time.monotonic()
            request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/pdf"})
            opener = urlopen if not proxy else build_opener(ProxyHandler({"http": proxy, "https": proxy})).open
            with opener(request, timeout=timeout) as response, temporary.open("wb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    size += len(block)
                    if len(prefix) < 16:
                        prefix += block[: 16 - len(prefix)]
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(f"download exceeded total timeout of {timeout}s")
            if not prefix.startswith(b"%PDF"):
                raise ValueError(f"response is not a PDF (prefix={prefix!r})")
            if size < 10240:
                raise ValueError(f"PDF is unexpectedly small ({size} bytes)")
            os.replace(temporary, output)
            return {
                "pmcid": pmcid,
                "status": "downloaded",
                "pdf_relpath": output.name,
                "pdf_sha256": digest.hexdigest(),
                "pdf_bytes": size,
                "source_url": url,
                "attempts": attempt,
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            last_error = f"{type(exc).__name__}: {exc}"
            retryable = not isinstance(exc, HTTPError) or exc.code == 429 or 500 <= exc.code < 600
            if attempt > retries or not retryable:
                break
            if isinstance(exc, HTTPError) and exc.code == 429:
                retry_after = str(exc.headers.get("Retry-After") or "").strip()
                delay = float(retry_after) if retry_after.isdigit() else min(15.0 * attempt, 60.0)
            else:
                delay = min(float(2 ** (attempt - 1)), 15.0)
            time.sleep(delay)
    return {
        "pmcid": pmcid,
        "status": "error",
        "error": last_error,
        "source_url": url,
        "attempts": retries + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize the 10k Europe PMC records as hashed PDFs.")
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--sleep", type=float, default=0.0, help="Delay after each completed batch.")
    parser.add_argument("--proxy", default=_default_proxy(), help="HTTP(S) proxy; use an empty string for direct access.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1 or args.offset < 0 or args.retries < 0:
        parser.error("workers and batch-size must be positive; offset and retries must be non-negative")

    root = args.root.resolve()
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    source_rows = _read_jsonl(args.source_manifest)
    selected = source_rows[args.offset : (args.offset + args.limit if args.limit else None)]
    if not selected:
        raise SystemExit("No rows selected")

    state_path = root / "download-state.jsonl"
    manifest_path = root / "manifest.jsonl"
    state: dict[str, dict[str, Any]] = {}
    if state_path.is_file():
        for row in _read_jsonl(state_path):
            if row.get("pmcid"):
                state[str(row["pmcid"])] = row

    candidates = []
    for row in selected:
        pmcid = str(row.get("pmcid") or "")
        prior = state.get(pmcid, {})
        expected_pdf = pdf_dir / f"{pmcid}.pdf"
        if prior.get("status") not in {"downloaded", "cached"} or not expected_pdf.is_file():
            candidates.append(row)
    completed = 0
    downloaded = 0
    cached = 0
    errors = 0
    user_agent = "PaperDistill-PDFBenchmark/1.0 (local reproducible corpus acquisition)"
    for start in range(0, len(candidates), args.batch_size):
        batch = candidates[start : start + args.batch_size]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(
                    _download_one,
                    row,
                    pdf_dir,
                    args.timeout,
                    user_agent,
                    args.proxy,
                    args.retries,
                )
                for row in batch
            ]
            for future in as_completed(futures):
                result = future.result()
                pmcid = str(result.get("pmcid") or "")
                source = next((row for row in batch if str(row.get("pmcid")) == pmcid), {})
                result.update(
                    {
                        "paper_id": source.get("paper_id"),
                        "doi": source.get("doi"),
                        "title": source.get("title"),
                        "source_record_sha256": source.get("record_sha256"),
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
        print(json.dumps({"completed": completed, "selected": len(candidates), "downloaded": downloaded, "cached": cached, "errors": errors}, ensure_ascii=False), flush=True)
        if args.sleep:
            time.sleep(args.sleep)

    output_rows: list[dict[str, Any]] = []
    for row in source_rows:
        pmcid = str(row.get("pmcid") or "")
        result = state.get(pmcid, {})
        output_rows.append({**row, "pdf_status": result.get("status", "pending"), "pdf_relpath": f"pdfs/{pmcid}.pdf" if result.get("pdf_sha256") else "", "pdf_sha256": result.get("pdf_sha256", ""), "pdf_bytes": result.get("pdf_bytes", 0), "pdf_error": result.get("error", "")})
    _write_jsonl(manifest_path, output_rows)
    _write_json(
        root / "summary.json",
        {
            "source_manifest": str(args.source_manifest.resolve()),
            "source_manifest_sha256": _file_sha256(args.source_manifest),
            "source_rows": len(source_rows),
            "selected_this_run": len(selected),
            "state_rows": len(state),
            "pdfs_present": sum(
                1 for row in output_rows if row.get("pdf_status") in {"downloaded", "cached"}
            ),
            "errors": sum(1 for row in output_rows if row.get("pdf_status") == "error"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
