from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdfbench.common import load_config, project_path, utc_now, write_json  # noqa: E402
from pdfbench.runner import (  # noqa: E402
    _execute_document,
    _external_process_requires_pause,
    gpu_preflight,
    load_sample_set,
    process_tree_pids,
    query_compute_processes,
    rebuild_run_index,
    terminate_process_tree,
)


def worker_exception_record(row: dict[str, Any], error: BaseException) -> dict[str, Any]:
    return {
        "status": "service_exception",
        "sample_id": str(row.get("sample_id", "")),
        "resume_skipped": False,
        "error_type": type(error).__name__,
        "error": str(error),
        "recorded_at": utc_now(),
    }


def service_environment(config: dict[str, Any], api_concurrency: int = 1) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            key: str(value).format(root=config["_project_root"])
            for key, value in config["tools"]["mineru"].get("environment", {}).items()
        }
    )
    threads = str(int(config["remote"]["cpu_threads"]))
    environment.update(
        {
            "OMP_NUM_THREADS": threads,
            "MKL_NUM_THREADS": threads,
            "OPENBLAS_NUM_THREADS": threads,
            "NUMEXPR_NUM_THREADS": threads,
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONIOENCODING": "utf-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "MINERU_MODEL_SOURCE": "local",
            "MINERU_API_MAX_CONCURRENT_REQUESTS": str(max(1, int(api_concurrency))),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        }
    )
    for credential in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "DATALAB_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
    ):
        environment.pop(credential, None)
    return environment


def service_command(config: dict[str, Any], host: str, port: int) -> list[str]:
    root = Path(config["_project_root"])
    executable = root / "envs" / "mineru" / "Scripts" / "mineru-api.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    return [
        str(executable),
        "--host",
        host,
        "--port",
        str(port),
        "--enable-vlm-preload",
        "true",
    ]


def wait_for_health(url: str, process: subprocess.Popen[bytes], timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MinerU API exited during startup with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
        except Exception as exc:  # service is expected to refuse connections while loading
            last_error = str(exc)
        time.sleep(2)
    raise TimeoutError(f"MinerU API health timeout: {last_error}")


def start_service(
    config: dict[str, Any], host: str, port: int, startup_timeout: int,
    api_concurrency: int = 1,
) -> tuple[subprocess.Popen[bytes], Any, Any]:
    logs = project_path(config, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    stdout_handle = (logs / "mineru-corpus-api.stdout.log").open("ab")
    stderr_handle = (logs / "mineru-corpus-api.stderr.log").open("ab")
    process = subprocess.Popen(
        service_command(config, host, port),
        cwd=Path(config["_project_root"]),
        env=service_environment(config, api_concurrency),
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        wait_for_health(f"http://{host}:{port}/health", process, startup_timeout)
    except Exception:
        terminate_process_tree(process.pid)
        stdout_handle.close()
        stderr_handle.close()
        raise
    return process, stdout_handle, stderr_handle


def stop_service(process: subprocess.Popen[bytes] | None, *handles: Any) -> None:
    if process is not None and process.poll() is None:
        terminate_process_tree(process.pid)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    for handle in handles:
        if handle is not None:
            handle.close()


def configure_client(config: dict[str, Any], api_url: str) -> None:
    for mode in ("primary", "fallback"):
        command = list(config["tools"]["mineru"][mode])
        if "--api-url" not in command:
            command.extend(["--api-url", api_url])
        config["tools"]["mineru"][mode] = command


def conflicting_gpu_processes(config: dict[str, Any], allowed: set[int]) -> list[dict[str, Any]]:
    gpu_index = int(config["remote"].get("gpu_index", 0))
    return [row for row in query_compute_processes(gpu_index) if int(row["pid"]) not in allowed]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the 10k MinerU corpus through one local, offline, persistent API service."
    )
    parser.add_argument("--config", type=Path, default=Path("config/benchmark.json"))
    parser.add_argument("--run-label", default="corpus10000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18922)
    parser.add_argument("--startup-timeout", type=int, default=900)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sample-id", action="append", default=[])
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--skip-any-success",
        action="store_true",
        help="Skip samples with a successful MinerU primary or fallback run under this label, regardless of signature.",
    )
    parser.add_argument(
        "--primary-backend",
        choices=("config", "pipeline", "hybrid"),
        default="config",
        help="Override MinerU primary backend in memory; benchmark config remains unchanged.",
    )
    parser.add_argument(
        "--client-concurrency",
        type=int,
        default=1,
        help="Number of concurrent document clients sharing the local API service.",
    )
    parser.add_argument(
        "--api-concurrency",
        type=int,
        default=0,
        help="API request semaphore; defaults to --client-concurrency.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.primary_backend != "config":
        primary = list(config["tools"]["mineru"]["primary"])
        if "-b" in primary:
            primary[primary.index("-b") + 1] = (
                "pipeline" if args.primary_backend == "pipeline" else "hybrid-engine"
            )
        if args.primary_backend == "pipeline":
            cleaned: list[str] = []
            skip_next = False
            for token in primary:
                if skip_next:
                    skip_next = False
                    continue
                if token == "--effort":
                    skip_next = True
                    continue
                cleaned.append(token)
            primary = cleaned
        config["tools"]["mineru"]["primary"] = primary
    configure_client(config, f"http://{args.host}:{args.port}")
    all_rows = load_sample_set(config, "corpus10000", args.sample_id or None)
    rows = list(all_rows)
    if args.limit:
        rows = rows[: args.limit]
    if args.client_concurrency < 1:
        raise ValueError("--client-concurrency must be at least 1")
    api_concurrency = args.api_concurrency or args.client_concurrency
    if api_concurrency < 1:
        raise ValueError("--api-concurrency must be at least 1")

    def has_success(sample_id: str) -> bool:
        run_root = project_path(config, "runs", "mineru")
        for mode in ("primary", "fallback"):
            for record_path in (run_root / mode / args.run_label / sample_id).glob("attempt-*/run.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if record.get("status") == "success":
                    return True
        return False

    pre_skipped = 0
    if args.skip_any_success:
        pending_rows = []
        for row in rows:
            if has_success(str(row["sample_id"])):
                pre_skipped += 1
            else:
                pending_rows.append(row)
        rows = pending_rows

    gate = gpu_preflight(config, duration=None, persist=True)
    if gate["status"] != "PASS":
        print(json.dumps({"status": "BLOCKED", "preflight": gate}, ensure_ascii=False, indent=2))
        return 3

    process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    completed = 0
    failed = 0
    resume_skipped = pre_skipped
    paused = False
    current = ""
    active: set[str] = set()
    shared_allowed_gpu_pids: set[int] = set()
    progress_lock = threading.Lock()
    progress_path = project_path(config, "runs", f"batch-{args.run_label}-corpus10000-service.json")

    def save_progress(status: str) -> None:
        with progress_lock:
            write_json(
                progress_path,
                {
                    "schema_version": "1.0",
                    "status": status,
                    "run_label": args.run_label,
                    "requested": len(all_rows),
                    "pending_requested": len(rows),
                    "completed": completed,
                    "failed": failed,
                    "resume_skipped": resume_skipped,
                    "current_sample_id": ",".join(sorted(active)) or current,
                    "service_pid": process.pid if process and process.poll() is None else None,
                    "client_concurrency": args.client_concurrency,
                    "api_concurrency": api_concurrency,
                    "primary_backend": args.primary_backend,
                    "updated_at": utc_now(),
                },
            )

    try:
        process, stdout_handle, stderr_handle = start_service(
            config, args.host, args.port, args.startup_timeout, api_concurrency
        )
        shared_allowed_gpu_pids.update(process_tree_pids(process.pid))
        def run_one(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
            sample_id = str(row["sample_id"])
            with progress_lock:
                active.add(sample_id)
            save_progress("RUNNING")
            try:
                allowed = shared_allowed_gpu_pids
                conflicts = conflicting_gpu_processes(config, allowed)
                if conflicts:
                    return ({"status": "paused_external_gpu_process", "sample_id": sample_id}, None)
                primary = _execute_document(
                    config, "mineru", "primary", row, args.run_label,
                    not args.no_resume, allowed_gpu_pids=allowed,
                )
                fallback: dict[str, Any] | None = None
                if primary.get("status") != "success" and not primary.get("resume_skipped"):
                    fallback = _execute_document(
                        config, "mineru", "fallback", row, args.run_label,
                        not args.no_resume, allowed_gpu_pids=shared_allowed_gpu_pids,
                    )
                return primary, fallback
            except Exception as error:
                record = worker_exception_record(row, error)
                try:
                    write_json(
                        project_path(config, "runs", "service-exceptions", args.run_label, f"{sample_id}.json"),
                        record,
                    )
                except Exception:
                    pass
                return record, None
            finally:
                with progress_lock:
                    active.discard(sample_id)

        if args.client_concurrency == 1:
            batches = [[row] for row in rows]
        else:
            batches = [rows[i : i + args.client_concurrency] for i in range(0, len(rows), args.client_concurrency)]
        for batch in batches:
            with ThreadPoolExecutor(max_workers=args.client_concurrency) as executor:
                futures = {executor.submit(run_one, row): row for row in batch}
                for future in as_completed(futures):
                    try:
                        primary, fallback = future.result()
                    except Exception as error:
                        primary = worker_exception_record(futures[future], error)
                        fallback = None
                    with progress_lock:
                        if primary.get("resume_skipped"):
                            resume_skipped += 1
                        elif primary.get("status") == "success":
                            completed += 1
                        elif fallback and fallback.get("status") == "success":
                            completed += 1
                        else:
                            failed += 1
                        if primary.get("status") == "paused_external_gpu_process":
                            paused = True
                    save_progress("PAUSED_EXTERNAL_GPU_PROCESS" if paused else "RUNNING")
            if paused:
                break
        save_progress("PAUSED_EXTERNAL_GPU_PROCESS" if paused else "COMPLETE")
    except Exception:
        try:
            save_progress("FAILED")
        except Exception:
            pass
        raise
    finally:
        stop_service(process, stdout_handle, stderr_handle)
        rebuild_run_index(config)

    print(progress_path.read_text(encoding="utf-8"))
    return 4 if paused else (2 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
