#!/usr/bin/env python3
"""
Standalone Tkinter GUI for building Voynich generator ledger JSON files.

Input format is selected explicitly:
  - JSON transcription exported by Voynich_Transcription_Export_v2.py
  - Plain/Gutenberg text file with Project Gutenberg header/footer stripped

The CLI's former "auto" input-format option is intentionally not exposed.
"""
from __future__ import annotations

import json
import queue
import random
import re
import threading
import tkinter as tk
from collections import Counter, defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_SEED_COUNT = 150
DEFAULT_PAGE_LENGTH = 80
DEFAULT_PAGE_COUNT = 100
DEFAULT_RANDOM_GENERATOR_SEED = 42
DEFAULT_SEED_FOLIO = "f1r"
DEFAULT_CODEX_FILE = "Quires_Scribes.json"

POSITIONS = ("prefix", "midfix", "suffix")
TIERS = ("80", "18", "2")
TIER_WEIGHTS = {"80": 0.80, "18": 0.18, "2": 0.02}

GUTENBERG_MARKER_RE = re.compile(
    r"\*\*\*\s*(START|END) OF (?:THE )?PROJECT GUTENBERG EBOOK.*?\*\*\*",
    flags=re.IGNORECASE | re.DOTALL,
)


def normalize_folio(value: Any) -> str:
    folio = str(value).strip().lower()
    if not folio:
        return folio
    return folio if folio.startswith("f") else f"f{folio}"


def folio_sort_key(folio: Any) -> Tuple[int, int, str]:
    match = re.match(r"^f?(\d+)([rv])?$", str(folio).lower())
    if not match:
        return (10**9, 9, str(folio))
    number, side = match.groups()
    return (int(number), {"r": 0, "v": 1}.get(side, 2), str(folio))


def line_sort_key(value: Any) -> Tuple[int, str]:
    match = re.match(r"^(\d+)(.*)$", str(value))
    if not match:
        return (0, str(value))
    return (int(match.group(1)), match.group(2))


def clean_alpha_token(token: Any) -> Optional[str]:
    token = str(token).strip().lower()
    if re.fullmatch(r"[a-z]+", token):
        return token
    return None


def parse_scribe_values(value: Any) -> List[int]:
    if value is None:
        return []
    value = re.sub(r"\bon\s+\d+[rv]\b", "", str(value), flags=re.IGNORECASE)
    return [int(x) for x in re.findall(r"\d+", value)]


def load_codicology(path: Optional[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if not path:
        return {}, {}

    candidate = Path(path)
    if not candidate.is_absolute():
        possible = [
            Path.cwd() / candidate,
            Path(__file__).resolve().parent / candidate,
            Path("/mnt/data") / candidate,
        ]
        candidate = next((p for p in possible if p.exists()), candidate)

    if not candidate.exists():
        return {}, {"codicology_file": str(path), "loaded": False}

    with candidate.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)

    if not isinstance(rows, list):
        raise ValueError("Codicology JSON must be a list of folio rows.")

    by_folio: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        folio_num = str(row.get("Folio", "")).strip()
        if not folio_num:
            continue

        raw_scribe = str(row.get("Scribe", "")).strip()
        values = parse_scribe_values(raw_scribe)
        base_scribe = values[0] if values else None

        for side in ("r", "v"):
            folio = normalize_folio(f"{folio_num}{side}")
            by_folio[folio] = {
                "quire": row.get("Quire"),
                "sheet": row.get("Sheet"),
                "currier_language": row.get("Currier_Language"),
                "scribe_raw": raw_scribe,
                "scribes": [base_scribe] if base_scribe is not None else [],
            }

        for override_scribe, override_folio in re.findall(r"(\d+)\s+on\s+(\d+[rv])", raw_scribe):
            folio = normalize_folio(override_folio)
            if folio in by_folio:
                by_folio[folio]["scribes"] = [int(override_scribe)]

    return by_folio, {
        "codicology_file": str(candidate),
        "loaded": True,
        "rows": len(rows),
        "folio_sides": len(by_folio),
    }


def strip_gutenberg_boilerplate(text: str) -> str:
    matches = list(GUTENBERG_MARKER_RE.finditer(text))
    start_end = None
    end_start = None

    for match in matches:
        marker = match.group(1).upper()
        if marker == "START" and start_end is None:
            start_end = match.end()
        elif marker == "END" and start_end is not None:
            end_start = match.start()
            break

    if start_end is not None and end_start is not None and start_end < end_start:
        return text[start_end:end_start]
    if start_end is not None:
        return text[start_end:]
    return text


def normalize_text_tokens(text: str) -> List[str]:
    return re.findall(r"[a-z]+", text.lower())


def load_text_pages(path: str, page_count: int, page_length: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    body = strip_gutenberg_boilerplate(raw)
    all_tokens = normalize_text_tokens(body)

    total_needed = max(0, page_count * page_length)
    used_tokens = all_tokens[:total_needed] if total_needed else list(all_tokens)

    records: List[Dict[str, Any]] = []
    folio_number = 1
    side = "r"

    for page_index in range(page_count):
        start = page_index * page_length
        page_tokens = used_tokens[start:start + page_length]
        if not page_tokens:
            break

        folio = f"f{folio_number}{side}"
        records.append({
            "folio": folio,
            "line": "1",
            "tokens": page_tokens,
            "text": " ".join(page_tokens),
            "scribes": [],
            "source_record": page_index,
        })

        if side == "r":
            side = "v"
        else:
            side = "r"
            folio_number += 1

    return records, {
        "source_type": "text",
        "normalization": "Project Gutenberg header/footer stripped when markers exist; lowercase a-z tokens only",
        "page_count_requested": page_count,
        "page_length": page_length,
        "raw_token_count_after_normalization": len(all_tokens),
        "used_token_count": len(used_tokens),
    }, used_tokens


def load_json_pages(path: str, codicology_by_folio: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    rows = data.get("records") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("JSON input must have top-level key 'records' containing a list.")

    records: List[Dict[str, Any]] = []
    all_tokens: List[str] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        folio = normalize_folio(row.get("folio", ""))
        raw_tokens = row.get("tokens")
        if isinstance(raw_tokens, list):
            tokens = [clean_alpha_token(tok) for tok in raw_tokens]
        else:
            tokens = [clean_alpha_token(tok) for tok in str(row.get("text", "")).split()]
        tokens = [tok for tok in tokens if tok]

        rec = {
            "folio": folio,
            "line": str(row.get("line", "")),
            "tokens": tokens,
            "text": " ".join(tokens),
            "scribes": list(row.get("scribes", [])) if isinstance(row.get("scribes", []), list) else [],
            "source_record": index,
        }

        if codicology_by_folio:
            meta = codicology_by_folio.get(folio, {})
            rec["scribes"] = list(meta.get("scribes", rec["scribes"]))
            rec["quire"] = meta.get("quire")
            rec["sheet"] = meta.get("sheet")
            rec["currier_language"] = meta.get("currier_language")

        records.append(rec)
        all_tokens.extend(tokens)

    return records, {
        "source_type": "json_records",
        "records_in_source": len(rows),
        "normalization": "lowercase a-z tokens only; ambiguous/punctuated tokens excluded",
    }, all_tokens


def filter_records(records: Sequence[Dict[str, Any]], folio_range: Optional[Sequence[str]] = None, scribe: Optional[int] = None) -> List[Dict[str, Any]]:
    selected = list(records)

    if folio_range:
        start, end = [normalize_folio(x) for x in folio_range]
        start_key, end_key = folio_sort_key(start), folio_sort_key(end)
        if start_key > end_key:
            start_key, end_key = end_key, start_key
        selected = [rec for rec in selected if start_key <= folio_sort_key(rec.get("folio", "")) <= end_key]

    if scribe is not None:
        selected = [rec for rec in selected if int(scribe) in set(rec.get("scribes", []))]

    return selected


def flatten_tokens(records: Sequence[Dict[str, Any]]) -> List[str]:
    return [token for rec in records for token in rec.get("tokens", [])]


def normalized_weights(counter: Counter) -> Dict[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return {}
    return {str(key): round(value / total, 8) for key, value in sorted(counter.items())}


def collect_statistics(tokens: Sequence[str]) -> Dict[str, Counter]:
    alphabet = Counter()
    token_counts = Counter()
    short_tokens = Counter()
    first_letters = Counter()
    word_lengths = Counter()
    followers: Dict[str, Dict[str, Counter]] = defaultdict(lambda: {pos: Counter() for pos in POSITIONS})

    for token in tokens:
        if not token:
            continue
        token_counts[token] += 1
        alphabet.update(token)
        first_letters[token[0]] += 1
        word_lengths[str(len(token))] += 1

        if len(token) <= 2:
            short_tokens[token] += 1

        if len(token) >= 2:
            chars = list(token)
            followers[chars[0]]["prefix"][chars[1]] += 1
            for idx in range(1, len(chars) - 2):
                followers[chars[idx]]["midfix"][chars[idx + 1]] += 1
            followers[chars[-2]]["suffix"][chars[-1]] += 1

    for glyph in alphabet:
        _ = followers[glyph]

    return {
        "alphabet": alphabet,
        "token_counts": token_counts,
        "short_tokens": short_tokens,
        "first_letters": first_letters,
        "word_lengths": word_lengths,
        "followers": followers,  # type: ignore[dict-item]
    }


def split_follower_bins(counter: Counter) -> Dict[str, List[str]]:
    bins = {tier: [] for tier in TIERS}
    if not counter:
        return bins

    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(count for _glyph, count in items)
    running = 0

    for glyph, count in items:
        before = running / total if total else 0
        running += count
        if before < 0.80:
            bins["80"].append(glyph)
        elif before < 0.98:
            bins["18"].append(glyph)
        else:
            bins["2"].append(glyph)

    return bins


def build_ledger(followers: Dict[str, Dict[str, Counter]]) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    ledger: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    for glyph in sorted(followers):
        ledger[glyph] = {}
        for position in POSITIONS:
            ledger[glyph][position] = split_follower_bins(followers[glyph][position])
    return ledger


def ledger_all_followers(ledger: Dict[str, Any], left: str, position: str) -> List[str]:
    row = ledger.get(left, {}).get(position, {})
    values: List[str] = []
    for tier in TIERS:
        values.extend(row.get(tier, []))
    return sorted(set(values))


def validate_token(token: str, ledger: Dict[str, Any], short_tokens: Dict[str, int]) -> bool:
    if not token:
        return False
    if len(token) == 1:
        return token in short_tokens or token[0] in ledger
    if token[0] not in ledger:
        return False
    if token[1] not in ledger_all_followers(ledger, token[0], "prefix"):
        return False
    for idx in range(2, len(token) - 1):
        if token[idx] not in ledger_all_followers(ledger, token[idx - 1], "midfix"):
            return False
    return token[-1] in ledger_all_followers(ledger, token[-2], "suffix")


def weighted_choice(rng: random.Random, values: Sequence[str], weights: Optional[Sequence[float]] = None) -> Optional[str]:
    if not values:
        return None
    if weights is None:
        return rng.choice(list(values))
    return rng.choices(list(values), weights=list(weights), k=1)[0]


def choose_from_weight_map(rng: random.Random, weights: Dict[str, float]) -> Optional[str]:
    if not weights:
        return None
    values = list(weights.keys())
    w = list(weights.values())
    return weighted_choice(rng, values, w)


def choose_ledger_follower(rng: random.Random, ledger: Dict[str, Any], left: str, position: str) -> Optional[str]:
    row = ledger.get(left, {}).get(position, {})
    tier_values = [tier for tier in TIERS if row.get(tier)]
    if not tier_values:
        return None
    tier_weights = [TIER_WEIGHTS[tier] for tier in tier_values]
    tier = weighted_choice(rng, tier_values, tier_weights)
    if tier is None:
        return None
    return rng.choice(row[tier])


def generate_one_ledger_word(
    rng: random.Random,
    ledger: Dict[str, Any],
    first_weights: Dict[str, float],
    length_weights: Dict[str, float],
    short_tokens: Dict[str, int],
) -> Optional[str]:
    length_raw = choose_from_weight_map(rng, length_weights)
    if length_raw is None:
        return None
    length = int(length_raw)
    if length <= 0:
        return None

    first = choose_from_weight_map(rng, first_weights)
    if first is None:
        return None

    if length == 1:
        return first if validate_token(first, ledger, short_tokens) else None

    token = first
    for pos in range(1, length):
        position = "prefix" if pos == 1 else ("suffix" if pos == length - 1 else "midfix")
        nxt = choose_ledger_follower(rng, ledger, token[-1], position)
        if nxt is None:
            return None
        token += nxt

    return token if validate_token(token, ledger, short_tokens) else None


def generate_random_seed_words(
    ledger: Dict[str, Any],
    first_weights: Dict[str, float],
    length_weights: Dict[str, float],
    short_tokens: Dict[str, int],
    seed_count: int,
    rng_seed: int,
    max_attempts_per_word: int = 200,
) -> List[str]:
    rng = random.Random(rng_seed)
    words: List[str] = []
    attempts = 0
    max_attempts = max(seed_count * max_attempts_per_word, max_attempts_per_word)

    while len(words) < seed_count and attempts < max_attempts:
        attempts += 1
        token = generate_one_ledger_word(rng, ledger, first_weights, length_weights, short_tokens)
        if token:
            words.append(token)

    if len(words) < seed_count:
        raise RuntimeError(
            f"Only generated {len(words)} random seed words out of requested {seed_count}. "
            "Ledger may be too sparse for the requested length distribution."
        )

    return words


def collect_folio_seed(records: Sequence[Dict[str, Any]], seed_folio: str) -> Dict[str, Any]:
    target = normalize_folio(seed_folio)
    selected = sorted([r for r in records if r.get("folio") == target], key=lambda r: line_sort_key(r.get("line", "")))
    lines = [{"line": str(rec.get("line", "")), "text": rec.get("text", "")} for rec in selected]
    words = [tok for rec in selected for tok in rec.get("tokens", [])]
    return {"mode": "folio", "folio": target, "records": lines, "words": words, "count": len(words)}


def sample_text_seed_words(tokens: Sequence[str], seed_count: int, unique: bool, rng_seed: int) -> List[str]:
    if seed_count <= 0:
        return []
    if not tokens:
        raise ValueError("Cannot sample seed words from an empty text token stream.")

    rng = random.Random(rng_seed)
    if unique:
        vocab = sorted(set(tokens))
        if seed_count > len(vocab):
            raise ValueError(f"Requested {seed_count} unique seed words, but only {len(vocab)} unique words are available.")
        return rng.sample(vocab, seed_count)
    return [rng.choice(list(tokens)) for _ in range(seed_count)]


def build_seed_block(
    source_type: str,
    records: Sequence[Dict[str, Any]],
    source_tokens: Sequence[str],
    ledger: Dict[str, Any],
    first_weights: Dict[str, float],
    length_weights: Dict[str, float],
    short_tokens: Dict[str, int],
    seed_folio: str,
    seed_count: int,
    unique_seeds: bool,
    random_seed_enabled: bool,
    rng_seed: int,
) -> Dict[str, Any]:
    if random_seed_enabled:
        words = generate_random_seed_words(
            ledger=ledger,
            first_weights=first_weights,
            length_weights=length_weights,
            short_tokens=short_tokens,
            seed_count=seed_count,
            rng_seed=rng_seed,
        )
        return {"mode": "random_generated", "count": len(words), "rng_seed": rng_seed, "words": words}

    if source_type == "json":
        return collect_folio_seed(records, seed_folio)

    if source_type == "text":
        words = sample_text_seed_words(source_tokens, seed_count, unique_seeds, rng_seed)
        return {
            "mode": "sampled_text",
            "count": len(words),
            "unique": bool(unique_seeds),
            "rng_seed": rng_seed,
            "words": words,
        }

    raise ValueError(f"Unsupported source type for seed construction: {source_type}")


def context_summary(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    scribes = Counter(s for rec in records for s in rec.get("scribes", []))
    folios = sorted({rec.get("folio") for rec in records if rec.get("folio")}, key=folio_sort_key)
    return {
        "folios_used_count": len(folios),
        "folios_used": folios,
        "scribes_seen": {str(k): v for k, v in sorted(scribes.items())},
    }


def build_ledger_output(
    input_path: str,
    input_format: str,
    records: Sequence[Dict[str, Any]],
    source_meta: Dict[str, Any],
    codicology_meta: Dict[str, Any],
    scribe: Optional[int],
    folio_range: Optional[Sequence[str]],
    seed_block: Dict[str, Any],
    stats: Dict[str, Counter],
    ledger: Dict[str, Any],
) -> Dict[str, Any]:
    alphabet_counts = stats["alphabet"]
    token_counts = stats["token_counts"]
    short_tokens = stats["short_tokens"]
    first_letters = stats["first_letters"]
    word_lengths = stats["word_lengths"]

    metadata = {
        "description": "Adjacency legality ledger generated from selected source corpus.",
        "source_file": str(input_path),
        "input_format": input_format,
        "source_meta": source_meta,
        "codicology_meta": codicology_meta,
        "scribe_filter": scribe,
        "folio_range": [normalize_folio(v) for v in folio_range] if folio_range else None,
        "filter_rule": "scribe/folio filters apply only while building this ledger; generator should not depend on source codicology",
        "token_count": sum(token_counts.values()),
        "unique_token_count": len(token_counts),
        "records_used": len(records),
        "context_summary": context_summary(records),
        "tiers": list(TIERS),
        "columns": list(POSITIONS),
        "binning": "cumulative follower frequency mass into 80/18/2 bins",
        "short_tokens": dict(sorted(short_tokens.items())),
        "first_tokens": dict(sorted(first_letters.items())),
        "first_token_weights": normalized_weights(first_letters),
        "word_length_histogram": dict(sorted(word_lengths.items(), key=lambda kv: int(kv[0]))),
        "word_length_weights": normalized_weights(word_lengths),
    }

    return {
        "metadata": metadata,
        "seed": seed_block,
        "alphabet": sorted(alphabet_counts),
        "ledger": ledger,
    }


class LedgerGeneratorGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Voynich Ledger Generator")
        self.geometry("900x620")
        self.minsize(780, 560)

        self.work_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.is_running = False

        base_dir = Path.cwd()
        codicology_default = base_dir / "Quires_Scribes.json"
        if not codicology_default.exists():
            codicology_default = base_dir / "quires_scribes_davis.json"

        self.input_format_var = tk.StringVar(value="json")
        self.input_var = tk.StringVar(value=str(base_dir / "TTLI.json"))
        self.output_var = tk.StringVar(value=str(base_dir / "Ledger_output.json"))
        self.codicology_var = tk.StringVar(value=str(codicology_default))
        self.scribe_var = tk.StringVar(value="All scribes")
        self.folio_start_var = tk.StringVar(value="")
        self.folio_end_var = tk.StringVar(value="")
        self.page_length_var = tk.StringVar(value=str(DEFAULT_PAGE_LENGTH))
        self.page_count_var = tk.StringVar(value=str(DEFAULT_PAGE_COUNT))
        self.seed_folio_var = tk.StringVar(value=DEFAULT_SEED_FOLIO)
        self.seed_count_var = tk.StringVar(value=str(DEFAULT_SEED_COUNT))
        self.unique_seeds_var = tk.BooleanVar(value=False)
        self.random_seed_var = tk.BooleanVar(value=False)
        self.rng_seed_var = tk.StringVar(value=str(DEFAULT_RANDOM_GENERATOR_SEED))
        self.status_var = tk.StringVar(value="Ready")

        self.json_only_widgets: list[tk.Widget] = []
        self.text_only_widgets: list[tk.Widget] = []
        self.seed_folio_widgets: list[tk.Widget] = []
        self.unique_seed_widgets: list[tk.Widget] = []
        self.scribe_combo: ttk.Combobox
        self.folio_start_combo: ttk.Combobox
        self.folio_end_combo: ttk.Combobox
        self.seed_folio_combo: ttk.Combobox

        self._build_widgets()
        self.update_scribe_dropdown(show_errors=False)
        self.update_folio_dropdowns(show_errors=False)
        self._apply_mode_state()

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        source = ttk.LabelFrame(outer, text="Source", padding=12)
        source.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        source.columnconfigure(1, weight=1)

        json_radio = ttk.Radiobutton(
            source,
            text="Voynich transcription JSON",
            variable=self.input_format_var,
            value="json",
            command=self._on_input_format_changed,
        )
        json_radio.grid(row=0, column=0, sticky="w", pady=(0, 8))

        text_radio = ttk.Radiobutton(
            source,
            text="Plain/Gutenberg text",
            variable=self.input_format_var,
            value="text",
            command=self._on_input_format_changed,
        )
        text_radio.grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8))

        ttk.Label(source, text="Input file").grid(row=1, column=0, sticky="w", pady=(0, 8))
        input_entry = ttk.Entry(source, textvariable=self.input_var)
        input_entry.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=(0, 8))
        input_entry.bind("<FocusOut>", lambda _event: self.update_folio_dropdowns(show_errors=True))
        ttk.Button(source, text="Browse...", command=self.choose_input).grid(row=1, column=2, sticky="ew", pady=(0, 8))

        ttk.Label(source, text="Output JSON").grid(row=2, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(source, text="Save As...", command=self.choose_output).grid(row=2, column=2, sticky="ew")

        json_frame = ttk.LabelFrame(outer, text="JSON Transcription Settings", padding=12)
        json_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        json_frame.columnconfigure(1, weight=1)
        json_frame.columnconfigure(3, weight=1)

        self.json_only_widgets.append(json_frame)
        self._json_label(json_frame, "Codicology JSON", 0, 0)
        codicology_entry = self._json_entry(json_frame, self.codicology_var, 0, 1, columnspan=3)
        codicology_entry.bind("<FocusOut>", lambda _event: self.update_scribe_dropdown(show_errors=True))
        codicology_button = ttk.Button(json_frame, text="Browse...", command=self.choose_codicology)
        codicology_button.grid(row=0, column=4, padx=(8, 0), pady=(0, 8))
        self.json_only_widgets.append(codicology_button)

        self._json_label(json_frame, "Scribe", 1, 0)
        self.scribe_combo = ttk.Combobox(json_frame, textvariable=self.scribe_var, width=12, state="readonly")
        self.scribe_combo.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=(0, 8))
        self.json_only_widgets.append(self.scribe_combo)

        self._json_label(json_frame, "Folio start", 1, 2)
        self.folio_start_combo = ttk.Combobox(json_frame, textvariable=self.folio_start_var, width=16, state="readonly", height=24)
        self.folio_start_combo.grid(row=1, column=3, sticky="ew", padx=(12, 8), pady=(0, 8))
        self.json_only_widgets.append(self.folio_start_combo)

        self._json_label(json_frame, "Folio end", 1, 4)
        self.folio_end_combo = ttk.Combobox(json_frame, textvariable=self.folio_end_var, width=16, state="readonly", height=24)
        self.folio_end_combo.grid(row=1, column=5, sticky="ew", padx=(12, 8), pady=(0, 8))
        self.json_only_widgets.append(self.folio_end_combo)

        text_frame = ttk.LabelFrame(outer, text="Text Settings", padding=12)
        text_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        text_frame.columnconfigure(1, weight=1)
        text_frame.columnconfigure(3, weight=1)
        self.text_only_widgets.append(text_frame)

        self._text_label(text_frame, "Words per page", 0, 0)
        self._text_entry(text_frame, self.page_length_var, 0, 1)
        self._text_label(text_frame, "Pages to read", 0, 2)
        self._text_entry(text_frame, self.page_count_var, 0, 3)

        seed = ttk.LabelFrame(outer, text="Seed Settings", padding=12)
        seed.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        seed.columnconfigure(1, weight=1)
        seed.columnconfigure(3, weight=1)

        seed_folio_label = ttk.Label(seed, text="Seed folio")
        seed_folio_label.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.seed_folio_combo = ttk.Combobox(seed, textvariable=self.seed_folio_var, width=14, state="readonly", height=24)
        self.seed_folio_combo.grid(row=0, column=1, sticky="w", padx=(12, 24), pady=(0, 8))
        self.seed_folio_widgets.extend([seed_folio_label, self.seed_folio_combo])

        ttk.Label(seed, text="Seed count").grid(row=0, column=2, sticky="w", pady=(0, 8))
        ttk.Entry(seed, textvariable=self.seed_count_var, width=14).grid(row=0, column=3, sticky="w", padx=(12, 24), pady=(0, 8))

        unique_check = ttk.Checkbutton(seed, text="Unique text seeds (1 occurence per word)", variable=self.unique_seeds_var)
        unique_check.grid(row=1, column=0, columnspan=2, sticky="w")
        self.unique_seed_widgets.append(unique_check)

        ttk.Checkbutton(
            seed,
            text="Generate random seed words from ledger",
            variable=self.random_seed_var,
            command=self._apply_mode_state,
        ).grid(row=1, column=2, columnspan=2, sticky="w")

        ttk.Label(seed, text="RNG seed").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(seed, textvariable=self.rng_seed_var, width=14).grid(row=2, column=1, sticky="w", padx=(12, 24), pady=(8, 0))

        bottom = ttk.Frame(outer)
        bottom.grid(row=4, column=0, sticky="nsew")
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        controls = ttk.Frame(bottom)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        self.run_button = ttk.Button(controls, text="Generate Ledger", command=self.start_build)
        self.run_button.grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.status_var).grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.log = tk.Text(bottom, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.log.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(bottom, orient=tk.VERTICAL, command=self.log.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    def _json_label(self, parent: tk.Widget, text: str, row: int, column: int) -> None:
        widget = ttk.Label(parent, text=text)
        widget.grid(row=row, column=column, sticky="w", pady=(0, 8))
        self.json_only_widgets.append(widget)

    def _json_entry(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        row: int,
        column: int,
        columnspan: int = 1,
    ) -> ttk.Entry:
        widget = ttk.Entry(parent, textvariable=variable)
        widget.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=(12, 8), pady=(0, 8))
        self.json_only_widgets.append(widget)
        return widget

    def _text_label(self, parent: tk.Widget, text: str, row: int, column: int) -> None:
        widget = ttk.Label(parent, text=text)
        widget.grid(row=row, column=column, sticky="w")
        self.text_only_widgets.append(widget)

    def _text_entry(self, parent: tk.Widget, variable: tk.StringVar, row: int, column: int) -> None:
        widget = ttk.Entry(parent, textvariable=variable, width=14)
        widget.grid(row=row, column=column, sticky="w", padx=(12, 24))
        self.text_only_widgets.append(widget)

    def _on_input_format_changed(self) -> None:
        base_dir = Path.cwd()
        if self.input_format_var.get() == "json":
            if not self.input_var.get().lower().endswith(".json"):
                self.input_var.set(str(base_dir / "TTLI.json"))
            self.update_folio_dropdowns(show_errors=True)
        else:
            if not self.input_var.get().lower().endswith(".txt"):
                self.input_var.set(str(base_dir / "pg18837.txt"))
        self._apply_mode_state()

    def _apply_mode_state(self) -> None:
        input_format = self.input_format_var.get()
        random_seed = self.random_seed_var.get()

        self._set_widgets_state(self.json_only_widgets, tk.NORMAL if input_format == "json" else tk.DISABLED)
        self._set_widgets_state(self.text_only_widgets, tk.NORMAL if input_format == "text" else tk.DISABLED)
        self._set_widgets_state(
            self.seed_folio_widgets,
            tk.NORMAL if input_format == "json" and not random_seed else tk.DISABLED,
        )
        self._set_widgets_state(
            self.unique_seed_widgets,
            tk.NORMAL if input_format == "text" and not random_seed else tk.DISABLED,
        )

    def _set_widgets_state(self, widgets: list[tk.Widget], state: str) -> None:
        for widget in widgets:
            try:
                if isinstance(widget, ttk.Combobox) and state == tk.NORMAL:
                    widget.configure(state="readonly")
                    continue
                widget.configure(state=state)
            except tk.TclError:
                pass

    def choose_input(self) -> None:
        input_format = self.input_format_var.get()
        if input_format == "json":
            filetypes = [("JSON transcription", "*.json"), ("All files", "*.*")]
            title = "Choose transcription JSON"
        else:
            filetypes = [("Text files", "*.txt"), ("All files", "*.*")]
            title = "Choose plain/Gutenberg text"

        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            self.input_var.set(path)
            if input_format == "json":
                self.update_folio_dropdowns(show_errors=True)

    def choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose output ledger JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            self.output_var.set(path)

    def choose_codicology(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose codicology JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.codicology_var.set(path)
            self.update_scribe_dropdown(show_errors=True)

    def update_scribe_dropdown(self, show_errors: bool) -> None:
        path = self.codicology_var.get().strip()
        try:
            scribes = self.read_scribes_from_codicology(path)
        except ValueError as exc:
            self.scribe_combo.configure(values=())
            if show_errors and self.input_format_var.get() == "json":
                messagebox.showerror("Codicology Error", str(exc))
            return

        values = ["All scribes"] + [str(scribe) for scribe in scribes]
        self.scribe_combo.configure(values=values)
        current = self.scribe_var.get().strip()
        if current not in values:
            self.scribe_var.set("All scribes")

    def read_scribes_from_codicology(self, path: str) -> list[int]:
        if not path:
            return [1]
        candidate = Path(path)
        if not candidate.is_file():
            raise ValueError(f"Codicology file was not found:\n{path}")

        with candidate.open("r", encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, list):
            raise ValueError("Codicology JSON must be a list of folio rows.")

        scribes = set()
        for row in rows:
            if isinstance(row, dict):
                raw_scribe = str(row.get("Scribe", ""))
                raw_scribe = re.sub(r"\bon\s+\d+[rv]\b", "", raw_scribe, flags=re.IGNORECASE)
                scribes.update(int(value) for value in re.findall(r"\d+", raw_scribe))
        if not scribes:
            raise ValueError("No scribe numbers were found in the codicology JSON.")
        return sorted(scribes)

    def update_folio_dropdowns(self, show_errors: bool) -> None:
        if self.input_format_var.get() != "json":
            return

        path = self.input_var.get().strip()
        try:
            folios = self.read_folios_from_transcription(path)
        except ValueError as exc:
            self.folio_start_combo.configure(values=())
            self.folio_end_combo.configure(values=())
            self.folio_start_var.set("")
            self.folio_end_var.set("")
            if show_errors:
                messagebox.showerror("Transcription Folio Error", str(exc))
            return

        self.folio_start_combo.configure(values=folios)
        self.folio_end_combo.configure(values=folios)
        self.seed_folio_combo.configure(values=folios)

        if self.folio_start_var.get() not in folios:
            self.folio_start_var.set(folios[0])
        if self.folio_end_var.get() not in folios:
            self.folio_end_var.set(folios[-1])
        if DEFAULT_SEED_FOLIO in folios and self.seed_folio_var.get() not in folios:
            self.seed_folio_var.set(DEFAULT_SEED_FOLIO)
        elif self.seed_folio_var.get() not in folios:
            self.seed_folio_var.set(folios[0])

    def read_folios_from_transcription(self, path: str) -> list[str]:
        if not path:
            raise ValueError("Transcription JSON is required before folios can be loaded.")
        candidate = Path(path)
        if not candidate.is_file():
            raise ValueError(f"Transcription file was not found:\n{path}")
        if candidate.suffix.lower() != ".json":
            raise ValueError("Transcription input must be a .json file.")

        with candidate.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise ValueError("Transcription JSON must have top-level key 'records' containing a list.")

        folios = []
        bad_folios = []
        for index, row in enumerate(records):
            if not isinstance(row, dict):
                continue
            raw_folio = str(row.get("folio", "")).strip()
            if not raw_folio:
                bad_folios.append(f"record {index}: missing folio")
                continue
            normalized = raw_folio.lower()
            if not normalized.startswith("f"):
                normalized = f"f{normalized}"
            if not self.is_valid_voynich_folio(normalized):
                bad_folios.append(f"record {index}: {raw_folio!r}")
                continue
            folios.append(normalized)

        if bad_folios:
            examples = "\n".join(bad_folios[:10])
            extra = "" if len(bad_folios) <= 10 else f"\n...and {len(bad_folios) - 10} more"
            raise ValueError(f"Transcription JSON contains invalid folio values:\n{examples}{extra}")
        if not folios:
            raise ValueError("No valid folios were found in the transcription JSON.")

        return sorted(set(folios), key=self.folio_dropdown_sort_key)

    def is_valid_voynich_folio(self, value: str) -> bool:
        return bool(re.fullmatch(r"f\d+[rv][a-z0-9]*", value))

    def folio_dropdown_sort_key(self, folio: str) -> tuple[int, int, int, str]:
        match = re.fullmatch(r"f(\d+)([rv])([a-z0-9]*)", folio)
        if not match:
            return (10**9, 9, 0, folio)
        number, side, suffix = match.groups()
        side_order = {"r": 0, "v": 1}.get(side, 2)
        if suffix.isdigit():
            suffix_order = int(suffix)
            suffix_text = ""
        else:
            suffix_order = 0
            suffix_text = suffix
        return (int(number), side_order, suffix_order, suffix_text)

    def start_build(self) -> None:
        if self.is_running:
            return

        try:
            config = self._read_config()
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return

        self.is_running = True
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("Generating...")
        self.clear_log()
        self.append_log("Generating ledger JSON.")

        worker = threading.Thread(target=self._run_build, args=(config,), daemon=True)
        worker.start()
        self.after(100, self._poll_worker)

    def _read_config(self) -> Dict[str, Any]:
        input_format = self.input_format_var.get()
        if input_format not in {"json", "text"}:
            raise ValueError("Choose JSON transcription or plain/Gutenberg text input.")

        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        if not input_path:
            raise ValueError("Input file is required.")
        if not output_path:
            raise ValueError("Output JSON is required.")
        if not Path(input_path).is_file():
            raise ValueError(f"Input file was not found:\n{input_path}")
        if not output_path.lower().endswith(".json"):
            raise ValueError("Output must be a .json file.")

        output_dir = Path(output_path).expanduser().resolve().parent
        if not output_dir.is_dir():
            raise ValueError(f"Output folder was not found:\n{output_dir}")

        if input_format == "json" and not input_path.lower().endswith(".json"):
            raise ValueError("JSON transcription input must be a .json file.")
        if input_format == "text" and not input_path.lower().endswith(".txt"):
            raise ValueError("Plain/Gutenberg text input must be a .txt file.")

        page_length = self._positive_int(self.page_length_var.get(), "Words per page")
        page_count = self._positive_int(self.page_count_var.get(), "Pages to read")
        seed_count = self._nonnegative_int(self.seed_count_var.get(), "Seed count")
        rng_seed = self._int_value(self.rng_seed_var.get(), "RNG seed")

        codicology_path = self.codicology_var.get().strip()
        scribe = self._optional_scribe()
        folio_start = self.folio_start_var.get().strip()
        folio_end = self.folio_end_var.get().strip()

        if input_format == "json":
            folios = self.read_folios_from_transcription(input_path)
            if folio_start and folio_start not in folios:
                raise ValueError("Folio start must be selected from the transcription folio list.")
            if folio_end and folio_end not in folios:
                raise ValueError("Folio end must be selected from the transcription folio list.")
            if codicology_path and not Path(codicology_path).is_file():
                raise ValueError(f"Codicology file was not found:\n{codicology_path}")
            if bool(folio_start) != bool(folio_end):
                raise ValueError("Folio range requires both start and end.")

        return {
            "input_format": input_format,
            "input_path": input_path,
            "output_path": output_path,
            "codicology_path": codicology_path if input_format == "json" else "",
            "scribe": scribe if input_format == "json" else None,
            "folio_range": [folio_start, folio_end] if input_format == "json" and folio_start and folio_end else None,
            "page_length": page_length,
            "page_count": page_count,
            "seed_folio": self.seed_folio_var.get().strip() or DEFAULT_SEED_FOLIO,
            "seed_count": seed_count,
            "unique_seeds": bool(self.unique_seeds_var.get()) if input_format == "text" else False,
            "random_seed": bool(self.random_seed_var.get()),
            "rng_seed": rng_seed,
        }

    def _run_build(self, config: Dict[str, Any]) -> None:
        try:
            input_format = config["input_format"]
            codicology_by_folio: Dict[str, Dict[str, Any]] = {}
            codicology_meta: Dict[str, Any] = {}

            if input_format == "json":
                codicology_by_folio, codicology_meta = load_codicology(config["codicology_path"])
                all_records, source_meta, _all_source_tokens = load_json_pages(config["input_path"], codicology_by_folio)
            else:
                all_records, source_meta, _all_source_tokens = load_text_pages(
                    config["input_path"],
                    config["page_count"],
                    config["page_length"],
                )

            selected = filter_records(
                all_records,
                folio_range=config["folio_range"] if input_format == "json" else None,
                scribe=config["scribe"] if input_format == "json" else None,
            )
            working_tokens = flatten_tokens(selected)
            if not working_tokens:
                raise ValueError("No usable tokens after input normalization and filters.")

            stats = collect_statistics(working_tokens)
            ledger = build_ledger(stats["followers"])  # type: ignore[arg-type]
            first_weights = normalized_weights(stats["first_letters"])
            length_weights = normalized_weights(stats["word_lengths"])
            short_tokens = dict(sorted(stats["short_tokens"].items()))

            seed_block = build_seed_block(
                source_type=input_format,
                records=selected,
                source_tokens=working_tokens,
                ledger=ledger,
                first_weights=first_weights,
                length_weights=length_weights,
                short_tokens=short_tokens,
                seed_folio=config["seed_folio"],
                seed_count=config["seed_count"],
                unique_seeds=config["unique_seeds"],
                random_seed_enabled=config["random_seed"],
                rng_seed=config["rng_seed"],
            )

            output = build_ledger_output(
                input_path=config["input_path"],
                input_format=input_format,
                records=selected,
                source_meta=source_meta,
                codicology_meta=codicology_meta,
                scribe=config["scribe"] if input_format == "json" else None,
                folio_range=config["folio_range"] if input_format == "json" else None,
                seed_block=seed_block,
                stats=stats,
                ledger=ledger,
            )

            with Path(config["output_path"]).open("w", encoding="utf-8") as handle:
                json.dump(output, handle, indent=2, ensure_ascii=False)
                handle.write("\n")

            lines = [
                f"ledger generated: {config['output_path']}",
                f"input format: {input_format}",
                f"records used: {len(selected)}",
                f"tokens used: {len(working_tokens)}",
                f"unique tokens: {len(stats['token_counts'])}",
                f"alphabet: {''.join(sorted(stats['alphabet']))}",
                f"seed mode: {seed_block.get('mode')}",
                f"seed words: {seed_block.get('count')}",
            ]
            self.work_queue.put(("success", "\n".join(lines)))
        except Exception as exc:
            self.work_queue.put(("error", str(exc)))

    def _poll_worker(self) -> None:
        try:
            kind, message = self.work_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_worker)
            return

        self.is_running = False
        self.run_button.configure(state=tk.NORMAL)

        if kind == "success":
            self.status_var.set("Complete")
            self.append_log(message)
            messagebox.showinfo("Complete", message)
        else:
            self.status_var.set("Failed")
            self.append_log(f"ERROR: {message}")
            messagebox.showerror("Build Failed", message)

    def clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _int_value(self, value: str, label: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer.") from exc

    def _positive_int(self, value: str, label: str) -> int:
        number = self._int_value(value, label)
        if number <= 0:
            raise ValueError(f"{label} must be greater than zero.")
        return number

    def _nonnegative_int(self, value: str, label: str) -> int:
        number = self._int_value(value, label)
        if number < 0:
            raise ValueError(f"{label} cannot be negative.")
        return number

    def _optional_int(self, value: str, label: str) -> Optional[int]:
        stripped = value.strip()
        if not stripped:
            return None
        return self._int_value(stripped, label)

    def _optional_scribe(self) -> Optional[int]:
        value = self.scribe_var.get().strip()
        if not value or value == "All scribes":
            return None
        return self._int_value(value, "Scribe")


def main() -> None:
    app = LedgerGeneratorGui()
    app.mainloop()


if __name__ == "__main__":
    main()
