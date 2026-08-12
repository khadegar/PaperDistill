from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import project_path, read_jsonl, sha256_file, utc_now, write_json, write_jsonl


BUNDLE_INCLUDE_ROOTS = (
    "README.md",
    "config",
    "scripts",
    "server",
    "src",
    "tests",
    "data",
    "inputs",
    "ground-truth",
    "offline/wheels",
    "offline/models",
    "offline/locks",
    "offline/python",
)


def _is_reproducible_bundle_file(path: Path) -> bool:
    if path.suffix.lower() in {".pyc", ".pyo", ".log", ".lock", ".tmp", ".incomplete"}:
        return False
    lowered_parts = tuple(part.lower() for part in path.parts)
    if "__pycache__" in lowered_parts:
        return False
    return not any(
        lowered_parts[index : index + 2] == ("xet", "logs")
        for index in range(max(0, len(lowered_parts) - 1))
    )


def _bundle_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative in BUNDLE_INCLUDE_ROOTS:
        path = root / relative
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = [file for file in path.rglob("*") if file.is_file()]
        else:
            candidates = []
        files.extend(
            file for file in candidates if _is_reproducible_bundle_file(file.relative_to(root))
        )
    return sorted(set(files), key=lambda file: file.relative_to(root).as_posix())


def build_bundle_manifest(config: dict[str, Any]) -> dict[str, Any]:
    root = Path(config["_project_root"])
    rows: list[dict[str, Any]] = []
    for file in _bundle_files(root):
        rows.append(
            {
                "relpath": file.relative_to(root).as_posix(),
                "size": file.stat().st_size,
                "sha256": sha256_file(file),
            }
        )
    rows.sort(key=lambda row: row["relpath"])
    manifest_path = project_path(config, "offline", "bundle-manifest.jsonl")
    write_jsonl(manifest_path, rows)
    summary = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "files": len(rows),
        "bytes": sum(int(row["size"]) for row in rows),
        "manifest_relpath": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
    }
    write_json(project_path(config, "offline", "bundle-summary.json"), summary)
    return summary


def verify_bundle_manifest(config: dict[str, Any]) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest_path = project_path(config, "offline", "bundle-manifest.jsonl")
    issues: list[str] = []
    checked = 0
    rows = list(read_jsonl(manifest_path))
    manifest_relpaths = {str(row["relpath"]) for row in rows}
    current_relpaths = {file.relative_to(root).as_posix() for file in _bundle_files(root)}
    for relpath in sorted(current_relpaths - manifest_relpaths):
        issues.append(f"unmanifested bundle file: {relpath}")
    for relpath in sorted(manifest_relpaths - current_relpaths):
        issues.append(f"manifest entry outside current bundle: {relpath}")
    for row in rows:
        path = root / row["relpath"]
        if not path.is_file():
            issues.append(f"missing: {row['relpath']}")
            continue
        checked += 1
        if path.stat().st_size != int(row["size"]):
            issues.append(f"size mismatch: {row['relpath']}")
            continue
        if sha256_file(path) != row["sha256"]:
            issues.append(f"hash mismatch: {row['relpath']}")
    return {
        "status": "PASS" if not issues else "FAIL",
        "checked": checked,
        "issues": issues,
        "verified_at": utc_now(),
    }
