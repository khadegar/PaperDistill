# Distilled Biomechanics Writing Playbook

## Contents

1. Governing model
2. Manuscript argument spine
3. Style profile distilled from the corpus
4. Evidence and citation behavior
5. Drafting sequence
6. Quality gates
7. Model-read semantic controls

## 1. Governing model

Write as a biomechanics researcher making a traceable technical argument, not as a general-purpose summarizer. Every section must perform a distinct job:

| Section | Primary job | Reader's question |
|---|---|---|
| Title | Identify object, intervention/method, and outcome | What exactly was studied? |
| Abstract | Compress the complete evidence chain | Why, what, how, what was found, and what follows? |
| Introduction | Establish a specific unresolved problem | Why was this study necessary? |
| Methods | Make the study reproducible and auditable | Could another group repeat or challenge it? |
| Results | Report the evidence in question-aligned order | What happened, by how much, and against what comparator? |
| Discussion | Explain meaning without upgrading evidence | Why did it happen, how does it compare, and where does inference stop? |
| Conclusion | Answer the research question proportionally | What is supported now? |

The corpus repeatedly uses a `clinical/biological need → mechanical mechanism → design or analysis method → validation → bounded implication` chain. Preserve all five links when the study spans them.

Journal section order is not the argument model. The web subset included conventional IMRaD, combined Results and Discussion, domain-led mathematical sections, and Results–Discussion–Methods. Map the same evidence chain into the target journal's structure rather than forcing one visible heading order.

## 2. Manuscript argument spine

Build this before prose:

```text
Problem state
  ↓
Failure mechanism or unmet design requirement
  ↓
Specific gap in prior methods/evidence
  ↓
Study objective or hypothesis
  ↓
Method choice and comparator
  ↓
Validation evidence
  ↓
Primary result with magnitude and uncertainty
  ↓
Mechanistic interpretation
  ↓
Bounded engineering, biological, or clinical implication
```

Reject a proposed paragraph when its role in this chain is unclear. Background breadth does not compensate for a missing gap, and methodological novelty does not compensate for missing validation.

### Contribution sentence

Use one contribution sentence that names the delta over prior work:

```text
This study [develops/evaluates/compares] [specific method or device] to address [defined limitation], and validates it against [baseline/data/experiment] using [primary outcomes].
```

Avoid unsupported priority claims. Use “for the first time” only after an explicit, current novelty search.

### Multiscale biological spine

When the study connects manufacturing, mechanics, and biology, expand the central chain:

```text
design decision → as-built exposure → mechanical/degradation response
→ cell or immune response → tissue response → translational boundary
```

Require evidence at every claimed link. A visually plausible architecture, favorable modulus, or cell assay does not by itself establish osseointegration or clinical performance.

## 3. Style profile distilled from the corpus

### Sentence and paragraph behavior

- Prefer technical sentences around 18–28 words, with shorter sentences for primary findings and longer sentences for definitions or mechanisms.
- Keep Methods syntactically steady and procedural. Allow greater sentence-length variation in the Introduction and Discussion.
- Use one stable term per concept. Do not rotate among synonyms for implant, scaffold, cage, model, or outcome.
- Make the grammatical subject informative: `the model`, `the optimized cage`, `the gradient scaffold`, or `the experiment`, not vague subjects such as `this` or `it`.

### Voice and tense

- Use present tense for established knowledge and statements visible in a figure or table.
- Use past tense for actions completed in the study and observed results.
- Use first-person plural selectively for research decisions: `We developed`, `We compared`, `We hypothesized`.
- Use passive voice when the procedure or specimen is more important than the actor: `Specimens were fabricated...`.
- Attribute interpretation to evidence: `The lower peak stress suggests...`; do not write `It is obvious that...`.

### Hedging ladder

Match the verb to evidential directness:

| Evidence state | Preferred language |
|---|---|
| Directly measured and replicated | `showed`, `demonstrated`, `was higher/lower` |
| Model output supported by validation | `predicted`, `indicated`, `was consistent with` |
| Mechanistic interpretation | `suggests`, `may reflect`, `is attributable to` |
| Translation beyond study conditions | `may support`, `has potential to`, `warrants evaluation` |

Do not translate reduced stress, strain, stiffness, or compliance directly into improved clinical outcome. Insert the biological or clinical bridge and label it as an inference.

### Preferred rhetorical rhythm

- Start a paragraph with its claim or object, not meta-commentary.
- Follow a comparison with a mechanism or boundary condition, not another unconnected comparison.
- End important paragraphs with a consequence for the research question, design decision, or next analysis.
- Use `However` or `In contrast` only for genuine opposition. Use `Therefore` only when the preceding sentences logically entail the next claim.

## 4. Evidence and citation behavior

Apply section-specific citation density:

- Introduction: cite clinical/biological context, competing methods, and the exact gap. Group citations only when they support the same proposition.
- Methods: cite parameter sources, constitutive laws, standards, algorithms, and validation datasets. Distinguish adopted values from values calibrated in the study.
- Results: cite no external literature unless the journal permits a combined Results and Discussion section. Link claims to the study's own figure, table, or dataset.
- Discussion: cite direct comparators and competing explanations. Compare conditions before comparing values.

For every quantitative sentence record:

```text
outcome + direction + magnitude + unit + uncertainty/dispersion + comparator + sample/model count + test + locator
```

Do not cite a review for a result when the original study is available. Reviews remain valuable for taxonomy, scope, and research-gap framing.

## 5. Drafting sequence

Draft in evidence order rather than manuscript order:

1. Freeze figures, tables, model outputs, and experimental results.
2. Write Results from the frozen evidence.
3. Write Methods so every reported result has a reproducible antecedent.
4. Build the Discussion around the primary result, mechanisms, comparison, implications, and limitations.
5. Write the Introduction to justify the study that was actually performed.
6. Write the Conclusion as a bounded answer.
7. Write the Abstract last and remove every detail not needed to reconstruct the evidence chain.
8. Finalize title and keywords after terminology stabilizes.

This order prevents promising analyses in the Introduction that never appear in Results and prevents Methods from describing procedures that produced no reported output.

## 6. Quality gates

### Argument gate

- One explicit gap, not a list of broad shortcomings.
- One primary objective/hypothesis aligned with the primary result.
- Comparator choice justified.
- Novelty expressed as a difference in capability, evidence, or scope.

### Reproducibility gate

- Geometry/sample provenance, material laws, boundary conditions, algorithms, process parameters, and statistics are complete.
- Verification, validation, calibration, and sensitivity analysis are not conflated.
- Every parameter is identified as measured, fitted, assumed, or sourced.

### Evidence gate

- Result sentences include magnitude and comparator where available.
- Figure/table callouts match the claim.
- Null and unfavorable results remain visible.
- Discussion claims never exceed the access level or study design.

### Prose gate

- Delete throat-clearing phrases and generic importance claims.
- Replace vague adjectives (`robust`, `excellent`, `significant` without a statistical meaning) with the measured property.
- Limit each sentence to one primary claim unless two clauses are causally linked.
- Vary sentence length outside procedural Methods.
- Verify consistent symbols, abbreviations, units, and anatomical terminology.

## 7. Model-read semantic controls

Apply [semantic-writing-rules.md](semantic-writing-rules.md) before drafting or revising substantive claims. In particular:

- maintain a `measured / predicted / inferred / recommended` claim ledger;
- keep verification, calibration, validation, sensitivity, and application prediction behind separate evidence gates;
- let the highest directly supported evidence rung control the verb;
- represent implant integration and success as an endpoint vector, not one proxy;
- trace causal arrows and identify missing links or discriminating tests;
- retain null, adverse, failed, and contradictory outcomes;
- state the material, geometry, manufacturing, age, or healing state attached to every result.

These controls were promoted from recurrent, locator-backed patterns and counterexamples in the completed model-read semantic set. They are reasoning constraints, not sources to cite.
