#!/usr/bin/env python3
"""
Voynich plausible production generator, v10 prototype.

# ============================================================
# CONFIGURABLE CONSTANTS
# ============================================================

PAGE_PHASE_EARLY = 0.30
PAGE_PHASE_MIDDLE = 0.70

LINE_ZONE_LEFT = 0.33
LINE_ZONE_RIGHT = 0.66

MAX_OPERATION_RETRIES = 25
MAX_FALLBACK_RETRIES = 50

MAX_VOWEL_RUN = 3
MAX_CONSONANT_RUN = 3

LOCAL_MEMORY_WINDOW = 120
FAMILY_BOOST_FACTOR = 1.35

EDGE_EDIT_BONUS = 1.50

TARGET_WORDS_MIN = 75
TARGET_WORDS_MAX = 88
MAX_WORDS_LINE_MIN = 5
MAX_WORDS_LINE_MAX = 9
TEXT_SEED_LINE_WORDS = 13

RANDOM_SEED = None

# ============================================================



Purpose
-------
Generate statistically plausible Scribe-1-herbal-like Voynich output using:
  - ledger adjacency validation
  - embedded f1r seed material
  - compact standard page-rule operation weights
  - measured gallows ED1 operation weights
  - local recursive copy/ED1 behavior

This is a plausibility generator, not a decoder and not a perfect manuscript clone.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GALLOWS = set("ktpf")
VOWELS = set("aeioy")
DEFAULT_LEDGER_COLUMNS = ("prefix", "midfix", "suffix")

PAGE_PHASE_EARLY = 0.30
PAGE_PHASE_MIDDLE = 0.70
LINE_ZONE_LEFT = 0.33
LINE_ZONE_RIGHT = 0.66
MAX_OPERATION_RETRIES = 25
MAX_FALLBACK_RETRIES = 50
LOCAL_MEMORY_WINDOW = 120
FAMILY_BOOST_FACTOR = 1.35
EDGE_EDIT_BONUS = 1.50
TARGET_WORDS_MIN = 75
TARGET_WORDS_MAX = 88
MAX_WORDS_LINE_MIN = 5
MAX_WORDS_LINE_MAX = 9
TEXT_SEED_LINE_WORDS = 13
RANDOM_SEED = None


EMBEDDED_F1R_RECORDS = [
    {"line": "1", "text": "fachys ykal ar ataiin shol shory y kor"},
    {"line": "2", "text": "sory ckhar y kair chtaiin shar are cthar cthar"},
    {"line": "3", "text": "syaiir sheky or ykaiin shod cthoary cthes daraiin sa"},
    {"line": "4", "text": "ooiin oteey oteos roloty daiin otaiin or okan"},
    {"line": "5", "text": "dair y chear cthaiin cphar cfhaiin"},
    {"line": "6", "text": "odar o shol cphoy oydar s cfhoaiin shodary"},
    {"line": "7", "text": "yshey shody otchol chocthy dain chor kos"},
    {"line": "8", "text": "daiin shos cfhol shody"},
    {"line": "9", "text": "dain os teody"},
    {"line": "10", "text": "ydain cphesaiin ol s cphey ytain shoshy"},
    {"line": "11", "text": "oksho kshoy otairin oteol okan shodain sckhey daiin"},
    {"line": "12", "text": "shoy ckhey kodaiin cphy cphodaiils cthey she oldain"},
    {"line": "13", "text": "dain oiin chol odaiin chodain chdy okain dan cthy"},
    {"line": "14", "text": "daiin shckhey ckeor chor shey kol chol chol kor chal"},
    {"line": "15", "text": "sho chol kshy kchy dor chodaiin sho kchom"},
    {"line": "16", "text": "ycho tchey chokain sheo pshol dydyd cthy daicthy"},
    {"line": "17", "text": "yto shol she kodshey cphealy dain ckhyds"},
    {"line": "18", "text": "dchar shcthaiin okaiir chey rchy cthols dlocta"},
    {"line": "19", "text": "shok chor chey dain ckhey"},
    {"line": "20", "text": "otol daiiin"},
    {"line": "21", "text": "cpho shaiin shokcheey chol tshodeesy shey pydeey chy ro"},
    {"line": "22", "text": "chol dain cthal dar shear kaiin dar shey"},
    {"line": "23", "text": "kaiin shoaiin okol daiin far cthol daiin ctholdar"},
    {"line": "24", "text": "ycheey oky daiin okchey dal"},
    {"line": "25", "text": "shody koshey cthy keey keey dal chtor"},
    {"line": "26", "text": "chol chok choty chotey"},
    {"line": "27", "text": "dchaiin"},
]

COPY_OPS = {"external_copy", "local_copy", "external_copy_fallback", "local_copy_fallback", "external_short_copy"}

# Prefer real visible ED1 neighbors over synthetic glyph edits.
# This is the main anti-gibberish control: generated ED1 should usually
# choose an already visible variant from the active source/local vocabulary.
ATTESTED_ED1_FIRST = True
ATTESTED_ED1_ATTEMPTS = 40
RANDOM_ED1_FALLBACK_PROBABILITY = 0.08
SEED_FALLBACK_PROBABILITY = 0.05
MAX_SOURCE_TOKEN_PAGE_COUNT = 5
MAX_FAMILY_PAGE_COUNT = 9

# Length/suffix drift controls.
# The v10.6 output over-produced length 4/5 and drifted into -ain/-aiin attractors.
TARGET_SHORT_COPY_BACKGROUND = 0.020
SHORT_TOKEN_SOURCE_BOOST = 1.18
SHORT_TOKEN_LOCAL_BOOST = 1.5
LENGTH_SELECTION_BIAS = {
    1: 1.7,
    2: 2.6,
    3: 0.5,
    4: 0.9,
    5: 0.9,
    6: 0.6,
    7: 0.95,
    8: 0.85,
    9: 0.9,
}
LENGTH_SELECTION_BIAS_DEFAULT = 0.92
LENGTH_45_PAGE_SOFT_SHARE = 0.58
LENGTH_45_PENALTY = 0.72
LONG_PARENT_BOOST_AFTER_SHORT_BULGE = 1.55

AIN_SUFFIX_RE = re.compile(r"(aiin|ain|iin|in)$")
AIN_PAGE_SOFT_SHARE = 0.24
AIN_PAGE_HARD_SHARE = 0.34
AIN_SELECTION_PENALTY = 0.22
AIN_MUTATION_PENALTY = 0.16
CROSS_PAGE_AIN_PRESSURE = 0.018

# Prevent generated pages from becoming full-strength sources.
# f1r remains a stable anchor; recent generated pages are sampled, but downweighted.
F1R_SOURCE_WEIGHT = 1.45
GENERATED_SOURCE_WEIGHT = 0.58
RECENT_SOURCE_WEIGHT = 1.15

# Conservative visual filters. These are deliberately simple plausibility guards.
MAX_VOWEL_RUN = 4
MAX_CONSONANT_RUN = 4
MAX_TOKEN_LEN = 12
MIN_MUTATED_LEN = 3

# Direct vocabulary-growth controls.
NOVEL_VARIANT_RATE = 0.22
NOVEL_VARIANT_MAX_ATTEMPTS = 80
NOVEL_VARIANT_PREFER_ED1 = True
NOVEL_VARIANT_ALLOW_SEED_FALLBACK = True
UNIQUE_TOKEN_BONUS = 1.0
REUSED_TOKEN_PENALTY = 0.91
MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY = 2

# Paragraph layout controls. These are layout-only except for paragraph-start initial gallows pressure.
PARAGRAPH_MODEL_ENABLED = True
PARAGRAPH_MIN_LINES = 4
PARAGRAPH_MAX_LINES = 8
PARAGRAPH_SECONDARY_PROBABILITY = 0.28
PARAGRAPH_TERTIARY_PROBABILITY = 0.08
PARAGRAPH_START_INITIAL_GALLOWS_MIN_RATE = 0.34
PARAGRAPH_TEXT_BLANK_LINES = True


# ============================================================
# GUI KNOB REGISTRY
# ============================================================
# Codex/GUI note:
# The constants below are the primary tuning surface. A GUI can expose
# these as sliders/toggles/dropdowns without editing generator logic.
#
# Recommended slider ranges are intentionally broad. The current values
# are proof-of-concept defaults, not final tuned values.

GUI_KNOBS = {
    # Structural glyph/constants
    "GALLOWS": {
        "value": "ktpf",
        "description": "Glyphs treated as gallows characters.",
    },
    "VOWELS": {
        "value": "aeioy",
        "description": "Glyphs treated as vowels for run-length filters and edit preferences.",
    },
    "DEFAULT_LEDGER_COLUMNS": {
        "value": ",".join(DEFAULT_LEDGER_COLUMNS),
        "description": "Ledger transition columns used when the ledger metadata does not specify columns.",
    },
    "COPY_OPS": {
        "value": ",".join(sorted(COPY_OPS)),
        "description": "Operation names treated as copy operations by repetition guards.",
    },

    # Legacy/default scheduling constants
    "PAGE_PHASE_EARLY": {
        "value": PAGE_PHASE_EARLY,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Legacy early-page phase boundary.",
    },
    "PAGE_PHASE_MIDDLE": {
        "value": PAGE_PHASE_MIDDLE,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Legacy middle-page phase boundary.",
    },
    "LINE_ZONE_LEFT": {
        "value": LINE_ZONE_LEFT,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Legacy left line-zone boundary.",
    },
    "LINE_ZONE_RIGHT": {
        "value": LINE_ZONE_RIGHT,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Legacy right line-zone boundary.",
    },
    "MAX_OPERATION_RETRIES": {
        "value": MAX_OPERATION_RETRIES,
        "min": 1,
        "max": 200,
        "step": 1,
        "description": "Legacy maximum retries for a generation operation.",
    },
    "MAX_FALLBACK_RETRIES": {
        "value": MAX_FALLBACK_RETRIES,
        "min": 1,
        "max": 300,
        "step": 1,
        "description": "Legacy maximum retries for fallback generation.",
    },
    "LOCAL_MEMORY_WINDOW": {
        "value": LOCAL_MEMORY_WINDOW,
        "min": 12,
        "max": 500,
        "step": 1,
        "description": "Legacy local memory window size.",
    },
    "FAMILY_BOOST_FACTOR": {
        "value": FAMILY_BOOST_FACTOR,
        "min": 0.1,
        "max": 5.0,
        "step": 0.05,
        "description": "Legacy boost for active token families.",
    },
    "EDGE_EDIT_BONUS": {
        "value": EDGE_EDIT_BONUS,
        "min": 0.1,
        "max": 5.0,
        "step": 0.05,
        "description": "Legacy bonus for edge-position edits.",
    },
    "TARGET_WORDS_MIN": {
        "value": TARGET_WORDS_MIN,
        "min": 1,
        "max": 300,
        "step": 1,
        "description": "Minimum generated tokens per page.",
    },
    "TARGET_WORDS_MAX": {
        "value": TARGET_WORDS_MAX,
        "min": 1,
        "max": 300,
        "step": 1,
        "description": "Maximum generated tokens per page.",
    },
    "MAX_WORDS_LINE_MIN": {
        "value": MAX_WORDS_LINE_MIN,
        "min": 1,
        "max": 40,
        "step": 1,
        "description": "Minimum generated line capacity before wrapping.",
    },
    "MAX_WORDS_LINE_MAX": {
        "value": MAX_WORDS_LINE_MAX,
        "min": 1,
        "max": 40,
        "step": 1,
        "description": "Maximum generated line capacity before wrapping.",
    },
    "RANDOM_SEED": {
        "value": "",
        "description": "Optional legacy random seed. Leave blank for no module-level seed.",
    },

    # Paragraph layout
    "PARAGRAPH_MODEL_ENABLED": {
        "value": PARAGRAPH_MODEL_ENABLED,
        "min": 0,
        "max": 1,
        "step": 1,
        "description": "Enable explicit paragraph boundary planning. Layout only; does not alter word-generation rules.",
    },
    "PARAGRAPH_MIN_LINES": {
        "value": PARAGRAPH_MIN_LINES,
        "min": 2,
        "max": 10,
        "step": 1,
        "description": "Minimum line number for first secondary paragraph start.",
    },
    "PARAGRAPH_MAX_LINES": {
        "value": PARAGRAPH_MAX_LINES,
        "min": 4,
        "max": 16,
        "step": 1,
        "description": "Maximum line number for first secondary paragraph start.",
    },
    "PARAGRAPH_SECONDARY_PROBABILITY": {
        "value": PARAGRAPH_SECONDARY_PROBABILITY,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Probability that a page has a second paragraph after the opening paragraph.",
    },
    "PARAGRAPH_TERTIARY_PROBABILITY": {
        "value": PARAGRAPH_TERTIARY_PROBABILITY,
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
        "description": "Probability that a page has a third paragraph.",
    },
    "PARAGRAPH_START_INITIAL_GALLOWS_MIN_RATE": {
        "value": PARAGRAPH_START_INITIAL_GALLOWS_MIN_RATE,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "description": "Minimum initial-gallows probability at planned paragraph starts.",
    },
    "PARAGRAPH_TEXT_BLANK_LINES": {
        "value": PARAGRAPH_TEXT_BLANK_LINES,
        "min": 0,
        "max": 1,
        "step": 1,
        "description": "Write blank lines before paragraph starts in plain-text output.",
    },

    # Source/local scheduling
    "F1R_SOURCE_WEIGHT": {
        "value": F1R_SOURCE_WEIGHT,
        "min": 0.25,
        "max": 10.00,
        "step": 0.05,
        "description": "Weight for embedded f1r as stable source anchor.",
    },
    "GENERATED_SOURCE_WEIGHT": {
        "value": GENERATED_SOURCE_WEIGHT,
        "min": 0.05,
        "max": 2.00,
        "step": 0.05,
        "description": "Weight for prior generated pages as future source material.",
    },
    "RECENT_SOURCE_WEIGHT": {
        "value": RECENT_SOURCE_WEIGHT,
        "min": 0.25,
        "max": 3.00,
        "step": 0.05,
        "description": "Boost for most recent source page.",
    },

    # ED1 behavior
    "ATTESTED_ED1_FIRST": {
        "value": ATTESTED_ED1_FIRST,
        "description": "When enabled, ED1 mutations first try to reuse an already visible one-edit-distance token before trying synthetic edits.",
    },
    "ATTESTED_ED1_ATTEMPTS": {
        "value": ATTESTED_ED1_ATTEMPTS,
        "min": 1,
        "max": 120,
        "step": 1,
        "description": "Attempts to find/use visible attested ED1 variants before giving up.",
    },
    "RANDOM_ED1_FALLBACK_PROBABILITY": {
        "value": RANDOM_ED1_FALLBACK_PROBABILITY,
        "min": 0.0,
        "max": 0.50,
        "step": 0.01,
        "description": "Probability of allowing synthetic ledger-valid ED1 when no attested ED1 works.",
    },
    "SEED_FALLBACK_PROBABILITY": {
        "value": SEED_FALLBACK_PROBABILITY,
        "min": 0.0,
        "max": 0.50,
        "step": 0.01,
        "description": "Probability of synthetic seed fallback instead of source/local copy fallback.",
    },

    # Repetition/family controls
    "MAX_SOURCE_TOKEN_PAGE_COUNT": {
        "value": MAX_SOURCE_TOKEN_PAGE_COUNT,
        "min": 1,
        "max": 20,
        "step": 1,
        "description": "Maximum page count for one exact token before non-copy operations reject it.",
    },
    "MAX_FAMILY_PAGE_COUNT": {
        "value": MAX_FAMILY_PAGE_COUNT,
        "min": 2,
        "max": 40,
        "step": 1,
        "description": "Maximum page count for one gallows-stripped family before non-copy rejection.",
    },
    "NOVEL_VARIANT_RATE": {
        "value": NOVEL_VARIANT_RATE,
        "min": 0.0,
        "max": 0.40,
        "step": 0.01,
        "description": "Chance per token slot to force a new unique ledger-valid variant.",
    },
    "NOVEL_VARIANT_MAX_ATTEMPTS": {
        "value": NOVEL_VARIANT_MAX_ATTEMPTS,
        "min": 1,
        "max": 300,
        "step": 1,
        "description": "Maximum attempts to find a new ledger-valid token during novelty mode.",
    },
    "NOVEL_VARIANT_PREFER_ED1": {
        "value": NOVEL_VARIANT_PREFER_ED1,
        "description": "When enabled, novelty mode tries ED1 variants before seed fallback.",
    },
    "NOVEL_VARIANT_ALLOW_SEED_FALLBACK": {
        "value": NOVEL_VARIANT_ALLOW_SEED_FALLBACK,
        "description": "When enabled, novelty mode can use a new synthetic seed token if ED1 novelty fails.",
    },
    "UNIQUE_TOKEN_BONUS": {
        "value": UNIQUE_TOKEN_BONUS,
        "min": 1.0,
        "max": 8.0,
        "step": 0.1,
        "description": "Selection boost for tokens not yet used in generated output.",
    },
    "REUSED_TOKEN_PENALTY": {
        "value": REUSED_TOKEN_PENALTY,
        "min": 0.01,
        "max": 1.0,
        "step": 0.01,
        "description": "Penalty for tokens already reused several times globally.",
    },
    "MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY": {
        "value": MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY,
        "min": 1,
        "max": 20,
        "step": 1,
        "description": "Global reuse count before reuse penalty applies.",
    },

    # Length controls
    "TARGET_SHORT_COPY_BACKGROUND": {
        "value": TARGET_SHORT_COPY_BACKGROUND,
        "min": 0.0,
        "max": 0.10,
        "step": 0.005,
        "description": "Background chance to preserve short source tokens.",
    },
    "SHORT_TOKEN_SOURCE_BOOST": {
        "value": SHORT_TOKEN_SOURCE_BOOST,
        "min": 0.25,
        "max": 5.00,
        "step": 0.05,
        "description": "Source selection boost for length <= 2 tokens.",
    },
    "SHORT_TOKEN_LOCAL_BOOST": {
        "value": SHORT_TOKEN_LOCAL_BOOST,
        "min": 0.25,
        "max": 5.00,
        "step": 0.05,
        "description": "Local selection boost for length <= 2 tokens.",
    },
    "LENGTH_SELECTION_BIAS_1": {
        "value": LENGTH_SELECTION_BIAS[1],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for one-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_2": {
        "value": LENGTH_SELECTION_BIAS[2],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for two-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_3": {
        "value": LENGTH_SELECTION_BIAS[3],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for three-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_4": {
        "value": LENGTH_SELECTION_BIAS[4],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for four-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_5": {
        "value": LENGTH_SELECTION_BIAS[5],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for five-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_6": {
        "value": LENGTH_SELECTION_BIAS[6],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for six-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_7": {
        "value": LENGTH_SELECTION_BIAS[7],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for seven-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_8": {
        "value": LENGTH_SELECTION_BIAS[8],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for eight-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_9": {
        "value": LENGTH_SELECTION_BIAS[9],
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for nine-character tokens.",
    },
    "LENGTH_SELECTION_BIAS_DEFAULT": {
        "value": LENGTH_SELECTION_BIAS_DEFAULT,
        "min": 0.10,
        "max": 3.00,
        "step": 0.05,
        "description": "Selection multiplier for token lengths not listed individually.",
    },
    "LENGTH_45_PAGE_SOFT_SHARE": {
        "value": LENGTH_45_PAGE_SOFT_SHARE,
        "min": 0.20,
        "max": 0.85,
        "step": 0.01,
        "description": "Soft page-share threshold before length 4/5 tokens are penalized.",
    },
    "LENGTH_45_PENALTY": {
        "value": LENGTH_45_PENALTY,
        "min": 0.05,
        "max": 1.00,
        "step": 0.01,
        "description": "Multiplier applied to length 4/5 after soft threshold.",
    },
    "LONG_PARENT_BOOST_AFTER_SHORT_BULGE": {
        "value": LONG_PARENT_BOOST_AFTER_SHORT_BULGE,
        "min": 0.25,
        "max": 5.00,
        "step": 0.05,
        "description": "Boost for length >= 6 source/local parents when a page has too many very short tokens.",
    },

    # -ain drift controls
    "AIN_SUFFIX_PATTERN": {
        "value": AIN_SUFFIX_RE.pattern,
        "description": "Regular expression for suffixes treated as the -ain-like drift family.",
    },
    "AIN_PAGE_SOFT_SHARE": {
        "value": AIN_PAGE_SOFT_SHARE,
        "min": 0.05,
        "max": 0.70,
        "step": 0.01,
        "description": "Soft threshold for -ain/-aiin/-iin/-in suffix family.",
    },
    "AIN_PAGE_HARD_SHARE": {
        "value": AIN_PAGE_HARD_SHARE,
        "min": 0.10,
        "max": 0.90,
        "step": 0.01,
        "description": "Hard rejection threshold for -ain-like suffix family on non-copy operations.",
    },
    "AIN_SELECTION_PENALTY": {
        "value": AIN_SELECTION_PENALTY,
        "min": 0.01,
        "max": 1.00,
        "step": 0.01,
        "description": "Selection multiplier after -ain soft threshold.",
    },
    "AIN_MUTATION_PENALTY": {
        "value": AIN_MUTATION_PENALTY,
        "min": 0.01,
        "max": 1.00,
        "step": 0.01,
        "description": "Attested ED1 multiplier for -ain-like variants after soft threshold.",
    },
    "CROSS_PAGE_AIN_PRESSURE": {
        "value": CROSS_PAGE_AIN_PRESSURE,
        "min": 0.0,
        "max": 0.10,
        "step": 0.001,
        "description": "Cross-page pressure against generated source pools dominated by -ain endings.",
    },

    # Visual filters
    "MAX_VOWEL_RUN": {
        "value": MAX_VOWEL_RUN,
        "min": 2,
        "max": 8,
        "step": 1,
        "description": "Reject generated tokens with vowel runs above this.",
    },
    "MAX_CONSONANT_RUN": {
        "value": MAX_CONSONANT_RUN,
        "min": 2,
        "max": 8,
        "step": 1,
        "description": "Reject generated tokens with consonant runs above this.",
    },
    "MAX_TOKEN_LEN": {
        "value": MAX_TOKEN_LEN,
        "min": 6,
        "max": 20,
        "step": 1,
        "description": "Maximum token length allowed.",
    },
    "MIN_MUTATED_LEN": {
        "value": MIN_MUTATED_LEN,
        "min": 1,
        "max": 6,
        "step": 1,
        "description": "Minimum length before deletion-style mutation is allowed.",
    },
}


def gui_knob_snapshot() -> Dict[str, Any]:
    """Return current GUI-facing tuning values for logging or GUI introspection."""
    snapshot = {}
    for name, spec in GUI_KNOBS.items():
        m = re.match(r"^LENGTH_SELECTION_BIAS_(\d+)$", name)
        if m:
            snapshot[name] = LENGTH_SELECTION_BIAS.get(int(m.group(1)), spec.get("value"))
        elif name == "AIN_SUFFIX_PATTERN":
            snapshot[name] = AIN_SUFFIX_RE.pattern
        elif name in {"GALLOWS", "VOWELS"}:
            snapshot[name] = "".join(sorted(globals().get(name, set())))
        elif name in {"DEFAULT_LEDGER_COLUMNS", "COPY_OPS"}:
            snapshot[name] = ",".join(sorted(globals().get(name, ())))
        elif name == "RANDOM_SEED":
            snapshot[name] = "" if RANDOM_SEED is None else RANDOM_SEED
        else:
            snapshot[name] = globals().get(name, spec.get("value"))
    return snapshot

# ============================================================

def folio_sort_key(folio: str) -> Tuple[int, str]:
    m = re.match(r"^f(\d+)([rv])$", str(folio))
    if not m:
        return (10**9, str(folio))
    n, side = m.groups()
    return (int(n) * 2 + (0 if side == "r" else 1), str(folio))


def folio_after_seed(index: int) -> str:
    # index=1 -> f1v; index=2 -> f2r
    folio_num = (index // 2) + 1
    side = "r" if index % 2 == 0 else "v"
    return f"f{folio_num}{side}"


def folio_parts(folio: str) -> Tuple[int, str]:
    m = re.match(r"^f(\d+)([rv])$", folio)
    if not m:
        return 0, ""
    return int(m.group(1)), m.group(2)


def physical_metadata(folio: str) -> Dict[str, Any]:
    folio_num, side = folio_parts(folio)
    page_index = (folio_num - 1) * 2 + (0 if side == "r" else 1)
    return {
        "quire": page_index // 16 + 1,
        "sheet": (page_index % 16) // 4 + 1,
        "folio_num": folio_num,
        "side": side,
    }


def line_sort_key(value: Any) -> Tuple[int, str]:
    m = re.match(r"^(\d+)(.*)$", str(value))
    if not m:
        return (0, str(value))
    return (int(m.group(1)), m.group(2))


def edit_distance(a: str, b: str, max_distance: int = 2) -> int:
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            val = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
            curr.append(val)
            row_min = min(row_min, val)
        if row_min > max_distance:
            return max_distance + 1
        prev = curr
    return prev[-1]


def family_form(token: str) -> str:
    return "".join(ch for ch in token if ch not in GALLOWS)


def max_run(token: str, charset: set[str]) -> int:
    best = cur = 0
    for ch in token:
        if ch in charset:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def cv_class(ch: str) -> str:
    if ch in GALLOWS:
        return "G"
    if ch in VOWELS:
        return "V"
    return "C"



def length_bias(token: str) -> float:
    return LENGTH_SELECTION_BIAS.get(len(token), LENGTH_SELECTION_BIAS_DEFAULT)


def is_ain_like(token: str) -> bool:
    return bool(AIN_SUFFIX_RE.search(token))


def page_share(tokens: List[str], predicate) -> float:
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if predicate(t)) / len(tokens)


def weighted_choice(rng: random.Random, items: Sequence[Any], weights: Optional[Sequence[float]] = None) -> Any:
    if not items:
        return None
    if weights is None:
        return rng.choice(list(items))
    clean = [max(0.0, float(w)) for w in weights]
    if sum(clean) <= 0:
        return rng.choice(list(items))
    return rng.choices(list(items), weights=clean, k=1)[0]


def strip_gutenberg_header_footer(text: str) -> str:
    start_patterns = [
        r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
        r"\*\*\*\s*START OF .*?\*\*\*",
    ]
    end_patterns = [
        r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
        r"\*\*\*\s*END OF .*?\*\*\*",
    ]
    start = 0
    for pattern in start_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            start = match.end()
            break
    end = len(text)
    for pattern in end_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            end = match.start()
            break
    return text[start:end]


def text_corpus_tokens(path: str | Path) -> List[str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        cleaned = strip_gutenberg_header_footer(f.read())
    return re.findall(r"[a-z]+", cleaned.lower())


def build_ledger_from_tokens(tokens: List[str]) -> Dict[str, Any]:
    """
    Build a simple adjacency ledger from cleaned alphabetic tokens.

    This should capture:
    - legal first characters
    - legal adjacent character pairs
    - legal followers per character
    - optional word length histogram
    """
    followers = defaultdict(set)
    first_chars = Counter()
    last_chars = Counter()
    length_counts = Counter()

    for tok in tokens:
        if not tok:
            continue

        first_chars[tok[0]] += 1
        last_chars[tok[-1]] += 1
        length_counts[len(tok)] += 1

        for a, b in zip(tok, tok[1:]):
            followers[a].add(b)

    return {
        "source": "rebuilt_from_text",
        "token_count": len(tokens),
        "unique_token_count": len(set(tokens)),
        "alphabet": sorted(set("".join(tokens))),
        "first_chars": dict(first_chars),
        "last_chars": dict(last_chars),
        "length_counts": dict(length_counts),
        "followers": {k: sorted(v) for k, v in sorted(followers.items())},
    }


def text_seed_records(tokens: Sequence[str], word_count: int, start: int = 0, folio: str = "f1r") -> List[Dict[str, Any]]:
    selected = list(tokens[max(0, start):max(0, start) + max(0, word_count)])
    return text_seed_records_from_words(selected, folio=folio)


def random_text_seed_records(tokens: Sequence[str], word_count: int, rng: random.Random, folio: str = "f1r") -> List[Dict[str, Any]]:
    pool = list(tokens)
    if not pool:
        return text_seed_records_from_words([], folio=folio)
    count = max(0, int(word_count))
    if count <= len(pool):
        selected = rng.sample(pool, count)
    else:
        selected = [rng.choice(pool) for _ in range(count)]
    return text_seed_records_from_words(selected, folio=folio)


def text_seed_records_from_words(selected: Sequence[str], folio: str = "f1r") -> List[Dict[str, Any]]:
    selected = list(selected)
    records = []
    line_no = 1
    index = 0
    while index < len(selected) or (not records and selected == []):
        line_tokens = selected[index:index + TEXT_SEED_LINE_WORDS]
        index += TEXT_SEED_LINE_WORDS
        records.append({"line": str(line_no), "text": " ".join(line_tokens)})
        line_no += 1
        if index >= len(selected):
            break
    return records


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("source") == "rebuilt_from_text":
            self._init_rebuilt_text_ledger(data)
            return
        metadata = data.get("metadata", {})
        self.source = "voynich"
        self.data = data
        self.rows: Dict[str, Any] = data["ledger"]
        self.alphabet: List[str] = list(data["alphabet"])
        self.columns = tuple(metadata.get("columns", DEFAULT_LEDGER_COLUMNS))
        self.tiers = tuple(metadata.get("tiers", ("80", "18", "2")))
        self.short_tokens = set(metadata.get("short_tokens", []))
        self.seed = data.get("seed") or {}
        self.followers: Dict[str, Dict[str, Tuple[List[str], List[float]]]] = {}
        self.first_token_weights = self._load_first_token_weights(metadata)
        self.tier_weights = self._tier_weights(self.tiers)
        for glyph, row in self.rows.items():
            self.followers[glyph] = {}
            for column in self.columns:
                values, weights = [], []
                for bucket, bw in self.tier_weights:
                    glyphs = row.get(column, {}).get(bucket, [])
                    if not glyphs:
                        continue
                    each = bw / len(glyphs)
                    values.extend(glyphs)
                    weights.extend([each] * len(glyphs))
                self.followers[glyph][column] = (values, weights)

    @classmethod
    def from_token_corpus(cls, tokens: List[str]) -> "Ledger":
        ledger_data = build_ledger_from_tokens(tokens)
        obj = cls.__new__(cls)
        obj.path = "<rebuilt_from_text>"
        obj._init_rebuilt_text_ledger(ledger_data)
        return obj

    def _init_rebuilt_text_ledger(self, ledger_data: Dict[str, Any]) -> None:
        self.source = "rebuilt_from_text"
        self.data = ledger_data
        self.rows = {}
        self.followers = {k: set(v) for k, v in ledger_data["followers"].items()}
        self.first_chars = Counter(ledger_data["first_chars"])
        self.last_chars = Counter(ledger_data["last_chars"])
        self.length_counts = Counter({int(k): v for k, v in ledger_data["length_counts"].items()})
        self.alphabet = sorted(set(ledger_data["alphabet"]))
        self.columns = DEFAULT_LEDGER_COLUMNS
        self.tiers = ("text",)
        self.short_tokens = {ch for ch, count in self.first_chars.items() if self.last_chars.get(ch, 0) and count}
        first_pairs = [(ch, float(count)) for ch, count in self.first_chars.items() if count > 0]
        if first_pairs:
            vals, wts = zip(*sorted(first_pairs))
            self.first_token_weights = (list(vals), list(wts))
        else:
            self.first_token_weights = (list(self.alphabet), None)
        self.tier_weights = (("text", 1.0),)

    def _tier_weights(self, tiers: Sequence[str]) -> Tuple[Tuple[str, float], ...]:
        vals = []
        for tier in tiers:
            try:
                vals.append((tier, float(tier) / 100.0))
            except ValueError:
                vals.append((tier, 1.0 / max(len(tiers), 1)))
        total = sum(v for _, v in vals) or 1.0
        return tuple((k, v / total) for k, v in vals)

    def _load_first_token_weights(self, metadata: Dict[str, Any]) -> Tuple[List[str], Optional[List[float]]]:
        raw = metadata.get("first_token_weights") or metadata.get("start_token_weights")
        if raw:
            pairs = [(g, float(w)) for g, w in raw.items() if g in self.rows and float(w) > 0]
            if pairs:
                vals, wts = zip(*sorted(pairs))
                return list(vals), list(wts)
        raw = metadata.get("first_tokens") or metadata.get("start_tokens")
        if raw:
            pairs = [(g, float(c)) for g, c in raw.items() if g in self.rows and float(c) > 0]
            if pairs:
                vals, wts = zip(*sorted(pairs))
                return list(vals), list(wts)
        return list(self.rows), None

    def legal_transition(self, left: str, right: str, column: str) -> bool:
        if getattr(self, "source", "voynich") == "rebuilt_from_text":
            return right in self.followers.get(left, set())
        row = self.rows.get(left, {}).get(column, {})
        return any(right in row.get(bucket, []) for bucket in self.tiers)

    def validate(self, token: str) -> bool:
        if not token:
            return False
        if len(token) > MAX_TOKEN_LEN:
            return False
        if getattr(self, "source", "voynich") == "rebuilt_from_text":
            if any(ch not in set(self.alphabet) for ch in token):
                return False
            if len(token) == 1:
                return token in self.short_tokens or token in self.first_chars
            if token[0] not in self.first_chars:
                return False
            return all(self.legal_transition(a, b, "text") for a, b in zip(token, token[1:]))
        if sum(1 for ch in token if ch in GALLOWS) > 1:
            return False
        if len(token) == 1:
            return token in self.short_tokens
        if token[0] not in self.rows:
            return False
        if not self.legal_transition(token[0], token[1], "prefix"):
            return False
        for i in range(2, len(token) - 1):
            if not self.legal_transition(token[i - 1], token[i], "midfix"):
                return False
        return self.legal_transition(token[-2], token[-1], "suffix")

    def choose_start_glyph(self, rng: random.Random, force_gallows: bool = False) -> str:
        if force_gallows and getattr(self, "source", "voynich") != "rebuilt_from_text":
            gallows = sorted(GALLOWS & set(self.rows))
            if gallows:
                return rng.choice(gallows)
        vals, wts = self.first_token_weights
        return weighted_choice(rng, vals, wts)

    def propose_seed(self, rng: random.Random, force_initial_gallows: bool = False) -> Optional[str]:
        first = self.choose_start_glyph(rng, force_gallows=force_initial_gallows)
        if getattr(self, "source", "voynich") == "rebuilt_from_text" and self.length_counts:
            lengths, weights = zip(*sorted(self.length_counts.items()))
            length = int(weighted_choice(rng, lengths, weights))
        else:
            length = rng.randint(4, 9)
        token = first
        for pos in range(1, length):
            if getattr(self, "source", "voynich") == "rebuilt_from_text":
                vals = sorted(self.followers.get(token[-1], set()))
                nxt = weighted_choice(rng, vals)
                if nxt is None:
                    return None
                token += nxt
                continue
            column = "prefix" if pos == 1 else ("suffix" if pos == length - 1 else "midfix")
            vals, wts = self.followers.get(token[-1], {}).get(column, ([], []))
            nxt = weighted_choice(rng, vals, wts)
            if nxt is None:
                return None
            token += nxt
        return token if self.validate(token) else None


class StandardPageRules:
    """
    Compact scheduler rules derived from the 10x10 heat-map JSON.

    This replaces direct use of the full heat map during generation. The rules
    are coarse page-phase x line-zone weights intended for plausibility, not
    exact positional reconstruction.
    """

    PAGE_PHASE_ORDER = ["page_opening", "early_page", "mid_page", "late_page", "page_end"]
    LINE_ZONE_ORDER = ["line_start", "line_body", "line_end"]

    FEATURE_TO_OP = {
        "external_exact_copy": "external_copy",
        "external_ed1": "external_ed1",
        "local_exact_copy": "local_copy",
        "local_ed1": "local_ed1",
    }

    def __init__(self, path: str | Path):
        with open(path, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.rules = self.data["rules"]
        self.page_ranges = {
            name: tuple(self.data["method"]["page_phases"][name]["range"])
            for name in self.PAGE_PHASE_ORDER
        }
        self.line_ranges = {
            name: tuple(self.data["method"]["line_zones"][name]["range"])
            for name in self.LINE_ZONE_ORDER
        }

    def page_phase(self, page_progress: float) -> str:
        x = max(0.0, min(0.999999, float(page_progress)))
        for name in self.PAGE_PHASE_ORDER:
            lo, hi = self.page_ranges[name]
            if lo <= x < hi:
                return name
        return self.PAGE_PHASE_ORDER[-1]

    def line_zone(self, line_progress: float) -> str:
        x = max(0.0, min(0.999999, float(line_progress)))
        for name in self.LINE_ZONE_ORDER:
            lo, hi = self.line_ranges[name]
            if lo <= x < hi:
                return name
        return self.LINE_ZONE_ORDER[-1]

    def rule(self, page_progress: float, line_progress: float) -> Dict[str, Any]:
        return self.rules[self.page_phase(page_progress)][self.line_zone(line_progress)]

    def cell(self, page_progress: float, line_progress: float) -> Tuple[int, int]:
        # Metadata index for output only: page-phase index and line-zone index.
        return (
            self.PAGE_PHASE_ORDER.index(self.page_phase(page_progress)),
            self.LINE_ZONE_ORDER.index(self.line_zone(line_progress)),
        )

    def rate(self, feature: str, page_progress: float, line_progress: float) -> float:
        rule = self.rule(page_progress, line_progress)
        return float(rule["feature_probabilities"].get(feature, 0.0))

    def operation_weights(self, page_progress: float, line_progress: float) -> Dict[str, float]:
        return dict(self.rule(page_progress, line_progress)["operation_weights"])


class GallowsWeights:
    DEFAULT_GALLOWS_CATEGORIES = {
        "gallows_insert": 53,
        "gallows_swap": 112,
        "gallows_delete": 35,
        "gallows_substitute_in": 73,
        "gallows_substitute_out": 88,
    }

    def __init__(self):
        self.data = {"gallows_categories": dict(self.DEFAULT_GALLOWS_CATEGORIES)}
        cats = self.DEFAULT_GALLOWS_CATEGORIES
        changing = sum(cats.get(k, 0) for k in (
            "gallows_insert", "gallows_swap", "gallows_delete", "gallows_substitute_in", "gallows_substitute_out"
        )) or 1
        self.change_weights = {
            "insert": cats.get("gallows_insert", 0) / changing,
            "swap": cats.get("gallows_swap", 0) / changing,
            "delete": cats.get("gallows_delete", 0) / changing,
            "substitute_in": cats.get("gallows_substitute_in", 0) / changing,
            "substitute_out": cats.get("gallows_substitute_out", 0) / changing,
        }


@dataclass
class TokenMeta:
    token: str
    operation: str
    source_folio: Optional[str]
    parent_token: Optional[str]
    position: int
    page_bucket: int
    line_bucket: int
    ledger_valid: bool
    ed_distance: Optional[int] = None
    gallows_action: Optional[str] = None


class SourceModel:
    def __init__(
        self,
        folio: str,
        source_folios: List[str],
        pages: Dict[str, List[Dict[str, Any]]],
        global_token_counts: Optional[Counter] = None,
    ):
        self.folio = folio
        self.source_folios = source_folios
        self.global_token_counts = global_token_counts or Counter()
        self.tokens_by_folio: Dict[str, List[str]] = defaultdict(list)
        self.token_freq = Counter()
        self.family_freq = Counter()
        for src in source_folios:
            for rec in pages.get(src, []):
                for meta in rec.get("generated_tokens", []):
                    tok = meta["token"]
                    if not tok:
                        continue
                    self.tokens_by_folio[src].append(tok)
                    self.token_freq[tok] += 1
                    fam = family_form(tok)
                    if fam:
                        self.family_freq[fam] += 1
        self.all_tokens = [tok for toks in self.tokens_by_folio.values() for tok in toks]
        self.ain_freq = sum(1 for tok in self.all_tokens if is_ain_like(tok))

    def source_weight(self, src: str) -> float:
        if src == "f1r":
            return F1R_SOURCE_WEIGHT
        # Generated pages should contribute, but not dominate as if they were manuscript truth.
        return GENERATED_SOURCE_WEIGHT * (RECENT_SOURCE_WEIGHT if src == self.source_folios[-1] else 1.0)

    def ed1_neighbors(self, parent: str, prefer_gallows: bool = False) -> List[str]:
        if not parent:
            return []
        out = []
        for tok in self.token_freq:
            if tok == parent:
                continue
            if prefer_gallows and not any(ch in GALLOWS for ch in tok):
                continue
            if edit_distance(parent, tok, 1) == 1:
                out.append(tok)
        return out

    def choose(
        self,
        rng: random.Random,
        active_families: Counter,
        prefer_gallows: bool = False,
        page_tokens: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        page_tokens = page_tokens or []
        short_bulge = page_share(page_tokens, lambda t: len(t) <= 2) > max(TARGET_SHORT_COPY_BACKGROUND * 2.0, 0.06)
        candidates: List[Tuple[str, str]] = []
        weights: List[float] = []
        for src, toks in self.tokens_by_folio.items():
            for tok in toks:
                if prefer_gallows and not any(ch in GALLOWS for ch in tok):
                    continue
                fam = family_form(tok)
                w = self.source_weight(src)
                w *= length_bias(tok)
                if short_bulge and len(tok) >= 6:
                    w *= LONG_PARENT_BOOST_AFTER_SHORT_BULGE
                if len(tok) <= 2:
                    w *= SHORT_TOKEN_SOURCE_BOOST
                w *= 1.0 + 0.12 * self.token_freq[tok]
                w *= 1.0 + 0.55 * min(active_families.get(fam, 0), 8)
                # Cross-page -ain pressure: don't let generated future pages collapse into one suffix family.
                if is_ain_like(tok):
                    w *= 1.0 / (1.0 + CROSS_PAGE_AIN_PRESSURE * self.ain_freq)
                global_count = self.global_token_counts.get(tok, 0)
                selection_reuse_count = max(0, global_count - self.token_freq.get(tok, 0))
                if selection_reuse_count == 0:
                    w *= UNIQUE_TOKEN_BONUS
                elif selection_reuse_count >= MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY:
                    w *= REUSED_TOKEN_PENALTY
                candidates.append((tok, src))
                weights.append(w)
        if not candidates:
            return None, None
        tok, src = weighted_choice(rng, candidates, weights)
        return tok, src


class PlausibleGenerator:
    def __init__(
        self,
        ledger_path: str,
        rules_path: str,
        seed: int = 1,
        ledger: Optional[Ledger] = None,
        seed_records: Optional[List[Dict[str, Any]]] = None,
    ):
        self.rng = random.Random(seed)
        self.ledger = ledger if ledger is not None else Ledger(ledger_path)
        self.heatmaps = StandardPageRules(rules_path)
        self.gallows = GallowsWeights()
        self.seed_records = seed_records or EMBEDDED_F1R_RECORDS
        self.progress_callback = None
        self.pages: Dict[str, List[Dict[str, Any]]] = {}
        self.stats = Counter()
        self.recent_tokens = deque(maxlen=24)
        self.global_token_counts = Counter()

    def seed_page(self, folio: str = "f1r") -> List[Dict[str, Any]]:
        if folio != "f1r":
            raise RuntimeError("Only the embedded f1r seed is available.")
        seeded = []
        for rec in self.seed_records:
            tokens = rec.get("text", "").split()
            metas = []
            for i, tok in enumerate(tokens, 1):
                metas.append(TokenMeta(tok, "seed", None, None, i, 0, 0, self.ledger.validate(tok)).__dict__)
                self.global_token_counts[tok] += 1
            line_no = int(rec["line"])
            seeded.append({
                "folio": folio,
                "line": str(rec["line"]),
                "text": " ".join(tokens),
                "paragraph_id": 1,
                "paragraph_start": line_no == 1,
                **physical_metadata(folio),
                "generated_tokens": metas,
            })
        self.pages[folio] = seeded
        return seeded

    def source_schedule(self, folio: str) -> Tuple[List[str], Optional[str]]:
        prior = sorted(self.pages, key=folio_sort_key)
        if folio == "f1v":
            return ["f1r"], "f1r"
        # Plausible working packet: seed + two most recent generated pages.
        pool: List[str] = []
        for item in ["f1r", *prior[-2:]]:
            if item in prior and item not in pool:
                pool.append(item)
        return pool, pool[0] if pool else None

    def choose_operation(self, page_progress: float, line_progress: float, local_count: int) -> str:
        w = self.heatmaps.operation_weights(page_progress, line_progress)
        if local_count < 4:
            w["local_copy"] *= 0.10
            w["local_ed1"] *= 0.10
        elif local_count < 12:
            w["local_copy"] *= 0.55
            w["local_ed1"] *= 0.55
        # Prevent external ED1 from swamping everything despite non-mutually-exclusive maps.
        w["external_ed1"] *= 0.85
        w["local_ed1"] *= 1.15
        return weighted_choice(self.rng, list(w), list(w.values()))

    def generate_pages(self, pages_after_seed: int) -> List[Dict[str, Any]]:
        out = list(self.seed_page("f1r"))
        if self.progress_callback:
            self.progress_callback(1, pages_after_seed + 1, "f1r")
        for idx in range(1, pages_after_seed + 1):
            folio = folio_after_seed(idx)
            out.extend(self.generate_page(folio))
            if self.progress_callback:
                self.progress_callback(idx + 1, pages_after_seed + 1, folio)
        return out

    def generate_page(self, folio: str) -> List[Dict[str, Any]]:
        source_folios, _anchor = self.source_schedule(folio)
        source = SourceModel(folio, source_folios, self.pages, self.global_token_counts)
        target_words_min = max(1, int(TARGET_WORDS_MIN))
        target_words_max = max(target_words_min, int(TARGET_WORDS_MAX))
        max_words_line_min = max(1, int(MAX_WORDS_LINE_MIN))
        max_words_line_max = max(max_words_line_min, int(MAX_WORDS_LINE_MAX))
        target_words = self.rng.randint(target_words_min, target_words_max)
        records: List[Dict[str, Any]] = []
        page_tokens: List[str] = []
        active_families: Counter = Counter()
        line_no = 1
        paragraph_plan = self.create_paragraph_plan(target_words)
        paragraph_starts = paragraph_plan["starts"]
        while len(page_tokens) < target_words:
            max_words_line = self.rng.randint(max_words_line_min, max_words_line_max)
            remaining = target_words - len(page_tokens)
            line_target = min(remaining, max_words_line)
            line_tokens: List[str] = []
            line_meta: List[Dict[str, Any]] = []
            while len(line_tokens) < line_target:
                page_progress = len(page_tokens) / max(target_words, 1)
                line_progress = len(line_tokens) / max(line_target, 1)
                is_page_first = len(page_tokens) == 0 and len(line_tokens) == 0
                is_line_first = len(line_tokens) == 0
                force_initial_gallows = self.should_force_initial_gallows(is_page_first, is_line_first, line_no, paragraph_starts, page_progress, line_progress)
                op = self.choose_operation(page_progress, line_progress, len(page_tokens))
                proposal = self.propose_token(op, source, page_tokens, active_families, force_initial_gallows, page_progress, line_progress)
                if proposal is None:
                    self.stats["null_proposals"] += 1
                    continue
                tok, actual_op, parent, src, ed, gallows_action = proposal
                if not self.accept_token(tok, actual_op, page_tokens=page_tokens):
                    self.stats["rejected"] += 1
                    continue
                line_tokens.append(tok)
                page_tokens.append(tok)
                self.global_token_counts[tok] += 1
                fam = family_form(tok)
                if fam:
                    active_families[fam] += 1
                self.recent_tokens.append(tok)
                pi, li = self.heatmaps.cell(page_progress, line_progress)
                line_meta.append(TokenMeta(tok, actual_op, src, parent, len(line_tokens), pi + 1, li + 1, self.ledger.validate(tok), ed, gallows_action).__dict__)
            paragraph_id = self.paragraph_id_for_line(line_no, paragraph_starts)
            records.append({
                "folio": folio,
                "line": str(line_no),
                "text": " ".join(line_tokens),
                "paragraph_id": paragraph_id,
                "paragraph_start": line_no in paragraph_starts,
                **physical_metadata(folio),
                "generated_tokens": line_meta,
            })
            line_no += 1
        self.pages[folio] = records
        return records

    def create_paragraph_plan(self, target_words: int) -> Dict[str, Any]:
        """
        Create paragraph boundaries for page layout.

        Layout only:
        - does not choose tokens
        - does not mutate words
        - does not alter source pools
        - does not change ledger validation
        """
        starts: set[int] = {1}

        if not PARAGRAPH_MODEL_ENABLED:
            return {"starts": starts, "target_words": target_words}

        first_min = max(2, int(PARAGRAPH_MIN_LINES))
        first_max = max(first_min, int(PARAGRAPH_MAX_LINES))

        if self.rng.random() < PARAGRAPH_SECONDARY_PROBABILITY:
            starts.add(self.rng.randint(first_min, first_max))

        if self.rng.random() < PARAGRAPH_TERTIARY_PROBABILITY:
            low = first_max + 1
            high = first_max + max(3, first_max - first_min + 1)
            starts.add(self.rng.randint(low, high))

        return {"starts": starts, "target_words": target_words}

    def paragraph_id_for_line(self, line_no: int, paragraph_starts: set[int]) -> int:
        return sum(1 for start in sorted(paragraph_starts) if start <= line_no)

    def should_force_initial_gallows(self, is_page_first: bool, is_line_first: bool, line_no: int, paragraph_starts: set[int], page_progress: float, line_progress: float) -> bool:
        if not is_line_first:
            return False
        rate = self.heatmaps.rate("initial_gallows", page_progress, line_progress)
        if is_page_first:
            rate = max(rate, 0.72)
        elif line_no in paragraph_starts:
            rate = max(rate, PARAGRAPH_START_INITIAL_GALLOWS_MIN_RATE)
        else:
            rate *= 0.75
        return self.rng.random() < rate

    def propose_novel_variant(
        self,
        source: SourceModel,
        page_tokens: List[str],
        active_families: Counter,
        page_progress: float,
        line_progress: float,
    ) -> Optional[Tuple[str, str, Optional[str], Optional[str], Optional[int], Optional[str]]]:
        """Force a new ledger-valid token, preferably as ED1 from source/local parent."""
        del active_families, page_progress, line_progress
        parents = []

        if page_tokens:
            parents.extend(page_tokens[-60:])
        parents.extend(source.all_tokens)

        if NOVEL_VARIANT_PREFER_ED1 and parents:
            for _ in range(NOVEL_VARIANT_MAX_ATTEMPTS):
                parent = self.rng.choice(parents)
                tok = self.standard_ed1(parent)
                gallows_action = None

                if not tok or tok == parent:
                    continue
                if tok in page_tokens:
                    continue
                if self.global_token_counts.get(tok, 0) > 0:
                    continue
                if self.accept_token(tok, "novel_ed1", page_tokens=page_tokens):
                    return tok, "novel_ed1", parent, None, 1, gallows_action

        if NOVEL_VARIANT_ALLOW_SEED_FALLBACK:
            for _ in range(NOVEL_VARIANT_MAX_ATTEMPTS):
                tok = self.ledger.propose_seed(self.rng, force_initial_gallows=False)

                if not tok:
                    continue
                if tok in page_tokens:
                    continue
                if self.global_token_counts.get(tok, 0) > 0:
                    continue
                if self.accept_token(tok, "novel_seed", page_tokens=page_tokens):
                    return tok, "novel_seed", None, None, None, None

        return None

    def propose_token(self, op: str, source: SourceModel, page_tokens: List[str], active_families: Counter, force_initial_gallows: bool, page_progress: float, line_progress: float) -> Optional[Tuple[str, str, Optional[str], Optional[str], Optional[int], Optional[str]]]:
        prefer_internal_gallows = self.rng.random() < self.heatmaps.rate("internal_gallows", page_progress, line_progress) * 0.18

        if (
            not force_initial_gallows
            and self.rng.random() < NOVEL_VARIANT_RATE
        ):
            proposal = self.propose_novel_variant(
                source,
                page_tokens,
                active_families,
                page_progress,
                line_progress,
            )
            if proposal is not None:
                return proposal

        # Preserve a low background rate of true short tokens. v10.6 lost these by later pages.
        if (
            not force_initial_gallows
            and len(page_tokens) > 8
            and self.rng.random() < TARGET_SHORT_COPY_BACKGROUND
            and page_share(page_tokens, lambda t: len(t) <= 2) < TARGET_SHORT_COPY_BACKGROUND
        ):
            short_source = [(tok, src) for src, toks in source.tokens_by_folio.items() for tok in toks if len(tok) <= 2]
            if short_source:
                tok, src = self.rng.choice(short_source)
                return tok, "external_short_copy", tok, src, 0, None

        if force_initial_gallows:
            parent, src = self.choose_parent_for_gallows(source, page_tokens, active_families)
            tok = self.add_initial_gallows(parent) if parent else None
            if tok and self.ledger.validate(tok):
                return tok, "initial_gallows_construct", parent, src, edit_distance(parent or "", tok, 2) if parent else None, "initial_construct"
            # Avoid flooding with synthetic initial-gallows seed forms.
            # If construction fails, continue to normal operation rather than forcing seed.
            if self.rng.random() < 0.18:
                for _ in range(12):
                    seed = self.ledger.propose_seed(self.rng, force_initial_gallows=True)
                    if seed and seed[0] in GALLOWS and self.accept_token(seed, "seed", page_tokens=page_tokens):
                        return seed, "initial_gallows_seed", None, None, None, "initial_seed"

        if op == "local_copy" and page_tokens:
            tok = self.choose_local_token(page_tokens, active_families)
            return tok, "local_copy", tok, None, 0, None

        if op == "local_ed1" and page_tokens:
            parent = self.choose_local_token(page_tokens, active_families)
            tok, gallows_action = self.mutate_ed1(parent, source=source, page_tokens=page_tokens, active_families=active_families, prefer_gallows=prefer_internal_gallows)
            if tok != parent:
                return tok, "local_ed1", parent, None, 1, gallows_action
            return parent, "local_copy", parent, None, 0, None

        if op == "external_copy":
            tok, src = source.choose(self.rng, active_families, prefer_gallows=prefer_internal_gallows, page_tokens=page_tokens)
            if tok:
                return tok, "external_copy", tok, src, 0, None

        if op == "external_ed1":
            parent, src = source.choose(self.rng, active_families, prefer_gallows=prefer_internal_gallows, page_tokens=page_tokens)
            if parent:
                tok, gallows_action = self.mutate_ed1(parent, source=source, page_tokens=page_tokens, active_families=active_families, prefer_gallows=prefer_internal_gallows)
                if tok != parent:
                    return tok, "external_ed1", parent, src, 1, gallows_action
                return parent, "external_copy", parent, src, 0, None

        # Final fallback: do NOT flood the page with synthetic seed words.
        # Prefer a visible source/local copy; use synthetic seed only rarely.
        if page_tokens and self.rng.random() > SEED_FALLBACK_PROBABILITY:
            parent = self.choose_local_token(page_tokens, active_families)
            return parent, "local_copy_fallback", parent, None, 0, None
        tok, src = source.choose(self.rng, active_families, prefer_gallows=False, page_tokens=page_tokens)
        if tok and self.rng.random() > SEED_FALLBACK_PROBABILITY:
            return tok, "external_copy_fallback", tok, src, 0, None

        for _ in range(20):
            tok = self.ledger.propose_seed(self.rng, force_initial_gallows=False)
            if tok and self.accept_token(tok, "seed", page_tokens=page_tokens):
                return tok, "seed", None, None, None, None
        return None

    def choose_parent_for_gallows(self, source: SourceModel, page_tokens: List[str], active_families: Counter) -> Tuple[Optional[str], Optional[str]]:
        if page_tokens and self.rng.random() < 0.45:
            return self.choose_local_token(page_tokens, active_families), None
        return source.choose(self.rng, active_families, prefer_gallows=False, page_tokens=page_tokens)

    def choose_local_token(self, page_tokens: List[str], active_families: Counter) -> str:
        recent = page_tokens[-30:]
        candidates = page_tokens[-60:] if len(page_tokens) > 60 else page_tokens
        short_bulge = page_share(page_tokens, lambda t: len(t) <= 2) > max(TARGET_SHORT_COPY_BACKGROUND * 2.0, 0.06)
        weights = []
        for tok in candidates:
            fam = family_form(tok)
            w = length_bias(tok)
            if short_bulge and len(tok) >= 6:
                w *= LONG_PARENT_BOOST_AFTER_SHORT_BULGE
            if len(tok) <= 2:
                w *= SHORT_TOKEN_LOCAL_BOOST
            if tok in recent:
                w *= 2.10
            w *= 1.0 + 0.42 * min(active_families.get(fam, 0), 8)
            if is_ain_like(tok) and page_share(page_tokens, is_ain_like) > AIN_PAGE_SOFT_SHARE:
                w *= AIN_SELECTION_PENALTY
            if len(tok) in (4, 5) and page_share(page_tokens, lambda t: len(t) in (4, 5)) > LENGTH_45_PAGE_SOFT_SHARE:
                w *= LENGTH_45_PENALTY
            if page_tokens and tok == page_tokens[-1]:
                w *= 0.35
            global_count = self.global_token_counts.get(tok, 0)
            if global_count == 0:
                w *= UNIQUE_TOKEN_BONUS
            elif global_count >= MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY:
                w *= REUSED_TOKEN_PENALTY
            weights.append(w)
        return weighted_choice(self.rng, candidates, weights)

    def add_initial_gallows(self, parent: Optional[str]) -> Optional[str]:
        if not parent:
            return None
        stem = parent[1:] if parent[0] in GALLOWS else parent
        if not stem or stem[0] in GALLOWS:
            return None
        gs = list(GALLOWS)
        self.rng.shuffle(gs)
        for g in gs:
            for tok in (g + stem, g + stem[1:] if len(stem) >= 5 else None):
                if tok and len(tok) >= 4 and self.ledger.validate(tok):
                    return tok
        return None

    def visible_ed1_neighbors(
        self,
        parent: str,
        source: Optional[SourceModel],
        page_tokens: List[str],
        prefer_gallows: bool = False,
    ) -> List[str]:
        """Return real ED1 variants visible in source/local material."""
        candidates = set()
        if source is not None:
            candidates.update(source.ed1_neighbors(parent, prefer_gallows=prefer_gallows))
        for tok in page_tokens:
            if tok == parent:
                continue
            if prefer_gallows and not any(ch in GALLOWS for ch in tok):
                continue
            if edit_distance(parent, tok, 1) == 1:
                candidates.add(tok)
        return sorted(candidates)

    def choose_attested_ed1(
        self,
        parent: str,
        source: Optional[SourceModel],
        page_tokens: List[str],
        active_families: Counter,
        prefer_gallows: bool = False,
    ) -> Optional[str]:
        neighbors = self.visible_ed1_neighbors(parent, source, page_tokens, prefer_gallows)
        if not neighbors:
            return None
        weights = []
        for tok in neighbors:
            fam = family_form(tok)
            w = length_bias(tok)
            w *= 1.0 + 0.50 * min(active_families.get(fam, 0), 8)
            if page_tokens and tok in page_tokens[-30:]:
                w *= 1.55
            if len(tok) <= 2:
                w *= SHORT_TOKEN_LOCAL_BOOST
            if is_ain_like(tok) and page_share(page_tokens, is_ain_like) > AIN_PAGE_SOFT_SHARE:
                w *= AIN_MUTATION_PENALTY
            if len(tok) in (4, 5) and page_share(page_tokens, lambda t: len(t) in (4, 5)) > LENGTH_45_PAGE_SOFT_SHARE:
                w *= LENGTH_45_PENALTY
            if any(ch in GALLOWS for ch in tok):
                w *= 1.10 if prefer_gallows else 0.82
            global_count = self.global_token_counts.get(tok, 0)
            source_count = source.token_freq.get(tok, 0) if source is not None else 0
            local_count = page_tokens.count(tok)
            selection_reuse_count = max(0, global_count - source_count - local_count)
            if selection_reuse_count == 0:
                w *= UNIQUE_TOKEN_BONUS
            elif selection_reuse_count >= MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY:
                w *= REUSED_TOKEN_PENALTY
            weights.append(w)
        return weighted_choice(self.rng, neighbors, weights)

    def mutate_ed1(
        self,
        parent: str,
        source: Optional[SourceModel] = None,
        page_tokens: Optional[List[str]] = None,
        active_families: Optional[Counter] = None,
        prefer_gallows: bool = False,
    ) -> Tuple[str, Optional[str]]:
        if not parent:
            return parent, None
        page_tokens = page_tokens or []
        active_families = active_families or Counter()

        if ATTESTED_ED1_FIRST:
            tok = self.choose_attested_ed1(parent, source, page_tokens, active_families, prefer_gallows)
            if tok and self.accept_token(tok, "ed1", page_tokens=page_tokens):
                return tok, "attested_ed1"

        # Gallows changes are allowed, but only after attested-neighbor search fails.
        for _ in range(ATTESTED_ED1_ATTEMPTS):
            if prefer_gallows or self.rng.random() < 0.10 or any(ch in GALLOWS for ch in parent):
                tok, action = self.gallows_mutation(parent)
                if tok != parent and self.accept_token(tok, "ed1", page_tokens=page_tokens):
                    return tok, action

            # Random ED1 is now a rare fallback, not the normal path.
            if self.rng.random() < RANDOM_ED1_FALLBACK_PROBABILITY:
                tok = self.standard_ed1(parent)
                if tok != parent and self.accept_token(tok, "ed1", page_tokens=page_tokens):
                    return tok, None

        return parent, None

    def standard_ed1(self, parent: str) -> str:
        chars = list(parent)
        if len(chars) < 2:
            return parent
        # f27r + 10-page notes: substitute dominates, but generator needs
        # length correction: short/mid parents should more often grow, not collapse.
        if len(chars) <= 5:
            op = weighted_choice(self.rng, ["substitute", "insert", "delete"], [0.42, 0.50, 0.08])
        elif len(chars) == 6:
            op = weighted_choice(self.rng, ["substitute", "insert", "delete"], [0.48, 0.34, 0.18])
        else:
            op = weighted_choice(self.rng, ["substitute", "insert", "delete"], [0.48, 0.25, 0.27])
        # Edge-biased but not edge-only.
        positions = list(range(len(chars)))
        weights = []
        for i in positions:
            edge = min(i, len(chars) - 1 - i)
            weights.append(2.4 if edge == 0 else (1.7 if edge == 1 else 1.0))
        if op == "delete" and len(chars) > MIN_MUTATED_LEN:
            pos = weighted_choice(self.rng, positions, weights)
            return "".join(chars[:pos] + chars[pos + 1:])
        if op == "insert" and len(chars) < MAX_TOKEN_LEN:
            pos = weighted_choice(self.rng, list(range(len(chars) + 1)), [1.8 if i in (0, 1, len(chars)) else 1.0 for i in range(len(chars) + 1)])
            left = chars[pos - 1] if pos > 0 else None
            right = chars[pos] if pos < len(chars) else None
            ch = self.choose_insert_glyph(left, right)
            return "".join(chars[:pos] + [ch] + chars[pos:])
        # substitute; preserve C/V class most of the time.
        pos = weighted_choice(self.rng, positions, weights)
        old = chars[pos]
        cls = cv_class(old)
        pool = [g for g in self.ledger.alphabet if g != old]
        if cls == "V" and self.rng.random() < 0.82:
            pool = [g for g in pool if g in VOWELS] or pool
        elif cls == "C" and self.rng.random() < 0.86:
            pool = [g for g in pool if g not in VOWELS and g not in GALLOWS] or pool
        chars[pos] = self.rng.choice(pool)
        return "".join(chars)

    def choose_insert_glyph(self, left: Optional[str], right: Optional[str]) -> str:
        # Preserve observed tendency: vowel-center expansion and consonant cluster extension.
        if left in VOWELS or right in VOWELS:
            if self.rng.random() < 0.58:
                return self.rng.choice(sorted(VOWELS & set(self.ledger.alphabet)))
        else:
            if self.rng.random() < 0.62:
                cons = [g for g in self.ledger.alphabet if g not in VOWELS and g not in GALLOWS]
                return self.rng.choice(cons)
        return self.rng.choice(self.ledger.alphabet)

    def gallows_mutation(self, parent: str) -> Tuple[str, Optional[str]]:
        chars = list(parent)
        actions = list(self.gallows.change_weights)
        weights = [self.gallows.change_weights[a] for a in actions]
        action = weighted_choice(self.rng, actions, weights)
        gallows_positions = [i for i, ch in enumerate(chars) if ch in GALLOWS]
        non_gallows_positions = [i for i, ch in enumerate(chars) if ch not in GALLOWS]
        if action == "insert" and len(chars) < MAX_TOKEN_LEN:
            # Measured: second position strongest, then initial, no final.
            poss = list(range(0, len(chars)))
            w = []
            for p in poss:
                w.append(4.0 if p == 1 else (2.6 if p == 0 else (1.0 if p < len(chars) - 1 else 0.1)))
            pos = weighted_choice(self.rng, poss, w)
            g = self.rng.choice(sorted(GALLOWS))
            return "".join(chars[:pos] + [g] + chars[pos:]), "gallows_insert"
        if action == "swap" and gallows_positions:
            pos = weighted_choice(self.rng, gallows_positions, [3.0 if i == 1 else 1.0 for i in gallows_positions])
            options = sorted(GALLOWS - {chars[pos]})
            chars[pos] = self.rng.choice(options)
            return "".join(chars), "gallows_swap"
        if action == "delete" and gallows_positions and len(chars) > MIN_MUTATED_LEN:
            pos = weighted_choice(self.rng, gallows_positions, [2.5 if i in (0, 1) else 1.0 for i in gallows_positions])
            return "".join(chars[:pos] + chars[pos + 1:]), "gallows_delete"
        if action == "substitute_in" and non_gallows_positions:
            pos = weighted_choice(self.rng, non_gallows_positions, [3.0 if i in (0, 1) else 1.0 for i in non_gallows_positions])
            chars[pos] = self.rng.choice(sorted(GALLOWS))
            return "".join(chars), "gallows_substitute_in"
        if action == "substitute_out" and gallows_positions:
            pos = weighted_choice(self.rng, gallows_positions, [3.0 if i in (0, 1) else 1.0 for i in gallows_positions])
            cons = [g for g in self.ledger.alphabet if g not in VOWELS and g not in GALLOWS]
            chars[pos] = self.rng.choice(cons)
            return "".join(chars), "gallows_substitute_out"
        return self.standard_ed1(parent), None

    def accept_token(self, token: str, op: str, page_tokens: Optional[List[str]] = None) -> bool:
        if not token:
            return False
        page_tokens = page_tokens or []
        if len(token) > MAX_TOKEN_LEN:
            return False
        if max_run(token, VOWELS) > MAX_VOWEL_RUN:
            return False
        consonants = set(self.ledger.alphabet) - VOWELS
        if max_run(token, consonants) > MAX_CONSONANT_RUN:
            return False
        if len(token) <= 2 and op not in COPY_OPS and op != "seed":
            return False
        if len(token) == 1 and page_share(page_tokens, lambda t: len(t) == 1) > 0.055:
            return False
        if self.recent_tokens and token == self.recent_tokens[-1]:
            return False
        if list(self.recent_tokens).count(token) >= 3:
            return False
        if page_tokens.count(token) >= MAX_SOURCE_TOKEN_PAGE_COUNT and op not in COPY_OPS:
            return False
        if is_ain_like(token) and page_share(page_tokens, is_ain_like) > AIN_PAGE_HARD_SHARE and op not in COPY_OPS:
            return False
        if len(token) in (4, 5) and page_share(page_tokens, lambda t: len(t) in (4, 5)) > 0.70 and op not in COPY_OPS:
            return False
        fam = family_form(token)
        if fam and sum(1 for t in page_tokens if family_form(t) == fam) >= MAX_FAMILY_PAGE_COUNT and op not in COPY_OPS:
            return False
        return self.ledger.validate(token)


def summarize(records: List[Dict[str, Any]]) -> Counter:
    c = Counter()
    for rec in records:
        for meta in rec.get("generated_tokens", []):
            c["tokens"] += 1
            c[f"op:{meta.get('operation')}"] += 1
            if meta.get("gallows_action"):
                c[f"gallows:{meta.get('gallows_action')}"] += 1
            if not meta.get("ledger_valid"):
                c["ledger_invalid"] += 1
    return c


def takahashi_line_tag(rec: Dict[str, Any], transcriber: str = "K") -> str:
    folio = rec.get("folio", "f0r")
    paragraph = rec.get("paragraph") or f"P{rec.get('paragraph_id', 1)}"
    line = rec.get("line", "1")
    return f"<{folio}.{paragraph}.{line};{transcriber}>"


def takahashi_line_text(rec: Dict[str, Any]) -> str:
    return ".".join(str(rec.get("text", "")).split())


def write_text(records: List[Dict[str, Any]], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        current = None
        for rec in records:
            if rec["folio"] != current:
                if current is not None:
                    f.write("\n")
                current = rec["folio"]
                f.write(f"<{current}>\n")
            f.write(f"{takahashi_line_tag(rec):<20} {takahashi_line_text(rec)}\n")


def records_to_text(records: List[Dict[str, Any]]) -> str:
    lines = []
    current = None
    for rec in records:
        if rec.get("folio") != current:
            if current is not None:
                lines.append("")
            current = rec.get("folio")
            lines.append(f"<{current}>")
        lines.append(f"{takahashi_line_tag(rec):<20} {takahashi_line_text(rec)}")
    return "\n".join(lines)


def apply_gui_knob_values(values: Dict[str, Any]) -> None:
    for name, value in values.items():
        m = re.match(r"^LENGTH_SELECTION_BIAS_(\d+)$", name)
        if m:
            LENGTH_SELECTION_BIAS[int(m.group(1))] = float(value)
        elif name == "AIN_SUFFIX_PATTERN":
            globals()["AIN_SUFFIX_RE"] = re.compile(str(value))
        elif name in {"GALLOWS", "VOWELS"}:
            globals()[name] = set(str(value).replace(",", "").replace(" ", ""))
        elif name == "DEFAULT_LEDGER_COLUMNS":
            globals()[name] = tuple(part.strip() for part in str(value).split(",") if part.strip())
        elif name == "COPY_OPS":
            globals()[name] = {part.strip() for part in str(value).split(",") if part.strip()}
        elif name == "RANDOM_SEED":
            text = str(value).strip()
            globals()[name] = int(text) if text else None
        elif name in GUI_KNOBS:
            globals()[name] = value


def tokenize_text(text: str) -> List[str]:
    return re.findall(r"[A-Za-z]+", text.lower())


def collect_text_records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            return [r for r in data["records"] if isinstance(r, dict)]
        if isinstance(data.get("pages"), list):
            return [r for r in data["pages"] if isinstance(r, dict)]
        found: List[Dict[str, Any]] = []
        for value in data.values():
            found.extend(collect_text_records(value))
        return found
    if isinstance(data, list):
        found = []
        for item in data:
            if isinstance(item, dict) and "text" in item:
                found.append(item)
            else:
                found.extend(collect_text_records(item))
        return found
    return []


def comparison_pages_from_json_payload(data: Any) -> List[Tuple[str, List[str]]]:
    records = collect_text_records(data)
    pages: Dict[str, List[str]] = defaultdict(list)
    for index, rec in enumerate(records):
        text = rec.get("text") or rec.get("transcription") or rec.get("line") or ""
        page_id = rec.get("folio") or rec.get("page") or rec.get("folio_id") or rec.get("id") or str(index)
        if isinstance(text, str):
            pages[str(page_id)].extend(tokenize_text(text))
    if pages:
        return [(page_id, pages[page_id]) for page_id in sorted(pages, key=folio_sort_key)]
    if isinstance(data, dict):
        text = data.get("text") or data.get("transcription") or ""
        if isinstance(text, str):
            tokens = tokenize_text(text)
            if tokens:
                return [("text", tokens)]
    return []


def load_comparison_json(path: str | Path, page_count: Optional[int] = None) -> Tuple[List[str], int, int]:
    with open(path, "r", encoding="utf-8") as f:
        pages = comparison_pages_from_json_payload(json.load(f))
    available_pages = len(pages)
    if page_count is not None:
        pages = pages[:max(0, page_count)]
    tokens = [tok for _page_id, page_tokens in pages for tok in page_tokens]
    return tokens, len(pages), available_pages


def length_distribution(tokens: Sequence[str]) -> Counter:
    return Counter(len(tok) for tok in tokens if tok)


def ranked_counts(tokens: Sequence[str]) -> List[int]:
    return [count for _tok, count in Counter(tokens).most_common()]


def character_bigram_counts(tokens: Sequence[str]) -> Counter:
    counts = Counter()
    for token in tokens:
        text = str(token)
        counts.update(text[i:i + 2] for i in range(max(0, len(text) - 1)))
    return counts


def character_trigram_counts(tokens: Sequence[str]) -> Counter:
    counts = Counter()
    for token in tokens:
        text = str(token)
        counts.update(text[i:i + 3] for i in range(max(0, len(text) - 2)))
    return counts


class ChartCanvas(tk.Canvas):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent, background="white", highlightthickness=1, highlightbackground="#c8c8c8")
        self.bind("<Configure>", lambda _event: self.redraw())
        self._kind = "zipf"
        self._output: Sequence[str] = []
        self._voynich: Sequence[str] = []

    def set_data(self, kind: str, output_tokens: Sequence[str], voynich_tokens: Sequence[str]) -> None:
        self._kind = kind
        self._output = output_tokens
        self._voynich = voynich_tokens
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 320)
        height = max(self.winfo_height(), 220)
        margin_left, margin_right, margin_top, margin_bottom = 54, 18, 20, 42
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        self.create_rectangle(margin_left, margin_top, width - margin_right, height - margin_bottom, outline="#d0d0d0")
        if self._kind == "zipf":
            self._draw_zipf(width, height, margin_left, margin_top, margin_bottom, plot_w, plot_h)
        else:
            self._draw_lengths(width, height, margin_left, margin_top, margin_bottom, plot_w, plot_h)
        self.create_text(width - 210, 16, text="output", fill="#1f77b4", anchor="w")
        self.create_line(width - 260, 16, width - 220, 16, fill="#1f77b4", width=2)
        self.create_text(width - 110, 16, text="Voynich", fill="#d62728", anchor="w")
        self.create_line(width - 160, 16, width - 120, 16, fill="#d62728", width=2)

    def _draw_zipf(self, width: int, height: int, ml: int, mt: int, mb: int, pw: int, ph: int) -> None:
        series = [ranked_counts(self._output), ranked_counts(self._voynich)]
        max_rank = max([len(s) for s in series if s] or [1])
        max_count = max([max(s) for s in series if s] or [1])
        self.create_text(width / 2, height - 14, text="Word rank (log scale)", fill="#333333")
        self.create_text(16, mt + ph / 2, text="Token count (log scale)", fill="#333333", angle=90)
        for tick in self._log_ticks(max_rank):
            x = ml + (math.log10(tick) / math.log10(max_rank)) * pw if max_rank > 1 else ml
            self.create_line(x, mt + ph, x, mt + ph + 4, fill="#777777")
            self.create_text(x, mt + ph + 14, text=str(tick), fill="#555555")
        for tick in self._log_ticks(max_count):
            y = mt + ph - (math.log10(tick) / math.log10(max_count)) * ph if max_count > 1 else mt + ph
            self.create_line(ml - 4, y, ml, y, fill="#777777")
            self.create_text(ml - 8, y, text=str(tick), fill="#555555", anchor="e")
        for counts, color in ((series[0], "#1f77b4"), (series[1], "#d62728")):
            if len(counts) < 2:
                continue
            pts = []
            for i, count in enumerate(counts, 1):
                x = ml + (math.log10(i) / math.log10(max_rank)) * pw if max_rank > 1 else ml
                y = mt + ph - (math.log10(count) / math.log10(max_count)) * ph if max_count > 1 else mt + ph
                pts.extend((x, y))
            self.create_line(*pts, fill=color, width=2, smooth=True)

    def _draw_lengths(self, width: int, height: int, ml: int, mt: int, mb: int, pw: int, ph: int) -> None:
        output = length_distribution(self._output)
        voynich = length_distribution(self._voynich)
        max_len = max(list(output) + list(voynich) + [1])
        max_count = max(list(output.values()) + list(voynich.values()) + [1])
        group_w = pw / max_len
        bar_w = max(3, group_w * 0.35)
        self.create_text(width / 2, height - 14, text="token length", fill="#333333")
        self.create_text(16, mt + ph / 2, text="count", fill="#333333", angle=90)
        for tick in self._linear_ticks(max_count):
            y = mt + ph - (tick / max_count) * ph if max_count else mt + ph
            self.create_line(ml - 4, y, ml, y, fill="#777777")
            self.create_text(ml - 8, y, text=str(tick), fill="#555555", anchor="e")
        for length in range(1, max_len + 1):
            x0 = ml + (length - 1) * group_w + group_w * 0.18
            for offset, value, color in ((0, output[length], "#1f77b4"), (bar_w + 2, voynich[length], "#d62728")):
                h = (value / max_count) * ph
                self.create_rectangle(x0 + offset, mt + ph - h, x0 + offset + bar_w, mt + ph, fill=color, outline="")
            if length == 1 or length % 2 == 0:
                self.create_text(ml + (length - 0.5) * group_w, height - mb + 14, text=str(length), fill="#555555")

    def _linear_ticks(self, max_value: int, tick_count: int = 5) -> List[int]:
        if max_value <= 0:
            return [0]
        raw_step = max_value / max(tick_count - 1, 1)
        magnitude = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
        normalized = raw_step / magnitude
        if normalized <= 1:
            step = magnitude
        elif normalized <= 2:
            step = 2 * magnitude
        elif normalized <= 5:
            step = 5 * magnitude
        else:
            step = 10 * magnitude
        ticks = list(range(0, int(math.ceil(max_value / step) * step) + 1, int(step)))
        return [tick for tick in ticks if tick <= max_value or tick == ticks[-1]]

    def _log_ticks(self, max_value: int) -> List[int]:
        if max_value <= 1:
            return [1]
        ticks = []
        power = 1
        while power <= max_value:
            ticks.append(power)
            if 2 * power <= max_value:
                ticks.append(2 * power)
            if 5 * power <= max_value:
                ticks.append(5 * power)
            power *= 10
        if max_value not in ticks:
            ticks.append(max_value)
        return sorted(set(ticks))


class GeneratorGUI:
    GUI_ONLY_HIDDEN_KNOBS = {
        "DEFAULT_LEDGER_COLUMNS",
        "COPY_OPS",
        "RANDOM_SEED",
        "PARAGRAPH_TEXT_BLANK_LINES",
    }

    GUI_KNOB_GROUPS = [
        (
            "Basic Layout",
            [
                "TARGET_WORDS_MIN",
                "TARGET_WORDS_MAX",
                "MAX_WORDS_LINE_MIN",
                "MAX_WORDS_LINE_MAX",
                "PARAGRAPH_MODEL_ENABLED",
                "PARAGRAPH_MIN_LINES",
                "PARAGRAPH_MAX_LINES",
                "PARAGRAPH_SECONDARY_PROBABILITY",
                "PARAGRAPH_TERTIARY_PROBABILITY",
                "PARAGRAPH_START_INITIAL_GALLOWS_MIN_RATE",
            ],
        ),
        (
            "Vocabulary Growth",
            [
                "NOVEL_VARIANT_RATE",
                "NOVEL_VARIANT_MAX_ATTEMPTS",
                "NOVEL_VARIANT_PREFER_ED1",
                "NOVEL_VARIANT_ALLOW_SEED_FALLBACK",
                "UNIQUE_TOKEN_BONUS",
                "REUSED_TOKEN_PENALTY",
                "MAX_GLOBAL_TOKEN_REUSE_BEFORE_PENALTY",
            ],
        ),
        (
            "Source Weighting",
            [
                "F1R_SOURCE_WEIGHT",
                "GENERATED_SOURCE_WEIGHT",
                "RECENT_SOURCE_WEIGHT",
                "LOCAL_MEMORY_WINDOW",
                "FAMILY_BOOST_FACTOR",
                "EDGE_EDIT_BONUS",
            ],
        ),
        (
            "ED1 and Fallback Behavior",
            [
                "ATTESTED_ED1_FIRST",
                "ATTESTED_ED1_ATTEMPTS",
                "RANDOM_ED1_FALLBACK_PROBABILITY",
                "SEED_FALLBACK_PROBABILITY",
                "MAX_OPERATION_RETRIES",
                "MAX_FALLBACK_RETRIES",
            ],
        ),
        (
            "Repetition Limits",
            [
                "MAX_SOURCE_TOKEN_PAGE_COUNT",
                "MAX_FAMILY_PAGE_COUNT",
            ],
        ),
        (
            "Length and Suffix Drift",
            [
                "TARGET_SHORT_COPY_BACKGROUND",
                "SHORT_TOKEN_SOURCE_BOOST",
                "SHORT_TOKEN_LOCAL_BOOST",
                "LENGTH_45_PAGE_SOFT_SHARE",
                "LENGTH_45_PENALTY",
                "LONG_PARENT_BOOST_AFTER_SHORT_BULGE",
                "AIN_SUFFIX_PATTERN",
                "AIN_PAGE_SOFT_SHARE",
                "AIN_PAGE_HARD_SHARE",
                "AIN_SELECTION_PENALTY",
                "AIN_MUTATION_PENALTY",
                "CROSS_PAGE_AIN_PRESSURE",
            ],
        ),
        (
            "Visual Filters",
            [
                "GALLOWS",
                "VOWELS",
                "MAX_VOWEL_RUN",
                "MAX_CONSONANT_RUN",
                "MAX_TOKEN_LEN",
                "MIN_MUTATED_LEN",
            ],
        ),
        (
            "Legacy Page Zones",
            [
                "PAGE_PHASE_EARLY",
                "PAGE_PHASE_MIDDLE",
                "LINE_ZONE_LEFT",
                "LINE_ZONE_RIGHT",
            ],
        ),
    ]

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Voynich Plausible Generator")
        self.root.geometry("1280x820")
        self.knob_vars: Dict[str, tk.Variable] = {}
        self.path_vars = {
            "ledger": tk.StringVar(value="Ledger_scribe1.json"),
            "rules": tk.StringVar(value="standard_page_rules_v1.json"),
            "comparison": tk.StringVar(value="ttli.json"),
            "text_seed_file": tk.StringVar(value=""),
            "rebuilt_ledger_output": tk.StringVar(value=""),
        }
        self.seed_var = tk.IntVar(value=42)
        self.page_count_var = tk.IntVar(value=100)
        self.text_seed_words_var = tk.IntVar(value=150)
        self.text_seed_start_var = tk.IntVar(value=0)
        self.text_ledger_mode_var = tk.StringVar(value="voynich")
        self.text_seed_mode_var = tk.StringVar(value="sequential")
        self.is_generating = False
        self.last_records: List[Dict[str, Any]] = []
        self.last_output_tokens: List[str] = []
        self.last_voynich_tokens: List[str] = []
        self._build()

    def _build(self) -> None:
        outer = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(outer, padding=8)
        right = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        outer.add(left, weight=0)
        outer.add(right, weight=1)

        self._build_controls(left)

        output_frame = ttk.Frame(right, padding=8)
        ttk.Label(output_frame, text="Tagged Output").pack(anchor="w")
        output_text_frame = ttk.Frame(output_frame)
        output_text_frame.pack(fill=tk.BOTH, expand=True)
        output_scrollbar = ttk.Scrollbar(output_text_frame, orient=tk.VERTICAL)
        self.output_text = tk.Text(output_text_frame, wrap="word", height=18, font=("Consolas", 10), yscrollcommand=output_scrollbar.set)
        output_scrollbar.configure(command=self.output_text.yview)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        output_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        right.add(output_frame, weight=2)

        tabs = ttk.Notebook(right)
        report_tab = ttk.Frame(tabs, padding=8)
        zipf_tab = ttk.Frame(tabs, padding=8)
        length_tab = ttk.Frame(tabs, padding=8)
        tabs.add(report_tab, text="Report")
        tabs.add(zipf_tab, text="Zipf")
        tabs.add(length_tab, text="Length Distribution")
        report_text_frame = ttk.Frame(report_tab)
        report_text_frame.pack(fill=tk.BOTH, expand=True)
        report_scrollbar = ttk.Scrollbar(report_text_frame, orient=tk.VERTICAL)
        self.report_text = tk.Text(report_text_frame, wrap="word", font=("Consolas", 10), yscrollcommand=report_scrollbar.set)
        report_scrollbar.configure(command=self.report_text.yview)
        self.report_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        report_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.zipf_chart = ChartCanvas(zipf_tab)
        self.zipf_chart.pack(fill=tk.BOTH, expand=True)
        self.length_chart = ChartCanvas(length_tab)
        self.length_chart.pack(fill=tk.BOTH, expand=True)
        right.add(tabs, weight=1)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress = ttk.Progressbar(status_frame, mode="determinate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(8, 4), pady=2)
        self.progress.pack_forget()

    def _build_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Controls").pack(anchor="w")
        self.mode_tabs = ttk.Notebook(parent)
        self.mode_tabs.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        voynich_tab = ttk.Frame(self.mode_tabs, padding=4)
        text_tab = ttk.Frame(self.mode_tabs, padding=4)
        self.mode_tabs.add(voynich_tab, text="Voynich Settings")
        self.mode_tabs.add(text_tab, text="Text Import")
        self._build_voynich_tab(voynich_tab)
        self._build_text_import_tab(text_tab)

    def _build_voynich_tab(self, parent: ttk.Frame) -> None:
        path_frame = ttk.Frame(parent)
        path_frame.pack(fill=tk.X, pady=(0, 8))
        for label, key in (("Ledger", "ledger"), ("Rules", "rules"), ("Comparison JSON", "comparison")):
            row = ttk.Frame(path_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.path_vars[key], width=28).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="...", width=3, command=lambda k=key: self._choose_file(k, json_only=True)).pack(side=tk.LEFT, padx=(4, 0))

        page_row = ttk.Frame(parent)
        page_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(page_row, text="Pages", width=16).pack(side=tk.LEFT)
        ttk.Spinbox(page_row, from_=1, to=500, textvariable=self.page_count_var, width=12).pack(side=tk.LEFT)

        seed_row = ttk.Frame(parent)
        seed_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(seed_row, text="Seed", width=16).pack(side=tk.LEFT)
        ttk.Spinbox(seed_row, from_=0, to=999999999, textvariable=self.seed_var, width=12).pack(side=tk.LEFT)
        button_row = ttk.Frame(parent)
        button_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(button_row, text="Generate", command=self.generate).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(button_row, text="Restore Defaults", command=self.restore_defaults).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        canvas = tk.Canvas(parent, width=420, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        rendered = set()
        for title, names in self.GUI_KNOB_GROUPS:
            group_names = [
                name for name in names
                if name in GUI_KNOBS and name not in self.GUI_ONLY_HIDDEN_KNOBS
            ]
            if not group_names:
                continue
            self._add_knob_group(scroll_frame, title, group_names)
            rendered.update(group_names)

            if title == "Basic Layout":
                length_bias_names = [name for name in GUI_KNOBS if self._is_length_bias_knob(name)]
                self._add_length_bias_group(scroll_frame, length_bias_names)
                rendered.update(length_bias_names)

        remaining = [
            name for name in GUI_KNOBS
            if name not in rendered
            and name not in self.GUI_ONLY_HIDDEN_KNOBS
            and not self._is_length_bias_knob(name)
        ]
        if remaining:
            self._add_knob_group(scroll_frame, "Other Settings", remaining)

    def _build_text_import_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="Import a cleaned Project Gutenberg/plain-text file as the seed page. The default mode still uses the Voynich ledger and statistics.",
            wraplength=390,
            foreground="#444444",
        ).pack(anchor="w", pady=(0, 8))

        for label, key, json_only in (
            ("Text Seed File", "text_seed_file", False),
            ("Ledger", "ledger", True),
            ("Rules", "rules", True),
            ("Comparison JSON", "comparison", True),
            ("Save Rebuilt Ledger", "rebuilt_ledger_output", True),
        ):
            row = ttk.Frame(parent)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.path_vars[key], width=26).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="...", width=3, command=lambda k=key, j=json_only: self._choose_file(k, json_only=j, save=k == "rebuilt_ledger_output")).pack(side=tk.LEFT, padx=(4, 0))

        mode_row = ttk.Frame(parent)
        mode_row.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(mode_row, text="Text Ledger Mode", width=18).pack(side=tk.LEFT)
        ttk.Combobox(
            mode_row,
            textvariable=self.text_ledger_mode_var,
            values=("voynich", "rebuild-from-text"),
            state="readonly",
            width=20,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        seed_mode_row = ttk.Frame(parent)
        seed_mode_row.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(seed_mode_row, text="Seed Page Mode", width=18).pack(side=tk.LEFT)
        ttk.Combobox(
            seed_mode_row,
            textvariable=self.text_seed_mode_var,
            values=("sequential", "random"),
            state="readonly",
            width=20,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        words_row = ttk.Frame(parent)
        words_row.pack(fill=tk.X, pady=2)
        ttk.Label(words_row, text="Text Seed Words", width=18).pack(side=tk.LEFT)
        ttk.Spinbox(words_row, from_=1, to=10000, textvariable=self.text_seed_words_var, width=12).pack(side=tk.LEFT)

        start_row = ttk.Frame(parent)
        start_row.pack(fill=tk.X, pady=2)
        ttk.Label(start_row, text="Text Seed Start", width=18).pack(side=tk.LEFT)
        ttk.Spinbox(start_row, from_=0, to=10000000, textvariable=self.text_seed_start_var, width=12).pack(side=tk.LEFT)
        ttk.Label(parent, text="Start offset is used only for sequential seed pages. Random mode uses the Seed value.", wraplength=390, foreground="#666666").pack(anchor="w", pady=(0, 4))

        page_row = ttk.Frame(parent)
        page_row.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(page_row, text="Pages", width=18).pack(side=tk.LEFT)
        ttk.Spinbox(page_row, from_=1, to=500, textvariable=self.page_count_var, width=12).pack(side=tk.LEFT)

        seed_row = ttk.Frame(parent)
        seed_row.pack(fill=tk.X, pady=2)
        ttk.Label(seed_row, text="Seed", width=18).pack(side=tk.LEFT)
        ttk.Spinbox(seed_row, from_=0, to=999999999, textvariable=self.seed_var, width=12).pack(side=tk.LEFT)

        button_row = ttk.Frame(parent)
        button_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(button_row, text="Generate", command=self.generate).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(button_row, text="Restore Defaults", command=self.restore_defaults).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    def _add_knob_group(self, parent: ttk.Frame, title: str, names: Sequence[str]) -> None:
        group = ttk.LabelFrame(parent, text=title, padding=(8, 6, 8, 8))
        group.pack(fill=tk.X, pady=(8, 8))
        for name in names:
            self._add_knob(group, name, GUI_KNOBS[name])

    def _is_length_bias_knob(self, name: str) -> bool:
        return bool(re.match(r"^LENGTH_SELECTION_BIAS_(\d+|DEFAULT)$", name))

    def _human_label(self, name: str) -> str:
        special = {
            "F1R_SOURCE_WEIGHT": "F1r Source Weight",
            "ATTESTED_ED1_FIRST": "Use Attested ED1 First",
            "ATTESTED_ED1_ATTEMPTS": "Attested ED1 Attempts",
            "RANDOM_ED1_FALLBACK_PROBABILITY": "Random ED1 Fallback Chance",
            "AIN_SUFFIX_PATTERN": "-ain Suffix Pattern",
            "AIN_PAGE_SOFT_SHARE": "-ain Page Soft Share",
            "AIN_PAGE_HARD_SHARE": "-ain Page Hard Share",
            "AIN_SELECTION_PENALTY": "-ain Selection Penalty",
            "AIN_MUTATION_PENALTY": "-ain Mutation Penalty",
            "CROSS_PAGE_AIN_PRESSURE": "Cross-Page -ain Pressure",
        }
        if name in special:
            return special[name]
        words = []
        for part in name.split("_"):
            if part in {"ED1"}:
                words.append(part)
            elif part == "GUI":
                words.append("GUI")
            else:
                words.append(part.capitalize())
        return " ".join(words)

    def _add_length_bias_group(self, parent: ttk.Frame, names: Sequence[str]) -> None:
        group = ttk.LabelFrame(parent, text="Length Selection Bias", padding=(8, 6, 8, 8))
        group.pack(fill=tk.X, pady=(8, 8))
        ttk.Label(
            group,
            text="Selection multipliers by token length. Values above 1 favor that length; values below 1 suppress it. Default applies to unlisted lengths.",
            wraplength=380,
            foreground="#444444",
        ).pack(anchor="w", pady=(0, 6))
        for name in names:
            self._add_length_bias_control(group, name, GUI_KNOBS[name])

    def _add_length_bias_control(self, parent: ttk.Frame, name: str, spec: Dict[str, Any]) -> None:
        value = spec.get("value")
        min_value = spec.get("min", 0)
        max_value = spec.get("max", 1)
        step = spec.get("step", 1)
        label = "Default Length" if name.endswith("_DEFAULT") else f"Length {name.rsplit('_', 1)[-1]}"
        var = tk.DoubleVar(value=float(value))
        self.knob_vars[name] = var
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
        ttk.Scale(
            row,
            from_=min_value,
            to=max_value,
            variable=var,
            orient=tk.HORIZONTAL,
            command=lambda raw, v=var, mn=min_value, mx=max_value, st=step: self._snap_var(v, raw, mn, mx, st, False),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Entry(row, textvariable=var, width=7).pack(side=tk.LEFT, padx=(6, 0))

    def _add_knob(self, parent: ttk.Frame, name: str, spec: Dict[str, Any]) -> None:
        box = ttk.Frame(parent, padding=(0, 7, 0, 7))
        box.pack(fill=tk.X)
        value = spec.get("value")
        ttk.Label(box, text=self._human_label(name)).pack(anchor="w")
        ttk.Label(box, text=spec.get("description", ""), wraplength=390, foreground="#444444").pack(anchor="w", pady=(1, 3))
        if isinstance(value, bool):
            var = tk.BooleanVar(value=value)
            self.knob_vars[name] = var
            ttk.Checkbutton(box, text="Enabled", variable=var).pack(anchor="w")
            return
        if isinstance(value, str):
            var = tk.StringVar(value=value)
            self.knob_vars[name] = var
            ttk.Entry(box, textvariable=var).pack(fill=tk.X)
            return
        min_value = spec.get("min", 0)
        max_value = spec.get("max", 1)
        step = spec.get("step", 1)
        var: tk.Variable = tk.IntVar(value=int(value)) if isinstance(value, int) and not isinstance(value, bool) else tk.DoubleVar(value=float(value))
        self.knob_vars[name] = var
        control_row = ttk.Frame(box)
        control_row.pack(fill=tk.X)
        ttk.Scale(
            control_row,
            from_=min_value,
            to=max_value,
            variable=var,
            orient=tk.HORIZONTAL,
            command=lambda raw, v=var, mn=min_value, mx=max_value, st=step, whole=isinstance(value, int): self._snap_var(v, raw, mn, mx, st, whole),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Entry(control_row, textvariable=var, width=9).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(box, text=f"Range: {min_value} to {max_value}, step {step}.", wraplength=390, foreground="#666666").pack(anchor="w", pady=(2, 0))

    def _snap_numeric(self, raw: Any, min_value: float, max_value: float, step: float, whole: bool) -> float | int:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(min_value)
        value = max(float(min_value), min(float(max_value), value))
        step = float(step) if step else 0.0
        if step > 0:
            value = float(min_value) + round((value - float(min_value)) / step) * step
            value = max(float(min_value), min(float(max_value), value))
        if whole:
            return int(round(value))
        return round(value, 6)

    def _snap_var(self, var: tk.Variable, raw: Any, min_value: float, max_value: float, step: float, whole: bool) -> None:
        snapped = self._snap_numeric(raw, min_value, max_value, step, whole)
        if var.get() != snapped:
            var.set(snapped)

    def _choose_file(self, key: str, json_only: bool = False, save: bool = False) -> None:
        filetypes = [("JSON files", "*.json"), ("All files", "*.*")] if json_only else [("Text files", "*.txt"), ("All files", "*.*")]
        if save:
            path = filedialog.asksaveasfilename(title="Choose output file", filetypes=filetypes, defaultextension=".json")
        else:
            path = filedialog.askopenfilename(title="Choose file", filetypes=filetypes)
        if path:
            self.path_vars[key].set(path)

    def restore_defaults(self) -> None:
        for name, var in self.knob_vars.items():
            if name in self.GUI_ONLY_HIDDEN_KNOBS:
                continue
            var.set(GUI_KNOBS[name].get("value"))
        self.page_count_var.set(10)
        self.seed_var.set(1234)
        self.path_vars["ledger"].set("Ledger_scribe1.json")
        self.path_vars["rules"].set("standard_page_rules_v1.json")
        self.path_vars["comparison"].set("ttli.json")
        self.path_vars["text_seed_file"].set("")
        self.path_vars["rebuilt_ledger_output"].set("")
        self.text_seed_words_var.set(150)
        self.text_seed_start_var.set(0)
        self.text_ledger_mode_var.set("voynich")
        self.text_seed_mode_var.set("sequential")
        apply_gui_knob_values(self._knob_values())
        self.status_var.set("Defaults restored.")

    def _knob_values(self) -> Dict[str, Any]:
        values = {}
        for name, var in self.knob_vars.items():
            if name in self.GUI_ONLY_HIDDEN_KNOBS:
                continue
            spec = GUI_KNOBS[name]
            original = spec.get("value")
            raw = var.get()
            if isinstance(original, bool):
                values[name] = bool(raw)
            elif isinstance(original, int):
                value = self._snap_numeric(raw, spec.get("min", raw), spec.get("max", raw), spec.get("step", 1), True)
                var.set(value)
                values[name] = value
            elif isinstance(original, str):
                values[name] = str(raw)
            else:
                value = self._snap_numeric(raw, spec.get("min", raw), spec.get("max", raw), spec.get("step", 0), False)
                var.set(value)
                values[name] = value
        return values

    def _is_text_import_mode(self) -> bool:
        return bool(hasattr(self, "mode_tabs") and self.mode_tabs.index(self.mode_tabs.select()) == 1)

    def generate(self) -> None:
        if self.is_generating:
            return
        try:
            comparison_path = self.path_vars["comparison"].get()
            requested_pages = max(1, int(self.page_count_var.get()))
            voynich_tokens, voynich_pages, available_voynich_pages = load_comparison_json(comparison_path, requested_pages)
            if voynich_pages < requested_pages:
                proceed = messagebox.askyesno(
                    "Comparison page limit",
                    f"Comparison JSON has {available_voynich_pages} pages, but {requested_pages} pages were requested.\n\n"
                    f"Continue by generating {requested_pages} output pages and comparing against the {available_voynich_pages} available Voynich pages?"
                )
                if not proceed:
                    self.status_var.set("Generation cancelled.")
                    return
            if not voynich_tokens:
                raise ValueError(f"No tokens found in comparison JSON: {comparison_path}")
            knob_values = self._knob_values()
            apply_gui_knob_values(knob_values)
            params = {
                "comparison_path": comparison_path,
                "requested_pages": requested_pages,
                "available_voynich_pages": available_voynich_pages,
                "voynich_tokens": voynich_tokens,
                "knob_values": knob_values,
                "ledger_path": self.path_vars["ledger"].get(),
                "rules_path": self.path_vars["rules"].get(),
                "seed": int(self.seed_var.get()),
                "is_text_import": self._is_text_import_mode(),
                "text_seed_file": self.path_vars["text_seed_file"].get().strip(),
                "text_seed_words": int(self.text_seed_words_var.get()),
                "text_seed_start": int(self.text_seed_start_var.get()),
                "text_seed_mode": self.text_seed_mode_var.get(),
                "text_ledger_mode": self.text_ledger_mode_var.get(),
                "rebuilt_ledger_output": self.path_vars["rebuilt_ledger_output"].get().strip(),
            }
            self._start_progress(requested_pages, f"Generating 0 of {requested_pages} pages...")
            threading.Thread(target=self._generate_worker, args=(params,), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Generation failed", str(exc))
            self.status_var.set(f"Generation failed: {exc}")

    def _start_progress(self, total_pages: int, message: str) -> None:
        self.is_generating = True
        self.status_var.set(message)
        self.progress.configure(maximum=max(1, total_pages), value=0)
        self.progress.pack(side=tk.RIGHT, padx=(8, 4), pady=2)

    def _stop_progress(self) -> None:
        self.progress.pack_forget()
        self.is_generating = False

    def _update_progress(self, completed_pages: int, total_pages: int, folio: str) -> None:
        self.progress.configure(maximum=max(1, total_pages), value=completed_pages)
        self.status_var.set(f"Generated {completed_pages} of {total_pages} pages... latest: {folio}")

    def _generate_worker(self, params: Dict[str, Any]) -> None:
        try:
            pages_after_seed = max(0, params["requested_pages"] - 1)
            active_ledger = None
            seed_records = None
            active_ledger_source = "voynich"
            if params["is_text_import"]:
                if not params["text_seed_file"]:
                    raise ValueError("Text Import mode requires a text seed file.")
                text_tokens = text_corpus_tokens(params["text_seed_file"])
                if not text_tokens:
                    raise ValueError(f"No alphabetic tokens found in text seed file: {params['text_seed_file']}")
                if params["text_seed_mode"] == "random":
                    seed_records = random_text_seed_records(
                        text_tokens,
                        params["text_seed_words"],
                        random.Random(params["seed"]),
                    )
                else:
                    seed_records = text_seed_records(
                        text_tokens,
                        params["text_seed_words"],
                        params["text_seed_start"],
                    )
                if params["text_ledger_mode"] == "rebuild-from-text":
                    active_ledger_source = "rebuilt_from_text"
                    active_ledger = Ledger.from_token_corpus(text_tokens)
                    if params["rebuilt_ledger_output"]:
                        Path(params["rebuilt_ledger_output"]).parent.mkdir(parents=True, exist_ok=True)
                        with open(params["rebuilt_ledger_output"], "w", encoding="utf-8") as f:
                            json.dump(active_ledger.data, f, indent=2, ensure_ascii=False)
            gen = PlausibleGenerator(
                params["ledger_path"],
                params["rules_path"],
                params["seed"],
                ledger=active_ledger,
                seed_records=seed_records,
            )
            gen.progress_callback = lambda done, total, folio: self.root.after(0, lambda: self._update_progress(done, total, folio))
            records = gen.generate_pages(pages_after_seed)
            output_tokens = [meta["token"] for rec in records for meta in rec.get("generated_tokens", []) if meta.get("token")]
            result = {
                "records": records,
                "output_tokens": output_tokens,
                "voynich_tokens": params["voynich_tokens"],
                "requested_pages": params["requested_pages"],
                "available_voynich_pages": params["available_voynich_pages"],
                "active_ledger_source": active_ledger_source,
                "mode": "text import" if params["is_text_import"] else "Voynich",
            }
            self.root.after(0, lambda: self._finish_generate(result))
        except Exception as exc:
            self.root.after(0, lambda err=exc: self._fail_generate(err))

    def _finish_generate(self, result: Dict[str, Any]) -> None:
        self._stop_progress()
        records = result["records"]
        output_tokens = result["output_tokens"]
        voynich_tokens = result["voynich_tokens"]
        requested_pages = result["requested_pages"]
        available_voynich_pages = result["available_voynich_pages"]
        active_ledger_source = result["active_ledger_source"]
        mode = result["mode"]
        try:
            self.last_records = records
            self.last_output_tokens = output_tokens
            self.last_voynich_tokens = voynich_tokens
            self._update_output(records)
            self._update_report(records, output_tokens, voynich_tokens, requested_pages, available_voynich_pages)
            self.zipf_chart.set_data("zipf", output_tokens, voynich_tokens)
            self.length_chart.set_data("length", output_tokens, voynich_tokens)
            self.status_var.set(f"Generated and compared {requested_pages} pages. Mode: {mode}; ledger: {active_ledger_source}.")
        except Exception as exc:
            messagebox.showerror("Generation failed", str(exc))
            self.status_var.set(f"Generation failed: {exc}")

    def _fail_generate(self, exc: Exception) -> None:
        self._stop_progress()
        messagebox.showerror("Generation failed", str(exc))
        self.status_var.set(f"Generation failed: {exc}")

    def _update_output(self, records: List[Dict[str, Any]]) -> None:
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, records_to_text(records))

    def _update_report(
        self,
        records: List[Dict[str, Any]],
        output_tokens: Sequence[str],
        voynich_tokens: Sequence[str],
        compared_pages: int,
        available_voynich_pages: int,
    ) -> None:
        summary = summarize(records)
        output_freq = Counter(output_tokens)
        voynich_freq = Counter(voynich_tokens)
        output_bigrams = character_bigram_counts(output_tokens)
        voynich_bigrams = character_bigram_counts(voynich_tokens)
        output_trigrams = character_trigram_counts(output_tokens)
        voynich_trigrams = character_trigram_counts(voynich_tokens)
        output_vocab = len(set(output_tokens))
        voynich_vocab = len(set(voynich_tokens))
        output_hapax = sum(1 for count in output_freq.values() if count == 1)
        voynich_hapax = sum(1 for count in voynich_freq.values() if count == 1)
        generated_pages = len(set(rec.get("folio") for rec in records))
        lines = [
            f"Comparison JSON: {self.path_vars['comparison'].get()}",
            f"Page-count rule: output pages = Voynich pages = {compared_pages}",
            f"Voynich pages available: {available_voynich_pages}",
            f"Voynich pages compared: {compared_pages}",
            f"Generated pages: {generated_pages}",
            "",
            f"Output tokens: {len(output_tokens)}",
            f"Output vocabulary size: {output_vocab}",
            f"Output hapax count: {output_hapax}",
            f"Voynich comparison tokens: {len(voynich_tokens)}",
            f"Voynich vocabulary size: {voynich_vocab}",
            f"Voynich hapax count: {voynich_hapax}",
            f"Ledger invalid: {summary.get('ledger_invalid', 0)}",
            "",
            "Operation counts:",
        ]
        lines.extend(f"  {k[3:]}: {v}" for k, v in sorted(summary.items()) if k.startswith("op:"))
        lines.append("")
        lines.append("Gallows action counts:")
        lines.extend(f"  {k[8:]}: {v}" for k, v in sorted(summary.items()) if k.startswith("gallows:"))
        lines.append("")
        lines.append(f"Top 20 output bigrams ({compared_pages} page{'s' if compared_pages != 1 else ''}):")
        lines.extend(f"  {bigram}: {count}" for bigram, count in output_bigrams.most_common(20))
        lines.append("")
        lines.append(f"Top 20 Voynich bigrams ({compared_pages} page{'s' if compared_pages != 1 else ''}):")
        lines.extend(f"  {bigram}: {count}" for bigram, count in voynich_bigrams.most_common(20))
        lines.append("")
        lines.append(f"Top 20 output trigrams ({compared_pages} page{'s' if compared_pages != 1 else ''}):")
        lines.extend(f"  {trigram}: {count}" for trigram, count in output_trigrams.most_common(20))
        lines.append("")
        lines.append(f"Top 20 Voynich trigrams ({compared_pages} page{'s' if compared_pages != 1 else ''}):")
        lines.extend(f"  {trigram}: {count}" for trigram, count in voynich_trigrams.most_common(20))
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, "\n".join(lines))

    def run(self) -> None:
        self.root.mainloop()


def launch_gui() -> None:
    GeneratorGUI().run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate plausible Scribe-1-herbal-like Voynich pages.")
    parser.add_argument("--gui", action="store_true", help="Launch the graphical tuning and comparison interface.")
    parser.add_argument("--ledger", default="Ledger_scribe1.json", help="Ledger JSON file.")
    parser.add_argument("--rules", default="standard_page_rules_v1.json", help="Compact page-rule JSON derived from heat-map tables.")
    parser.add_argument("--pages", type=int, default=5, help="Pages after f1r seed to generate.")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--seed-mode", choices=["embedded", "text-sequential", "text-random"], default="embedded", help="Use embedded f1r seed, sequential cleaned text, or random cleaned text words.")
    parser.add_argument("--text-seed-file", default=None, help="Plain text/Gutenberg file used when a text seed or rebuilt text ledger mode is active.")
    parser.add_argument("--text-seed-words", type=int, default=150, help="Number of cleaned text words to use as seed material.")
    parser.add_argument("--text-seed-start", type=int, default=0, help="Starting word offset for text seed material.")
    parser.add_argument(
        "--text-ledger-mode",
        choices=["voynich", "rebuild-from-text"],
        default="voynich",
        help="Use the Voynich ledger, or rebuild adjacency ledger from cleaned text seed corpus."
    )
    parser.add_argument(
        "--rebuilt-ledger-output",
        default=None,
        help="Optional path to save rebuilt text-derived ledger JSON."
    )
    parser.add_argument("--json-output", default="plausible_generator_output.json", help="Path for generated JSON output.")
    parser.add_argument("--text-output", default="plausible_generator_output.txt", help="Path for generated plain-text output.")
    parser.add_argument("--knobs-output", default=None, help="Optional path to write GUI knob metadata JSON and exit.")
    args = parser.parse_args()

    if args.gui or len(sys.argv) == 1:
        launch_gui()
        return

    if args.knobs_output:
        Path(args.knobs_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.knobs_output, "w", encoding="utf-8") as f:
            json.dump(GUI_KNOBS, f, indent=2, ensure_ascii=False)
        print("GUI knobs:", args.knobs_output)
        return

    text_tokens: List[str] = []
    seed_records = None
    if args.seed_mode in {"text-sequential", "text-random"} or args.text_ledger_mode == "rebuild-from-text":
        if not args.text_seed_file:
            raise SystemExit("--text-seed-file is required for text seed or rebuilt text ledger mode.")
        text_tokens = text_corpus_tokens(args.text_seed_file)
        if not text_tokens:
            raise SystemExit(f"No alphabetic tokens found in text seed file: {args.text_seed_file}")

    if args.seed_mode == "text-sequential":
        seed_records = text_seed_records(text_tokens, args.text_seed_words, args.text_seed_start)
    elif args.seed_mode == "text-random":
        seed_records = random_text_seed_records(text_tokens, args.text_seed_words, random.Random(args.seed))

    active_ledger_source = "voynich"
    active_ledger = None
    if args.text_ledger_mode == "rebuild-from-text":
        active_ledger_source = "rebuilt_from_text"
        active_ledger = Ledger.from_token_corpus(text_tokens)
        if args.rebuilt_ledger_output:
            Path(args.rebuilt_ledger_output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.rebuilt_ledger_output, "w", encoding="utf-8") as f:
                json.dump(active_ledger.data, f, indent=2, ensure_ascii=False)

    gen = PlausibleGenerator(args.ledger, args.rules, args.seed, ledger=active_ledger, seed_records=seed_records)
    records = gen.generate_pages(args.pages)
    summary = summarize(records)
    payload = {
        "generator": "Voynich plausible production generator v10 prototype",
        "inputs": {
            "ledger": args.ledger,
            "rules": args.rules,
            "gallows": "built_in_default_weights",
        },
        "seed": args.seed,
        "pages_after_seed": args.pages,
        "seed_mode": args.seed_mode,
        "text_ledger_mode": args.text_ledger_mode,
        "text_seed_file": args.text_seed_file,
        "text_seed_words": args.text_seed_words,
        "text_seed_start": args.text_seed_start,
        "active_ledger_source": active_ledger_source,
        "gui_knobs": gui_knob_snapshot(),
        "summary": dict(summary),
        "records": records,
    }
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.text_output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    write_text(records, args.text_output)

    print("Generated records:", len(records))
    print("Total tokens:", summary.get("tokens", 0))
    print("Ledger invalid:", summary.get("ledger_invalid", 0))
    print("Operation counts:")
    for k, v in sorted(summary.items()):
        if k.startswith("op:"):
            print(f"  {k[3:]}: {v}")
    print("Gallows action counts:")
    for k, v in sorted(summary.items()):
        if k.startswith("gallows:"):
            print(f"  {k[8:]}: {v}")
    print("JSON:", args.json_output)
    print("TEXT:", args.text_output)


if __name__ == "__main__":
    main()
