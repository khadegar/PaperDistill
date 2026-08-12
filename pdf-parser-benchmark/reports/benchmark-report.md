# A100 PDF-to-Markdown Benchmark Report

- Benchmark: `pdf-markdown-a100-v1`
- Generated: `2026-08-11T19:10:34Z`
- Host: `10.201.29.159`
- GPU gate: average utilization ≤ 5%, free VRAM ≥ 75000 MiB
- Sample: 50 exact DOI-matched Zotero PDF ↔ PMC/JATS pairs
- Pages: 714
- Fixed manifest SHA-256: `52e4d01418facc8e68950650ce862190e20f18202158ddec34e646a8647a7e3d`
- Offline bundle manifest SHA-256: `469e9d19ebf7eebcb09644fc0bec4e3f37deb13d4ab6d7da11fc0d45e86eef50`
- Bundle SHA-256 values recorded by `main` runs: none
- Zotero snapshot: 1111 existing PDFs; 94 PDF-internal DOI matches; 62 eligible unique candidates

## Sample composition

- `layout_stress`: 10
- `formula_fe_topology`: 10
- `table_review_clinical`: 10
- `figure_am_biomaterial`: 10
- `standard_born_digital`: 10

## Reproducibility and execution contract

- Windows Server, Python 3.11, one document at a time, eight CPU threads.
- Every run starts only after a 30-second GPU gate and is sampled every 2 seconds.
- Per-document timeout: 15 minutes; one tool-specific OCR fallback is retained separately after a primary failure.
- Inherited external-API credentials are removed and non-local HTTP(S) traffic is routed to a closed loopback endpoint.
- Raw output trees and Markdown files are hashed before evaluation normalization.
- MinerU 3.4.4: local `hybrid-engine/high` primary; local pipeline fallback.
- Marker 2.0.0: local Surya GGUF with CUDA llama.cpp, `balanced` primary; `force_ocr` fallback.
- Docling 2.114.0: local GraniteDocling VLM/CUDA primary; local standard CUDA OCR/table fallback.
- Official references: [MinerU](https://github.com/opendatalab/MinerU), [Marker](https://github.com/datalab-to/marker), [Docling](https://docling-project.github.io/docling/).

## Scoring contract

Automatic fidelity contributes 80 points: body token/character fidelity 25, scientific identifiers 15, headings/reading order 10, tables 15, formulas 10, and figure captions 5.
The blinded review contributes 20 points across reading order (5), tables (5), formulas (4), captions (3), and overall completeness (3). Speed is excluded from fidelity and used only after fidelity and success rate.
A recommendation requires at least 48 clean primary completions, zero silent truncations, and no systematic scientific-identifier integrity risk.

## Tool comparison

| Tool | Version | Primary success | Clean / 50 | Fallback success | ID risks | Trunc. | Auto / 80 | Manual / 20 | Total / 100 | Median pages/s | Peak VRAM MiB | Peak RAM GiB | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| mineru | 3.4.4 | 50 | 50 | 0/0 | 49 | 0 | 55.36 | 14.65 | 70.01 | 0.0494 | 13981 | 5.98 | FAIL |
| marker | 2.0.0 | 48 | 46 | 0/0 | 37 | 2 | 51.58 | 13.22 | 64.80 | 0.0879 | 5318 | 9.26 | FAIL |
| docling | 2.114.0 | 47 | 47 | 0/0 | 45 | 0 | 54.75 | 15.27 | 70.01 | 0.0432 | 4474 | 4.36 | FAIL |

## Stratified automatic fidelity

| Tool | Stratum | Clean / 10 | Auto / 80 | Median pages/s |
|---|---|---:|---:|---:|
| mineru | layout_stress | 10 | 52.11 | 0.0630 |
| mineru | formula_fe_topology | 10 | 55.16 | 0.0467 |
| mineru | table_review_clinical | 10 | 59.65 | 0.0482 |
| mineru | figure_am_biomaterial | 9 | 52.44 | 0.0476 |
| mineru | standard_born_digital | 10 | 57.43 | 0.0441 |
| marker | layout_stress | 10 | 55.19 | 0.1280 |
| marker | formula_fe_topology | 8 | 49.73 | 0.0302 |
| marker | table_review_clinical | 9 | 47.52 | 0.1236 |
| marker | figure_am_biomaterial | 9 | 51.86 | 0.0879 |
| marker | standard_born_digital | 10 | 53.59 | 0.0460 |
| docling | layout_stress | 9 | 53.20 | 0.0589 |
| docling | formula_fe_topology | 9 | 53.28 | 0.0432 |
| docling | table_review_clinical | 9 | 53.04 | 0.0479 |
| docling | figure_am_biomaterial | 10 | 56.74 | 0.0426 |
| docling | standard_born_digital | 10 | 57.47 | 0.0423 |

## Recommendation

No parser passed the production gate. The best observed candidate was `mineru`, but it is not recommended for production until the blocking gates below are resolved.

### Gate blockers

- `mineru`: systematic scientific-identifier integrity risk
- `marker`: clean completions 46/48 required; silent truncations 2/0 allowed; systematic scientific-identifier integrity risk
- `docling`: clean completions 47/48 required; systematic scientific-identifier integrity risk

## Lowest-scoring conversions

| Sample | Tool | Stratum | Auto score | Failure / boundary |
|---|---|---|---:|---|
| P006 | docling | layout_stress | 0.00 | markdown_missing |
| P013 | docling | formula_fe_topology | 0.00 | timeout |
| P022 | docling | table_review_clinical | 0.00 | timeout |
| P013 | marker | formula_fe_topology | 0.00 | timeout |
| P022 | marker | table_review_clinical | 0.00 | timeout |
| P040 | mineru | figure_am_biomaterial | 35.83 | silent truncation |
| P032 | mineru | figure_am_biomaterial | 40.98 | low fidelity |
| P014 | mineru | formula_fe_topology | 43.42 | low fidelity |
| P003 | mineru | layout_stress | 43.54 | low fidelity |
| P031 | marker | figure_am_biomaterial | 43.88 | silent truncation |

## Interpretation limits

JATS is an exact-DOI textual reference, not a pixel-identical representation of the publisher PDF; equations, tables, captions, and reference formatting may differ by publication version. The automatic score therefore measures recoverable scholarly content against JATS, while the blinded PDF review covers page reading order and placement-sensitive defects.
A primary process exit is not treated as semantic success: truncation and identifier gates remain separate. Conversely, a single missing identifier does not count as a process failure; only repeated risks can trigger the systematic-integrity gate.

## Integrity statement

This benchmark is isolated from the 10,000-paper production corpus and semantic-card workflow. Raw parser outputs are hashed before normalization; no benchmark result updates the writing Skill or corpus state.
