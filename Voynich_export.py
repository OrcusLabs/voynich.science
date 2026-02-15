#!/usr/bin/env python3
import json
import csv
import os
import re
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox


def now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def load_container(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def folio_sort_key(fid: str):
    m = re.match(r"^f(\d+)(r|v)(\d+)?$", fid)
    if not m:
        return (999999, 9, 999999)
    return (int(m.group(1)), 0 if m.group(2) == "r" else 1, int(m.group(3) or 0))


class ExportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Voynich Export")
        self.geometry("980x720")

        self.container = None
        self.sources = {}
        self.transcribers = []
        self.folios = []
        self.last_selected_sources = []
        self.last_selected_transcribers = []
        self.last_sources_signature = None

        self._build_ui()

    def _build_ui(self):
        # Voynich JSON file
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        tk.Label(top, text="Voynich JSON").pack(side="left")
        self.path_var = tk.StringVar(value="")
        tk.Entry(top, textvariable=self.path_var, width=70).pack(side="left", padx=6)
        tk.Button(top, text="Browse", command=self._browse_container).pack(side="left")
        tk.Button(top, text="Load", command=self._load_container).pack(side="left", padx=6)

        # Main selection area
        mid = tk.Frame(self)
        mid.pack(fill="both", expand=True, padx=10)

        # Sources
        src_frame = tk.LabelFrame(mid, text="Sources")
        src_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.src_list = tk.Listbox(src_frame, selectmode="extended")
        self.src_list.pack(fill="both", expand=True, padx=5, pady=5)
        self.src_list.bind("<<ListboxSelect>>", lambda _e: self._refresh_transcribers())

        # Transcribers
        tr_frame = tk.LabelFrame(mid, text="Transcribers")
        tr_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.tr_list = tk.Listbox(tr_frame, selectmode="extended")
        self.tr_list.pack(fill="both", expand=True, padx=5, pady=5)

        # Folios
        fol_frame = tk.LabelFrame(mid, text="Folios")
        fol_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.fol_list = tk.Listbox(fol_frame, selectmode="extended")
        fol_scroll = tk.Scrollbar(fol_frame, orient="vertical", command=self.fol_list.yview)
        self.fol_list.configure(yscrollcommand=fol_scroll.set)
        self.fol_list.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        fol_scroll.pack(side="right", fill="y")

        # Options
        opt = tk.LabelFrame(self, text="Options")
        opt.pack(fill="x", padx=10, pady=5)

        self.view_var = tk.StringVar(value="normalized")
        for v in ["raw", "normalized", "splat"]:
            tk.Radiobutton(opt, text=v, variable=self.view_var, value=v).pack(side="left", padx=6)

        self.format_var = tk.StringVar(value="json")
        for v in ["json", "csv", "txt"]:
            tk.Radiobutton(opt, text=v, variable=self.format_var, value=v).pack(side="left", padx=6)

        self.all_folios_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="All folios", variable=self.all_folios_var,
                       command=self._toggle_all_folios).pack(side="left", padx=10)

        # Output
        out = tk.Frame(self)
        out.pack(fill="x", padx=10, pady=8)
        tk.Label(out, text="Output file").pack(side="left")
        self.out_var = tk.StringVar(value="")
        tk.Entry(out, textvariable=self.out_var, width=70).pack(side="left", padx=6)
        tk.Button(out, text="Browse", command=self._browse_output).pack(side="left")
        tk.Button(out, text="Export", command=self._export).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="Load a Voynich JSON to begin.")
        tk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=6)

    def _browse_container(self):
        path = filedialog.askopenfilename(title="Select Voynich JSON", filetypes=[("JSON", "*.json")])
        if path:
            self.path_var.set(path)

    def _browse_output(self):
        fmt = self.format_var.get()
        ext = {"json": ".json", "csv": ".csv", "txt": ".txt"}.get(fmt, ".json")
        path = filedialog.asksaveasfilename(title="Export file", defaultextension=ext)
        if path:
            self.out_var.set(path)

    def _load_container(self):
        path = self.path_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "Voynich JSON not found.")
            return
        try:
            self.container = load_container(path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load JSON: {e}")
            return

        self.sources = {s["source_id"]: s for s in self.container.get("sources", []) if "source_id" in s}
        self.transcribers = list(self.container.get("transcribers", []))
        self.folios = sorted(self.container.get("pages", {}).keys(), key=folio_sort_key)

        self.src_list.delete(0, tk.END)
        for sid in sorted(self.sources.keys()):
            self.src_list.insert(tk.END, sid)

        self.fol_list.delete(0, tk.END)
        for f in self.folios:
            self.fol_list.insert(tk.END, f)

        # Do not populate transcribers until the user selects at least one source
        self.tr_list.delete(0, tk.END)
        self.last_selected_sources = []
        self.last_selected_transcribers = []
        self.last_sources_signature = None

        self.status_var.set(f"Loaded {os.path.basename(path)}: {len(self.sources)} sources, {len(self.folios)} folios.")

    def _refresh_transcribers(self):
        selected_sources = self._selected_sources()

        if not selected_sources:
            self.tr_list.delete(0, tk.END)
            return

        if selected_sources:
            self.last_selected_sources = selected_sources
        sig = tuple(sorted(selected_sources)) if selected_sources else None
        if sig != self.last_sources_signature:
            self.tr_list.selection_clear(0, tk.END)
            self.last_selected_transcribers = []
            self.last_sources_signature = sig
        else:
            selected_sources = self.last_selected_sources

        self.tr_list.delete(0, tk.END)
        for t in self.transcribers:
            sid = t.get("source_id")
            if selected_sources and sid not in selected_sources:
                continue
            label = f"{t.get('id','')} | {t.get('name','')}"
            self.tr_list.insert(tk.END, label)

    def _selected_sources(self):
        idx = self.src_list.curselection()
        if not idx:
            return []
        return [self.src_list.get(i) for i in idx]

    def _selected_transcribers(self):
        idx = self.tr_list.curselection()
        if not idx:
            return list(self.last_selected_transcribers)
        out = []
        for i in idx:
            label = self.tr_list.get(i)
            tid = label.split("|", 1)[0].strip()
            out.append(tid)
        if out:
            self.last_selected_transcribers = out
        return out

    def _selected_folios(self):
        if self.all_folios_var.get():
            return []
        idx = self.fol_list.curselection()
        if not idx:
            return []
        return [self.fol_list.get(i) for i in idx]

    def _export(self):
        if not self.container:
            messagebox.showerror("Error", "Load Voynich JSON first.")
            return
        out_path = self.out_var.get().strip()
        if not out_path:
            messagebox.showerror("Error", "Choose an output file.")
            return

        selected_sources = self._selected_sources()
        selected_transcribers = self._selected_transcribers()
        selected_folios = self._selected_folios()
        view = self.view_var.get()

        if not selected_sources:
            selected_sources = list(self.sources.keys())
        if selected_transcribers:
            # If user has transcriber filters, limit sources to those transcribers' sources.
            tid_to_source = {t.get("id"): t.get("source_id") for t in self.transcribers if t.get("id")}
            selected_sources = [
                sid for sid in selected_sources
                if sid in {tid_to_source.get(tid) for tid in selected_transcribers}
            ]
        if not selected_folios:
            selected_folios = self.folios

        records = []
        pages = self.container.get("pages", {})
        for folio in selected_folios:
            page = pages.get(folio)
            if not page:
                continue
            # Standard lines
            for line_key, line in (page.get("lines") or {}).items():
                for sid, srec in (line.get("sources") or {}).items():
                    base_sid = srec.get("source_id") or sid.split(":", 1)[0]
                    if base_sid not in selected_sources:
                        continue
                    tid = srec.get("transcriber_id")
                    if selected_transcribers and tid not in selected_transcribers:
                        continue
                    views = srec.get("views", {})
                    if view == "raw":
                        text = views.get("raw", {}).get("record", "")
                    else:
                        text = views.get(view, {}).get("text", "")
                    records.append({
                        "folio": folio,
                        "line": line_key,
                        "source_id": base_sid,
                        "source_key": sid,
                        "transcriber_id": tid,
                        "view": view,
                        "text": text,
                        "locator": srec.get("locator", {}),
                    })

            # Takahashi-style blocks
            for block_type, blocks in (page.get("blocks") or {}).items():
                if not isinstance(blocks, dict):
                    continue
                for block_index, block in blocks.items():
                    for line_key, line in (block.get("lines") or {}).items():
                        for sid, srec in (line.get("sources") or {}).items():
                            base_sid = srec.get("source_id") or sid.split(":", 1)[0]
                            if base_sid not in selected_sources:
                                continue
                            tid = srec.get("transcriber_id")
                            if selected_transcribers and tid not in selected_transcribers:
                                continue
                            views = srec.get("views", {})
                            if view == "raw":
                                text = views.get("raw", {}).get("record", "")
                            else:
                                text = views.get(view, {}).get("text", "")
                            records.append({
                                "folio": folio,
                                "line": line_key,
                                "source_id": base_sid,
                                "source_key": sid,
                                "transcriber_id": tid,
                                "view": view,
                                "text": text,
                                "locator": srec.get("locator", {}),
                            })

        fmt = self.format_var.get()
        try:
            if fmt == "json":
                obj = {"exported_utc": now_utc_iso(), "view": view, "records": records}
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
            elif fmt == "csv":
                with open(out_path, "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(
                        f,
                        fieldnames=["folio", "line", "source_id", "source_key", "transcriber_id", "view", "text",
                                   "locator_json"]
                    )
                    w.writeheader()
                    for r in records:
                        row = dict(r)
                        row["locator_json"] = json.dumps(r.get("locator", {}), ensure_ascii=False)
                        row.pop("locator", None)
                        w.writerow(row)
            else:
                with open(out_path, "w", encoding="utf-8") as f:
                    for r in records:
                        loc = json.dumps(r.get("locator", {}), ensure_ascii=False)
                        f.write(
                            f"{r['folio']}\t{r['line']}\t{r['source_id']}\t{r['source_key']}\t{r['transcriber_id']}\t{r['text']}\t{loc}\n"
                        )
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")
            return

        self.status_var.set(f"Exported {len(records)} records to {out_path}")

    def _toggle_all_folios(self):
        if self.all_folios_var.get():
            self.fol_list.selection_clear(0, tk.END)
            self.fol_list.configure(state="disabled")
        else:
            self.fol_list.configure(state="normal")


if __name__ == "__main__":
    app = ExportApp()
    app.mainloop()
