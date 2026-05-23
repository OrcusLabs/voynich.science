#!/usr/bin/env python3
"""
GUI wrapper for building a mappings-v3 style word mapping JSON from a
normalized Voynich transcription JSON.

Defaults:
    transcription: TTLI.json
    metadata:      quires_scribes.json
    output:        mappings_output.json
"""
import json
import math
import os
import queue
import re
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, messagebox, ttk

GALLOWS = set("ktpf")


def edit_distance(a, b, max_distance=1):
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


def normalize_nan(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def folio_number(folio):
    m = re.match(r"^f?(\d+)", str(folio))
    if not m:
        return None
    return int(m.group(1))


def folio_sort_key(folio):
    # Supports f85r1 / f86v5 / f102v2 suffix pages.
    m = re.match(r"^f?(\d+)([rv])?([A-Za-z0-9]*)$", str(folio))
    if not m:
        return (10**9, str(folio), 0, "")
    n, side, suffix = m.groups()
    side_order = {"r": 0, "v": 1}.get(side, 2)
    suffix_key = int(suffix) if suffix.isdigit() else suffix
    return (int(n), side_order, suffix_key, str(folio))


def folio_base_sort_key(folio):
    m = re.match(r"^f?(\d+)([rv])?([A-Za-z0-9]*)$", str(folio))
    if not m:
        return (10**9, str(folio))
    n, side, _suffix = m.groups()
    side_order = {"r": 0, "v": 1}.get(side, 2)
    return (int(n), side_order)


def folio_suffix_sort_key(folio):
    m = re.match(r"^f?(\d+)([rv])?([A-Za-z0-9]*)$", str(folio))
    if not m:
        return ""
    suffix = m.group(3) or ""
    return int(suffix) if suffix.isdigit() else suffix


def record_sort_key(rec):
    # V3 id order uses lexical line ordering within a folio side. For split
    # folios such as f67r1/f67r2, the side is grouped first, then line, then suffix.
    return (folio_base_sort_key(rec["folio"]), str(rec["line"]), folio_suffix_sort_key(rec["folio"]))


def load_transcription(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "records" not in data:
        raise ValueError(f"{path} is not a supported transcription JSON: expected top-level 'records'")
    return data["records"]


def strip_gutenberg_boilerplate(text):
    start_re = re.compile(r"^\s*\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b.*$", re.IGNORECASE | re.MULTILINE)
    end_re = re.compile(r"^\s*\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b.*$", re.IGNORECASE | re.MULTILINE)

    start = start_re.search(text)
    if start:
        text = text[start.end():]

    end = end_re.search(text)
    if end:
        text = text[:end.start()]

    return text.strip()


def load_gutenberg_text_records(path, words_per_line=12, lines_per_folio=40):
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()

    text = strip_gutenberg_boilerplate(text)
    tokens = [match.group(0).lower() for match in re.finditer(r"[A-Za-z]+", text)]
    if not tokens:
        raise ValueError(f"No alphabetic words found in text file: {path}")

    records = []
    for line_index, start in enumerate(range(0, len(tokens), words_per_line), 1):
        folio_number_value = ((line_index - 1) // lines_per_folio) + 1
        line_number = ((line_index - 1) % lines_per_folio) + 1
        records.append(
            {
                "folio": f"f{folio_number_value}r",
                "line": str(line_number),
                "text": " ".join(tokens[start:start + words_per_line]),
            }
        )
    return records


def load_folio_metadata(path):
    with open(path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)
    by_folio = {}
    for row in rows:
        by_folio[int(row["Folio"])] = {
            "quire": int(row["Quire"]),
            "sheet": int(row["Sheet"]),
            "currier_language": normalize_nan(row.get("Currier_Language")),
            "scribe_raw": normalize_nan(row.get("Scribe")),
        }
    return by_folio


def hand_and_scribe_for_folio(folio, currier_language, scribe_raw):
    """
    Legacy V3 hand/scribe labels.

    This is deliberately separated from quire/sheet/Currier metadata because
    mappings V3 used the older hand labels (A1, B2, B3, A4, A4?, B5, BX, BY, etc.),
    not a direct one-to-one copy of the Davis scribe field.
    """
    n = folio_number(folio)
    cur = currier_language

    # Main Scribe/Hand 1 herbal + pharma block, except B-language Scribe 2 folios below.
    if 1 <= n <= 57:
        if cur == "B":
            return ["B2"], [2]
        return ["A1"], [1]

    # B-language Scribe 2 biological/cosmological blocks.
    if n in {75, 76, 77, 78, 79, 80, 81, 82, 83, 84}:
        return ["B2"], [2]

    # Special stars/rosettes/cosmological material with no V3 hand/scribe assignment.
    if 67 <= n <= 73 or 87 <= n <= 93 or n == 99:
        return [], []

    # Unassigned text blocks use plain Currier hand label only.
    if 58 <= n <= 66:
        return ([cur] if cur else []), []
    if n == 102:
        return ([cur] if cur else []), []

    # V3 special labels.
    if n in {85, 86}:
        return ["B3"], [3]
    if n in {94, 95}:
        return ["B5"], [5]
    if n == 96:
        return ["A4"], [4]
    if n in {100, 101}:
        return ["A4?"], [4]
    if n in {103, 104, 106}:
        return ["BX"], []
    if n == 105:
        return ["BY"], []
    if n in {107, 108, 111, 112, 113, 114, 115, 116}:
        return ["B"], []

    # Conservative fallback from raw scribe field.
    if isinstance(scribe_raw, str):
        m = re.match(r"^\s*(\d+)", scribe_raw)
        if m:
            s = int(m.group(1))
            label = f"{cur}{s}" if cur else ""
            return ([label] if label else []), [s]
    return [], []


def gallows_info(token):
    positions = [i for i, ch in enumerate(token) if ch in GALLOWS]
    any_g = bool(positions)
    initial = bool(token) and token[0] in GALLOWS
    mid = any(i > 0 for i in positions)

    all_stripped = "".join(ch for ch in token if ch not in GALLOWS) if any_g else None
    initial_stripped = token[1:] if initial else None
    mid_stripped = None
    if mid:
        mid_stripped = token[0] + "".join(ch for ch in token[1:] if ch not in GALLOWS)

    return {
        "any": any_g,
        "initial": initial,
        "mid": mid,
        "count": len(positions),
        "stripped": {
            "all": all_stripped,
            "initial": initial_stripped,
            "mid": mid_stripped,
        },
        "ed1_stripped": {
            "all": [],
            "initial": [],
            "mid": [],
        },
    }


def first_pass(records, folio_meta):
    words = OrderedDict()

    for rec in sorted(records, key=record_sort_key):
        folio = rec["folio"]
        line = str(rec["line"])
        meta = folio_meta.get(folio_number(folio), {})
        currier_language = meta.get("currier_language")
        hands, scribes = hand_and_scribe_for_folio(folio, currier_language, meta.get("scribe_raw"))

        for pos, token in enumerate(str(rec.get("text", "")).split(), 1):
            if token not in words:
                words[token] = {
                    "id": len(words) + 1,
                    "gallows": gallows_info(token),
                    "len": len(token),
                    "count": 0,
                    "first": {"f": folio, "l": line, "p": pos},
                    "occurrences": [],
                    "outlier": False,
                }

            words[token]["count"] += 1
            words[token]["occurrences"].append({
                "folio": folio,
                "line": line,
                "position": pos,
                "quire": meta.get("quire"),
                "sheet": meta.get("sheet"),
                "currier_language": currier_language,
                "hands": hands,
                "scribes": scribes,
            })

    return words


def add_ed1_stripped(words):
    """
    Populate gallows.ed1_stripped by token id.

    For each stripped form, keep all vocabulary tokens at Levenshtein distance
    <= 1. Implemented with exact/deletion/wildcard indexes rather than a full
    O(V^2) scan.
    """
    word_items = list(words.items())

    exact = {}
    deletes_from_token = {}
    wildcards = {}

    for token, info in word_items:
        token_id = info["id"]
        exact.setdefault(token, []).append(token_id)

        # For query s and candidate token one char longer:
        # token is ED1 insertion if deleting one char from token gives s.
        seen_deletes = set()
        for i in range(len(token)):
            key = token[:i] + token[i + 1:]
            if key not in seen_deletes:
                deletes_from_token.setdefault(key, []).append(token_id)
                seen_deletes.add(key)

        # For same-length substitution.
        seen_wildcards = set()
        for i in range(len(token)):
            key = (len(token), i, token[:i] + "*" + token[i + 1:])
            if key not in seen_wildcards:
                wildcards.setdefault(key, []).append(token_id)
                seen_wildcards.add(key)

    for token, info in word_items:
        stripped = info["gallows"]["stripped"]
        for strip_key in ("all", "initial", "mid"):
            s = stripped.get(strip_key)
            if not s:
                continue

            ids = set()

            # distance 0
            ids.update(exact.get(s, []))

            # candidate token is one char longer than s
            ids.update(deletes_from_token.get(s, []))

            # candidate token is one char shorter than s
            for i in range(len(s)):
                ids.update(exact.get(s[:i] + s[i + 1:], []))

            # same-length substitution
            for i in range(len(s)):
                ids.update(wildcards.get((len(s), i, s[:i] + "*" + s[i + 1:]), []))

            info["gallows"]["ed1_stripped"][strip_key] = sorted(ids)


def add_outliers(words):
    """
    Current reconstruction of V3 outlier flag.

    NOTE: V3's outlier flag is not recoverable from the transcription schema
    by inspection alone unless its historical rule is known. This conservative
    rule marks singleton gallows tokens whose stripped forms have no ED1 support.
    """
    for token, info in words.items():
        ed = info["gallows"]["ed1_stripped"]
        info["outlier"] = (
            info["count"] == 1
            and info["gallows"]["any"]
            and not ed["all"]
            and not ed["initial"]
            and not ed["mid"]
        )


def build_mapping(transcription_path, metadata_path):
    records = load_transcription(transcription_path)
    folio_meta = load_folio_metadata(metadata_path)
    words = first_pass(records, folio_meta)
    add_ed1_stripped(words)
    add_outliers(words)
    return {"words": words}


def build_mapping_from_text(text_path):
    records = load_gutenberg_text_records(text_path)
    words = first_pass(records, {})
    add_ed1_stripped(words)
    add_outliers(words)
    return {"words": words}


class MappingGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Create Voynich Mappings JSON")
        self.geometry("760x390")
        self.minsize(680, 360)

        self.work_queue = queue.Queue()
        self.is_running = False

        base_dir = os.getcwd()
        self.source_mode_var = tk.StringVar(value="json")
        self.transcription_var = tk.StringVar(value=os.path.join(base_dir, "TTLI.json"))
        self.metadata_var = tk.StringVar(value=os.path.join(base_dir, "quires_scribes.json"))
        self.output_var = tk.StringVar(value=os.path.join(base_dir, "mappings_output.json"))
        self.status_var = tk.StringVar(value="Ready")

        self._build_widgets()

    def _build_widgets(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(5, weight=1)

        mode_frame = ttk.Frame(root)
        mode_frame.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(mode_frame, text="Source").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(mode_frame, text="Transcription JSON", variable=self.source_mode_var, value="json", command=self.update_source_mode).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(mode_frame, text="Gutenberg text", variable=self.source_mode_var, value="text", command=self.update_source_mode).pack(side=tk.LEFT)

        self.source_label = ttk.Label(root, text="Transcription JSON")
        self.source_label.grid(row=1, column=0, sticky="w", pady=(0, 8))
        self._entry(root, self.transcription_var, 1)
        self.source_browse_button = ttk.Button(root, text="Browse...", command=self.choose_transcription)
        self.source_browse_button.grid(row=1, column=2, padx=(8, 0), pady=(0, 8))

        self.metadata_label = ttk.Label(root, text="Metadata JSON")
        self.metadata_label.grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.metadata_entry = self._entry(root, self.metadata_var, 2)
        self.metadata_button = ttk.Button(root, text="Browse...", command=self.choose_metadata)
        self.metadata_button.grid(row=2, column=2, padx=(8, 0), pady=(0, 8))

        ttk.Label(root, text="Output JSON").grid(row=3, column=0, sticky="w", pady=(0, 8))
        self._entry(root, self.output_var, 3)
        ttk.Button(root, text="Save As...", command=self.choose_output).grid(row=3, column=2, padx=(8, 0), pady=(0, 8))

        controls = ttk.Frame(root)
        controls.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 12))
        controls.columnconfigure(1, weight=1)

        self.run_button = ttk.Button(controls, text="Build Mapping", command=self.start_build)
        self.run_button.grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.status_var).grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.log = tk.Text(root, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew")

        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.log.yview)
        scrollbar.grid(row=5, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.update_source_mode()

    def _entry(self, parent, variable, row):
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(0, 8))
        return entry

    def choose_transcription(self):
        if self.source_mode_var.get() == "text":
            self.choose_text_source()
            return
        path = filedialog.askopenfilename(
            title="Choose transcription JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.transcription_var.set(path)

    def choose_text_source(self):
        path = filedialog.askopenfilename(
            title="Choose Gutenberg text file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.transcription_var.set(path)
            self.output_var.set(os.path.join(os.path.dirname(path), f"mappings_{os.path.splitext(os.path.basename(path))[0]}.json"))

    def update_source_mode(self):
        if self.source_mode_var.get() == "text":
            self.source_label.configure(text="Gutenberg text")
            self.metadata_label.configure(text="Metadata JSON (not used)")
            self.metadata_entry.configure(state=tk.DISABLED)
            self.metadata_button.configure(state=tk.DISABLED)
        else:
            self.source_label.configure(text="Transcription JSON")
            self.metadata_label.configure(text="Metadata JSON")
            self.metadata_entry.configure(state=tk.NORMAL)
            self.metadata_button.configure(state=tk.NORMAL)

    def choose_metadata(self):
        path = filedialog.askopenfilename(
            title="Choose metadata JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.metadata_var.set(path)

    def choose_output(self):
        path = filedialog.asksaveasfilename(
            title="Choose output JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            self.output_var.set(path)

    def start_build(self):
        if self.is_running:
            return

        source_mode = self.source_mode_var.get()
        transcription_path = self.transcription_var.get().strip()
        metadata_path = self.metadata_var.get().strip()
        output_path = self.output_var.get().strip()

        error = self.validate_paths(transcription_path, metadata_path, output_path, source_mode)
        if error:
            messagebox.showerror("Invalid Input", error)
            return

        self.is_running = True
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("Building...")
        self.clear_log()
        if source_mode == "text":
            self.append_log("Building mapping JSON from Gutenberg text. Header/footer markers will be stripped if present.")
        else:
            self.append_log("Building mapping JSON from transcription JSON. This may take a moment.")

        worker = threading.Thread(
            target=self.run_build,
            args=(source_mode, transcription_path, metadata_path, output_path),
            daemon=True,
        )
        worker.start()
        self.after(100, self.poll_worker)

    def validate_paths(self, transcription_path, metadata_path, output_path, source_mode):
        if not transcription_path:
            return "Source file is required."
        if source_mode == "json" and not metadata_path:
            return "Metadata JSON is required."
        if not output_path:
            return "Output JSON is required."
        if source_mode == "json" and not transcription_path.lower().endswith(".json"):
            return "Transcription must be a .json file."
        if source_mode == "text" and not transcription_path.lower().endswith(".txt"):
            return "Gutenberg source should be a .txt file."
        if source_mode == "json" and not metadata_path.lower().endswith(".json"):
            return "Metadata must be a .json file."
        if not output_path.lower().endswith(".json"):
            return "Output must be a .json file."
        if not os.path.isfile(transcription_path):
            return f"Source file was not found:\n{transcription_path}"
        if source_mode == "json" and not os.path.isfile(metadata_path):
            return f"Metadata file was not found:\n{metadata_path}"

        output_dir = os.path.dirname(os.path.abspath(output_path)) or os.getcwd()
        if not os.path.isdir(output_dir):
            return f"Output folder was not found:\n{output_dir}"
        return None

    def run_build(self, source_mode, transcription_path, metadata_path, output_path):
        try:
            if source_mode == "text":
                generated = build_mapping_from_text(transcription_path)
            else:
                generated = build_mapping(transcription_path, metadata_path)
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(generated, handle, indent=2, ensure_ascii=False)
                handle.write("\n")

            lines = [
                f"WROTE: {output_path}",
                f"WORDS: {len(generated['words'])}",
                f"OCCURRENCES: {sum(info['count'] for info in generated['words'].values())}",
            ]

            self.work_queue.put(("success", "\n".join(lines)))
        except Exception as exc:
            self.work_queue.put(("error", str(exc)))

    def poll_worker(self):
        try:
            kind, message = self.work_queue.get_nowait()
        except queue.Empty:
            self.after(100, self.poll_worker)
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

    def clear_log(self):
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def append_log(self, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)


def main():
    app = MappingGui()
    app.mainloop()


if __name__ == "__main__":
    main()
