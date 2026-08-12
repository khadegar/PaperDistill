from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            yield value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def latest_terminal(project_root: Path, tool: str, mode: str, run_label: str, sample_id: str) -> dict[str, Any] | None:
    base = project_root / "runs" / tool / mode / run_label / sample_id
    if not base.is_dir():
        return None
    for path in sorted(base.glob("attempt-*/run.json"), reverse=True):
        try:
            record = read_json(path)
        except Exception:
            continue
        if record.get("status") in {"success", "failed"}:
            return record
    return None


def export_record(
    project_root: Path,
    export_root: Path,
    record: dict[str, Any],
    pmcid: str,
    mode: str,
) -> tuple[Path | None, list[str]]:
    issues: list[str] = []
    if record.get("status") != "success" or not record.get("markdown_relpath"):
        return None, issues
    source = project_root / str(record["markdown_relpath"])
    if not source.is_file():
        return None, ["markdown_missing"]
    actual_hash = sha256_file(source)
    expected_hash = str(record.get("markdown_sha256") or "")
    if expected_hash and actual_hash != expected_hash:
        return None, ["markdown_hash_mismatch"]
    target = export_root / mode / f"{pmcid}.md"
    if not target.is_file() or sha256_file(target) != actual_hash:
        atomic_copy(source, target)
    if sha256_file(target) != actual_hash:
        return None, ["export_hash_mismatch"]
    if target.stat().st_size < 1000:
        issues.append("markdown_suspiciously_small")
    return target, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a hashed, flat Markdown package from corpus run records.")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path("data/corpus-10000-manifest.jsonl"))
    parser.add_argument("--tool", required=True, choices=["mineru", "marker", "docling"])
    parser.add_argument("--run-label", default="corpus10000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else project_root / args.manifest
    export_root = (
        args.output.resolve()
        if args.output
        else project_root / "exports" / "corpus10000" / args.tool
    )
    rows: list[dict[str, Any]] = []
    for paper in read_jsonl(manifest_path):
        pmcid = str(paper["pmcid"])
        primary = latest_terminal(project_root, args.tool, "primary", args.run_label, pmcid)
        fallback = latest_terminal(project_root, args.tool, "fallback", args.run_label, pmcid)
        primary_target, primary_issues = (
            export_record(project_root, export_root, primary, pmcid, "primary")
            if primary
            else (None, [])
        )
        fallback_target, fallback_issues = (
            export_record(project_root, export_root, fallback, pmcid, "fallback")
            if fallback
            else (None, [])
        )
        preferred = primary_target or fallback_target
        preferred_mode = "primary" if primary_target else ("fallback" if fallback_target else "")
        preferred_target: Path | None = None
        if preferred:
            preferred_target = export_root / "preferred" / f"{pmcid}.md"
            preferred_hash = sha256_file(preferred)
            if not preferred_target.is_file() or sha256_file(preferred_target) != preferred_hash:
                atomic_copy(preferred, preferred_target)
        rows.append(
            {
                "sample_id": str(paper["sample_id"]),
                "pmcid": pmcid,
                "doi": str(paper.get("doi") or ""),
                "input_sha256": str(paper["pdf_sha256"]),
                "input_kind": str(paper.get("input_kind") or "publisher_pdf"),
                "publication_status": str(paper.get("publication_status") or "published"),
                "source_override": paper.get("source_override"),
                "page_count": int(paper.get("page_count") or 0),
                "primary_status": primary.get("status") if primary else "pending",
                "primary_failure_reason": primary.get("failure_reason", "") if primary else "",
                "primary_markdown_relpath": primary_target.relative_to(export_root).as_posix() if primary_target else "",
                "primary_markdown_sha256": sha256_file(primary_target) if primary_target else "",
                "fallback_status": fallback.get("status") if fallback else "not_run",
                "fallback_markdown_relpath": fallback_target.relative_to(export_root).as_posix() if fallback_target else "",
                "fallback_markdown_sha256": sha256_file(fallback_target) if fallback_target else "",
                "preferred_mode": preferred_mode,
                "preferred_markdown_relpath": preferred_target.relative_to(export_root).as_posix() if preferred_target else "",
                "preferred_markdown_sha256": sha256_file(preferred_target) if preferred_target else "",
                "issues": [*primary_issues, *fallback_issues],
            }
        )

    write_jsonl(export_root / "index.jsonl", rows)
    summary = {
        "tool": args.tool,
        "run_label": args.run_label,
        "expected": len(rows),
        "primary_success": sum(1 for row in rows if row["primary_status"] == "success"),
        "fallback_success": sum(1 for row in rows if row["fallback_status"] == "success"),
        "preferred_available": sum(1 for row in rows if row["preferred_mode"]),
        "issue_rows": sum(1 for row in rows if row["issues"]),
        "index_sha256": sha256_file(export_root / "index.jsonl"),
        "status": "PASS" if all(row["preferred_mode"] and not row["issues"] for row in rows) else "PARTIAL",
    }
    write_json(export_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
