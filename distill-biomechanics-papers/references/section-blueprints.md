# Section Blueprints

## Contents

1. Title
2. Abstract
3. Introduction
4. Methods
5. Results
6. Discussion
7. Conclusion
8. Review-paper variant
9. Evidence-state pass

## 1. Title

Name the study object and the differentiating method or evidence. Keep the title searchable and literal.

Useful forms:

```text
[Mechanism]-based [optimization/design method] of [implant/scaffold] for [outcome]
[Mechanical or biological outcome] of [manufactured structure] with [architecture/process variable]
[Device/model]: development, verification, and validation under [loading/application]
[Patient-specific device] designed by [method] and evaluated by [validation mode]
```

Do not add `novel`, `advanced`, or `optimized` unless the manuscript defines the comparison that earns the term.

## 2. Abstract

Use a six-move abstract for original studies:

1. `Context`: name the clinical/biological application.
2. `Problem`: identify the mechanical or manufacturing failure mechanism.
3. `Objective`: state the study action and exact novelty delta.
4. `Methods`: identify design/model, key variables, comparator, and validation.
5. `Results`: report the primary quantitative findings, including direction and magnitude.
6. `Meaning`: give one bounded implication and, when material, one limitation.

Drafting frame:

```text
[Application] is limited by [specific failure mechanism]. Existing [devices/methods] [defined limitation].
This study [developed/evaluated] [method/device] to [objective]. [Model/specimen count] was analyzed under [conditions]
and compared with [baseline]; [validation method] assessed [metric]. [Primary result with magnitude and uncertainty].
[Secondary result]. These findings [calibrated implication], subject to [main boundary].
```

The Zotero subset averaged about 246 abstract words and the web full-text subset about 203. Treat this as a calibrated range, not a target; follow the journal's article type and word limit.

Abstract audit:

- No citation unless required.
- No acronym used only once.
- No method detail without a corresponding result.
- At least one numerical primary result for quantitative studies.
- No clinical efficacy claim from a purely numerical study.

## 3. Introduction

Use a biomechanics-specific funnel:

1. Clinical or biological importance of the device/tissue/problem.
2. Mechanical mechanism connecting the problem to failure or adaptation.
3. Current device, model, optimization, or manufacturing approaches.
4. What those approaches omit under the study's actual conditions.
5. Exact knowledge or capability gap.
6. Objective/hypothesis, contribution, comparator, and validation plan.
7. Optional roadmap for mathematically dense manuscripts.

Paragraph contract:

- Paragraph 1: the problem and affected outcome.
- Paragraphs 2–3: prior approaches organized by function, not author chronology.
- Paragraph 4: unresolved limitation with boundary conditions.
- Final paragraph: what was done, why it differs, how it was tested, and the hypothesis when applicable.

Strong gap:

```text
Existing studies optimize initial construct stiffness, but do not account for the time-dependent change in bone–implant load transfer during remodeling.
```

Weak gap:

```text
Few studies have investigated this important topic.
```

Use the weak form only when the search log supports it, and immediately specify what is absent.

## 4. Methods

Order Methods according to causal dependency. A common computational/experimental sequence is:

1. Study design, specimens or image data, and ethics.
2. Geometry acquisition and preprocessing.
3. Material/constitutive models.
4. Device, scaffold, or optimization design.
5. Boundary, contact, and loading conditions.
6. Solver, sensitivity, convergence, and stopping criteria.
7. Manufacturing and post-processing when applicable.
8. Experimental/mechanical/biological validation.
9. Outcomes and statistical analysis.

For every method component answer:

```text
What was done? Why was it selected? Which values/settings were used? Where did they come from?
How was quality checked? Which result depends on it?
```

Equations:

- Introduce the physical purpose before the equation.
- Define every symbol immediately after first use.
- State units and admissible ranges.
- Translate objective and constraints into physical or clinical meaning.
- Separate algorithmic convergence from physical validation.

## 5. Results

Mirror the Methods and research questions. A reliable sequence is:

1. Quality control, convergence, or validation.
2. Primary outcome.
3. Comparator/baseline analysis.
4. Parameter, sensitivity, subgroup, or failure-mode analysis.
5. Manufacturing or experimental agreement when present.

Use this sentence pattern:

```text
Under [condition], [design/model] produced [outcome] of [value ± uncertainty], which was [difference] relative to [comparator] ([test/effect size]).
```

Then point to the evidence:

```text
The spatial distribution was concentrated at [region] (Fig. X), whereas [comparator] showed [contrasting pattern].
```

Keep mechanism claims out of a separate Results section. If the journal uses combined Results and Discussion, label the transition from observation to interpretation.

Before writing each Results paragraph, mark every planned statement as `measured` or `predicted`. Move mechanistic explanations to the Discussion or explicitly label them as interpretation in a combined section. Report nulls, failed states, reconstruction losses, and adverse outcomes in the same evidence sequence as favorable results.

For multiscale biomaterial or biological studies, use a causal results ladder when it better matches the experiment:

```text
composition/process → as-built architecture → mechanics/degradation
→ in vitro response → in vivo response → pathway evidence
```

Do not use a later biological outcome to hide a failed manufacturing, mechanical, or cytocompatibility prerequisite.

## 6. Discussion

Use six moves:

1. Answer the primary research question with the main finding.
2. Explain the most plausible mechanical/biological/manufacturing mechanism.
3. Compare with prior studies after aligning geometry, material, loading, porosity, process, and outcome definition.
4. State design, methodological, biological, or clinical implications at the supported level.
5. State limitations and their likely direction of bias.
6. Define the next validation step that addresses the largest uncertainty.

Opening frame:

```text
The principal finding was [result], supporting/refuting [hypothesis]. The effect was most evident under [condition] and was accompanied by [secondary evidence].
```

Mechanism frame:

```text
This response may be explained by [mechanism], because [measured/model evidence]. An alternative explanation is [alternative], which was not isolated by [study limitation].
```

Limitation frame:

```text
This study is limited by [specific choice]. This choice likely [direction of effect] because [reason]. Accordingly, the findings apply to [scope] and require [validation] before [broader claim].
```

Avoid ending with a generic call for “more research.” Name the missing experiment, cohort, load case, material, scale, or time horizon.

For mixed in vitro/in vivo studies, discuss concordance and discordance explicitly. A cell-level mechanism should not be presented as the cause of an animal outcome unless the pathway is measured at both levels or supported by an intervention that isolates it.

Use a causal-arrow audit for every mechanism paragraph:

```text
observation → proposed intermediate → downstream outcome
```

Label each arrow as measured, predicted, inferred, or missing. State a competing explanation and the next discriminating test when the central arrow is not directly identified.

## 7. Conclusion

Use four moves in one short section:

1. Restate what was developed or tested.
2. Give the primary supported result.
3. State the contribution relative to the comparator or gap.
4. Give one bounded application or next validation step.

Do not introduce new citations, mechanisms, parameter values, or recommendations.

## 8. Review-paper variant

Use this architecture for narrative/systematic reviews in the field:

1. Scope and why the review is needed.
2. Search sources, dates, queries, screening, and item types.
3. Taxonomy based on engineering decisions, not paper chronology.
4. Cross-study matrices for design variables, materials/processes, loads, outcomes, and validation.
5. Convergent findings and contradictions.
6. Methodological weaknesses and transfer limits.
7. Research agenda ordered by evidential bottleneck.

Useful taxonomy axes include:

- patient-specific versus generic geometry
- dense, lattice, TPMS, stochastic, and topology-optimized architecture
- static, fatigue, permeability, biological, animal, cadaveric, and clinical validation
- initial mechanics versus time-dependent remodeling/degradation
- as-designed versus as-manufactured geometry
- single-scale versus global–local or multiscale optimization

Determine article kind before pooling evidence. Keep original studies, systematic reviews, scoping reviews, narrative reviews, and bibliometric studies in separate evidence roles. Expose moderators and null findings before giving a direction; do not let publication count or citation prominence stand in for effect evidence.

## 9. Evidence-state pass

Run this pass after every section draft:

| Section | Permitted transition | Blocking transition |
|---|---|---|
| Introduction | external evidence → bounded gap | review/bibliometric frequency → efficacy or absolute novelty |
| Methods | decision → parameter provenance → quality check | sourced parameter → implied calibration |
| Results | measured/predicted output → magnitude/comparator/uncertainty | output → unlabelled mechanism or clinical benefit |
| Discussion | evidence → qualified inference → boundary | proxy → endpoint substitution |
| Conclusion | highest evidence rung → proportional answer | feasibility → universal superiority or clinical efficacy |

Use the ledgers in [semantic-writing-rules.md](semantic-writing-rules.md) to resolve a blocked transition.
