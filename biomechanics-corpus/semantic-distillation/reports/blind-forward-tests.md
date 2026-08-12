# Fresh-Context Blind Forward Tests

Date: 2026-08-10  
Skill: `distill-biomechanics-papers`  
Agents: three fresh-context `gpt-5.6-luna` workers at maximum reasoning effort  
Access restriction: published Skill and referenced guidance only; no semantic cards or promoted-rule JSONL

## Test 1 — FE and topology optimization

Prompts tested calibration data reused as validation, outcome-specific validation, SIMP mass reduction, manual topology smoothing, external-only dimensional metrology, fatigue, and biological-fixation overclaim.

Pass observations:

- classified mesh convergence as verification and ligament fitting as calibration;
- rejected reuse of the calibration ROM curve as independent validation;
- treated cage stress and adjacent-segment IDP as application predictions;
- noted that ROM validation would not transitively validate local stress or IDP;
- separated raw topology, smoothed CAD, printed external dimensions, internal geometry, mechanics, fatigue, and biology;
- constrained `optimal` to the stated design contract and load case.

## Test 2 — Additive manufacturing, porous scaffolds, and biology

Prompts tested CAD versus micro-CT as-built porosity, static compression, 10^6-cycle fatigue, MC3T3-E1 ALP, and a complete multicomponent coating compared only with bare substrate.

Pass observations:

- ordered Results as as-built metrology → static mechanics → fatigue → cell assay;
- kept fatigue protocol and static strength as separate evidence;
- limited ALP to an in-vitro osteogenic-marker outcome rather than osseointegration;
- wrote the coating effect as a combined association;
- rejected component attribution and synergy without component-isolating/factorial controls;
- proposed the exact comparator matrix and missing interface/tissue validation.

## Test 3 — Review synthesis and clinical comparison

Prompts tested a mixed in-vitro/animal/case-series/RCT review with positive and null findings, and a retrospective custom-plate versus intramedullary-nail cohort with different approaches and fixation mechanics.

Pass observations:

- stratified by study type, population/model, comparator, endpoint, time, bias, and certainty;
- retained null and contradictory RCT results;
- rejected study count as an efficacy estimate;
- used `associated with` for the retrospective comparison;
- identified treatment-bundle, selection, approach, fixation, workflow, rehabilitation, and follow-up confounding;
- bounded the conclusion to the cohort and proposed matched/prospective validation.

## Verdict

PASS. All three fresh-context agents applied the intended evidence-state ledger, validation firewall, proxy ladder, lossy-state transitions, component-attribution rule, heterogeneity rule, and comparator audit. Reported residual gaps were missing study inputs in the hypothetical prompts, not missing Skill safeguards.
