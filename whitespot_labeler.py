import csv
import io
import json
import sys
import tempfile
import tkinter as tk
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
LABELS = [
    "Wet",
    "Minimum dry",
    "Moderate",
    "Very dry",
]

LABEL_CHOICES = [
    ("1", LABELS[0], "Surface still looks moist", "no"),
    ("2", LABELS[1], "Slightly dry, still partly moist", "minimal"),
    ("3", LABELS[2], "Clearly dry in several areas", "moderate"),
    ("4", LABELS[3], "Very dry or fully dried", "extreme"),
]

THEME = {
    "bg": "#f4f7f6",
    "panel": "#ffffff",
    "ink": "#17202a",
    "muted": "#637381",
    "line": "#d8e0df",
    "stage": "#101820",
    "accent": "#157f72",
    "no": "#2e7d32",
    "minimal": "#8a6d1d",
    "moderate": "#c45f1a",
    "extreme": "#b3261e",
}


@dataclass
class ImageItem:
    dataset: str
    archive_path: str


class WhiteSpotLabeler(tk.Tk):
    def __init__(self, default_zip=None):
        super().__init__()
        self.title("Dryness Image Labeler")
        self.geometry("1100x780")
        self.minsize(780, 560)
        self.configure(bg=THEME["bg"])

        self.zip_path = Path(default_zip).expanduser() if default_zip else None
        self.zip_file = None
        self.datasets = {}
        self.items = []
        self.index = 0
        self.annotations = {}
        self.current_photo = None
        self.zoom_factor = 1.0
        self.zoom_step = 1.25
        self.min_zoom = 0.5
        self.max_zoom = 8.0
        self.progress_var = tk.DoubleVar(value=0)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="whitespot_labeler_")

        self._build_ui()
        if self.zip_path and self.zip_path.exists():
            self._load_zip(self.zip_path)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self._configure_styles()

        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight= 1)
        header.columnconfigure(1, weight=0)
        ttk.Label(header, text="Dryness Image Labeler", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Help", command=self._show_help).grid(row=0, column=1, sticky="e")
        ttk.Label(
            header,
            text="A simple guided tool for labeling image dryness and saving the results to CSV and Excel.",
            style="MutedOnBg.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        steps = ttk.Frame(header, style="App.TFrame")
        steps.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for col, text in enumerate(("1. Choose ZIP", "2. Pick dataset", "3. Label images", "4. Open CSV/Excel")):
            steps.columnconfigure(col, weight=1)
            ttk.Label(steps, text=text, anchor="center", style="Step.TLabel").grid(
                row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0)
            )

        file_bar = ttk.Frame(header, style="App.TFrame")
        file_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        file_bar.columnconfigure(1, weight=1)
        ttk.Button(file_bar, text="Choose Image ZIP File", command=self._choose_zip, style="Accent.TButton").grid(
            row=0, column=0, padx=(0, 10)
        )
        self.zip_label = ttk.Label(file_bar, text="No ZIP selected yet", anchor="w", style="MutedOnBg.TLabel")
        self.zip_label.grid(row=0, column=1, sticky="ew")

        picker = ttk.Frame(root, padding=16, style="Panel.TFrame")
        picker.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        picker.columnconfigure(0, weight=3)
        picker.columnconfigure(1, weight=2)
        picker.rowconfigure(1, weight=1)

        ttk.Label(picker, text="Step 2: Choose the dataset you want to label", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.dataset_list = tk.Listbox(
            picker,
            height=10,
            activestyle="none",
            bd=0,
            bg=THEME["panel"],
            fg=THEME["ink"],
            highlightthickness=1,
            highlightbackground=THEME["line"],
            highlightcolor=THEME["accent"],
            selectbackground=THEME["accent"],
            selectforeground="#ffffff",
            font=("Segoe UI", 11),
        )
        self.dataset_list.grid(row=1, column=0, sticky="nsew", pady=(8, 0), padx=(0, 16))
        self.dataset_list.bind("<<ListboxSelect>>", lambda _event: self._update_dataset_summary())
        self.dataset_list.bind("<Double-Button-1>", lambda _event: self._start_selected_dataset())

        side = ttk.Frame(picker, style="Panel.TFrame")
        side.grid(row=1, column=1, sticky="nsew", pady=(8, 0))
        side.columnconfigure(0, weight=1)
        ttk.Label(side, text="What to do", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.dataset_summary = ttk.Label(
            side,
            text="Step 1: Click Choose Image ZIP File above.\n\nThen select a dataset from the list.",
            style="Body.TLabel",
            wraplength=340,
        )
        self.dataset_summary.grid(row=1, column=0, sticky="ew", pady=(8, 16))
        ttk.Button(
            side,
            text="Start Labeling Selected Dataset",
            command=self._start_selected_dataset,
            style="Accent.TButton",
        ).grid(
            row=2, column=0, sticky="ew"
        )
        self.picker_frame = picker

        labeler = ttk.Frame(root, padding=16, style="Panel.TFrame")
        labeler.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        labeler.columnconfigure(0, weight=1)
        labeler.rowconfigure(4, weight=1)
        labeler.grid_remove()
        self.labeler_frame = labeler

        nav = ttk.Frame(labeler, style="Panel.TFrame")
        nav.grid(row=0, column=0, sticky="ew")
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Back to Datasets", command=self._show_picker).grid(row=0, column=0, padx=(0, 10))
        self.status_label = ttk.Label(nav, text="", anchor="w", style="Section.TLabel")
        self.status_label.grid(row=0, column=1, sticky="ew")
        ttk.Button(nav, text="Save CSV/Excel Now", command=self._save_annotations).grid(row=0, column=2)

        progress_row = ttk.Frame(labeler, style="Panel.TFrame")
        progress_row.grid(row=1, column=0, sticky="ew", pady=(10, 12))
        progress_row.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.count_label = ttk.Label(progress_row, text="", style="MutedOnPanel.TLabel", anchor="e")
        self.count_label.grid(row=0, column=1, padx=(12, 0))

        self.question_label = ttk.Label(
            labeler,
            text="Look at the image, then choose the button that best matches the dryness level.",
            anchor="center",
            style="Question.TLabel",
        )
        self.question_label.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        zoom_row = ttk.Frame(labeler, style="Panel.TFrame")
        zoom_row.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        ttk.Button(
            zoom_row,
            text="Zoom Out",
            command=lambda: self._change_zoom(1 / self.zoom_step),
        ).pack(side="left", padx=5)

        ttk.Button(
            zoom_row,
            text="Reset Zoom",
            command=self._reset_zoom,
        ).pack(side="left", padx=5)

        ttk.Button(
            zoom_row,
            text="Zoom In",
            command=lambda: self._change_zoom(self.zoom_step),
        ).pack(side="left", padx=5)

        self.zoom_label = ttk.Label(zoom_row, text="Zoom: 100%")
        self.zoom_label.pack(side="left", padx=10)

        image_shell = tk.Frame(labeler, bg=THEME["stage"], highlightthickness=1, highlightbackground=THEME["line"])
        image_shell.grid(row=4, column=0, sticky="nsew")
        image_shell.columnconfigure(0, weight=1)
        image_shell.rowconfigure(0, weight=1)
        self.image_label = tk.Label(image_shell, anchor="center", bg=THEME["stage"], fg="#ffffff")
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", lambda _event: self._show_current_image())

        self.path_label = ttk.Label(labeler, text="", anchor="center", style="Path.TLabel")
        self.path_label.grid(row=5, column=0, sticky="ew", pady=(10, 0))

        buttons = ttk.Frame(labeler, style="Panel.TFrame")
        buttons.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        for col, (key, label, helper, color_key) in enumerate(LABEL_CHOICES):
            buttons.columnconfigure(col, weight=1)
            button = tk.Button(
                buttons,
                text=f"{key}. {label}\n{helper}",
                command=lambda value=label: self._label_current(value),
                bg=THEME[color_key],
                fg="#ffffff",
                activebackground=THEME[color_key],
                activeforeground="#ffffff",
                bd=0,
                padx=10,
                pady=12,
                font=("Segoe UI", 10, "bold"),
                justify="center",
                wraplength=210,
                cursor="hand2",
            )
            button.grid(row=0, column=col, sticky="ew", padx=4)

        keys = ttk.Frame(labeler, style="Panel.TFrame")
        keys.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        keys.columnconfigure(1, weight=1)
        ttk.Button(keys, text="Previous Image", command=self._previous_image).grid(row=0, column=0)
        ttk.Label(
            keys,
            text="Tip: You can use 1-4 on the keyboard. Labels are saved automatically after each click.",
            anchor="center",
            style="MutedOnPanel.TLabel",
        ).grid(row=0, column=1, sticky="ew")
        ttk.Button(keys, text="Skip Image", command=self._next_image).grid(row=0, column=2)

        for key, label, _helper, _color_key in LABEL_CHOICES:
            self.bind(key, lambda _event, value=label: self._label_current(value))
        self.bind("<Left>", lambda _event: self._previous_image())
        self.bind("<Right>", lambda _event: self._next_image())
        self.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.bind("<Control-minus>", lambda _event: self._change_zoom(1 / self.zoom_step))
        self.bind("<Control-equal>", lambda _event: self._change_zoom(self.zoom_step))
        self.bind("<Control-plus>", lambda _event: self._change_zoom(self.zoom_step))
        self.bind("<Control-0>", lambda _event: self._reset_zoom())

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=THEME["bg"])
        style.configure("Panel.TFrame", background=THEME["panel"])
        style.configure("Title.TLabel", background=THEME["bg"], foreground=THEME["ink"], font=("Segoe UI", 20, "bold"))
        style.configure("Section.TLabel", background=THEME["panel"], foreground=THEME["ink"], font=("Segoe UI", 12, "bold"))
        style.configure("Question.TLabel", background=THEME["panel"], foreground=THEME["ink"], font=("Segoe UI", 13, "bold"))
        style.configure("Body.TLabel", background=THEME["panel"], foreground=THEME["ink"], font=("Segoe UI", 10))
        style.configure("MutedOnBg.TLabel", background=THEME["bg"], foreground=THEME["muted"], font=("Segoe UI", 10))
        style.configure("MutedOnPanel.TLabel", background=THEME["panel"], foreground=THEME["muted"], font=("Segoe UI", 10))
        style.configure("Path.TLabel", background=THEME["panel"], foreground=THEME["muted"], font=("Segoe UI", 9))
        style.configure(
            "Step.TLabel",
            background="#e7eeed",
            foreground=THEME["ink"],
            padding=(8, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#e7eeed",
            background=THEME["accent"],
            bordercolor="#e7eeed",
        )

    def _show_help(self):
        messagebox.showinfo(
            "How to use Dryness Image Labeler",
            "1. Click Choose Image ZIP File.\n"
            "2. Select the dataset you want to label.\n"
            "3. Click Start Labeling Selected Dataset.\n"
            "4. For each image, choose the button that best describes the dryness level.\n\n"
            "The app saves after every label. Your CSV and Excel files are saved beside the ZIP file.",
        )

    def _choose_zip(self):
        path = filedialog.askopenfilename(
            title="Choose image ZIP",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self._load_zip(Path(path))

    def _load_zip(self, zip_path):
        try:
            if self.zip_file:
                self.zip_file.close()
            self.zip_file = ZipFile(zip_path)
        except (BadZipFile, OSError) as exc:
            messagebox.showerror("Could not open ZIP", str(exc))
            return

        datasets = {}
        for info in self.zip_file.infolist():
            if info.is_dir():
                continue
            suffix = Path(info.filename).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                continue
            parts = info.filename.replace("\\", "/").split("/")
            dataset = parts[1] if len(parts) >= 2 and parts[0].lower() == "cropped" else parts[0]
            datasets.setdefault(dataset, []).append(ImageItem(dataset=dataset, archive_path=info.filename))

        for entries in datasets.values():
            entries.sort(key=lambda item: item.archive_path.lower())

        self.zip_path = zip_path
        self.datasets = dict(sorted(datasets.items()))
        self.annotations = self._load_existing_annotations()
        self.zip_label.config(text=f"Selected: {zip_path.name}")
        self._populate_dataset_list()
        self._show_picker()

    def _load_existing_annotations(self):
        csv_path = self._output_base().with_suffix(".csv")
        json_path = self._output_base().with_suffix(".json")
        rows = []

        try:
            if csv_path.exists():
                with csv_path.open("r", newline="", encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
            elif json_path.exists():
                with json_path.open("r", encoding="utf-8") as handle:
                    rows = json.load(handle)
        except Exception as exc:
            messagebox.showwarning("Could not load saved labels", str(exc))
            rows = []

        annotations = {}
        for row in rows:
            image_path = row.get("image_path")
            label = row.get("label")
            dataset = row.get("dataset", "")
            labeled_at = row.get("labeled_at", "")
            if image_path and label:
                annotations[image_path] = {
                    "dataset": dataset,
                    "image_name": row.get("image_name", Path(image_path).name),
                    "image_path": image_path,
                    "label": label,
                    "labeled_at": labeled_at,
                }
        return annotations

    def _populate_dataset_list(self):
        self.dataset_list.delete(0, tk.END)
        if not self.datasets:
            self.dataset_list.insert(tk.END, "No images found in this ZIP")
            self.dataset_summary.config(
                text="No supported image files were found.\n\nTry a ZIP that contains PNG, JPG, JPEG, BMP, GIF, or WEBP images."
            )
            return
        for name, items in self.datasets.items():
            labeled = self._labeled_count(items)
            self.dataset_list.insert(tk.END, f"{name}    Progress: {labeled}/{len(items)} labeled")
        self.dataset_list.selection_set(0)
        self._update_dataset_summary()

    def _update_dataset_summary(self):
        if not self.datasets:
            return
        selection = self.dataset_list.curselection()
        if not selection:
            self.dataset_summary.config(text="Select a dataset to see its progress.")
            return
        dataset = list(self.datasets.keys())[selection[0]]
        items = self.datasets[dataset]
        labeled = self._labeled_count(items)
        remaining = len(items) - labeled
        output_base = self._output_base()
        self.dataset_summary.config(
            text=(
                f"{dataset}\n\n"
                f"Total images: {len(items)}\n"
                f"Already labeled: {labeled}\n"
                f"Left to label: {remaining}\n\n"
                "Click Start Labeling Selected Dataset when you are ready.\n\n"
                "Autosave files:\n"
                f"{output_base.with_suffix('.csv').name}\n"
                f"{output_base.with_suffix('.xlsx').name}"
            )
        )

    def _labeled_count(self, items):
        return sum(1 for item in items if item.archive_path in self.annotations)

    def _start_selected_dataset(self):
        if not self.datasets:
            messagebox.showwarning("Choose a ZIP first", "Click Choose Image ZIP File before starting.")
            return
        selection = self.dataset_list.curselection()
        if not selection:
            messagebox.showwarning("Choose a dataset", "Click one dataset in the list, then press Start Labeling.")
            return
        dataset = list(self.datasets.keys())[selection[0]]
        self.items = self.datasets[dataset]
        self.index = self._first_unlabeled_index(dataset)
        self.picker_frame.grid_remove()
        self.labeler_frame.grid()
        self._show_current_image()

    def _first_unlabeled_index(self, dataset):
        for idx, item in enumerate(self.datasets[dataset]):
            if item.archive_path not in self.annotations:
                return idx
        return 0

    def _show_picker(self):
        self._populate_dataset_list()
        self.labeler_frame.grid_remove()
        self.picker_frame.grid()

    def _update_zoom_label(self):
        if hasattr(self, "zoom_label"):
            self.zoom_label.config(text=f"Zoom: {int(self.zoom_factor * 100)}%")

    def _change_zoom(self, factor):
        self.zoom_factor *= factor
        self.zoom_factor = max(self.min_zoom, min(self.max_zoom, self.zoom_factor))
        self._update_zoom_label()
        self._show_current_image()

    def _reset_zoom(self):
        self.zoom_factor = 1.0
        self._update_zoom_label()
        self._show_current_image()

    def _on_zoom_wheel(self, event):
        if event.delta > 0:
            self._change_zoom(self.zoom_step)
        else:
            self._change_zoom(1 / self.zoom_step)

    def _show_current_image(self):
        if not self.items or not self.zip_file:
            return
        item = self.items[self.index]
        try:
            data = self.zip_file.read(item.archive_path)
            self.current_photo = self._make_photo(data)
        except Exception as exc:
            self.current_photo = None
            self.image_label.config(text=f"Could not load image:\n{exc}", image="")
            return

        if self.current_photo is None:
            self.image_label.config(
                image="",
                text="Image preview requires Pillow for zoom support.",
            )
        else:
            self.image_label.config(image=self.current_photo, text="")
        label = self.annotations.get(item.archive_path, {}).get("label", "unlabeled")
        label_text = f"Already labeled: {label}" if label != "unlabeled" else "Not labeled yet"
        self.status_label.config(text=f"Step 3: Image {self.index + 1} of {len(self.items)}")
        percent = ((self.index + 1) / len(self.items)) * 100
        labeled = self._labeled_count(self.items)
        self.progress_var.set(percent)
        self.count_label.config(text=f"{label_text} | {labeled}/{len(self.items)} saved")
        self.path_label.config(text=item.archive_path)

    def _make_photo(self, data):
        width = max(self.image_label.winfo_width() - 20, 200)
        height = max(self.image_label.winfo_height() - 20, 200)

        if Image and ImageTk:
            image = Image.open(io.BytesIO(data))

            original_w, original_h = image.size

            base_scale = min(width / original_w, height / original_h)
            scale = base_scale * self.zoom_factor

            new_w = max(1, int(original_w * scale))
            new_h = max(1, int(original_h * scale))

            image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

            return ImageTk.PhotoImage(image)

        return None

    def _label_current(self, label):
        if not self.items:
            return
        item = self.items[self.index]
        self.annotations[item.archive_path] = {
            "dataset": item.dataset,
            "image_name": Path(item.archive_path).name,
            "image_path": item.archive_path,
            "label": label,
            "labeled_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_annotations(silent=True)
        self._next_image()

    def _previous_image(self):
        if self.items and self.index > 0:
            self.index -= 1
            self._show_current_image()

    def _next_image(self):
        if not self.items:
            return
        if self.index < len(self.items) - 1:
            self.index += 1
            self._show_current_image()
            return
        self._save_annotations()
        messagebox.showinfo(
            "Dataset finished",
            "You reached the last image in this dataset.\n\nYour CSV and Excel files have been saved.",
        )
        self._show_picker()

    def _output_base(self):
        if not self.zip_path:
            return Path.cwd() / "dryness_labels"
        return self.zip_path.with_name(f"{self.zip_path.stem}_dryness_labels")

    def _save_annotations(self, silent=False):
        if not self.zip_path:
            if not silent:
                messagebox.showwarning("Choose a ZIP first", "Open an image ZIP before saving labels.")
            return
        if not self.annotations:
            if not silent:
                messagebox.showwarning(
                    "No labels yet",
                    "Label at least one image first. The app will then create CSV and Excel files automatically.",
                )
            return
        csv_path = self._output_base().with_suffix(".csv")
        json_path = self._output_base().with_suffix(".json")
        xlsx_path = self._output_base().with_suffix(".xlsx")
        rows = self._export_rows()

        try:
            self._write_csv(csv_path, rows)
            self._write_json(json_path, rows)
            self._write_xlsx(xlsx_path, rows)
        except OSError as exc:
            messagebox.showerror(
                "Could not save labels",
                f"Close the CSV or Excel file if it is open, then click Save Now.\n\n{exc}",
            )
            return

        if not silent:
            messagebox.showinfo(
                "Saved",
                f"Saved {len(rows)} labeled images to:\n{csv_path}\n{xlsx_path}\n{json_path}",
            )

    def _export_rows(self):
        rows = []
        for row in self.annotations.values():
            image_path = row["image_path"]
            rows.append(
                {
                    "dataset": row.get("dataset", ""),
                    "image_name": row.get("image_name", Path(image_path).name),
                    "image_path": image_path,
                    "label": row.get("label", ""),
                    "labeled_at": row.get("labeled_at", ""),
                }
            )
        return sorted(rows, key=lambda row: (row["dataset"], row["image_path"]))

    def _write_csv(self, csv_path, rows):
        fieldnames = ["dataset", "image_name", "image_path", "label", "labeled_at"]
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_json(self, json_path, rows):
        with json_path.open("w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2)

    def _write_xlsx(self, xlsx_path, rows):
        headers = ["dataset", "image_name", "image_path", "label", "labeled_at"]
        sheet_rows = [headers] + [[row.get(header, "") for header in headers] for row in rows]
        worksheet_xml = self._worksheet_xml(sheet_rows)

        with ZipFile(xlsx_path, "w", ZIP_DEFLATED) as workbook:
            workbook.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
            )
            workbook.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
            )
            workbook.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Dryness Labels" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
            )
            workbook.writestr(
                "xl/_rels/workbook.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
            )
            workbook.writestr(
                "xl/styles.xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellXfs>
</styleSheet>""",
            )
            workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)

    def _worksheet_xml(self, rows):
        column_widths = [24, 34, 90, 24, 24]
        cols = "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(column_widths, start=1)
        )
        row_xml = []
        for row_number, row in enumerate(rows, start=1):
            cells = []
            for column_number, value in enumerate(row, start=1):
                cell_reference = f"{self._excel_column(column_number)}{row_number}"
                cells.append(
                    f'<c r="{cell_reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                )
            row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<cols>{cols}</cols>"
            f"<sheetData>{''.join(row_xml)}</sheetData>"
            "</worksheet>"
        )

    def _excel_column(self, number):
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    def _on_close(self):
        try:
            self._save_annotations(silent=True)
        finally:
            if self.zip_file:
                self.zip_file.close()
            self.temp_dir.cleanup()
            self.destroy()


def main():
    default_zip = None
    if len(sys.argv) > 1:
        default_zip = sys.argv[1]
    else:
        candidate = Path.home() / "Downloads" / "Cropped-20260610T042233Z-3-001.zip"
        if candidate.exists():
            default_zip = candidate

    app = WhiteSpotLabeler(default_zip=default_zip)
    app.mainloop()


if __name__ == "__main__":
    main()
