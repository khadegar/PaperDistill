# Writing and Export

## Contents

1. Source-of-truth model
2. Original research
3. Review manuscripts
4. Claim-grounded drafting
5. Style and revision
6. Export package

## 1. Source-of-truth model

- Research notes and synthesis: Markdown
- Structured corpus and claims: JSON/JSONL/CSV
- References: BibTeX
- Canonical English manuscript: LaTeX
- Submission PDF: compiled from LaTeX
- Collaboration copy: DOCX exported from the same validated source

Do not make independent edits to LaTeX and DOCX. Apply accepted DOCX feedback to LaTeX, then regenerate exports.

## 2. Original research

Use a journal-specific IMRaD structure when available. Default architecture:

1. Title and structured/unstructured abstract
2. Introduction: context, bounded gap, objective/hypothesis, contribution
3. Methods: geometry/materials/model/design/manufacturing/testing/statistics
4. Results: findings without interpretive inflation
5. Discussion: interpretation, comparison, mechanism, implications, limitations
6. Conclusion
7. Data/code availability, ethics, CRediT, funding, conflicts, AI disclosure as applicable

Treat the user's own data and outputs as the only source for Results. Literature may contextualize but must not fill missing results.

## 3. Review manuscripts

### Narrative review

Organize by mechanisms, methods, design variables, evidence maturity, or unresolved questions. State search limitations and avoid systematic-review language.

### Systematic review

Require a predeclared question, reproducible database searches, screening decisions, extraction schema, study-quality assessment, and flow counts. Use quantitative pooling only when outcome definitions, designs, and models are sufficiently compatible.

Never call a review systematic solely because it contains many references.

## 4. Claim-grounded drafting

Before prose, create a section-level claim map:

| Claim | Role | Evidence IDs | Counter-evidence | Confidence | Planned citation |
|---|---|---|---|---|---|

Write each paragraph as:

1. Topic claim
2. Evidence with scope and uncertainty
3. Comparison or counter-evidence
4. Interpretation bounded by the evidence
5. Link to the manuscript's question or contribution

Attach `% CLAIMS: ...` comments as defined in `data-contracts.md`.

Citation rules:

- Prefer the most direct primary source for a specific result.
- Use reviews for landscape statements and citation chaining, not as substitutes for primary evidence.
- Do not stack citations that were not individually checked.
- State disagreement when sources conflict.
- Preserve units, population/model, comparator, and direction.

## 5. Style and revision

Use discipline-specific, compact academic English:

- Start paragraphs with substantive claims.
- Avoid generic significance language and unsupported superlatives.
- Vary sentence structure naturally without sacrificing precision.
- Use cautious verbs calibrated to evidence: `demonstrated`, `suggested`, `was associated with`, `predicted`.
- Distinguish computational prediction, bench observation, animal evidence, and clinical outcome.
- Define abbreviations once and keep terminology stable.
- Use active voice when it improves clarity; do not force it in every sentence.

For revision, keep a traceability table with comment, decision, evidence, changed location, and status. Do not accept a reviewer suggestion that would make the manuscript less accurate; document a reasoned disagreement.

## 6. Export package

Before export:

1. Run project validation and resolve blocking errors.
2. Refresh target-journal article type and author instructions.
3. Freeze citekeys and update the bibliography.
4. Compile PDF from LaTeX with Tectonic or XeLaTeX.
5. Convert the same source to DOCX with Pandoc.
6. Render every DOCX page to images and visually inspect layout.
7. Check figures, tables, equations, references, cross-references, fonts, and page breaks.

Deliver, as requested:

```text
main.tex
references.bib
figures/
tables/
manuscript.pdf
manuscript.docx
cover-letter.tex or .docx
supplementary-material.*
audit-report.json
```

Keep rendered PNG pages as internal QA artifacts unless the user requests them.
