# Model-Read Semantic Writing Rules

## Contents

1. Status and use
2. Cross-domain reasoning rules
3. Computational and topology rules
4. Manufacturing and biomaterial rules
5. Biological, clinical, and review rules
6. Drafting protocol

## 1. Status and use

This reference contains capabilities promoted from 45 completed full-text semantic cards in `biomechanics-fulltext-10k-v1`. The primary reader was `gpt-5.6-sol` at `max` reasoning effort. Each accepted rule recurred in at least three papers, crossed at least two journals, retained chunk-level locators, and included a boundary or counterexample. The external auditable source is `<CORPUS_ROOT>/semantic-distillation/synthesis/promoted-rules.jsonl`.

Use these rules as reasoning constraints, not as scientific evidence or prose to imitate. Verify claim-specific literature again before citing it in a manuscript. The 45-paper semantic set supports the capabilities below; it does not imply semantic reading of all 10,000 corpus records.

## 2. Cross-domain reasoning rules

### SDR-0001 — Evidence-state ledger

Label each substantive claim before drafting:

- `measured`: directly observed or quantified in the study;
- `predicted`: produced by a model, simulation, deconvolution, or algorithm;
- `inferred`: mechanistic or causal interpretation of evidence;
- `recommended`: future experiment, design choice, or practice proposal.

Preserve the label through abstracts, summaries, revisions, and conclusions. Do not turn a prediction into a measurement or an inference into a demonstrated mechanism by shortening the sentence.

### SDR-0002 — Validation firewall

Create separate rows for:

1. numerical or implementation `verification`;
2. parameter `calibration`;
3. independent `validation`;
4. parameter or structural `sensitivity`;
5. `application prediction`.

For each row, name the dataset, outcome, independence, coverage, and error metric. Validation is not transitive: agreement in range of motion does not validate local stress, and a calibrated group cannot serve as undisclosed independent validation.

### SDR-0003 and SDR-0004 — Evidence rung and endpoint vector

Let the highest directly supported rung control the verb. Keep architecture, cell response, animal tissue response, interface mechanics, imaging, clinical failure, and patient-reported function distinct.

For implant integration, build an endpoint vector:

```text
surface/contact | bone amount/pattern | formation rate | matrix quality
| interface mechanics | fixation | symptoms/function | revision | durability
```

State which elements were measured and which remain missing. Do not let BIC, micro-CT, ISQ, stress, survival, or PROM stand in for the whole vector.

### SDR-0014 — Null and failure evidence

Report null, adverse, failed, and contradictory outcomes with denominator, time, condition, and failure mode. Use them to restrict the mechanism and conclusion. Distinguish `zero events`, `not assessed`, and `not reported`.

### SDR-0015 — Causal-arrow audit

Write a multiscale claim as:

```text
design/exposure → mechanics or transport → cell/immune response
→ tissue response → function or clinical outcome
```

Tag every arrow `measured`, `predicted`, `inferred`, or `missing`. When an arrow is inferred, name at least one competing explanation and the perturbation or measurement that would discriminate it.

## 3. Computational and topology rules

### SDR-0005 — Complete design contract

Before using `optimized`, report:

- anatomy/geometry and provenance;
- materials and parameter provenance;
- contacts, supports, loads, directions, and weights;
- objective and its physical meaning;
- constraints and their clinical/manufacturing meaning;
- design variables and protected/non-design regions;
- interpolation, filter, projection, regularization, and mesh dependence;
- initialization, continuation, convergence, and solution status;
- comparator, ablation or parameter sweep;
- reconstruction, manufacturing, and validation plan.

Use identical objective and constraint terminology in the abstract, Methods, figures, and implementation.

### SDR-0006 — Conditional optimum and Pareto reporting

Attach every optimum to architecture, dimensions, material/process state, loading, model or species, time, objective, and constraints. Report mass, stiffness, stress, displacement, strength, fatigue, transport, manufacturability, and biological evidence as separate axes. Use `trade-off` when axes move in conflicting directions.

### SDR-0007 and SDR-0018 — Lossy state transitions

Keep these states separate:

```text
raw optimization field → reconstructed CAD → manufacturable design
→ as-built part → mechanically tested construct → biological/clinical application
```

For filtering, thresholding, local extraction, topology cleanup, or manual repair, record thresholds, removed and restored features, boundary artifacts, geometry/volume change, objective loss, reanalysis, and sensitivity. A cleaned numerical geometry is not an as-built validation.

### SDR-0017 — Algorithm evidence ladder

Report algorithmic evidence in this order:

```text
mathematical property/proof → implementation verification → numerical benchmark
→ controlled efficiency comparison → physical validation → biomedical translation
```

Disclose hardware, software, precision, stopping rule, initialization, and problem size for runtime comparisons. Agreement with another numerical method or analytical benchmark does not establish specimen truth or clinical utility.

### SDR-0010 — Patient-specific decomposition

Audit personalization across geometry, material, load/boundary, implant/contact, pathology, biology, surgical tolerance, workflow, and outcomes. State exactly which dimensions were individualized and validated. Do not use `patient-specific` without this decomposition.

## 4. Manufacturing and biomaterial rules

### SDR-0008 — Material-state ledger

Name the state attached to every property: CAD, as-printed, cleaned, heat-treated, machined, surface-treated, sterilized, dehydrated, rehydrated, immersed, aged, or explanted. Do not transfer a nominal or pre-process property to a later state without measurement.

### SDR-0009 — Static-to-fatigue firewall

Separate static strength, stiffness, micromotion, fatigue life, interface fixation, and clinical durability. A static stress below bulk yield strength supports only the stated static comparison. Fatigue claims require a relevant load history, ratio, frequency, environment, runout/failure definition, as-built state, defects or surface state, specimen count, and test or validated fatigue model.

### SDR-0011 — Component attribution

For coatings, scaffolds, cells, drugs, and treatment bundles, make a component-by-comparator matrix. Identify whether each effect is isolated, jointly observed, or inferred. Reserve `synergy` for an interaction contrast or equivalent design; otherwise write `combined effect` or `outcome during the combined regimen`.

### SDR-0016 — Degradation evidence

Use `immersion-conditioned response`, `swelling`, `erosion`, or `mechanical change` unless degradation is supported by direct mass loss, molecular change, chemical products, or another explicit degradation measure. Match every degradation claim to time, medium, geometry, and material state.

## 5. Biological, clinical, and review rules

### SDR-0012 — Article-kind firewall

Determine whether the source is original research, systematic review, scoping review, narrative review, bibliometric study, cohort, case series, or case report before extracting evidence. Separate a review's search and synthesis from the results of included studies. Use reviews for taxonomy and evidence maps; use original studies for direct result claims when available.

### SDR-0013 — Comparator audit

Before writing clinical or device superiority, test whether groups also differ in surgical approach, fixation mechanics, selection, workflow, rehabilitation, anatomy, loading, or follow-up. When these factors are not isolated, report an association and the plausible confounding direction.

### SDR-0019 — Heterogeneity before synthesis

Stratify by study type, species/population, anatomy/defect, intervention form/dose, comparator, fixation, time, endpoint, bias, and certainty before stating a direction. Do not use study count, citation frequency, or pooled direction as a substitute for comparability.

### SDR-0020 — Case and treatment-bundle timeline

Write cases as:

```text
patient/anatomy → constraint → rejected option → decision rationale
→ intervention → complication or turning point → dated structural outcome
→ dated functional outcome → bounded lesson
```

Extract a transferable decision principle, but keep causal and efficacy language hypothesis-generating when there is no isolating comparator.

## 6. Drafting protocol

Before prose, create five linked tables:

1. `claim ledger`: claim ID, evidence state, source, locator, permissible verb;
2. `validation ledger`: verification, calibration, validation, sensitivity, application;
3. `state ledger`: CAD/material/process/age/healing state attached to each outcome;
4. `endpoint vector`: measured proxies and missing functional/clinical endpoints;
5. `boundary ledger`: comparator confounding, scope, likely bias direction, and next discriminating test.

Then draft in evidence order. During revision, reject any sentence that crosses a ledger boundary without an explicit bridge and qualifier.
