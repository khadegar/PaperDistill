# Writing-Corpus Provenance

## Contents

1. Source-layer policy
2. External production corpus v1
3. Web full-text seed subset v1
4. Zotero seed corpus v1
5. Model-read semantic set v1
6. Calibration-seed coverage and limits
7. Updating the corpus

## 1. Source-layer policy

Use three complementary source layers and one derived calibration system:

- `web_evidence`: papers found through the existing web/database workflow in `search-access.md`. Preserve this route for topic coverage, current work, citation verification, and counter-evidence.
- `external_fulltext_corpus`: the external, versioned, queryable open-full-text production corpus. Use it for broad section retrieval and deterministic aggregate writing-pattern analysis.
- `zotero_calibration`: representative full texts from the user's Zotero library. Use this route for the user's recurring research context and locally available evidence.
- `web_calibration`: a manually inspected subset of web-discovered full texts selected to fill qualitative writing-coverage gaps in the Zotero subset.

Derive writing calibration only from inspected full text, regardless of whether it came from the web or Zotero. Do not replace web-discovered evidence with Zotero items. Do not treat either calibration subset as a closed bibliography or citation whitelist. A draft may cite either discovery route when the cited paper directly supports the claim.

The production JATS files, source PDFs, indexed full text, and SQLite database remain outside the Skill. Only configuration, provenance, aggregate non-substitutive patterns, and writing rules belong in the Skill.

## 2. External production corpus v1

Profile: `biomechanics-fulltext-10k-v1`. Target: 10,000 unique English open full texts. Storage root at initial build: `D:\CodeX\PaperDistill\biomechanics-corpus`.

Selection uses nine overlapping subject strata: bone/implant finite-element biomechanics, fixation devices, topology optimization, porous/TPMS scaffolds, additive manufacturing, mechanobiology/remodeling, osseointegration/bone ingrowth, patient-specific implants, and bone-regeneration biomaterials. No journal whitelist is used.

Audited production state on 2026-08-10:

- 13,776 unique candidates in the deduplicated stratum union.
- 10,000 logical papers selected into `manifest.jsonl`.
- 3,776 additional deduplicated candidates retained as the initial reserve pool.
- Deduplication preference: DOI → PMCID → PMID → normalized title/year.
- 10,000 substantive JATS records parsed with no zero-content record.
- 208,253 sections and 55,404,973 words in the current parsed records.
- Current corpus and semantic structural audits pass after replacing failed records and rebuilding derived artifacts.

Read `<CORPUS_ROOT>/reports/stats.json` and `<CORPUS_ROOT>/reports/audit.json` for the machine-readable current state. Parsing and indexing support deterministic retrieval and aggregate statistics; they do not constitute model-level semantic reading.

Automated acquisition uses Europe PMC open-access JATS with article-level provenance and license fields. The corpus is checkpointed and resumable. Permanent missing full texts are logged and replaced from the reserve pool so that the completion criterion remains 10,000 successfully parsed records. See `large-corpus-workflow.md` and `assets/corpus-profile-10k.json`.

## 3. Web full-text seed subset v1

Calibration date: 2026-08-10. Open full-text-read papers: 8. These papers were found through the preserved web/database route and selected to add study designs that were less represented in the Zotero subset.

| Repository ID | Year | Journal | DOI | Calibration role |
|---|---:|---|---|---|
| [`PMC10449641`](https://pmc.ncbi.nlm.nih.gov/articles/PMC10449641/) | 2023 | Frontiers in Bioengineering and Biotechnology | `10.3389/fbioe.2023.1240125` | Separate calibration and validation sets; CAD-versus-as-built geometry; fatigue prediction |
| [`PMC9319900`](https://pmc.ncbi.nlm.nih.gov/articles/PMC9319900/) | 2022 | Materials | `10.3390/ma15144729` | As-designed versus as-built morphology; multiscale metrology; process–geometry–mechanics chain |
| [`PMC5549492`](https://pmc.ncbi.nlm.nih.gov/articles/PMC5549492/) | 2017 | International Journal of Biomaterials | `10.1155/2017/5093063` | Mechanical fixation followed by in vitro and in vivo biological evaluation |
| [`PMC8492259`](https://pmc.ncbi.nlm.nih.gov/articles/PMC8492259/) | 2021 | BioMed Research International | `10.1155/2021/2899043` | Material comparator study; biomechanical and microstructural outcome alignment |
| [`PMC8319128`](https://pmc.ncbi.nlm.nih.gov/articles/PMC8319128/) | 2021 | Scientific Reports | `10.1038/s41598-021-94980-1` | Patient-specific implant design; multiple physiological load cases; manufacturing constraints |
| [`PMC6591284`](https://pmc.ncbi.nlm.nih.gov/articles/PMC6591284/) | 2019 | Scientific Reports | `10.1038/s41598-019-44872-2` | Reduced-order time-dependent regeneration model; optimization; experimental plausibility check |
| [`PMC5036184`](https://pmc.ncbi.nlm.nih.gov/articles/PMC5036184/) | 2016 | Scientific Reports | `10.1038/srep34072` | AM characterization followed by mechanical, cell, and load-bearing animal evidence |
| [`PMC11009309`](https://pmc.ncbi.nlm.nih.gov/articles/PMC11009309/) | 2024 | Nature Communications | `10.1038/s41467-024-47189-5` | Results-forward multiscale narrative; material–surface–architecture–immune–bone mechanism chain |

Derived web-subset profile:

- Abstracts averaged about 203 words across the eight papers, with about 24 words per sentence after normalization.
- Section order varied by journal: conventional IMRaD, combined Results and Discussion, domain-led mathematical sections, and Results–Discussion–Methods were all present.
- Recurrent transferable sequence: define the design conflict, expose the missing causal or validation link, compare controlled alternatives, report quality/as-built evidence before dependent outcomes, and bound translation by the highest validation level reached.
- Biological papers used an evidence ladder rather than a single outcome: material/process quality → mechanics or degradation → cell response → animal tissue response → bounded mechanism.
- Numerical–experimental papers kept calibration, validation, and application predictions distinct and treated as-built geometry as evidence rather than a cosmetic manufacturing detail.

The web seed subset augments qualitative writing calibration only. It does not narrow, replace, or otherwise modify the live search strategy in `search-access.md`.

## 4. Zotero seed corpus v1

Calibration date: 2026-08-10. Full-text-read papers: 14.

| Zotero key | Year | Journal | DOI | Calibration role |
|---|---:|---|---|---|
| `4B7XZCSJ` | 2020 | Computer Methods in Applied Mechanics and Engineering | `10.1016/j.cma.2019.112702` | Time-dependent topology optimization; mathematical formulation; parameter studies |
| `54HDYQ2M` | 2005 | Journal of Biomechanics | `10.1016/j.jbiomech.2004.05.022` | Physiological loading; pre-clinical test rationale; numerical-to-experimental comparison |
| `6VLYCMPB` | 2026 | North American Spine Society Journal | `10.1016/j.xnsj.2026.100890` | Ex vivo implant testing; paired comparison; translational discussion |
| `9LD8IUEW` | 2025 | Results in Engineering | `10.1016/j.rineng.2025.103932` | Review-paper search reporting; AM–implant–topology synthesis |
| `AMEM3CT7` | 2013 | Structural and Multidisciplinary Optimization | `10.1007/s00158-013-0978-6` | Critical method review; taxonomy; benchmark-driven comparison |
| `D2ZCKUU5` | 2025 | Journal of the Mechanical Behavior of Biomedical Materials | `10.1016/j.jmbbm.2024.106864` | Porous hip stems; remodeling and fatigue; gradient-versus-uniform comparison |
| `E3USK2EJ` | 2017 | Virtual and Physical Prototyping | `10.1080/17452759.2017.1307769` | Fixation-device redesign; stress shielding; topology optimization with AM bridge |
| `IMLEXNYS` | 2021 | Journal of Biomechanics | `10.1016/j.jbiomech.2021.110233` | Mechanobiology-based scaffold optimization; time-dependent sensitivity; limitations |
| `KU5SBX2Z` | 2023 | Computers & Structures | `10.1016/j.compstruc.2023.107132` | Elasticity matching; smooth-boundary optimization; manufacturability demonstration |
| `LQYIWA9H` | 2024 | Journal of the Mechanical Behavior of Biomedical Materials | `10.1016/j.jmbbm.2024.106695` | Patient-specific cage design; full-scale optimization; FE and mechanical validation |
| `MQTF5AQ8` | 2019 | Acta Biomaterialia | `10.1016/j.actbio.2019.05.046` | TPMS scaffold fabrication; as-built characterization; static and fatigue reporting |
| `NAE8DCU5` | 2003 | Journal of Biomechanics | `10.1016/S0021-9290(03)00071-X` | Site-dependent bone properties; regression/statistical comparison; bounded inference |
| `NUQE5UFD` | 2017 | Computer Methods in Biomechanics and Biomedical Engineering | `10.1080/10255842.2016.1193596` | FE verification and validation; mesh/material sensitivity; multiple load cases |
| `U2PZMEAA` | 2020 | Journal of the Mechanical Behavior of Biomedical Materials | `10.1016/j.jmbbm.2020.103982` | Global–local optimization; cage biomechanics; fabrication and compression testing |

Derived corpus profile:

- 14 introductions, 13 abstracts, 11 explicit methods sections, 12 results sections, and 10 separate discussions detected.
- Abstracts averaged about 246 words and 22 words per sentence after text normalization.
- Titles averaged about 12 words; only 2 of 14 used a colon. Prefer informative single-clause titles unless a subtitle adds study-design information.
- Coverage: 12 bone-implant papers, 12 topology-optimization papers, 11 finite-element/biomechanics papers, 10 porous-scaffold papers, 7 additive-manufacturing papers, and 5 mechanobiology papers. Categories overlap.

These values calibrate defaults; they are not journal requirements.

## 5. Model-read semantic set v1

Semantic reading date: 2026-08-10. Completed full-text semantic cards: 45. Selection used five papers from each of the nine discovery strata and deliberately included original computational, manufacturing, material, cell, animal and clinical work plus systematic, scoping, narrative and bibliometric reviews, case reports, nulls, failures and overclaim counterexamples.

For every selected paper, the primary `gpt-5.6-sol` reader at `max` reasoning effort read every substantive non-reference packet chunk and recorded:

- problem, exact gap, objective, design, comparator and method spine;
- verification, calibration, validation, sensitivity and application roles;
- measured, predicted, inferred and recommended evidence states;
- argument map, proxy bridges, limitations, likely bias direction and scope;
- section moves and transferable capability candidates with chunk locators.

Selected batches also received independent Luna subagent cross-checks to challenge article-kind classification, evidence boundaries and overclaim judgments. These cross-checks supplement the primary reading; they are not represented as formal dual-human annotation.

Cross-paper promotion produced 20 accepted writing rules. Every accepted rule has at least three completed cards, at least two journals, source chunk locators, an explicit scope and a counterexample/boundary. Strict structural validation reports 45 selected, 45 completed, 0 pending, 0 invalid, 0 orphan, 0 errors and 0 warnings. The auditable artifacts remain external:

- `<CORPUS_ROOT>/semantic-distillation/cards/`
- `<CORPUS_ROOT>/semantic-distillation/synthesis/promoted-rules.jsonl`
- `<CORPUS_ROOT>/semantic-distillation/reports/validation.json`
- `<CORPUS_ROOT>/semantic-distillation/reports/blind-forward-tests.md`

Three fresh-context Luna forward tests covered FE calibration/validation, topology reconstruction and as-built evidence, AM/fatigue/ALP, multicomponent attribution, heterogeneous review synthesis, and confounded clinical comparators. All passed the intended evidence-boundary checks; the agents had access to the published Skill references but not to the semantic cards or promoted-rule artifact.

This is a balanced semantic calibration batch, not a claim that all 10,000 papers were semantically read. Open-access availability, recent-year concentration, English-language selection and five-per-stratum sampling remain coverage limits. Extend the batch when a new domain, study design or target-journal convention is not represented.

## 6. Calibration-seed coverage and limits

The earlier manually inspected calibration seed contains 22 full-text-read papers: 14 from Zotero and 8 from the web. It remains useful for the user's recurring topics and source-route calibration. The 45-card semantic set is a separate, auditable model-read layer sampled from the external production corpus. Do not add 22 and 45 as a unique-paper total unless DOI/title deduplication has been performed.

Together, the two calibration routes strongly represent computational mechanics, implant design, porous metals, topology optimization, additive manufacturing, mechanical characterization, biological evidence ladders, and mixed numerical–experimental validation. The web subset specifically improves coverage of cell assays, animal studies, biodegradable scaffolds, manufacturing deviation, and multiscale mechanisms.

Clinical cohorts, randomized trials, polymer/ceramic scaffold systems, regulatory submissions, and many target-journal-specific formats remain lightly represented. For those tasks, retrieve current web/database exemplars and apply the relevant reporting guideline before drafting.

The corpus includes original studies and reviews. Keep their writing patterns separate:

- Original studies: problem → gap → objective/hypothesis → method → quantitative evidence → bounded implication.
- Reviews: scope/search method → taxonomy → cross-study comparison → unresolved contradictions → research agenda.

## 7. Updating the corpus

When the user requests recalibration:

1. Preserve the existing web/database search strategy and its logs.
2. Resume the external corpus with `manage_fulltext_corpus.py fetch`; rebuild the index and run `distill_large_corpus.py` after material batches.
3. Select qualitative seed full texts from both web/database discovery and Zotero by domain, study type, target journal, recency, and validation quality rather than citation count alone.
4. Record provenance as `external_fulltext_corpus`, `web_calibration`, or `zotero_calibration`; deduplicate across routes by DOI or normalized title/year.
5. Run `scripts/distill_writing_patterns.py` on paired `.txt` and `.bib` seed files when local text is available.
6. Prepare semantic packets with `prepare_semantic_distillation.py`; complete cards only after a primary model reads every substantive chunk.
7. Validate cards and locators with `validate_semantic_distillation.py --strict`.
8. Promote only rules that satisfy recurrence, diversity, traceability, credibility, counterexample, scope and utility gates.
9. Add only recurrent, transferable patterns. Do not store long quotations or source full text in the Skill.
10. Version material profile/subset changes and record added/removed DOI values and the reason for each change.
