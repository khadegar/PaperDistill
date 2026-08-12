# Model-read Semantic Distillation

## Contents

1. Purpose and evidence layers
2. Select the reading set
3. Read and annotate each paper
4. Extract writing capabilities
5. Promote cross-paper rules
6. Update the Skill
7. Validate and report coverage

## 1. Purpose and evidence layers

Use semantic distillation when the user asks Codex to learn writing ability from a paper corpus rather than merely retrieve evidence or compute corpus statistics.

Keep four layers distinct:

| Layer | Unit | Permissible use |
|---|---|---|
| Corpus statistics | All parsed records | Calibrate prevalence, length, headings, and terminology |
| Model-read paper cards | Complete substantive body of selected papers | Analyze argument, reporting logic, evidence boundaries, and writing moves |
| Cross-paper capabilities | Adjudicated recurring patterns | Guide planning, drafting, revision, and audit |
| Claim-specific literature | Sources verified for the current manuscript | Support scientific assertions and citations |

Never describe a record as semantically read because it was parsed, indexed, searched, or matched by a regular expression. Report the exact number of completed semantic cards separately from the parsed-corpus count.

## 2. Select the reading set

Run `scripts/prepare_semantic_distillation.py` against the external corpus. Use its output only to prepare reading material; do not treat its lexical classifications as semantic findings.

Build a deterministic, auditable set that:

- covers every configured discovery stratum;
- diversifies journal, year, article kind, and study design;
- includes original studies, reviews, favorable examples, weak examples, null results, and contradictory evidence;
- excludes corrections, retractions, editorials, duplicate datasets, and records without substantive text;
- preserves the web and Zotero calibration routes alongside the external corpus;
- stores full source text outside the Skill.

Use staged coverage. Start with a cross-domain batch, inspect selection errors, then extend the same versioned sample. Do not imply that a 45- or 90-paper semantic batch represents 10,000 model-read papers.

## 3. Read and annotate each paper

Read every non-reference chunk in the selected packet. Complete one card based on `assets/semantic-card-template.json`.

The model designated as the primary reader must personally read every declared chunk and make the semantic judgments in the card. By default this is the root Codex model. When the user explicitly designates Luna Max for a corpus or shard, record `luna_primary / gpt-5.6-luna / max` and treat that Luna worker—not its summary—as the primary reader for those cards. Use a different reader, when available, to challenge classifications and boundaries. Do not run the card assembler with `--write` until the designated primary-reader overlay is complete and source-linked.

For every completed card:

1. Verify the packet and record hashes.
2. Record all chunk locators read and any omissions.
3. Identify the research problem, exact gap, objective or hypothesis, study design, comparator, method spine, and primary outcomes.
4. Separate verification, calibration, validation, sensitivity, and application prediction.
5. Reconstruct the central argument with claim, data, warrant, backing, qualifier, and rebuttal.
6. Classify statements as measured, predicted, inferred, or recommended.
7. Identify proxy-to-outcome bridges and overclaim risks.
8. Describe section-level writing moves with source chunk locators.
9. Record author-reported and reader-identified limitations, including likely bias direction.
10. Propose only concise, source-independent writing capabilities; never store source sentences as templates.

Use `strong`, `adequate`, `weak`, or `unclear` for component quality. Use `high`, `moderate`, `low`, or `uncertain` for overall credibility. A prestigious venue does not override a critical methodological weakness.

## 4. Extract writing capabilities

Extract abilities, not phrases. Prefer capabilities such as:

- convert a broad clinical problem into a mechanically testable gap;
- align the final Introduction paragraph with comparator and validation design;
- order Methods by causal dependency rather than software chronology;
- disclose parameter provenance as measured, fitted, assumed, or sourced;
- distinguish mathematical topology, reconstructed CAD, and as-built geometry;
- align simulation and experiment without reusing calibration data as validation;
- present quality control before dependent outcomes;
- compare study conditions before comparing values;
- explain a mechanism using measured intermediates and an explicit alternative explanation;
- state a limitation with direction of bias and the next discriminating test;
- stop translation at the highest evidence level reached.

For each candidate capability, record its manuscript use, domain, design scope, supporting locators, counterexample or boundary, and confidence.

Reject candidates that merely restate a topic, repeat a source phrase, depend on one stylistic quirk, or confuse frequent wording with effective reasoning.

## 5. Promote cross-paper rules

Promote a candidate into a Skill rule only when all applicable gates pass:

- `recurrence`: supported by at least three semantically read papers;
- `diversity`: spans at least two journals and either two study designs or a clearly declared narrow design module;
- `traceability`: every supporting paper has chunk locators;
- `quality`: at least two supporting papers have moderate or high credibility;
- `counterexample`: a contrary or weak execution has been examined;
- `scope`: the rule states where it applies and where it stops;
- `independence`: the rule is phrased without copying source language;
- `utility`: the rule changes a planning, drafting, revision, or audit decision.

Record rejected and deferred candidates outside the Skill so future batches can revisit them. Do not erase contradictory executions; use them to define failure modes and boundaries.

Classify promoted rules:

- `cross-domain`: useful across biomechanics article types;
- `section-specific`: title, abstract, Introduction, Methods, Results, Discussion, or Conclusion;
- `domain-specific`: FE, implant, topology optimization, porous/TPMS, AM/fatigue, mechanobiology, biological, or mixed validation;
- `study-design-specific`: computational, bench, cell, animal, clinical, or review;
- `anti-overclaim`: controls evidence-to-inference transitions.

## 6. Update the Skill

Write detailed promoted rules into the smallest relevant reference:

- cross-domain reasoning and prose rules: `distilled-writing-playbook.md`;
- section moves: `section-blueprints.md`;
- domain and study-design rules: `domain-writing-modules.md`;
- credibility and claim boundaries: `evidence-quality.md`;
- corpus counts and semantic-reading coverage: `corpus-provenance.md`.

Keep `SKILL.md` as a router and core workflow. Add only the semantic-distillation trigger, reading route, and hard distinction between parsed and model-read counts. Do not place paper-by-paper cards or source full text in the Skill.

Assign each promoted rule a stable identifier in the external synthesis report. Where helpful, cite the identifier in a Skill reference comment or provenance table, not in manuscript prose.

## 7. Validate and report coverage

Before claiming an updated semantic profile:

- validate every card against the template and permitted enums;
- verify card hashes and chunk locators against the packet;
- reconcile selected, completed, excluded, and adjudicated counts;
- confirm that no source paragraphs entered the Skill;
- run the Skill validator and representative writing prompts;
- forward-test difficult distinctions with fresh-context agents when the user permits subagents;
- report the parsed-corpus count and semantic-card count separately;
- label remaining coverage gaps by stratum, study design, and validation level.

Use this wording pattern:

```text
The profile uses N parsed full texts for deterministic calibration and M completed full-text semantic cards for model-read writing rules. It does not claim semantic reading of all N papers.
```
