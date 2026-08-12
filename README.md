# PaperDistill

Tools and instructions for distilling biomechanics, bone-implant, topology-optimization,
porous-scaffold, mechanobiology, and additive-manufacturing papers into an auditable
scientific-writing skill.

## Repository scope

This repository tracks the reusable Skill, extraction/validation scripts, benchmark
configuration, semantic-card metadata, and compact reports. The 10,000-PDF corpus,
parsed full text, MinerU model caches, runtime environments, and generated Markdown
outputs remain outside Git and are reproducible from the manifests and scripts.

The root `.gitignore` intentionally excludes those large/generated paths. Do not remove
those exclusions without first choosing an object-storage or Git-LFS retention plan.

## Main components

- `distill-biomechanics-papers/` - Codex Skill, semantic writing rules, domain modules,
  templates, and validation scripts.
- `biomechanics-corpus/` - compact corpus manifests, reports, semantic cards, and
  synthesis metadata; raw records, packets, and SQLite indexes are local artifacts.
- `pdf-parser-benchmark/` - MinerU/Marker/Docling benchmark code, tests, configs,
  scoring, and reproducibility documentation; PDFs and run outputs are excluded.

## Reproducibility

Run the benchmark and corpus scripts from their respective README/config files. Keep
the local manifests and SHA-256 indexes alongside any externally stored PDF/Markdown
artifacts so semantic distillation can be resumed without copying the full corpus into
Git.
