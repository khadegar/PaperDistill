# Domain Extraction Schema

## Contents

1. Common study fields
2. Biomechanics
3. Bone implants
4. Topology optimization
5. Porous scaffolds
6. Additive manufacturing
7. Cross-domain comparisons

Load the common fields plus every applicable module. Use `null` for a field that the paper addresses but does not report; omit fields that do not apply.

## 1. Common study fields

- Research objective and hypothesis
- Study type: computational, bench, cadaveric, animal, clinical, review, method, standard
- Anatomy, population/species, pathology, intervention/device, comparator
- Material and manufacturing route
- Sample/model count and unit of analysis
- Independent variables, outcomes, and statistical method
- Primary and secondary findings with uncertainty
- Validation method and benchmark
- Reported limitations, funding, conflicts, data/code availability
- Relevance to the current research question

## 2. Biomechanics module

### Geometry and model construction

- Anatomy and levels/regions
- Geometry source: CT, MRI, laser scan, CAD, statistical shape model, idealized
- Subject specificity and segmentation method
- Included structures and simplifications
- Coordinate system and reference configuration

### Materials and discretization

- Constitutive model per component
- Elastic/plastic/viscoelastic/poroelastic/anisotropic assumptions
- Homogeneous versus heterogeneous mapping
- Density-modulus relationship and calibration
- Element type, mesh size/count, order, and quality
- Mesh convergence or discretization sensitivity

### Loading and interactions

- Load magnitude, direction, waveform, and rationale
- Boundary conditions and constraints
- Muscle, follower, joint, impact, or physiological loading
- Contact formulation, friction, tie, cohesive, or interface law
- Preload, surgical state, and load cases

### Outputs and credibility

- Stress/strain metric and averaging method
- Displacement, range of motion, stiffness, load sharing, micromotion
- Failure criterion, fatigue method, remodeling/mechanoregulation law
- Verification, validation, calibration, and sensitivity analysis
- Experimental or clinical agreement and error metric

## 3. Bone-implant module

- Implant class, indication, anatomical site, surgical approach
- Commercial/custom/prototype status
- Implant dimensions, material, coating, surface, and porosity
- Fixation: cemented, press-fit, screw, plate, nail, cage, stem, graft
- Bone quality and defect/fracture classification
- Bone-implant contact, interference fit, friction, ingrowth, bonding
- Primary stability, micromotion, subsidence, migration, pullout, cutout
- Stress shielding, adjacent bone strain, interface stress, fatigue risk
- Comparator device and clinically meaningful threshold
- Biological fixation, osseointegration, fusion, histology, imaging
- Follow-up, adverse events, failure/revision, and translational maturity

## 4. Topology-optimization module

- Design domain, non-design domain, and parameterization
- Objective function and physical meaning
- Constraints: volume, stress, displacement, fatigue, eigenfrequency, porosity, connectivity
- Method: SIMP, BESO/ESO, level set, MMC/MMV, phase field, evolutionary, data-driven
- Single/multiple load cases and uncertainty/robustness
- Filters, projection, minimum length scale, symmetry, draw direction
- Additive-manufacturing constraints: overhang, self-support, enclosed voids, powder removal
- Multi-material, multiscale, graded, lattice, or infill formulation
- Solver, convergence rule, mesh/design resolution, computational cost
- Geometry reconstruction and smoothing
- Baseline design, optimized improvement, mass/stiffness trade-off
- Reanalysis after reconstruction and experimental validation

## 5. Porous-scaffold module

- Architecture: strut lattice, sheet/solid TPMS, stochastic, Voronoi, foam, topology-optimized
- Unit cell/type and mathematical definition
- Uniform, graded, conformal, or patient-specific layout
- Nominal and measured pore size, strut/wall thickness, and porosity
- Relative density, surface area, tortuosity, permeability, anisotropy
- Base material, coating, composite, biodegradation/corrosion
- Elastic modulus, yield/plateau stress, energy absorption, fatigue
- Test direction, boundary condition, specimen size, and standards
- As-designed versus as-built geometry and deviation
- Cell type, viability, adhesion, proliferation, differentiation
- In vivo model, bone ingrowth, vascularization, histomorphometry
- Mechanical-biological trade-off and target bone match

## 6. Additive-manufacturing module

- Process: LPBF/SLM, EBM, DED, binder jetting, material extrusion, SLA/DLP, bioprinting
- Machine, energy source, atmosphere, and build chamber
- Feedstock chemistry, particle size/morphology, reuse, filament/resin details
- Layer thickness, power, speed, hatch spacing, scan strategy, energy density
- Build orientation, supports, nesting, compensation, and resolution
- Heat treatment, HIP, stress relief, machining, polishing, etching, coating
- Density, defects, roughness, residual stress, microstructure, texture
- Dimensional accuracy and CT/metrology method
- Mechanical and fatigue test standards, environment, and specimen geometry
- Sterilization, cleaning, powder removal, biocompatibility
- Process-structure-property-biological response chain
- Reproducibility across builds, machines, lots, and laboratories

## 7. Cross-domain comparisons

When synthesizing, compare like with like:

- Separate nominal from measured porosity and geometry.
- Separate coupon properties from full-device behavior.
- Separate static stiffness/strength from fatigue and long-term biological fixation.
- Separate idealized FE predictions from validated subject-specific models.
- Separate optimization-domain performance from reconstructed/manufactured performance.
- Separate in vitro, animal, and clinical evidence.
- Harmonize units and outcome definitions before quantitative comparison.
- Treat different load cases, anatomy, bone quality, and boundary conditions as potential moderators.

