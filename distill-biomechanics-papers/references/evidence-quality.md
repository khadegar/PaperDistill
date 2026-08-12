# Evidence and Quality Rules

## Contents

1. Separation of concepts
2. Article-level assessment
3. Study-type checks
4. Claim-level confidence
5. Synthesis rules
6. Semantic claim gates

## 1. Separation of concepts

Score these dimensions independently:

- **Relevance:** how directly the study answers the current question
- **Credibility:** whether design, validation, analysis, and reporting support its conclusions
- **Access:** what content was actually read
- **Venue signal:** journal and article-type context
- **Contribution:** why the item matters to the manuscript

Do not use venue signal as a substitute for credibility or relevance.

## 2. Article-level assessment

Rate each dimension `strong`, `adequate`, `weak`, or `unclear`:

1. Research question and design alignment
2. Model/sample representativeness
3. Methods and parameter transparency
4. Verification and validation
5. Outcome definition and statistical/uncertainty treatment
6. Robustness, sensitivity, and comparator quality
7. Reproducibility: data, code, geometry, protocol, or sufficient detail
8. Conclusion proportionality
9. Bias, selective reporting, funding, and conflicts
10. Directness to the current question

Overall credibility:

- `high`: no critical weakness; most dimensions strong; independent validation or strong triangulation
- `moderate`: credible core result with bounded weaknesses
- `low`: important weaknesses materially limit inference
- `uncertain`: insufficient access or reporting to judge

Do not average away a critical flaw. Name the flaw and restrict permissible claims.

## 3. Study-type checks

### Computational/finite-element studies

Check geometry provenance, constitutive laws, boundary/load justification, contact, mesh convergence, code/solution verification, validation, sensitivity, uncertainty, and whether conclusions exceed modeled conditions.

### Bench and material studies

Check specimen number, independent builds/lots, specimen geometry, standards, calibration, environmental conditions, preconditioning, failure definition, statistics, and as-built characterization.

### Topology-optimization studies

Check objective/constraint clarity, convergence, mesh/design dependence, baseline fairness, manufacturing constraints, reconstruction loss, reanalysis, physical realization, and validation.

### Cell and animal studies

Check controls, sample size, randomization, blinding, predefined outcomes, time points, species/model relevance, attrition, histology/imaging quantification, and mechanical-biological alignment.

### Clinical studies

Check cohort definition, comparator, confounding, follow-up, missing data, endpoint validity, adverse events, effect size/uncertainty, registration, and generalizability.

### Reviews

Check question, search coverage, reproducibility, screening, duplicate handling, extraction, risk-of-bias treatment, synthesis suitability, and whether conclusions reflect included evidence.

## 4. Claim-level confidence

Assign confidence after considering:

- source credibility
- directness to the exact claim
- access level and locator quality
- agreement across independent studies
- precision and uncertainty
- plausible competing explanations

Use:

- `high`: direct, well-validated, precise, and corroborated
- `moderate`: direct and credible but limited in validation, precision, or replication
- `low`: indirect, weakly validated, imprecise, or based on one fragile study
- `uncertain`: evidence not accessible or insufficient to classify

An abstract-only record cannot receive high confidence for a detailed method or numerical result.

## 5. Synthesis rules

- Report study counts only after deduplication.
- Do not count multiple publications from one dataset as independent confirmation.
- Weight evidence qualitatively unless effect measures and populations/models are sufficiently comparable.
- State heterogeneity before reporting a pooled or representative value.
- Identify moderators such as anatomy, material, porosity, load case, model type, manufacturing process, and validation level.
- Include negative, null, failed, and contradictory findings.
- Separate established findings, plausible interpretations, open questions, and recommendations.
- Phrase novelty as a bounded gap in the searched corpus, not an absolute claim about all literature.

## 6. Semantic claim gates

Apply these blocking gates before accepting a sentence:

1. `State gate`: is it measured, predicted, inferred, or recommended?
2. `Role gate`: is the support verification, calibration, validation, sensitivity, or application evidence?
3. `Rung gate`: does the verb exceed the highest direct evidence level?
4. `Proxy gate`: is one endpoint being substituted for a different functional or clinical outcome?
5. `State gate`: do CAD, as-built, processed, aged, and healing states match?
6. `Comparator gate`: does the comparison isolate the claimed component, design variable, or intervention?
7. `Scope gate`: are anatomy, architecture, load, species/population, and time attached to the conclusion?
8. `Failure gate`: are null, adverse, failed, and contradictory observations represented?

For topology and AM, require evidence at each transition from raw numerical field through reconstructed CAD, as-built part, mechanics, biology, and clinical use. For algorithms, require proof, numerical verification, performance benchmarking, physical validation, and application evidence to remain distinct. For reviews, treat secondary summaries and bibliometric indicators as indirect evidence for scientific effects.

Use [semantic-writing-rules.md](semantic-writing-rules.md) for the operational definitions and promoted rule IDs.
