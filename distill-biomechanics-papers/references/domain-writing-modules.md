# Domain Writing Modules

## Contents

1. Finite-element biomechanics
2. Bone implants and fixation devices
3. Topology optimization
4. Porous and TPMS scaffolds
5. Additive manufacturing and fatigue
6. Coupled mechanobiology
7. Mixed numerical–experimental studies
8. In vitro and in vivo bone-regeneration studies
9. Clinical, review, and algorithm evidence

Load only the modules relevant to the manuscript.

## 1. Finite-element biomechanics

### Methods order

1. Anatomy/sample and image provenance, resolution, inclusion criteria.
2. Segmentation, smoothing, geometry repair, and coordinate system.
3. Element type, mesh size, local refinement, and mesh-convergence criterion.
4. Constitutive laws, heterogeneity mapping, and parameter provenance.
5. Contacts, friction, ligaments/connectors, constraints, and load application.
6. Solver type, nonlinear settings, time/load steps, and convergence tolerances.
7. Verification, validation, sensitivity, and uncertainty.
8. Outcome definitions and regions of interest.

### Required distinctions

- `verification`: equations, implementation, mesh, and numerical convergence.
- `validation`: agreement with independent experimental or in vivo evidence.
- `calibration`: parameters fitted to observations.
- `sensitivity`: response to parameter variation.

Do not call literature-range agreement a complete validation without stating its independence and coverage. Report loading directions and outcome diversity; a model validated only for range of motion does not automatically validate local stress.

Build an explicit validation ledger. State which dataset calibrates parameters, which independent dataset validates which outcome, which quantities were only sensitivity-tested, and which outputs remain application predictions. When the model is called patient-specific, decompose personalization into geometry, material, load/boundary, contact, pathology, and calibration.

### Results order

Report mesh convergence and validation before predictions that depend on the model. Give agreement ranges, error metrics, and outliers rather than writing only “good agreement.”

## 2. Bone implants and fixation devices

Build the narrative through this chain:

```text
clinical complication → mechanical cause → design variable → mechanical/biological proxy → validation level
```

Report:

- anatomical site, pathology, procedure, and fixation state
- implant material and interface condition
- load cases and their physiological source
- stress/strain/SED/load-transfer outcomes in both implant and bone
- loosening, subsidence, fracture, or stress-shielding proxy definition
- whether the design meets strength and fatigue requirements while reducing stiffness mismatch

Do not equate a stress-shielding proxy with observed bone resorption unless a remodeling, animal, longitudinal, or clinical outcome supports the link.

## 3. Topology optimization

Report the optimization problem in this order:

1. Design and non-design domains.
2. State, design, and dependent variables.
3. Objective function with physical meaning.
4. Constraints with clinical/manufacturing meaning.
5. Material interpolation or geometry representation.
6. Filtering, projection, perimeter, connectivity, or regularization.
7. Sensitivity derivation and optimizer.
8. Initialization, continuation, move limits, convergence, and mesh dependence.
9. Baseline designs and ablation/parameter studies.
10. Geometry reconstruction and manufacturability.

Minimum comparison set when feasible:

- conventional/off-the-shelf or dense design
- a stiffness/compliance-only optimization
- the proposed multiphysics/time-dependent/patient-specific formulation
- at least one parameter or constraint sensitivity

State whether the topology is a mathematical design field, reconstructed CAD, or fabricated geometry at each validation stage.

Quantify every lossy transformation: filter/threshold settings, removed and restored features, raw-to-reconstructed volume and objective change, reanalysis, minimum feature size, and manufacturing deviation. Report competing axes in a Pareto table and constrain `optimal` to the exact load, geometry, material state, objective, and constraints.

## 4. Porous and TPMS scaffolds

Report architecture at both global and local scales:

- pore/feature topology and mathematical definition
- unit-cell size, wall/strut thickness, relative density, and porosity
- isotropy/anisotropy and spatial grading
- pore size distribution, connectivity, surface area, and permeability when relevant
- intended bone/tissue property target and anatomical site
- as-designed and as-built measurements

Mechanical reporting should separate elastic modulus, yield/plateau strength, energy absorption, fatigue life, and failure mode. Biological language must identify whether evidence is inferred from architecture, measured in vitro, demonstrated in vivo, or clinical.

Attach every measurement to a state: mathematical unit cell, reconstructed CAD, as-built architecture, post-processed surface, aged/immersed part, or explant. Treat nominal pore size, porosity, and surface area as design inputs until internal three-dimensional metrology verifies them.

## 5. Additive manufacturing and fatigue

### Manufacturing methods

Report:

- machine, material/powder, lot where material variability matters
- build orientation, layer thickness, energy/process parameters, supports
- atmosphere, heat treatment, surface treatment, cleaning, and machining
- dimensional/metrological method and deviation from CAD
- sample count, exclusions, and failed builds

### Fatigue methods and results

Report load ratio, waveform, frequency, environment, runout definition, stress normalization, censoring, and statistical model. Distinguish compressive and tensile behavior. Connect crack initiation to observed as-built defects or stress concentrations only when imaging/fractography supports it.

The calibrated AM exemplar shows why design and process must be reported in parallel: topology alone does not determine fatigue behavior.

A static peak stress below bulk yield is not a fatigue result. Before claiming durability, connect load history, environment, as-built defect/surface state, failure/runout definition, and observed failure mode. Keep external fit, internal architecture, static mechanics, fatigue, and biological fixation as independent validation blocks.

## 6. Coupled mechanobiology

Define:

- biological state variable and update rule
- mechanical stimulus and lazy/dead zone if used
- time step, remodeling/degradation horizon, and parameter source
- coupling direction and update sequence
- temporal sensitivity/adjoint treatment
- uncertainty in patient-specific or experimentally fitted parameters

Separate a predicted favorable stimulus from demonstrated tissue growth. When long-term outcomes are simulated, compare with an initial-only or time-independent design.

Trace `design/exposure → stimulus → cell/immune response → tissue → function`. Label every arrow and avoid treating a pathology label imposed through a boundary condition as modeled disease biology. Require a perturbation, rescue, or orthogonal measurement before using causal pathway language.

## 7. Mixed numerical–experimental studies

State the role of each evidence stream:

- calibration data set
- validation data set
- confirmatory experiment
- manufacturability demonstration
- mechanistic observation

Avoid using the same measurement for calibration and validation without disclosure. Align specimen geometry, boundary conditions, contact, and outcome definitions between simulation and experiment. Report disagreement and plausible sources rather than tuning the narrative around agreement alone.

## 8. In vitro and in vivo bone-regeneration studies

Build the evidence in causal order:

```text
material/process quality → as-built architecture → mechanical/degradation behavior
→ cell response → animal tissue response → bounded biological mechanism
```

Do not skip an upstream link when a downstream claim depends on it. In particular, a nominal pore size or CAD topology is not the exposure actually experienced by cells or tissue unless the as-built structure has been measured.

### Methods reporting

- Define material composition, sterilization, surface state, architecture, as-built metrology, and batch/specimen allocation.
- For cell work, report cell source, passage, seeding density, culture medium, controls, time points, assay normalization, image quantification, and biological versus technical replicates.
- For animal work, report ethics approval, reporting guideline, species/strain/sex/age, defect site and size, group allocation, randomization, blinding, sample-size rationale, anesthesia/analgesia, exclusions, endpoints, and adverse events.
- Separate early immune/inflammatory outcomes, osteogenic markers, bone volume/ingrowth, interface strength, degradation, and systemic safety. Define every ROI and threshold used for micro-CT or histomorphometry.
- State whether in vitro observations selected the in vivo design, served as mechanistic support, or were independently confirmatory.

### Results and interpretation

Report fabrication and baseline mechanical quality before biological outcomes. Within biological Results, move from viability and phenotype to tissue-level structure and function, preserving time-point and group comparisons. State sample counts alongside each analysis and distinguish representative images from quantified evidence.

Mechanistic claims require converging evidence across the proposed pathway. Concordant cell markers and animal bone formation may support a mechanism; either alone supports only its own measurement level. Negative, transient, and discordant outcomes remain visible.

Translate by validation level:

- architecture or assay only: `is consistent with` or `may support`
- replicated in vitro response: `promoted/inhibited [measured cell outcome]`
- animal defect response: `increased/decreased [measured tissue outcome] in this model`
- clinical benefit: reserve for clinical evidence

For multicomponent systems, map every ingredient and proposed interaction to the comparator that identifies it. Use `combined effect` when no factorial or interaction contrast supports synergy. Apply a separate endpoint ladder to each claimed function, such as antimicrobial activity, osteogenic response, vascularization, mechanics, and safety.

## 9. Clinical, review, and algorithm evidence

### Clinical device studies

- Audit whether the comparator also changes surgical approach, fixation, selection, workflow, rehabilitation, anatomy, or follow-up.
- Separate device survival, imaging, interface mechanics, symptoms, PROMs, revision, and adverse events.
- Report sparse-event estimates with event counts, intervals, missingness, and confounding.
- Treat case timelines and treatment bundles as feasibility and decision evidence unless an effect is isolated.

### Reviews and bibliometrics

- Identify article kind before extraction.
- Keep review-process findings, included-study results, synthesis, and recommendations separate.
- Stratify moderators before summarizing direction; preserve null and contradictory studies.
- Treat publication/citation/keyword metrics as literature-system indicators, not effect evidence.

### Computational algorithms

- Separate proof, implementation verification, numerical benchmark, controlled runtime, physical validation, and biomedical application.
- Report hardware/software, stopping criteria, initialization, problem size, worst-case error, and boundary artifacts.
- Treat agreement with another numerical method as method agreement, not specimen ground truth.
