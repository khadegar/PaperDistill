from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .common import (
    project_path,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional on the local selector host
    psutil = None


def _float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _nvidia_smi(args: list[str], timeout: int = 20) -> str:
    completed = subprocess.run(
        ["nvidia-smi", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"nvidia-smi failed ({completed.returncode}): {message}")
    return completed.stdout.decode("utf-8", errors="replace")


def query_gpu(gpu_index: int = 0) -> dict[str, Any]:
    fields = [
        "name",
        "uuid",
        "memory.total",
        "memory.used",
        "memory.free",
        "utilization.gpu",
        "utilization.memory",
        "temperature.gpu",
        "power.draw",
    ]
    text = _nvidia_smi(
        [f"--id={gpu_index}", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"]
    ).strip()
    values = [value.strip() for value in text.split(",")]
    if len(values) != len(fields):
        raise RuntimeError(f"Unexpected nvidia-smi GPU row: {text}")
    result: dict[str, Any] = {"sampled_at": utc_now()}
    for field, value in zip(fields, values):
        key = field.replace(".", "_")
        result[key] = value if field in {"name", "uuid"} else _float(value)
    return result


def query_compute_processes(gpu_index: int = 0) -> list[dict[str, Any]]:
    del gpu_index  # compute-app query is driver-wide; one GPU is present on the selected host.
    text = _nvidia_smi(
        ["--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits"]
    ).strip()
    if not text or "no running" in text.lower():
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",", 3)]
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        rows.append(
            {
                "gpu_uuid": parts[0],
                "pid": int(parts[1]),
                "process_name": parts[2],
                "used_memory_mib": _float(parts[3]),
            }
        )
    return rows


def process_tree_pids(pid: int) -> set[int]:
    output = {pid}
    if psutil is None:
        return output
    try:
        process = psutil.Process(pid)
        output.update(child.pid for child in process.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return output


def process_tree_identities(pid: int) -> dict[int, float]:
    """Snapshot a process tree as PID -> creation time.

    A PID alone is not a safe ownership token because Windows can reuse it.
    The runner records descendants while the launched parser is alive and uses
    the creation time to verify that a surviving process is still the same
    parser-owned instance before cleanup.
    """

    if psutil is None:
        return {}
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {}
    identities: dict[int, float] = {}
    for process in processes:
        try:
            identities[int(process.pid)] = float(process.create_time())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return identities


def process_tree_rss_bytes(pid: int) -> int:
    if psutil is None:
        return 0
    total = 0
    try:
        processes = [psutil.Process(pid), *psutil.Process(pid).children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0
    for process in processes:
        try:
            total += int(process.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def terminate_process_tree(pid: int) -> None:
    if psutil is None:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, timeout=20)
        except Exception:
            pass
        return
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            root.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        _, alive = psutil.wait_procs([*children, root], timeout=10)
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def cleanup_observed_descendants(observed: dict[int, float]) -> tuple[list[int], list[str]]:
    """Stop surviving parser descendants whose identity was observed in-tree.

    Some parser packages spawn local OCR/model servers and detach them just as
    the command exits.  At that point a fresh tree walk can no longer establish
    ownership.  This function only acts on descendants captured while the
    launched command was alive, and only if PID *and* creation time still match.
    """

    if psutil is None:
        return [], ["psutil unavailable; descendant cleanup was not attempted"] if observed else []
    cleaned: list[int] = []
    issues: list[str] = []
    tolerance_seconds = 0.01
    for pid, expected_create_time in sorted(observed.items()):
        try:
            process = psutil.Process(pid)
            actual_create_time = float(process.create_time())
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            issues.append(f"PID {pid}: identity check denied: {exc}")
            continue
        if abs(actual_create_time - float(expected_create_time)) > tolerance_seconds:
            issues.append(f"PID {pid}: skipped because the PID was reused")
            continue
        terminate_process_tree(pid)
        try:
            current = psutil.Process(pid)
            if abs(float(current.create_time()) - actual_create_time) <= tolerance_seconds:
                issues.append(f"PID {pid}: still alive after cleanup")
                continue
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            issues.append(f"PID {pid}: post-cleanup check denied: {exc}")
            continue
        cleaned.append(pid)
    return cleaned, issues


def gpu_preflight(config: dict[str, Any], duration: int | None = None, persist: bool = True) -> dict[str, Any]:
    remote = config["remote"]
    duration_seconds = int(duration if duration is not None else remote["preflight_duration_seconds"])
    interval = max(1, int(remote["preflight_interval_seconds"]))
    # Include both endpoints so a configured 30-second gate samples at
    # t=0,5,...,30 instead of returning after roughly 25 seconds.
    count = max(1, math.floor(duration_seconds / interval) + 1)
    gpu_index = int(remote.get("gpu_index", 0))
    samples: list[dict[str, Any]] = []
    processes_seen: dict[int, dict[str, Any]] = {}
    for index in range(count):
        sample = query_gpu(gpu_index)
        sample["compute_processes"] = query_compute_processes(gpu_index)
        for process in sample["compute_processes"]:
            processes_seen[int(process["pid"])] = process
        samples.append(sample)
        if index + 1 < count:
            time.sleep(interval)
    average_utilization = sum(float(sample["utilization_gpu"]) for sample in samples) / len(samples)
    minimum_free = min(float(sample["memory_free"]) for sample in samples)
    issues: list[str] = []
    if processes_seen:
        issues.append(f"GPU compute processes present: {sorted(processes_seen)}")
    max_average = float(remote["maximum_average_gpu_utilization_percent"])
    if average_utilization > max_average:
        issues.append(f"average GPU utilization {average_utilization:.2f}% exceeds {max_average:.2f}%")
    required_free = float(remote["minimum_free_vram_mib"])
    if minimum_free < required_free:
        issues.append(f"minimum free VRAM {minimum_free:.0f} MiB is below {required_free:.0f} MiB")
    result = {
        "schema_version": "1.0",
        "status": "PASS" if not issues else "BLOCKED",
        "started_at": samples[0]["sampled_at"],
        "completed_at": utc_now(),
        "duration_seconds": duration_seconds,
        "average_gpu_utilization_percent": round(average_utilization, 4),
        "minimum_free_vram_mib": minimum_free,
        "compute_processes": list(processes_seen.values()),
        "issues": issues,
        "samples": samples,
    }
    if persist:
        stamp = re.sub(r"[^0-9]", "", result["completed_at"])
        write_json(project_path(config, "logs", f"preflight-{stamp}.json"), result)
        write_json(project_path(config, "logs", "preflight-latest.json"), result)
    return result


def _format_value(value: str, variables: dict[str, str]) -> str:
    rendered = value.format(**variables)
    # Configured paths use portable POSIX separators, but service URLs must
    # retain URI separators on Windows.
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", rendered):
        return rendered
    return rendered.replace("/", os.sep)


def _tool_command(config: dict[str, Any], tool: str, mode: str, input_path: Path, output_path: Path) -> tuple[list[str], dict[str, str]]:
    root = str(Path(config["_project_root"]).resolve())
    variables = {"root": root, "input": str(input_path.resolve()), "output": str(output_path.resolve())}
    tool_config = config["tools"][tool]
    command = [_format_value(str(value), variables) for value in tool_config[mode]]
    environment = {
        key: _format_value(str(value), variables) for key, value in tool_config.get("environment", {}).items()
    }
    return command, environment


def _next_attempt_dir(config: dict[str, Any], tool: str, mode: str, sample_id: str, run_label: str) -> Path:
    base = project_path(config, "runs", tool, mode, run_label, sample_id)
    base.mkdir(parents=True, exist_ok=True)
    existing = [path for path in base.iterdir() if path.is_dir() and re.match(r"attempt-\d{3}$", path.name)]
    number = max([int(path.name.split("-")[1]) for path in existing] or [0]) + 1
    target = base / f"attempt-{number:03d}"
    target.mkdir(parents=False, exist_ok=False)
    return target


def _terminal_existing(
    config: dict[str, Any], tool: str, mode: str, sample_id: str, run_label: str, signature: str
) -> dict[str, Any] | None:
    base = project_path(config, "runs", tool, mode, run_label, sample_id)
    if not base.is_dir():
        return None
    for record_path in sorted(base.glob("attempt-*/run.json"), reverse=True):
        try:
            record = read_json(record_path)
        except Exception:
            continue
        if record.get("run_signature") == signature and record.get("status") in {"success", "failed"}:
            return record
    return None


def _find_markdown(output_dir: Path) -> Path | None:
    files = [path for path in output_dir.rglob("*.md") if path.is_file() and path.stat().st_size > 0]
    if not files:
        return None
    return sorted(files, key=lambda path: (path.stat().st_size, path.stat().st_mtime_ns, str(path)), reverse=True)[0]


def _hash_output_tree(output_dir: Path, manifest_path: Path) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    for path in sorted((item for item in output_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        rows.append(
            {
                "relpath": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    write_jsonl(manifest_path, rows)
    tree_payload = "\n".join(
        f"{row['sha256']}  {row['bytes']}  {row['relpath']}" for row in rows
    )
    return rows, sha256_text(tree_payload)


def _timing_phase(config: dict[str, Any], tool: str, mode: str, run_label: str) -> str:
    base = project_path(config, "runs", tool, mode, run_label)
    sentinel = base / ".successful-run.json"
    if sentinel.is_file():
        return "steady_state"
    if base.is_dir():
        for record_path in base.glob("*/attempt-*/run.json"):
            try:
                record = read_json(record_path)
            except Exception:
                continue
            if record.get("status") == "success":
                write_json(
                    sentinel,
                    {
                        "status": "success_seen",
                        "sample_id": record.get("sample_id"),
                        "started_at": record.get("started_at"),
                    },
                )
                return "steady_state"
    return "cold_start"


def _external_process_requires_pause(record: dict[str, Any]) -> bool:
    """Return true only for a conflict observed in the current execution.

    Successful records keep historical telemetry. A resume-skipped record may
    therefore retain an old external-process flag even though the fresh GPU
    preflight passed; replaying that flag would make the batch unresumable.
    """

    return bool(record.get("external_compute_process_detected")) and not bool(record.get("resume_skipped"))


def _execute_document(
    config: dict[str, Any],
    tool: str,
    mode: str,
    row: dict[str, Any],
    run_label: str,
    resume: bool,
    allowed_gpu_pids: set[int] | None = None,
) -> dict[str, Any]:
    root = Path(config["_project_root"])
    input_path = root / row["staged_pdf_relpath"]
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    output_probe = project_path(config, "runs", "_probe")
    command_probe, env_probe = _tool_command(config, tool, mode, input_path, output_probe)
    signature_payload = {
        "tool": tool,
        "version": config["tool_versions"][tool],
        "mode": mode,
        "command": command_probe,
        "environment": env_probe,
        "input_sha256": row["pdf_sha256"],
        "timeout": config["remote"]["per_document_timeout_seconds"],
    }
    signature = sha256_text(json.dumps(signature_payload, sort_keys=True, ensure_ascii=False))
    if resume:
        existing = _terminal_existing(config, tool, mode, row["sample_id"], run_label, signature)
        if existing is not None:
            return {**existing, "resume_skipped": True}

    attempt_dir = _next_attempt_dir(config, tool, mode, row["sample_id"], run_label)
    raw_output_dir = attempt_dir / "raw"
    raw_output_dir.mkdir()
    command, tool_environment = _tool_command(config, tool, mode, input_path, raw_output_dir)
    timing_phase = _timing_phase(config, tool, mode, run_label)
    environment = os.environ.copy()
    environment.update(tool_environment)
    threads = str(int(config["remote"]["cpu_threads"]))
    # Keep local model servers reachable while failing closed for external
    # HTTP(S) traffic.  This also prevents inherited API credentials from
    # silently turning a local parser run into a remote-LLM run.
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
    environment.update(
        {
            "OMP_NUM_THREADS": threads,
            "MKL_NUM_THREADS": threads,
            "OPENBLAS_NUM_THREADS": threads,
            "NUMEXPR_NUM_THREADS": threads,
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONIOENCODING": "utf-8",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "all_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
        }
    )
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    started_at = utc_now()
    start_monotonic = time.monotonic()
    timeout_seconds = int(config["remote"]["per_document_timeout_seconds"])
    monitor_interval = max(1, int(config["remote"]["monitor_interval_seconds"]))
    gpu_index = int(config["remote"].get("gpu_index", 0))
    telemetry: list[dict[str, Any]] = []
    timed_out = False
    external_processes: dict[int, dict[str, Any]] = {}
    observed_descendants: dict[int, float] = {}
    cleanup_pids: list[int] = []
    cleanup_errors: list[str] = []
    peak_rss = 0
    return_code: int | None = None
    launch_error = ""
    try:
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=environment,
                cwd=root,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            while process.poll() is None:
                elapsed = time.monotonic() - start_monotonic
                own_identities = process_tree_identities(process.pid)
                own_pids = set(own_identities) or process_tree_pids(process.pid)
                own_pids.update(allowed_gpu_pids or set())
                # Parallel production workers share this allow-list. Register
                # each worker's descendants so sibling MinerU requests are
                # not mistaken for an external GPU user; the preflight still
                # rejects any new baseline PID before workers are launched.
                if allowed_gpu_pids is not None:
                    allowed_gpu_pids.update(own_identities)
                for child_pid, created_at in own_identities.items():
                    if child_pid != process.pid:
                        observed_descendants[child_pid] = created_at
                peak_rss = max(peak_rss, process_tree_rss_bytes(process.pid))
                try:
                    gpu = query_gpu(gpu_index)
                    compute = query_compute_processes(gpu_index)
                    for item in compute:
                        if int(item["pid"]) not in own_pids:
                            external_processes[int(item["pid"])] = item
                    gpu["compute_processes"] = compute
                    gpu["own_process_pids"] = sorted(own_pids)
                    gpu["rss_bytes"] = process_tree_rss_bytes(process.pid)
                    telemetry.append(gpu)
                except Exception as exc:
                    telemetry.append({"sampled_at": utc_now(), "telemetry_error": str(exc)})
                if elapsed >= timeout_seconds:
                    timed_out = True
                    terminate_process_tree(process.pid)
                    break
                time.sleep(monitor_interval)
            try:
                return_code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process.pid)
                return_code = process.wait(timeout=30)
    except Exception as exc:
        launch_error = str(exc)
    finally:
        cleanup_pids, cleanup_errors = cleanup_observed_descendants(observed_descendants)

    duration = time.monotonic() - start_monotonic
    raw_manifest_path = attempt_dir / "raw-output-manifest.jsonl"
    raw_files, raw_tree_sha256 = _hash_output_tree(raw_output_dir, raw_manifest_path)
    markdown_path = _find_markdown(raw_output_dir)
    status = "success" if return_code == 0 and markdown_path is not None and not timed_out else "failed"
    if timed_out:
        failure_reason = "timeout"
    elif launch_error:
        failure_reason = f"launch_error: {launch_error}"
    elif return_code != 0:
        failure_reason = f"return_code_{return_code}"
    elif markdown_path is None:
        failure_reason = "markdown_missing"
    else:
        failure_reason = ""
    gpu_rows = [row for row in telemetry if "memory_used" in row]
    bundle_summary_path = project_path(config, "offline", "bundle-summary.json")
    bundle_manifest_sha256 = ""
    if bundle_summary_path.is_file():
        try:
            bundle_manifest_sha256 = str(read_json(bundle_summary_path).get("manifest_sha256") or "")
        except Exception:
            bundle_manifest_sha256 = ""
    executable_path = Path(command[0])
    record = {
        "schema_version": "1.0",
        "benchmark_id": config["benchmark_id"],
        "sample_id": row["sample_id"],
        "paper_id": row["paper_id"],
        "pmcid": row["pmcid"],
        "doi": row["doi"],
        "benchmark_stratum": row["benchmark_stratum"],
        "tool": tool,
        "tool_version": config["tool_versions"][tool],
        "mode": mode,
        "run_label": run_label,
        "run_signature": signature,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "tool_executable_sha256": sha256_file(executable_path) if executable_path.is_file() else "",
        "status": status,
        "failure_reason": failure_reason,
        "return_code": return_code,
        "timed_out": timed_out,
        "started_at": started_at,
        "completed_at": utc_now(),
        "duration_seconds": round(duration, 6),
        "timing_phase": timing_phase,
        "page_count": row["page_count"],
        "pages_per_second": round(float(row["page_count"]) / duration, 6) if duration > 0 else 0.0,
        "peak_vram_mib": max([float(item["memory_used"]) for item in gpu_rows] or [0.0]),
        "peak_ram_bytes": peak_rss,
        "external_compute_process_detected": bool(external_processes),
        "external_compute_processes": list(external_processes.values()),
        "observed_descendant_count": len(observed_descendants),
        "cleanup_pids": cleanup_pids,
        "cleanup_errors": cleanup_errors,
        "input_relpath": row["staged_pdf_relpath"],
        "input_sha256": row["pdf_sha256"],
        "command": command,
        "environment_overrides": {**tool_environment, "*_NUM_THREADS": threads},
        "stdout_relpath": stdout_path.relative_to(root).as_posix(),
        "stderr_relpath": stderr_path.relative_to(root).as_posix(),
        "telemetry_relpath": (attempt_dir / "telemetry.jsonl").relative_to(root).as_posix(),
        "raw_output_manifest_relpath": raw_manifest_path.relative_to(root).as_posix(),
        "raw_output_file_count": len(raw_files),
        "raw_output_tree_sha256": raw_tree_sha256,
        "markdown_relpath": markdown_path.relative_to(root).as_posix() if markdown_path else "",
        "markdown_sha256": sha256_file(markdown_path) if markdown_path else "",
        "markdown_bytes": markdown_path.stat().st_size if markdown_path else 0,
        "resume_skipped": False,
    }
    write_jsonl(attempt_dir / "telemetry.jsonl", telemetry)
    write_json(attempt_dir / "run.json", record)
    if status == "success":
        write_json(
            project_path(config, "runs", tool, mode, run_label, ".successful-run.json"),
            {"status": "success_seen", "sample_id": row["sample_id"], "started_at": started_at},
        )
    return record


def load_sample_set(config: dict[str, Any], sample_set: str, sample_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    if sample_set == "corpus10000":
        production_path = project_path(config, "data", "corpus-10000-manifest.jsonl")
        if not production_path.is_file():
            raise FileNotFoundError(
                f"Production manifest is missing: {production_path}. "
                "Materialize and stage the hashed PDFs before starting the corpus run."
            )
        selected = list(read_jsonl(production_path))
        reserve: list[dict[str, Any]] = []
    else:
        selected = list(read_jsonl(project_path(config, "data", "manifest.jsonl")))
        reserve = list(read_jsonl(project_path(config, "data", "reserve-manifest.jsonl")))
    requested = set(sample_ids or [])
    if requested:
        rows = [row for row in [*selected, *reserve] if row.get("sample_id") in requested]
        missing = requested - {str(row.get("sample_id")) for row in rows}
        if missing:
            raise KeyError(f"Unknown sample IDs: {sorted(missing)}")
        return sorted(rows, key=lambda row: row["sample_id"])
    if sample_set == "smoke":
        return [row for row in reserve if row.get("smoke")]
    if sample_set == "pilot":
        return [row for row in selected if row.get("pilot")]
    if sample_set == "full":
        return selected
    if sample_set == "corpus10000":
        return selected
    if sample_set == "repeat":
        seed = str(config["selection_policy"]["manual_blind_seed"])
        return sorted(selected, key=lambda row: sha256_text(seed + row["sample_id"]))[:5]
    raise ValueError(f"Unknown sample set: {sample_set}")


def rebuild_run_index(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    runs_root = project_path(config, "runs")
    if runs_root.is_dir():
        for path in runs_root.glob("*/*/*/*/attempt-*/run.json"):
            try:
                rows.append(read_json(path))
            except Exception:
                continue
    rows.sort(key=lambda row: (row.get("tool", ""), row.get("mode", ""), row.get("run_label", ""), row.get("sample_id", ""), row.get("started_at", "")))
    write_jsonl(project_path(config, "runs", "index.jsonl"), rows)
    return rows


def run_benchmark(
    config: dict[str, Any],
    tools: list[str],
    sample_set: str,
    sample_ids: list[str] | None = None,
    run_label: str = "main",
    resume: bool = True,
) -> dict[str, Any]:
    rows = load_sample_set(config, sample_set, sample_ids)
    unknown = [tool for tool in tools if tool not in config["tools"]]
    if unknown:
        raise KeyError(f"Unknown tools: {unknown}")
    initial_gate = gpu_preflight(config, duration=None, persist=True)
    if initial_gate["status"] != "PASS":
        blocked_request: list[str] | dict[str, Any]
        if sample_set == "corpus10000":
            blocked_request = {
                "count": len(rows),
                "first": rows[0]["sample_id"] if rows else "",
                "last": rows[-1]["sample_id"] if rows else "",
            }
        else:
            blocked_request = [row["sample_id"] for row in rows]
        result = {
            "schema_version": "1.0",
            "status": "BLOCKED",
            "sample_set": sample_set,
            "run_label": run_label,
            "requested_tools": tools,
            "requested_samples": blocked_request,
            "records_this_invocation": 0,
            "run_records_total": len(rebuild_run_index(config)),
            "stopped_for_external_process": True,
            "preflight": initial_gate,
            "records": [],
            "completed_at": utc_now(),
        }
        write_json(project_path(config, "runs", f"batch-{run_label}-{sample_set}.json"), result)
        return result
    production_run = sample_set == "corpus10000"
    batch_records: list[dict[str, Any]] = []
    resume_skipped = 0
    stopped_for_external_process = False
    for tool in tools:
        for row in rows:
            if resume:
                root = Path(config["_project_root"])
                input_path = root / row["staged_pdf_relpath"]
                output_probe = project_path(config, "runs", "_probe")
                command_probe, env_probe = _tool_command(
                    config, tool, "primary", input_path, output_probe
                )
                signature_payload = {
                    "tool": tool,
                    "version": config["tool_versions"][tool],
                    "mode": "primary",
                    "command": command_probe,
                    "environment": env_probe,
                    "input_sha256": row["pdf_sha256"],
                    "timeout": config["remote"]["per_document_timeout_seconds"],
                }
                signature = sha256_text(
                    json.dumps(signature_payload, sort_keys=True, ensure_ascii=False)
                )
                existing = _terminal_existing(
                    config, tool, "primary", row["sample_id"], run_label, signature
                )
                if existing is not None and existing.get("status") == "success":
                    resume_skipped += 1
                    if not production_run:
                        batch_records.append({**existing, "resume_skipped": True})
                    continue
            gate = gpu_preflight(config, duration=1, persist=False)
            if gate["status"] != "PASS":
                stopped_for_external_process = True
                batch_records.append(
                    {
                        "tool": tool,
                        "sample_id": row["sample_id"],
                        "status": "blocked_preflight",
                        "issues": gate["issues"],
                    }
                )
                break
            primary = _execute_document(config, tool, "primary", row, run_label, resume)
            batch_records.append(primary)
            if _external_process_requires_pause(primary):
                stopped_for_external_process = True
                break
            if primary.get("status") != "success":
                fallback_gate = gpu_preflight(config, duration=1, persist=False)
                if fallback_gate["status"] == "PASS":
                    fallback = _execute_document(config, tool, "fallback", row, run_label, resume)
                    batch_records.append(fallback)
                    if _external_process_requires_pause(fallback):
                        stopped_for_external_process = True
                        break
            if not production_run:
                rebuild_run_index(config)
        if stopped_for_external_process:
            break
    index = rebuild_run_index(config)
    requested_samples: list[str] | dict[str, Any]
    if production_run:
        requested_samples = {
            "count": len(rows),
            "first": rows[0]["sample_id"] if rows else "",
            "last": rows[-1]["sample_id"] if rows else "",
        }
    else:
        requested_samples = [row["sample_id"] for row in rows]
    result = {
        "schema_version": "1.0",
        "status": "PAUSED" if stopped_for_external_process else "COMPLETE",
        "sample_set": sample_set,
        "run_label": run_label,
        "requested_tools": tools,
        "requested_samples": requested_samples,
        "records_this_invocation": len(batch_records),
        "resume_skipped": resume_skipped,
        "run_records_total": len(index),
        "stopped_for_external_process": stopped_for_external_process,
        "preflight": initial_gate,
        "records": [] if production_run else batch_records,
        "completed_at": utc_now(),
    }
    write_json(project_path(config, "runs", f"batch-{run_label}-{sample_set}.json"), result)
    return result
