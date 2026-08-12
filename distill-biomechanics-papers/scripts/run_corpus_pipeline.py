#!/usr/bin/env python3
"""Run the resumable full-text fetch, index, distillation, and final audit."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from _common import utc_now, write_json


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGER = SCRIPT_DIR / "manage_fulltext_corpus.py"
DISTILLER = SCRIPT_DIR / "distill_large_corpus.py"
DEFAULT_PROFILE = SCRIPT_DIR.parent / "assets" / "corpus-profile-10k.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=2.0)
    parser.add_argument("--max-fetch-passes", type=int, default=5)
    parser.add_argument("--audit-sample-size", type=int, default=400)
    parser.add_argument("--log", type=Path, help="Append pipeline and child-process output to this UTF-8 log")
    parser.add_argument(
        "--wait-for-pid",
        type=int,
        help="Wait for an already-running fetch process before resuming the pipeline",
    )
    return parser.parse_args()


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_for_process(pid: int) -> None:
    print(f"[pipeline] waiting for PID {pid}", flush=True)
    while process_exists(pid):
        time.sleep(15)
    print(f"[pipeline] PID {pid} finished; beginning reconciliation", flush=True)


def run_stage(name: str, command: list[str], stages: list[dict[str, Any]]) -> int:
    started = utc_now()
    print(f"[pipeline] {name}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False, stdout=sys.stdout, stderr=sys.stderr)
    stages.append(
        {
            "stage": name,
            "started_at": started,
            "completed_at": utc_now(),
            "returncode": completed.returncode,
        }
    )
    return completed.returncode


def main() -> int:
    args = parse_args()
    log_stream = None
    if args.log:
        log_path = args.log.resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log_stream
        sys.stderr = log_stream
    root = args.root.resolve()
    profile = args.profile.resolve()
    running_path = root / "reports" / "pipeline-running.json"
    write_json(
        running_path,
        {
            "schema_version": "1.0",
            "status": "running",
            "started_at": utc_now(),
            "pid": os.getpid(),
            "root": str(root),
            "profile": str(profile),
            "log": str(args.log.resolve()) if args.log else None,
        },
    )
    if args.max_fetch_passes <= 0:
        print("ERROR: --max-fetch-passes must be positive", file=sys.stderr)
        return 2
    if args.wait_for_pid:
        wait_for_process(args.wait_for_pid)

    stages: list[dict[str, Any]] = []
    fetch_complete = False
    for pass_number in range(1, args.max_fetch_passes + 1):
        returncode = run_stage(
            f"fetch-pass-{pass_number}",
            [
                sys.executable,
                str(MANAGER),
                "--profile",
                str(profile),
                "fetch",
                "--root",
                str(root),
                "--workers",
                str(args.workers),
                "--requests-per-second",
                str(args.requests_per_second),
                "--progress-every",
                "100",
            ],
            stages,
        )
        if returncode == 0:
            fetch_complete = True
            break
        if returncode != 3:
            break
        replacement_returncode = run_stage(
            f"replace-permanent-failures-{pass_number}",
            [
                sys.executable,
                str(MANAGER),
                "--profile",
                str(profile),
                "replace-failures",
                "--root",
                str(root),
            ],
            stages,
        )
        if replacement_returncode != 0:
            break

    reparse_returncode = run_stage(
        "reparse-all",
        [
            sys.executable,
            str(MANAGER),
            "--profile",
            str(profile),
            "fetch",
            "--root",
            str(root),
            "--workers",
            str(args.workers),
            "--reparse-existing",
            "--progress-every",
            "500",
        ],
        stages,
    )
    fetch_complete = fetch_complete or reparse_returncode == 0
    index_returncode = run_stage(
        "index",
        [sys.executable, str(MANAGER), "--profile", str(profile), "index", "--root", str(root)],
        stages,
    )
    distill_returncode = run_stage(
        "distill",
        [
            sys.executable,
            str(DISTILLER),
            "--root",
            str(root),
            "--audit-sample-size",
            str(max(args.audit_sample_size, 0)),
        ],
        stages,
    )
    audit_returncode = run_stage(
        "audit",
        [sys.executable, str(MANAGER), "--profile", str(profile), "audit", "--root", str(root)],
        stages,
    )
    stats_returncode = run_stage(
        "stats",
        [sys.executable, str(MANAGER), "--profile", str(profile), "stats", "--root", str(root), "--json"],
        stages,
    )

    stats_path = root / "reports" / "stats.json"
    stats: dict[str, Any] = {}
    if stats_path.is_file():
        with stats_path.open("r", encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            stats = loaded
    manifest_count = int(stats.get("manifest_papers") or 0)
    parsed_count = int(stats.get("active_parsed_records") or stats.get("parsed_records") or 0)
    target_complete = manifest_count > 0 and parsed_count == manifest_count
    status = "complete" if fetch_complete and target_complete and not any(
        value
        for value in (reparse_returncode, index_returncode, distill_returncode, audit_returncode, stats_returncode)
    ) else "partial"
    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "status": status,
        "root": str(root),
        "profile": str(profile),
        "fetch_complete": fetch_complete,
        "target_complete": target_complete,
        "manifest_papers": manifest_count,
        "parsed_records": parsed_count,
        "stages": stages,
    }
    write_json(root / "reports" / "pipeline.json", report)
    write_json(running_path, {**report, "pid": os.getpid()})
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if status == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
