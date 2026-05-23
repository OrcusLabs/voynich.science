import io
import shutil
import queue
import threading
import traceback
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

from ed1_network_mapper import (
    build_ed1_graph,
    apply_contextual_edge_weights,
    component_summary,
    export_tables,
    filter_tokens_by_folio_range_and_scribe,
    filtered_graph_by_similarity,
    filter_counter,
    load_token_occurrences_from_mappings,
    load_mappings,
    mapping_folios_and_scribes,
    mappings_have_folio_metadata,
    parse_folio_list,
    plot_context_similarity_distribution,
    plot_context_weighted_ego_network,
    plot_component_sizes,
    plot_degree_distribution,
    plot_ego_network,
    plot_top_frequency_backbone,
    build_context_vectors,
    EXCLUDE_FOLIOS,
    folio_sort_key,
)


class ImageViewer(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.image = None
        self.original_image = None
        self.png_path = None
        self.svg_path = None
        self.resize_after_id = None

        toolbar = ttk.Frame(self, padding=(0, 0, 0, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.save_png_button = ttk.Button(toolbar, text="Save PNG", command=lambda: self.save_as("png"))
        self.save_svg_button = ttk.Button(toolbar, text="Save SVG", command=lambda: self.save_as("svg"))
        self.save_png_button.pack(side=tk.LEFT, padx=(0, 6))
        self.save_svg_button.pack(side=tk.LEFT)
        self.set_save_state(tk.DISABLED)

        self.canvas = tk.Canvas(self, background="#f7f7f7", highlightthickness=0)
        self.canvas.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.canvas.bind("<Configure>", self.schedule_resize)

    def set_save_state(self, state):
        self.save_png_button.configure(state=state)
        self.save_svg_button.configure(state=state)

    def load(self, path):
        self.canvas.delete("all")
        self.png_path = Path(path)
        self.svg_path = self.png_path.with_suffix(".svg")
        if Image is not None:
            self.original_image = Image.open(self.png_path)
        else:
            self.original_image = None
        self.after_idle(self.render_scaled_image)
        self.set_save_state(tk.NORMAL)

    def clear(self):
        self.canvas.delete("all")
        self.image = None
        self.original_image = None
        self.png_path = None
        self.svg_path = None
        self.set_save_state(tk.DISABLED)

    def schedule_resize(self, _event=None):
        if not self.png_path:
            return
        if self.resize_after_id is not None:
            self.after_cancel(self.resize_after_id)
        self.resize_after_id = self.after(80, self.render_scaled_image)

    def render_scaled_image(self):
        self.resize_after_id = None
        if not self.png_path or not self.png_path.exists():
            return

        canvas_width = max(self.canvas.winfo_width(), 1)
        canvas_height = max(self.canvas.winfo_height(), 1)
        self.canvas.delete("all")

        if Image is None:
            self.image = tk.PhotoImage(file=str(self.png_path))
            x = max((canvas_width - self.image.width()) // 2, 0)
            y = max((canvas_height - self.image.height()) // 2, 0)
            self.canvas.create_image(x, y, image=self.image, anchor="nw")
            return

        if self.original_image is None:
            self.original_image = Image.open(self.png_path)
        source_width, source_height = self.original_image.size
        scale = min(canvas_width / source_width, canvas_height / source_height)
        scale = max(scale, 0.05)
        target_size = (
            max(1, int(source_width * scale)),
            max(1, int(source_height * scale)),
        )
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        resized = self.original_image.resize(target_size, resample)
        self.image = ImageTk.PhotoImage(resized)
        x = (canvas_width - target_size[0]) // 2
        y = (canvas_height - target_size[1]) // 2
        self.canvas.create_image(x, y, image=self.image, anchor="nw")

    def save_as(self, file_type):
        source = self.png_path if file_type == "png" else self.svg_path
        if not source or not source.exists():
            messagebox.showerror("No chart", f"No {file_type.upper()} chart is available to save.")
            return

        destination = filedialog.asksaveasfilename(
            title=f"Save {file_type.upper()} chart",
            initialfile=source.name,
            defaultextension=f".{file_type}",
            filetypes=[
                (f"{file_type.upper()} files", f"*.{file_type}"),
                ("All files", "*.*"),
            ],
        )
        if not destination:
            return
        shutil.copyfile(source, destination)


class Ed1NetworkMapperGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ED1 Network Mapper")
        self.geometry("1180x820")
        self.minsize(920, 640)
        self.result_queue = queue.Queue()
        self.worker = None

        self.mappings_var = tk.StringVar()
        self.output_prefix_var = tk.StringVar(value=str(Path.cwd() / "ed1_network"))
        self.min_count_var = tk.StringVar(value="1")
        self.top_labels_var = tk.StringVar(value="50")
        self.top_backbone_var = tk.StringVar(value="300")
        self.ego_word_var = tk.StringVar(value="chedy")
        self.ego_radius_var = tk.StringVar(value="1")
        self.max_nodes_var = tk.StringVar()
        self.min_length_var = tk.StringVar(value="2")
        self.use_context_weights_var = tk.BooleanVar(value=False)
        self.edge_weighting_mode_var = tk.StringVar(value="none")
        self.min_context_similarity_var = tk.DoubleVar(value=0.0)
        self.start_folio_var = tk.StringVar(value="")
        self.end_folio_var = tk.StringVar(value="")
        self.scribe_var = tk.StringVar(value="all")
        self.exclude_folios_var = tk.StringVar(value=", ".join(sorted(EXCLUDE_FOLIOS)))
        self.status_var = tk.StringVar(value="Choose a mappings JSON file.")
        self.folio_metadata_available = True
        self.available_folios = []
        self.available_scribes = []

        self._build_controls()
        self._build_tabs()

    def _build_controls(self):
        controls = ttk.Frame(self, padding=10)
        controls.pack(side=tk.TOP, fill=tk.X)
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Mappings JSON").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(controls, textvariable=self.mappings_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(controls, text="Browse", command=self.choose_mappings).grid(row=0, column=2, padx=(8, 0), pady=3)

        ttk.Label(controls, text="Output prefix").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(controls, textvariable=self.output_prefix_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(controls, text="Choose", command=self.choose_output_prefix).grid(row=1, column=2, padx=(8, 0), pady=3)

        options = ttk.Frame(controls)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for column in range(10):
            options.columnconfigure(column, weight=0)

        ttk.Label(options, text="Min count").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.min_count_var, width=8).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(options, text="Backbone N").grid(row=0, column=2, sticky="w")
        ttk.Entry(options, textvariable=self.top_backbone_var, width=8).grid(row=0, column=3, padx=(6, 18))
        ttk.Label(options, text="Max nodes").grid(row=0, column=4, sticky="w")
        ttk.Entry(options, textvariable=self.max_nodes_var, width=10).grid(row=0, column=5, padx=(6, 18))
        ttk.Label(options, text="Min glyph length").grid(row=0, column=6, sticky="w")
        ttk.Entry(options, textvariable=self.min_length_var, width=8).grid(row=0, column=7, padx=(6, 18))
        self.run_button = ttk.Button(options, text="Run Analysis", command=self.run_analysis)
        self.run_button.grid(row=0, column=8, padx=(0, 8))
        ttk.Button(options, text="Clear", command=self.clear_outputs).grid(row=0, column=9)

        ttk.Label(options, text="Ego word").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(options, textvariable=self.ego_word_var, width=12).grid(row=1, column=1, padx=(6, 18), pady=(8, 0))
        ttk.Label(options, text="Ego radius").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(options, textvariable=self.ego_radius_var, width=8).grid(row=1, column=3, padx=(6, 18), pady=(8, 0))

        ttk.Checkbutton(
            options,
            text="Use contextual edge weights",
            variable=self.use_context_weights_var,
            command=self.update_context_controls,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(options, text="Edge weighting mode").grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.edge_mode_combo = ttk.Combobox(
            options,
            textvariable=self.edge_weighting_mode_var,
            values=["none", "combined context", "left context only", "right context only"],
            width=20,
            state="readonly",
        )
        self.edge_mode_combo.grid(row=2, column=3, columnspan=2, sticky="w", padx=(6, 18), pady=(8, 0))
        ttk.Label(options, text="Minimum context similarity").grid(row=2, column=5, sticky="w", pady=(8, 0))
        self.min_similarity_spin = ttk.Spinbox(
            options,
            from_=0.0,
            to=1.0,
            increment=0.05,
            textvariable=self.min_context_similarity_var,
            width=8,
            format="%.2f",
        )
        self.min_similarity_spin.grid(row=2, column=6, sticky="w", padx=(6, 18), pady=(8, 0))

        ttk.Label(options, text="Start folio").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.start_folio_combo = ttk.Combobox(
            options,
            textvariable=self.start_folio_var,
            values=[],
            width=12,
            state="readonly",
        )
        self.start_folio_combo.grid(row=3, column=1, sticky="w", padx=(6, 18), pady=(8, 0))
        self.start_folio_combo.bind("<<ComboboxSelected>>", self.on_start_folio_changed)

        ttk.Label(options, text="End folio").grid(row=3, column=2, sticky="w", pady=(8, 0))
        self.end_folio_combo = ttk.Combobox(
            options,
            textvariable=self.end_folio_var,
            values=[],
            width=12,
            state="readonly",
        )
        self.end_folio_combo.grid(row=3, column=3, sticky="w", padx=(6, 18), pady=(8, 0))

        ttk.Label(options, text="Scribe").grid(row=3, column=4, sticky="w", pady=(8, 0))
        self.scribe_combo = ttk.Combobox(
            options,
            textvariable=self.scribe_var,
            values=["all"],
            width=12,
            state="readonly",
        )
        self.scribe_combo.grid(row=3, column=5, sticky="w", padx=(6, 18), pady=(8, 0))

        ttk.Label(options, text="Exclude folios").grid(row=3, column=6, sticky="w", pady=(8, 0))
        ttk.Entry(options, textvariable=self.exclude_folios_var, width=30).grid(row=3, column=7, columnspan=3, sticky="ew", padx=(6, 0), pady=(8, 0))
        self.update_context_controls()

        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 0, 10, 8)).pack(side=tk.TOP, fill=tk.X)

    def update_context_controls(self):
        state = "readonly" if self.use_context_weights_var.get() else tk.DISABLED
        entry_state = tk.NORMAL if self.use_context_weights_var.get() else tk.DISABLED
        if self.use_context_weights_var.get() and self.edge_weighting_mode_var.get() == "none":
            self.edge_weighting_mode_var.set("combined context")
        if not self.use_context_weights_var.get():
            self.edge_weighting_mode_var.set("none")
        self.edge_mode_combo.configure(state=state)
        self.min_similarity_spin.configure(state=entry_state)
        folio_state = "readonly" if self.folio_metadata_available else tk.DISABLED
        self.start_folio_combo.configure(state=folio_state)
        self.end_folio_combo.configure(state=folio_state)
        self.scribe_combo.configure(state=folio_state)

    def on_start_folio_changed(self, _event=None):
        if not self.available_folios:
            return
        start = self.start_folio_var.get()
        try:
            start_index = self.available_folios.index(start)
        except ValueError:
            start_index = 0
        end_values = self.available_folios[start_index:]
        self.end_folio_combo.configure(values=end_values)
        if self.end_folio_var.get() not in end_values:
            self.end_folio_var.set(end_values[-1] if end_values else "")

    def _build_tabs(self):
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        report_frame = ttk.Frame(self.tabs)
        report_frame.rowconfigure(0, weight=1)
        report_frame.columnconfigure(0, weight=1)
        self.report_text = tk.Text(report_frame, wrap="word", font=("Consolas", 10), undo=False)
        report_scroll = ttk.Scrollbar(report_frame, orient=tk.VERTICAL, command=self.report_text.yview)
        self.report_text.configure(yscrollcommand=report_scroll.set)
        self.report_text.grid(row=0, column=0, sticky="nsew")
        report_scroll.grid(row=0, column=1, sticky="ns")

        self.backbone_viewer = ImageViewer(self.tabs)
        self.sizes_viewer = ImageViewer(self.tabs)
        self.degree_viewer = ImageViewer(self.tabs)
        self.ego_viewer = ImageViewer(self.tabs)
        self.context_ego_viewer = ImageViewer(self.tabs)
        self.context_distribution_viewer = ImageViewer(self.tabs)

        self.tabs.add(report_frame, text="Report")
        self.tabs.add(self.sizes_viewer, text="Component Sizes")
        self.tabs.add(self.degree_viewer, text="Degree Distribution")
        self.tabs.add(self.backbone_viewer, text="Top Backbone")
        self.tabs.add(self.ego_viewer, text="Ego Network")
        self.tabs.add(self.context_ego_viewer, text="Context Ego")
        self.tabs.add(self.context_distribution_viewer, text="Context Similarity")

    def choose_mappings(self):
        path = filedialog.askopenfilename(
            title="Choose mappings JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.mappings_var.set(path)
        stem = Path(path).with_suffix("").name
        self.output_prefix_var.set(str(Path(path).parent / f"{stem}_ed1_network"))
        self.folio_metadata_available = mappings_have_folio_metadata(path)
        if not self.folio_metadata_available:
            self.available_folios = []
            self.available_scribes = []
            self.start_folio_var.set("")
            self.end_folio_var.set("")
            self.scribe_var.set("all")
            self.start_folio_combo.configure(values=[])
            self.end_folio_combo.configure(values=[])
            self.scribe_combo.configure(values=["all"])
            self.status_var.set("Folio range and scribe filtering require folio-level token metadata.")
            messagebox.showwarning(
                "Folio filters unavailable",
                "Folio range and scribe filtering require folio-level token metadata.",
            )
        else:
            self.available_folios, self.available_scribes = mapping_folios_and_scribes(path)
            self.start_folio_combo.configure(values=self.available_folios)
            self.end_folio_combo.configure(values=self.available_folios)
            self.scribe_combo.configure(values=["all"] + self.available_scribes)
            if self.available_folios:
                self.start_folio_var.set(self.available_folios[0])
                self.end_folio_var.set(self.available_folios[-1])
            self.scribe_var.set("all")
            self.status_var.set("Mappings file selected.")
        self.update_context_controls()

    def choose_output_prefix(self):
        path = filedialog.asksaveasfilename(
            title="Choose output prefix",
            initialfile="ed1_network",
            defaultextension="",
            filetypes=[("Output prefix", "*")],
        )
        if path:
            self.output_prefix_var.set(path)

    def clear_outputs(self):
        self.report_text.delete("1.0", tk.END)
        self.sizes_viewer.clear()
        self.degree_viewer.clear()
        self.backbone_viewer.clear()
        self.ego_viewer.clear()
        self.context_ego_viewer.clear()
        self.context_distribution_viewer.clear()
        self.status_var.set("Cleared.")

    def parse_positive_int(self, value, name, allow_blank=False):
        value = value.strip()
        if allow_blank and not value:
            return None
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer.") from exc
        if number < 1:
            raise ValueError(f"{name} must be at least 1.")
        return number

    def parse_float_range(self, value, name, low=0.0, high=1.0):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number.") from exc
        if number < low or number > high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        return number

    def run_analysis(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            mappings = Path(self.mappings_var.get().strip())
            output_prefix = Path(self.output_prefix_var.get().strip())
            min_count = self.parse_positive_int(self.min_count_var.get(), "Min count")
            top_backbone = self.parse_positive_int(self.top_backbone_var.get(), "Backbone N")
            max_nodes = self.parse_positive_int(self.max_nodes_var.get(), "Max nodes", allow_blank=True)
            min_length = self.parse_positive_int(self.min_length_var.get(), "Min glyph length")
            ego_radius = self.parse_positive_int(self.ego_radius_var.get(), "Ego radius")
            ego_word = self.ego_word_var.get().strip().lower()
            min_context_similarity = self.parse_float_range(
                self.min_context_similarity_var.get(),
                "Minimum context similarity",
            )
            start_folio = self.start_folio_var.get().strip()
            end_folio = self.end_folio_var.get().strip()
            scribe = self.scribe_var.get().strip() or "all"
        except ValueError as exc:
            messagebox.showerror("Invalid option", str(exc))
            return

        if not mappings.exists():
            messagebox.showerror("Missing file", "Choose an existing mappings JSON file.")
            return
        if not output_prefix.name:
            messagebox.showerror("Missing output prefix", "Choose an output prefix.")
            return
        if not ego_word:
            messagebox.showerror("Missing ego word", "Choose an ego anchor word.")
            return
        if self.folio_metadata_available:
            if not start_folio or not end_folio:
                messagebox.showerror("Missing folio range", "Choose both a start folio and an end folio.")
                return
            if folio_sort_key(end_folio) < folio_sort_key(start_folio):
                messagebox.showerror("Invalid folio range", "End folio cannot be lower than start folio.")
                return

        output_prefix.parent.mkdir(parents=True, exist_ok=True)
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("Running ED1 analysis...")
        self.report_text.delete("1.0", tk.END)
        self.sizes_viewer.clear()
        self.degree_viewer.clear()
        self.backbone_viewer.clear()
        self.ego_viewer.clear()
        self.context_ego_viewer.clear()
        self.context_distribution_viewer.clear()

        args = {
            "mappings": mappings,
            "output_prefix": output_prefix,
            "min_count": min_count,
            "top_backbone": top_backbone,
            "max_nodes": max_nodes,
            "min_length": min_length,
            "ego_word": ego_word,
            "ego_radius": ego_radius,
            "use_context_weights": self.use_context_weights_var.get(),
            "edge_weighting_mode": self.edge_weighting_mode_var.get(),
            "min_context_similarity": min_context_similarity,
            "start_folio": start_folio,
            "end_folio": end_folio,
            "scribe": scribe,
            "exclude_folios": parse_folio_list(self.exclude_folios_var.get()),
            "has_folio_metadata": self.folio_metadata_available,
        }
        self.worker = threading.Thread(target=self._run_worker, args=(args,), daemon=True)
        self.worker.start()
        self.after(150, self.check_worker)

    def _run_worker(self, args):
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                if args["min_length"] <= 1:
                    print("WARNING: very short forms are included; single-character junk nodes may dominate.")
                tokens = []
                raw_token_count = 0
                if args["has_folio_metadata"]:
                    all_tokens = load_token_occurrences_from_mappings(args["mappings"])
                    raw_token_count = len(all_tokens)
                    tokens = filter_tokens_by_folio_range_and_scribe(
                        all_tokens,
                        start_folio=args["start_folio"],
                        end_folio=args["end_folio"],
                        scribe=args["scribe"],
                        exclude_folios=args["exclude_folios"],
                    )
                    counter = Counter(token["word"] for token in tokens)
                    print(
                        f"Corpus folio range: {args['start_folio']} to {args['end_folio']}; "
                        f"scribe: {args['scribe']}; "
                        f"tokens selected: {len(tokens)} of {raw_token_count}"
                    )
                else:
                    counter = load_mappings(args["mappings"])
                    print("WARNING: Folio range and scribe filtering require folio-level token metadata.")

                counter = filter_counter(counter, args["min_count"], args["min_length"], args["max_nodes"])
                if not counter:
                    raise ValueError("No word forms remain after filtering.")
                if tokens:
                    tokens = [token for token in tokens if token["word"] in counter]
                print(f"Building ED1 graph from {len(counter)} word forms...")
                graph = build_ed1_graph(counter)
                plot_graph = graph

                if args["use_context_weights"]:
                    print()
                    print(
                        "ED1 connectivity measures formal mutation adjacency. "
                        "Context-weighted ED1 edges estimate whether two formally adjacent words also occur "
                        "in similar local environments. This may help distinguish accidental ED1 proximity "
                        "from reinforced word-family behavior."
                    )
                    if not args["has_folio_metadata"]:
                        print("Contextual edge weighting was skipped because no folio-level token metadata was found.")
                    else:
                        if not tokens:
                            print("WARNING: No tokens remain after folio/scribe/exclude filtering; context similarities are all 0.")
                        left_vectors, right_vectors, combined_vectors = build_context_vectors(tokens)
                        apply_contextual_edge_weights(graph, left_vectors, right_vectors, combined_vectors)
                        plot_context_similarity_distribution(graph, args["output_prefix"])
                        plot_context_weighted_ego_network(
                            graph,
                            counter,
                            args["ego_word"],
                            args["ego_radius"],
                            args["output_prefix"],
                            mode=args["edge_weighting_mode"],
                            minimum_similarity=args["min_context_similarity"],
                        )
                        plot_graph = filtered_graph_by_similarity(
                            graph,
                            args["edge_weighting_mode"],
                            args["min_context_similarity"],
                        )
                        print(
                            f"Context folio range: {args['start_folio']} to {args['end_folio']}; "
                            f"scribe: {args['scribe']}; "
                            f"mode: {args['edge_weighting_mode']}; "
                            f"minimum similarity: {args['min_context_similarity']:.2f}; "
                            f"tokens used after word filters: {len(tokens)}"
                        )

                component_summary(plot_graph, counter)
                export_tables(plot_graph, counter, args["output_prefix"])
                plot_component_sizes(plot_graph, args["output_prefix"])
                plot_degree_distribution(plot_graph, args["output_prefix"])
                plot_top_frequency_backbone(
                    plot_graph,
                    counter,
                    args["output_prefix"],
                    top_n=args["top_backbone"],
                    edge_weighting_mode=args["edge_weighting_mode"] if args["use_context_weights"] else "none",
                )
                plot_ego_network(
                    plot_graph,
                    counter,
                    args["ego_word"],
                    args["ego_radius"],
                    args["output_prefix"],
                    edge_weighting_mode=args["edge_weighting_mode"] if args["use_context_weights"] else "none",
                )
                print(f"Outputs written with prefix: {args['output_prefix']}")

            self.result_queue.put(
                {
                    "ok": True,
                    "report": buffer.getvalue(),
                    "sizes_png": Path(f"{args['output_prefix']}_component_sizes.png"),
                    "degree_png": Path(f"{args['output_prefix']}_degree_distribution.png"),
                    "backbone_png": Path(f"{args['output_prefix']}_top_backbone.png"),
                    "ego_png": Path(f"{args['output_prefix']}_ego_{args['ego_word']}.png"),
                    "context_ego_png": Path(f"{args['output_prefix']}_context_ego_{args['ego_word']}.png"),
                    "context_distribution_png": Path(f"{args['output_prefix']}_context_similarity_distribution.png"),
                }
            )
        except Exception:
            self.result_queue.put({"ok": False, "error": traceback.format_exc()})

    def check_worker(self):
        try:
            result = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(150, self.check_worker)
            return

        self.run_button.configure(state=tk.NORMAL)
        if not result["ok"]:
            self.status_var.set("Analysis failed.")
            self.report_text.insert(tk.END, result["error"])
            self.tabs.select(0)
            messagebox.showerror("Analysis failed", "See the Report tab for details.")
            return

        self.report_text.insert(tk.END, result["report"])
        self.sizes_viewer.load(result["sizes_png"])
        self.degree_viewer.load(result["degree_png"])
        self.backbone_viewer.load(result["backbone_png"])
        self.ego_viewer.load(result["ego_png"])
        if result["context_ego_png"].exists():
            self.context_ego_viewer.load(result["context_ego_png"])
        if result["context_distribution_png"].exists():
            self.context_distribution_viewer.load(result["context_distribution_png"])
        self.status_var.set("Analysis complete.")
        self.tabs.select(0)


def main():
    app = Ed1NetworkMapperGui()
    app.mainloop()


if __name__ == "__main__":
    main()
