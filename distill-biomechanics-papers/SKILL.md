---
name: distill-biomechanics-papers
description: Write, outline, revise, and audit English scientific papers in biomechanics, bone implants, finite-element modeling, topology optimization, porous or TPMS scaffolds, mechanobiology, biomaterials, and additive manufacturing using model-read semantic writing rules, a queryable external 10k-scale full-text corpus, Zotero/local literature, and verified live web/database evidence. Use when Codex needs to semantically distill full papers, build or query the corpus, or turn a research question, data, figures, literature, or draft into a journal-ready title, abstract, introduction, methods, results, discussion, conclusion, review article, response-aligned revision, claim-evidence outline, or LaTeX/DOCX/PDF manuscript.
---

# Write Biomechanics Papers from Distilled Literature

Use Chinese for planning and diagnostic feedback by default. Write manuscript prose in publication-ready English unless the user requests another language.

## First rule

Treat the distilled corpus as a writing calibration layer, not a phrase bank or citation whitelist. Reconstruct each manuscript from the user's research question, evidence, figures, data, and verified literature. Never imitate a source sentence closely or cite a paper merely because its structure informed the writing.

## Route the request

| User intent | Mode | Deliverable |
|---|---|---|
| Turn an idea/results set into a paper plan | `plan` | Writing Brief, argument spine, outline, evidence map |
| Draft one manuscript section | `draft-section` | Section contract, English prose, claim links, self-audit |
| Draft an original article | `write-original` | Claim-grounded IMRaD manuscript |
| Draft a review | `write-review` | Search-grounded taxonomy, synthesis, gap agenda |
| Improve an existing draft | `revise` | Diagnosed and revised text with change rationale |
| Compute aggregate writing-pattern calibration | `calibrate` | Versioned corpus statistics and diagnostic excerpts |
| Personally read papers and learn transferable abilities | `semantic-distill` | Completed semantic cards, promoted rules, coverage report |
| Build or resume the external full-text corpus | `corpus-build` | Versioned manifest, parsed JATS, index, and status report |
| Retrieve task-relevant full-text sections | `corpus-query` | Ranked, deduplicated evidence candidates with provenance |
| Find or verify evidence | `research` | Reproducible mixed web/Zotero corpus |
| Check references and claims | `audit` | Blocking integrity report |
| Produce submission files | `export` | LaTeX, PDF, DOCX, Markdown, and BibTeX package |

For an end-to-end request, run `plan → research/distill → outline approval → section drafting → audit → export`. Do not force the full pipeline for a single-section request.

## Load relevant guidance

Always read before manuscript drafting:

- [references/distilled-writing-playbook.md](references/distilled-writing-playbook.md) for the argument model, calibrated style, and quality gates.
- [references/section-blueprints.md](references/section-blueprints.md) for section-level rhetorical moves.
- [references/semantic-writing-rules.md](references/semantic-writing-rules.md) for model-read evidence boundaries, validation firewalls, and promoted writing capabilities.

Read conditionally:

- [references/domain-writing-modules.md](references/domain-writing-modules.md) for the applicable FE, implant, topology, scaffold, AM, fatigue, mechanobiology, biological, or mixed-validation module.
- [references/corpus-provenance.md](references/corpus-provenance.md) when explaining or updating the distilled basis.
- [references/large-corpus-workflow.md](references/large-corpus-workflow.md) before building, updating, indexing, distilling, or querying the external 10k-scale corpus.
- [references/search-access.md](references/search-access.md) before web, institutional, Zotero, or database retrieval.
- [references/domain-extraction.md](references/domain-extraction.md) when extracting evidence from papers.
- [references/evidence-quality.md](references/evidence-quality.md) before synthesis or claim weighting.
- [references/data-contracts.md](references/data-contracts.md) before creating an auditable project.
- [references/journal-presets.md](references/journal-presets.md) for journal matching or corpus breadth, never as an inclusion whitelist.
- [references/writing-export.md](references/writing-export.md) before final formatting or export.
- [references/luna-full-corpus-protocol.md](references/luna-full-corpus-protocol.md) before assigning full-corpus or shard-level semantic reading to Luna Max.

## Writing workflow

### 1. Establish the Writing Brief

Use `assets/writing-brief-template.md`. Capture:

- article type and target journal
- clinical/biological problem and mechanical mechanism
- exact gap and novelty delta
- primary objective or hypothesis
- comparator, primary outcome, and validation level
- available data, figures, local literature, Zotero items, and web sources
- applicable domain modules and known limitations

If information is incomplete, create a provisional brief with explicit assumptions and missing-evidence flags. Do not fabricate study details.

### 2. Freeze the evidence boundary

Separate:

- `study_evidence`: the user's analyses, experiments, figures, and tables
- `external_evidence`: verified full text, abstract, or metadata with access labels
- `inference`: interpretation derived from evidence
- `recommendation`: proposed future action or design choice

Do not write numerical Results until the underlying values and locators are available. Do not infer full-text details from an abstract.

Create five ledgers before prose when the study is technically complex:

- claim state: `measured / predicted / inferred / recommended`;
- evidence role: `verification / calibration / validation / sensitivity / application prediction`;
- material or geometry state: CAD, reconstructed, as-built, processed, aged, healing, or explanted;
- endpoint vector: measured proxies and missing functional/clinical outcomes;
- boundary: comparator confounding, scope, bias direction, and next discriminating test.

### 3. Build the argument spine

Create one chain before drafting:

```text
problem → mechanism → exact gap → objective/hypothesis → method/comparator
→ validation → primary result → interpretation → bounded implication
```

Attach claim IDs and evidence locators. Use `assets/section-draft-template.md` for paragraph planning. Resolve broken links before prose.

### 4. Select the manuscript architecture

- Computational method/topology study: formulation → implementation → benchmark → biomedical case → sensitivity/manufacturability.
- FE biomechanics/validation study: model development → verification → validation → application predictions → uncertainty.
- Implant design study: clinical failure → mechanical target → design → FE/experimental validation → translational boundary.
- Scaffold/AM study: architecture and process → as-built characterization → static/fatigue/transport/biological outcomes → failure mode.
- In vitro/in vivo biomaterial study: process and as-built exposure → mechanics/degradation → cell response → animal tissue response → bounded mechanism.
- Mixed numerical–experimental study: simulation and experiment receive separate methods, aligned conditions, and explicit calibration/validation roles.
- Review: reproducible search → design/outcome taxonomy → cross-study matrices → contradictions → evidence bottlenecks.
- Algorithm study: mathematical property → implementation verification → benchmark and controlled runtime → physical validation → biomedical boundary.

### 5. Draft in evidence order

Draft `Results → Methods → Discussion → Introduction → Conclusion → Abstract → Title`. This is the default learned from the corpus-to-writing workflow; use another order only when the user's materials justify it.

For each paragraph:

1. State one functional claim.
2. Supply the study result or external evidence.
3. Explain the mechanism only at the supported level.
4. State the consequence or transition.

### 6. Run section audits

- Abstract: contains context, problem, objective, methods, quantitative result, and bounded meaning.
- Introduction: ends with the exact gap, contribution, comparator/validation, and hypothesis when applicable.
- Methods: supports reproducibility; separates verification, validation, calibration, and sensitivity.
- Results: follows research-question order; reports magnitude, unit, uncertainty, comparator, and locator.
- Discussion: begins with the answer, compares conditions before values, tests alternative explanations, and states directional limitations.
- Conclusion: introduces no new result, citation, mechanism, or recommendation.
- Evidence state: no sentence changes from predicted/inferred to measured/demonstrated during compression.
- Endpoint scope: no architecture, cell, imaging, mechanical, or clinical proxy substitutes for another endpoint without a labeled bridge.

### 7. Audit and export

Run `scripts/validate_project.py` before export. Resolve every blocking orphan citation, unverified used claim, result without full-text access/locator, duplicate study, and missing BibTeX key. Then use `scripts/export_manuscript.py`.

## Hybrid source policy

Preserve both source layers:

- Keep the existing web/database discovery workflow for current, classic, contradictory, and target-journal evidence.
- Use Zotero full texts and local PDFs for the user's recurring research context and locally available evidence.
- Use the external open-full-text corpus for broad retrieval and aggregate writing calibration; keep its source files outside the Skill.
- Use completed model-read semantic cards and accepted cross-paper rules for reasoning calibration; keep cards, packets, and locators outside the Skill.
- Keep the manually inspected web and Zotero subsets as qualitative seed/audit exemplars; do not mistake them for the full production corpus.
- Derive writing calibration from inspected full text on either route, never from metadata or abstract alone.
- Merge by DOI or normalized title/year while retaining every provenance route.
- Weight evidence at the study level; journal rank and Zotero frequency are not inclusion criteria.
- Record whether each item came from `external_fulltext_corpus`, `web_calibration`, or `zotero_calibration`.

## Large-corpus operating rule

Never place all 10,000 papers in model context. Use this sequence:

1. Discover and deduplicate to the versioned manifest.
2. Fetch and parse supported open full texts with checkpoints.
3. Index sections in SQLite FTS.
4. Compute deterministic corpus-level writing metrics.
5. Retrieve only task-relevant sections and diversify the working set.
6. Verify claim-specific evidence against the cited full text and record locators.

Corpus statistics calibrate structure, terminology, and reporting expectations. They do not establish novelty, consensus, mechanism, or efficacy. Preserve live web/database search and Zotero/local retrieval for evidence gaps and current or user-specific literature.

## Semantic-distillation operating rule

Use this route when the user asks Codex itself to read papers and learn writing ability:

1. Prepare a versioned, domain-balanced reading set with `prepare_semantic_distillation.py`.
2. Read every substantive non-reference chunk of each selected packet; lexical labels are candidate cues only.
3. Complete one semantic card per paper with article kind, design, evidence states, validation roles, argument map, limitations, section moves, capability candidates, hashes, and locators.
4. Record the actual primary reader. Use the root model by default; when the user explicitly designates Luna Max, each Luna worker personally reads every assigned chunk and is recorded as `luna_primary / gpt-5.6-luna / max`. A summary from another agent never replaces that read.
5. Promote a rule only after recurrence in at least three completed cards, support across at least two journals, traceable locators, adequate credibility, a counterexample, and an explicit scope.
6. Update the smallest relevant reference and keep `SKILL.md` as the router.
7. Run strict semantic validation and fresh-context forward tests before reporting the Skill updated.

Always report parsed-corpus and completed-semantic-card counts separately. Read live counts from the semantic status/validation reports; never imply semantic reading of every parsed record until every selected card is completed and globally validated.

## Distilled style defaults

Apply these defaults unless the target journal or reporting guideline differs:

- Use informative, literal titles; the Zotero subset averaged about 12 words and rarely needed a subtitle.
- Keep abstracts within the target journal's limit; the two calibration subsets averaged roughly 200–250 words, with at least one quantitative primary result in quantitative studies.
- Use present tense for established knowledge, past tense for completed methods/results, and calibrated modal verbs for inference.
- Use first-person plural for research choices and passive voice for procedures when the actor is irrelevant.
- Prefer stable technical terminology, direct paragraph openings, and explicit comparators.
- Link clinical or biological implications through a measured mechanism; never promote a numerical proxy directly to clinical efficacy.
- Report unfavorable, null, and contradictory findings alongside favorable results.

## Source and citation integrity

- Never invent references, DOI values, parameters, statistics, page locators, or experimental conditions.
- Verify important citations by DOI/publisher metadata and inspect full text for claim-specific use.
- Cite original studies for results when available; use reviews for taxonomy and field-level synthesis.
- Maintain one-to-one correspondence among claim IDs, manuscript citations, and BibTeX entries.
- Keep quotations short and exceptional. Prefer precise paraphrase with a locator.
- Search for counter-evidence before claiming consensus, superiority, novelty, or clinical relevance.

## Scripts

Inspect `--help` before optional or overwrite arguments.

```powershell
python scripts/init_project.py --target <PROJECT_DIR> --title "<TITLE>"
python scripts/distill_writing_patterns.py --corpus <PAIRED_TEXT_BIB_DIR> --out-json <REPORT.json> --out-md <REPORT.md>
python scripts/manage_fulltext_corpus.py discover --root <EXTERNAL_CORPUS_DIR>
python scripts/manage_fulltext_corpus.py fetch --root <EXTERNAL_CORPUS_DIR>
python scripts/manage_fulltext_corpus.py index --root <EXTERNAL_CORPUS_DIR>
python scripts/manage_fulltext_corpus.py query --root <EXTERNAL_CORPUS_DIR> --query "<TERMS>" --top 30
python scripts/manage_fulltext_corpus.py stats --root <EXTERNAL_CORPUS_DIR> --json
python scripts/manage_fulltext_corpus.py audit --root <EXTERNAL_CORPUS_DIR>
python scripts/manage_fulltext_corpus.py replace-failures --root <EXTERNAL_CORPUS_DIR>
python scripts/distill_large_corpus.py --root <EXTERNAL_CORPUS_DIR>
python scripts/run_corpus_pipeline.py --root <EXTERNAL_CORPUS_DIR>
python scripts/prepare_semantic_distillation.py --root <EXTERNAL_CORPUS_DIR> --per-stratum 5 --card-stubs
python scripts/assemble_semantic_cards.py --root <EXTERNAL_CORPUS_DIR> --all
python scripts/validate_semantic_distillation.py --root <EXTERNAL_CORPUS_DIR> --strict
python scripts/manage_semantic_reading.py status --root <EXTERNAL_CORPUS_DIR> --json
python scripts/manage_semantic_reading.py claim --root <EXTERNAL_CORPUS_DIR> --worker <LUNA_WORKER> --limit 3 --json
python scripts/manage_semantic_reading.py verify --root <EXTERNAL_CORPUS_DIR> --lease-id <LEASE_ID> --json
python scripts/zotero_journal_stats.py --bib <LIBRARY.bib> --domain biomechanics --top 50
python scripts/extract_pdf.py <PAPER.pdf> --out <PAGES.jsonl>
python scripts/build_matrix.py --project <PROJECT_DIR>
python scripts/validate_project.py --project <PROJECT_DIR>
python scripts/audit_scientific_prose.py <MANUSCRIPT.tex> --mode original --submission-ready
python scripts/export_manuscript.py --project <PROJECT_DIR> --formats pdf,docx
```

Do not overwrite user files without explicit instruction. Do not bundle source full texts in the Skill.

## Checkpoints for guided full-manuscript work

Require confirmation at these boundaries:

1. Writing Brief and exact research gap.
2. Evidence boundary and claim map.
3. Manuscript architecture and paragraph-level outline.
4. Results/Methods factual freeze.
5. Discussion interpretation and limitations.
6. Target journal before final formatting.

Citation integrity and export validation remain blocking quality gates.

## Output discipline

- Present Chinese planning or diagnosis separately from English manuscript prose.
- Label all assumptions, unavailable evidence, and unresolved conflicts.
- Report corpus flow as `identified → deduplicated → screened → full text → included` when research is performed.
- Report semantic coverage as `selected → fully read → completed → promoted`, separately from parsed-corpus coverage.
- Deliver drafts with stable citation keys and claim-evidence traceability.
- For DOCX delivery, render and inspect every page with the installed document workflow.
