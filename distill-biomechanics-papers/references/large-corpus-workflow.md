# 10k Full-text Corpus Workflow

## Purpose

Use a three-layer calibration system:

- The external production corpus targets 10,000 deduplicated open full texts. It supplies broad terminology, section architecture, reporting-frequency, and retrieval coverage.
- The model-read semantic set supplies traceable qualitative interpretation of argument structure, evidence boundaries, rhetorical moves, and domain-specific writing logic.
- The manually inspected web/Zotero seeds supply additional qualitative exemplars and source-route coverage.

Do not load the production corpus into the model context. Index it once, compute deterministic aggregate metrics, and retrieve only task-relevant sections. Corpus frequency calibrates writing form; it does not prove a scientific claim.

## Preserve all source routes

The production corpus adds a source layer; it does not replace either existing route:

1. Continue live web/database discovery for current, classic, contradictory, and target-journal evidence.
2. Continue using Zotero and local PDFs for the user's recurring literature and locally accessible full text.
3. Use the external OA corpus for broad retrieval and writing-pattern calibration.
4. Merge logical papers by DOI, then PMCID/PMID, then normalized title/year, while retaining every provenance route.

For a manuscript claim, inspect and verify the retrieved source at claim level. Never promote an aggregate corpus pattern to empirical evidence.

## External layout

Keep all source full texts outside the Skill:

```text
<CORPUS_ROOT>/
|-- manifest.jsonl
|-- candidates.jsonl
|-- raw/jats/*.xml.gz
|-- records/*.json.gz
|-- corpus.sqlite
|-- logs/fetch-status.jsonl
`-- reports/
    |-- discovery.json
    |-- stats.json
    |-- index.json
    |-- pipeline-running.json
    |-- pipeline.json
    |-- writing-patterns.json
    `-- writing-patterns.md
```

`assets/corpus-profile-10k.json` is the versioned selection configuration. `reports/stats.json` is the current acquisition state and must be consulted instead of relying on a hard-coded paper count.

## Build and resume

Inspect `--help` before overriding defaults.

```powershell
python scripts/manage_fulltext_corpus.py estimate
python scripts/manage_fulltext_corpus.py discover --root <CORPUS_ROOT>
python scripts/manage_fulltext_corpus.py fetch --root <CORPUS_ROOT>
python scripts/manage_fulltext_corpus.py index --root <CORPUS_ROOT>
python scripts/manage_fulltext_corpus.py stats --root <CORPUS_ROOT> --json
python scripts/manage_fulltext_corpus.py audit --root <CORPUS_ROOT>
python scripts/manage_fulltext_corpus.py replace-failures --root <CORPUS_ROOT>
python scripts/distill_large_corpus.py --root <CORPUS_ROOT>
python scripts/run_corpus_pipeline.py --root <CORPUS_ROOT>
```

`fetch` is checkpointed: an existing raw XML and parsed record are skipped. Use `--reparse-existing` after changing the JATS parser; this rebuilds records from local raw XML without redownloading it. Keep the configured global request-start rate even when multiple workers are used. `discover` retains the full deduplicated candidate pool as `candidates.jsonl`; `replace-failures` records a permanent 404/410 or invalid-full-text event and substitutes the best unused candidate with overlapping strata while keeping the manifest target fixed.

`run_corpus_pipeline.py` is the unattended end-to-end runner. It resumes fetching, retries unresolved items for a bounded number of passes, replaces permanent failures, reparses every retained raw XML with the current parser, rebuilds the index, recomputes aggregate patterns with a 400-paper stratified audit sample, runs structural reconciliation, refreshes statistics, and writes `reports/pipeline.json`. While active, its PID and log path are recorded in `reports/pipeline-running.json`.

The automated route uses Europe PMC open-access JATS. Add publisher or institutional copies only through their supported access route and store the article-level license/provenance. Zotero/local PDF extraction remains a separate supplemental route.

## Selection model

Build a union across overlapping strata, then deduplicate and select to the unique-paper target. The default profile covers:

- bone/implant finite-element biomechanics
- bone implants and fixation
- topology optimization
- porous, lattice, and TPMS scaffolds
- additive manufacturing
- mechanobiology and remodeling
- osseointegration and bone ingrowth
- patient-specific implants
- bone-regeneration biomaterials

Quotas are coverage controls, not journal or quality rankings. A paper may belong to several strata. Do not impose a top-journal whitelist.

## Index and task retrieval

Rebuild `corpus.sqlite` after a material acquisition batch. For a writing task:

1. Translate the Writing Brief into concept clusters, synonyms, and required study designs.
2. Query titles, headings, and section text; prefer the relevant section types.
3. Deduplicate results at the paper level and diversify by stratum, year, study design, and evidence direction.
4. Retrieve a small working set of sections, normally 20–80 papers for synthesis and fewer for direct claim verification.
5. Verify important claims against the full text and record locators in the project evidence map.

Example:

```powershell
python scripts/manage_fulltext_corpus.py query --root <CORPUS_ROOT> --query "stress shielding finite element implant" --section introduction --section discussion --top 30
```

If the corpus index lacks a current, classic, contradictory, or target-journal source, use the preserved live web/database workflow. If Zotero contains a locally important paper, include it even when it is absent from the OA corpus.

## Distillation model

Run `distill_large_corpus.py` after indexing or after a substantial fetch batch. It reads parsed records one at a time and produces only aggregate metrics plus stable IDs for a deterministic stratified audit sample. It measures:

- title and abstract length distributions
- section presence, order, word counts, and sentence length
- rhetorical-move frequencies by section
- domain-reporting markers for FE, topology optimization, porous scaffolds, AM, validation, uncertainty, and translational caution
- journal, year, stratum, and license composition

The ID-only audit sample should be deeply inspected before changing the writing playbook. Accept a new rule only when it is recurrent across multiple papers and remains sensible across at least two venues or study designs. Store the derived rule, scope, counterexamples, and corpus version; do not store source paragraphs.

## Semantic reading and rule promotion

After deterministic distillation, use `scripts/prepare_semantic_distillation.py` to create a domain-balanced full-body reading set and stable chunk locators. Then follow `semantic-distillation-workflow.md`.

Keep these states explicit:

- `parsed`: a JATS record exists;
- `packet_prepared`: all substantive sections were rendered for reading;
- `semantic_read_complete`: a model-read card accounts for the packet and passes structural validation;
- `adjudicated`: the card has survived consistency and counterexample review;
- `rule_promoted`: a recurrent capability has passed cross-paper promotion gates and was written into the Skill.

Never infer a later state from an earlier one. In particular, 10,000 parsed records do not mean 10,000 semantic readings. Report deterministic-corpus and model-read coverage separately.

Do not promote a rule from frequency alone. Require at least three located paper cards, two journals, a stated scope, a counterexample or failure mode, and adequate credibility. Keep cards and synthesis reports in `<CORPUS_ROOT>/semantic-distillation/`; place only concise transferable rules and provenance counts in the Skill.

## Quality gates

Before calling a corpus build complete, require:

- manifest contains the target number of unique logical papers
- raw and parsed counts are reconciled; failed items have explicit logs
- permanent access failures are replaced from the reserve pool with an auditable event log
- duplicate DOI/PMCID checks pass
- JATS sections are parseable and methods/results inheritance has been checked on a sample
- SQLite paper count matches the parsed-record count
- retrieval smoke tests return domain-relevant sections
- aggregate reports state the analyzed full-text count and do not imply that unacquired manifest items were analyzed
- every manuscript claim still has a verified source and locator

## Updates

Version selection-profile changes. Preserve the prior manifest and reports when the query set, target, or deduplication policy changes materially. Routine `fetch`, `index`, and `distill` runs may update the current build in place because they are deterministic and resumable; record timestamps and counts in `reports/`.
