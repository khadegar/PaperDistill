# Workflow

## Contents

1. Intake
2. Scope and search
3. Ingest and screening
4. Distillation and synthesis
5. Writing and audit
6. Recovery paths

## 1. Intake

Collect only fields that change the current task:

- Research question or broad direction
- Manuscript mode: original, narrative review, systematic review, or evidence brief
- Anatomy, device, material, process, and modeling method
- Existing inputs: PDFs, DOI/PMID list, RIS/BibTeX, Zotero collection, data/results, draft
- Time range and languages
- Target journal or journal family, if any
- Desired artifact and deadline

For a broad direction, propose two or three answerable research questions and converge before a large search. For a supplied paper or draft, enter at the relevant stage without repeating completed work.

## 2. Scope and search

Produce a research-question brief containing:

- `research_question`
- `sub_questions`
- `in_scope` and `out_of_scope`
- population/anatomy/device/material/process
- intervention/design/comparator/outcomes when applicable
- method families and validation types
- date and language limits
- anticipated paper type

Design at least three query blocks:

1. Anatomy/device/problem terms
2. Mechanics/design/manufacturing terms
3. Method/outcome/validation terms

Use synonyms, spelling variants, acronyms, controlled vocabulary, and database-specific syntax. Record the exact submitted query rather than an idealized reconstruction.

When the external production corpus is available, query it first for broad full-text recall and study-design coverage, then run the preserved live web/database search for currency, classic sources, counter-evidence, and target-journal evidence. Add relevant Zotero/local items independently. The corpus query is a retrieval aid, not a replacement search strategy.

Checkpoint: approve the question, scope, databases, query concepts, date range, and inclusion/exclusion criteria.

## 3. Ingest and screening

Accept any combination of:

- Local PDF files or directories
- DOI, PMID, ISBN, or arXiv identifiers
- RIS, BibTeX, CSV, or JSON exports
- Zotero local library or collection
- Search-result exports from subscription databases
- Stable paper/section IDs retrieved from the external full-text corpus

Normalize and deduplicate in this order:

1. Exact DOI
2. PMID or arXiv ID
3. Normalized title plus year
4. Fuzzy title plus first author, with manual confirmation

Preserve all discovery sources and Zotero item keys on the retained record. Never count attachment children as papers.

Screen with explicit decisions:

- `include`
- `exclude_scope`
- `exclude_type`
- `exclude_duplicate`
- `exclude_language`
- `exclude_no_evidence`
- `awaiting_full_text`
- `uncertain_manual_review`

Do not exclude a relevant study solely because its journal is not top-ranked.

Checkpoint: present the flow counts, included titles, uncertain records, and leading exclusion reasons before batch distillation.

## 4. Distillation and synthesis

For each paper selected into the task-level working set:

1. Verify bibliographic metadata.
2. Record access level and source hash.
3. Extract the common study fields.
4. Select applicable domain modules from `domain-extraction.md`.
5. Extract outcomes with units, uncertainty, comparator, and locator.
6. Record author-stated and analyst-identified limitations separately.
7. Emit claim-level evidence records.
8. Assign article-level credibility and relevance assessments.

Synthesize by question, mechanism, method, or outcome. Do not organize the main synthesis as one paragraph per paper.

Required synthesis outputs:

- Evidence matrix
- Consensus and convergence map
- Contradictions and plausible moderators
- Methodological pattern map
- Negative/null evidence
- Knowledge and validation gaps
- Candidate contribution statements
- Claims that remain unsupported

Checkpoint: approve the evidence matrix and gap map before manuscript planning.

Do not perform claim-level extraction on all 10,000 production records. Use deterministic corpus aggregation for writing-pattern statistics, then retrieve a bounded task-specific set for detailed extraction and verification.

## 5. Writing and audit

### Original-study path

Treat the user's results as primary evidence and literature as contextual evidence. Build:

1. Research gap and contribution statement
2. Journal-fit statement
3. IMRaD outline with word budget
4. Claim-evidence map
5. Section drafts
6. Limitations and reporting statements

Do not invent results, analyses, statistics, ethical approvals, or study settings.

### Review path

Use narrative synthesis by default. Activate systematic-review reporting only when the user explicitly selects it and the search/screening process supports it. Do not label an informal search as systematic.

### Audit path

Before export, verify:

- Every citekey resolves to one BibTeX entry.
- Every external claim has at least one evidence record.
- Quantitative claims have locators and access level `full_text_read` or `supplement_read`.
- Source statements are faithful to direction, population/model, comparator, and uncertainty.
- Citations and reference list are bidirectionally complete.
- Retracted, corrected, preprint, and published-version relationships are explicit.
- Target-journal article-type and formatting rules are current.

Checkpoint: approve target venue and article type. Integrity failures must be resolved before export.

## 6. Recovery paths

| Problem | Action |
|---|---|
| Research question remains broad | Offer three narrower RQs and defer large retrieval |
| Too few records | Expand synonyms, adjacent fields, citation chaining, and date range |
| Too many records | Tighten population/device, method, outcome, and validation requirements |
| Full text unavailable | Keep metadata/abstract status and limit permissible claims |
| Scanned PDF | Mark `ocr_required`; do not infer missing text |
| Conflicting results | Compare model/sample, material, boundary conditions, outcome definition, and bias |
| Duplicate Zotero records | Merge for analysis; retain every Zotero key in provenance |
| Journal facts are stale | Refresh official scope, article types, and current metrics |
| Export command unavailable | Preserve validated LaTeX/Markdown sources and report the missing renderer |
