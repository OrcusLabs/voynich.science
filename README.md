# Voynich Manuscript Generator and Analysis Tools

This repository contains the primary generator, analyzer, transcription data, and supporting tools used in the study of constrained generative models for the Voynich Manuscript.

The project focuses on:

* local copy/mutate generation behavior,
* adjacency legality constraints ("ledger" systems),
* source-pool inheritance,
* positional structure,
* and manuscript-wide ecological analysis.

This repository does **not** claim a definitive decipherment of the Voynich Manuscript. The included tools and datasets are intended for computational and structural analysis of manuscript production behavior.

---

# Core Files

## Voynich_Generator_v10_12.py

Primary manuscript generator.

Generates synthetic Voynich-style text using:

* adjacency legality constraints,
* local copy/mutate behavior,
* source-pool inheritance,
* positional rules,
* and page-level ecological constraints.

This is the primary experimental generator used in the associated research.

---

## Voynich_Analyzer_v1.py

Primary analysis engine.

Analyzes Voynich transcription data for:

* exact-copy behavior,
* edit-distance relationships,
* source-page inheritance,
* sheet/quire reduction,
* local vs external derivation,
* and manuscript ecological structure.

---

# Data Files

## TTLI.json

Primary Takahashi-derived Voynich transcription dataset used throughout the project.

Contains folio, line, and token-level transcription data.

---

## Voynich_Transcriptions.json

Data file used by `Voynich_Transcription_Export_v2.py` to export multiple Voynich transcriptions into JSON files.

---

## mappings_v3.json

Primary mapping database generated from transcription analysis.

Contains:

* token relationships,
* parent associations,
* edit-distance structures,
* source relationships,
* and supporting metadata used by the analyzer and generator.

---

## mappings_TTLI.json

Alternative or earlier TTLI-based mappings dataset retained for comparison and validation purposes.

---

## Ledger_scribe1.json

Primary adjacency legality ledger.

Defines legal glyph and token transition behavior used by the generator system.

Includes structural constraints and first-character statistical weighting derived from the manuscript.

---

## standard_page_rules_v1.json

Page-level structural rules and statistical constraints used during generation.

Includes:

* positional weighting,
* line structure behavior,
* and manuscript formatting constraints.

---

## Quires_Scribes.json

Metadata file containing:

* quire assignments,
* scribal associations,
* Currier-style classifications,
* and manuscript grouping information.

Used by the analyzer and generator systems.

---

# Supporting Tools

## Ledger_Generator_GUI_v1.py

GUI tool for generating and editing ledger structures from transcription data.

Used to construct adjacency legality systems for generation experiments.

---

## Create_Mappings_Gui_v1.py

GUI tool for generating mapping datasets from transcription sources and metadata files.

Used to create analyzer-ready relationship structures.

---

## Voynich_Transcription_Export_v2.py

Utility tool used to export Voynich transcription sources into repository-compatible JSON transcription datasets.

Used to generate files such as:

* `TTLI.json`
* `ZLZB.json`

from the source transcription data contained in `Voynich_Transcriptions.json`.

---

# Notes

The repository is intended primarily for:

* computational linguistics,
* manuscript ecology analysis,
* generative modeling,
* and Voynich Manuscript structural research.

The included tools are research and experimental systems rather than polished production software.
