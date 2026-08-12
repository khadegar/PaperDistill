# PDF-to-Markdown GPU Benchmark

This project benchmarks MinerU, Marker, and Docling on 50 local Zotero PDFs
whose DOI values match the local PMC/JATS corpus exactly.  It is deliberately
separate from `biomechanics-corpus`: running the benchmark does not update the
10,000-paper manifest, semantic cards, or the writing Skill.

## Reproducible workflow

```powershell
python scripts/pdfbench.py select --config config/benchmark.json
python scripts/pdfbench.py validate-manifest --config config/benchmark.json
python scripts/pdfbench.py preflight --config config/benchmark.json --duration 30
python scripts/pdfbench.py run --config config/benchmark.json --tool mineru --sample-set smoke
python scripts/pdfbench.py run --config config/benchmark.json --tool all --sample-set pilot
python scripts/pdfbench.py evaluate --config config/benchmark.json --run-label pilot --sample-set pilot
python scripts/pdfbench.py run --config config/benchmark.json --tool all --sample-set full
python scripts/pdfbench.py evaluate --config config/benchmark.json
python scripts/pdfbench.py blind-package --config config/benchmark.json
python scripts/pdfbench.py run --config config/benchmark.json --tool all --sample-set repeat --run-label repeat
python scripts/pdfbench.py evaluate --config config/benchmark.json --run-label repeat --sample-set repeat
python scripts/pdfbench.py repeatability --config config/benchmark.json --baseline-label main --repeat-label repeat
python scripts/pdfbench.py report --config config/benchmark.json
```

On a shared GPU server, the guarded controller can resume a paused batch only
after the runner's 30-second GPU preflight passes. It preserves single-document
execution and never terminates an external GPU process:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File server\run_guarded_batch.ps1 `
  -Root C:\Users\zzx\PaperDistillGPU\benchmark-v1 `
  -Tool all -SampleSet pilot -RunLabel pilot -PollSeconds 60
```

Create `runs\controller\pilot-pilot-all.stop` to stop it between attempts.
Controller state and per-attempt output are written under `runs\controller`.

The local selector snapshots Zotero metadata read-only, hashes every selected
PDF and JATS source, and stages only the 50 benchmark papers plus five reserve
papers.  The remote runner processes one PDF at a time, samples GPU/RAM usage,
and stops scheduling new work if an unrelated compute process appears.
Each run begins with the configured 30-second GPU idle gate. Marker balanced
mode uses the bundled Surya GGUF model through a pinned CUDA llama.cpp build;
the bundled GoNoto font prevents runtime artifact downloads, and the local
server exits after each paper so it remains inside the process guard.
MinerU keeps the requested local `hybrid-engine/high` configuration and uses
its official Transformers CUDA backend on Windows; LMDeploy is intentionally
absent because Turbomind is unsupported on this Server 2019 host and the
LMDeploy PyTorch path requires Triton, which has no supported Windows wheel.
Docling uses the explicit `docling convert` VLM command and bundled
GraniteDocling artifacts. Every raw output file is hashed before evaluation.
The runner removes inherited external-API credentials and routes non-local
HTTP(S) traffic to a closed loopback endpoint; only the bundled models and the
local Marker inference server are reachable during conversion.

## Main artifacts

- `data/manifest.jsonl`: fixed 50-paper benchmark manifest.
- `data/reserve-manifest.jsonl`: one reserve paper per stratum.
- `inputs/pdfs/`: hashed benchmark PDFs copied from Zotero.
- `ground-truth/`: matched parsed records and original JATS XML.
- `runs/<tool>/`: raw, immutable parser output and telemetry.
- `scores/`: per-paper automatic metrics and optional blinded manual scores.
- `reports/benchmark-report.md`: final selection report.

All machine-readable files are UTF-8 JSON/JSONL/CSV.  Paths in manifests use
portable relative paths for staged material and retain the original source path
only as provenance.
