# Project Data Contracts

## Contents

1. Project layout
2. Identifiers
3. Project configuration
4. External production corpus
5. Project corpus manifest
6. Paper records
7. Claim evidence
8. Manuscript links
9. Semantic-distillation artifacts

## 1. Project layout

Use this stable layout:

```text
project/
|-- project.json
|-- search/
|   `-- search-log.csv
|-- corpus/
|   |-- manifest.jsonl
|   |-- records/
|   `-- extracts/
|-- evidence/
|   |-- claims.jsonl
|   `-- matrix.csv
|-- manuscript/
|   |-- main.tex
|   |-- references.bib
|   `-- sections/
|-- audit/
`-- exports/
```

Keep machine-readable state in JSON/JSONL/CSV. Keep human analysis in Markdown and the canonical manuscript in LaTeX.

## 2. Identifiers

- `paper_id`: `doi:<normalized-doi>` when a DOI exists; otherwise `pmid:<id>`, `arxiv:<id>`, or `title:<sha12>`.
- `claim_id`: project-unique `C000001` sequence.
- `search_id`: `S-YYYYMMDD-NNN`.
- `citekey`: stable BibTeX key; never silently change it after manuscript use.
- `source_hash`: SHA-256 of the analyzed PDF or exported source record.

Normalize DOI by lowercasing and removing `https://doi.org/`, `http://dx.doi.org/`, `doi:`, whitespace, and trailing punctuation.

## 3. Project configuration

Required `project.json` fields:

```json
{
  "schema_version": "1.0",
  "title": "Project title",
  "research_question": "",
  "manuscript_mode": "original",
  "analysis_language": "zh-CN",
  "manuscript_language": "en",
  "date_range": {"from": null, "to": null},
  "domains": [],
  "target_journal": null,
  "citation_style": "journal-specific",
  "writing_profile": "biomechanics-distilled-10k-v1",
  "source_layers": ["external_fulltext_corpus", "web_evidence", "web_calibration", "zotero_calibration"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

Allowed `manuscript_mode` values: `original`, `narrative_review`, `systematic_review`, `evidence_brief`.

Allowed domain values: `biomechanics`, `bone_implant`, `topology_optimization`, `porous_scaffold`, `additive_manufacturing`, `mechanobiology`, `osseointegration`, `patient_specific_implant`, `bone_regeneration_biomaterial`.

`writing_profile` and `source_layers` are optional provenance fields. `external_fulltext_corpus` marks the queryable external OA production corpus; `web_evidence` preserves live web/database discovery and verification; `web_calibration` marks inspected seed full texts selected through that route; `zotero_calibration` marks inspected seed full texts selected from the user's library. These labels identify provenance, not citation eligibility, and do not restrict which relevant papers may be cited.

## 4. External production corpus

The 10k-scale corpus is shared infrastructure and remains outside individual project directories and outside the Skill. Its stable layout is defined in `large-corpus-workflow.md`.

The external `candidates.jsonl` retains the full deduplicated reserve pool; `manifest.jsonl` records the fixed-size active selection. Core manifest fields are:

```json
{
  "paper_id": "doi:10.xxxx/example",
  "title": "Paper title",
  "year": 2024,
  "doi": "10.xxxx/example",
  "pmid": "12345678",
  "pmcid": "PMC1234567",
  "journal": "Journal title",
  "publication_type": "research article",
  "discovery_strata": ["biomechanics_fe_bone"],
  "source_url": "https://europepmc.org/article/MED/12345678",
  "fulltext_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1234567/fullTextXML",
  "selected": true
}
```

Each parsed `records/<PMCID>.json.gz` stores bibliography, license/provenance, abstract, section hierarchy, canonical section types, and section text. `source_acquired: true` means the JATS payload was acquired; it does not mean every manuscript claim has been manually verified. Claim-level use still requires a locator and verification status in the project evidence map.

`reports/stats.json`, not the target in the profile or the manifest count, is the source of truth for how many full texts are currently parsed and indexed. `reports/writing-patterns.json` must state the exact analyzed record count and must not embed source paragraphs.

Permanent access failures are written to `logs/replacements.jsonl`. A replacement manifest row additionally records `replaces_paper_id`, `replacement_reason`, and `replaced_at`; its `selection_rank` remains the rank of the removed row.

## 5. Project corpus manifest

Store one JSON object per logical paper in `corpus/manifest.jsonl`:

```json
{
  "paper_id": "doi:10.xxxx/example",
  "title": "Paper title",
  "year": 2024,
  "doi": "10.xxxx/example",
  "pmid": null,
  "arxiv_id": null,
  "citekey": "author_keyword_2024",
  "item_type": "journal_article",
  "discovery_sources": ["zotero", "scopus"],
  "zotero_item_keys": ["AB12CD34"],
  "local_files": [],
  "access_level": "abstract_read",
  "screening_decision": "include",
  "source_hash": null,
  "version_relation": null,
  "verified_at": null
}
```

Do not create separate logical papers for duplicate Zotero records or attachment children.

## 6. Paper records

Store `corpus/records/<safe-paper-id>.json`. Required top-level fields:

```json
{
  "schema_version": "1.0",
  "paper_id": "doi:10.xxxx/example",
  "bibliography": {},
  "provenance": {},
  "study": {},
  "domains": {},
  "outcomes": [],
  "limitations": {"reported": [], "analyst_identified": []},
  "quality": {},
  "summary_zh": "",
  "writing_uses": []
}
```

`bibliography` must include title, authors, year, source title, identifiers, item type, and citekey.

`provenance` must include access level, source path or URL, source hash when local, pages/sections reviewed, and retrieval/verification timestamp.

Each outcome must include:

- outcome name and definition
- value, unit, uncertainty, and sample/denominator when quantitative
- comparator or baseline
- direction and interpretation
- locator: page, section, table, figure, or supplement
- whether the value is directly reported or derived

## 7. Claim evidence

Store one JSON object per line in `evidence/claims.jsonl`:

```json
{
  "claim_id": "C000001",
  "claim_text_zh": "",
  "claim_text_en": "",
  "claim_type": "result",
  "paper_id": "doi:10.xxxx/example",
  "citekey": "author_keyword_2024",
  "access_level": "full_text_read",
  "locator": {"page": 7, "section": "Results", "table": "2", "figure": null},
  "evidence_paraphrase": "",
  "direction": "supports",
  "directness": "direct",
  "confidence": "moderate",
  "verification_status": "verified",
  "verified_at": "ISO-8601",
  "notes": ""
}
```

Allowed values:

- `claim_type`: `background`, `method`, `result`, `comparison`, `mechanism`, `gap`, `limitation`, `recommendation`
- `direction`: `supports`, `refutes`, `mixed`, `context`
- `directness`: `direct`, `indirect`, `extrapolated`
- `confidence`: `high`, `moderate`, `low`, `uncertain`
- `verification_status`: `verified`, `unverified`, `stale`, `conflict`

Quantitative `result` and `comparison` claims require `full_text_read` or `supplement_read` plus a non-empty locator.

## 8. Manuscript links

Place a machine-readable comment immediately after a supported sentence or paragraph in LaTeX:

```tex
Porous titanium reduced construct stiffness relative to the dense baseline \cite{author_keyword_2024}.
% CLAIMS: C000001,C000014
```

Rules:

- Every `CLAIMS` identifier must exist in `claims.jsonl`.
- Every claim citekey must exist in `references.bib`.
- Every external empirical assertion must have a claim link.
- The comment is intentionally invisible in PDF and DOCX exports.
- A paragraph may cite multiple claims, but do not reuse a claim for a materially different proposition.

`scripts/validate_project.py` enforces these cross-file relationships.

## 9. Semantic-distillation artifacts

Keep model-reading artifacts under the external corpus, not inside the Skill:

```text
<CORPUS_ROOT>/semantic-distillation/
|-- screening.jsonl
|-- selection.jsonl
|-- packets/<PMCID>.md
|-- cards/<PMCID>.json
|-- synthesis/promoted-rules.jsonl
`-- reports/
    |-- selection.json
    `-- validation.json
```

Use `scripts/prepare_semantic_distillation.py` to create unannotated packets and selection metadata. Use `assets/semantic-card-template.json` for the per-paper card. A packet is reading material, not evidence that a model read the paper. Mark a paper as semantically read only when its card has `reading.status: completed`, packet and source hashes match, all substantive chunk locators are accounted for, and structural validation passes.

For a pending row migrated from a verified PDF conversion, `record_path` may
identify the preferred MinerU Markdown outside the corpus root, and
`source_record_sha256` is the SHA-256 of those Markdown bytes. Record the PDF
path/hash, converter mode, source format, prior JATS path/hash, unavailable
visuals, and quality flags in packet/card provenance. The production PDF
manifest hash must equal both the current PDF bytes and the converter's recorded
input hash. This migration never constitutes semantic reading and never writes
the semantic roots of a card.

The packet records a packet-builder schema version and each chunk's source-line
span, block types, flags, and word count. Card provenance also records the
MinerU version/profile and parser quality flags. Migration transactions are
bounded to at most 100 papers and retain a durable manifest with old/new hashes;
uncommitted transaction PMCIDs are not eligible for Luna claims.

Store these provenance fields in every card:

- `paper_id`, `pmcid`, and `source_record_sha256`;
- reader role, model, reasoning effort, timestamp, and access level;
- packet hash, chunk locators read, declared omissions, and adjudication status;
- bibliography and discovery strata;
- study, argument, evidence-boundary, limitations, writing-capability, and quality objects.

Use stable packet locators of the form `S003:C02`, where `S003` is the zero-padded source-section ordinal and `C02` is the zero-padded chunk within that section. Do not convert a packet locator into a manuscript citation locator; verify the original full text again for claim-specific citation use.

Store one promoted capability per line in `synthesis/promoted-rules.jsonl`. Include:

```json
{
  "rule_id": "SDR-0001",
  "capability": "State a limitation with its likely direction of bias.",
  "scope": {
    "sections": ["discussion"],
    "domains": ["biomechanics"],
    "study_designs": ["computational", "bench"]
  },
  "supporting_cards": ["semantic:PMC0000001", "semantic:PMC0000002", "semantic:PMC0000003"],
  "supporting_locators": ["PMC0000001:S012:C01"],
  "supporting_journals": ["Journal A", "Journal B"],
  "counterexample_or_boundary": "Do not assign a bias direction when competing effects prevent one.",
  "credibility_gate": "passed",
  "promotion_status": "accepted"
}
```

Require at least three supporting cards, two journals, traceable locators, an explicit boundary or counterexample, and moderate/high credibility in at least two supporting cards before `promotion_status` can be `accepted`. Keep deferred or rejected candidates outside the Skill for later batches.
