"""
app_gui.py - desktop front-end (PyInstaller entry point).

v2.4 note (middle-path speed update): extraction now runs on a THREAD pool
(see legal_pipeline/pipeline.py). Threads run inside this same process, so —
unlike a process pool — they do NOT relaunch the frozen .exe and therefore do
NOT spawn extra console windows. Consequently NO multiprocessing.freeze_support()
call is needed or wanted here; this entry point is intentionally unchanged from
the threading perspective. OCR still runs at full 300 DPI for accuracy; only the
per-document OCR page cap (config: Ingestion.ocr_max_pages) and thread count
(config: Ingestion.extract_threads) affect speed.

v2.3 fix (2026-08-16): OCR checkbox uses a classic tk.Checkbutton instead of
ttk.Checkbutton to avoid the low-res "clam" indicator that can look like an "X"
at high DPI on Tk 8.6.x.

v2.2 fix (2026-08-16): Force the "clam" ttk theme unconditionally so the Start
button honours custom fg/bg (the native "vista" theme ignored the background,
making white-on-light-grey text invisible).

v2.1 fix: DPI awareness declared before Tk window creation; plain-ASCII button
labels (no emoji glyphs that can fail to render inside a frozen .exe).

v2.0 - modernised UI (card-based layout, accurate multi-phase progress bar,
threaded pipeline run).

v1.3 fix (kept): pipeline.run() is called with the CORRECT keyword names
(log=, progress=); _progress()/_apply_progress() accept the 4th positional
argument (filename) that pipeline.py passes on every call:
progress(phase, i, total, name).
"""
from __future__ import annotations
import datetime as _dt
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------
# DPI awareness MUST be set before any Tk window is created.
# --------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from legal_pipeline.config import Config
from legal_pipeline import runtime_paths
from legal_pipeline.pipeline import run as run_pipeline

APP_TITLE = "Document Migration & Deduplication"
APP_SUBTITLE = "Created By Naman Sharma"

# ---------------------------------------------------------------- Palette --
COLOR_BG = "#F4F6F9"
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#E2E6EC"
COLOR_HEADER_BG = "#12233F"
COLOR_HEADER_TEXT = "#FFFFFF"
COLOR_HEADER_SUB = "#B7C4D9"
COLOR_ACCENT = "#2563EB"
COLOR_ACCENT_DARK = "#1D4ED8"
COLOR_TEXT = "#1F2937"
COLOR_MUTED = "#5B6472"
COLOR_LOG_BG = "#0F1720"
COLOR_LOG_TEXT = "#D6E2EE"

STRICTEST_SIMILARITY = 0.98

PHASE_META = {
    "scan":     "Scanning folder",
    "extract":  "Reading documents",
    "enrich":   "Analysing documents",
    "dedupe":   "Finding duplicates",
    "organise": "Organising files",
    "tracker":  "Building the tracker",
    "done":     "Finished",
}
PROGRESS_BANDS = {
    "scan":     (0, 3),
    "extract":  (3, 45),
    "enrich":   (45, 80),
    "dedupe":   (80, 83),
    "organise": (83, 97),
    "tracker":  (97, 99),
    "done":     (99, 100),
}


def compute_progress_percent(phase: str, current: int, total: int, last_pct: float = 0.0) -> float:
    """Pure function: maps a (phase, current, total) progress event to an
    overall 0-100 percentage. No Tk/UI dependency."""
    if phase not in PROGRESS_BANDS:
        return last_pct
    lo, hi = PROGRESS_BANDS[phase]
    if phase == "done":
        return 100.0
    if total and total > 0:
        frac = max(0.0, min(1.0, current / total))
        return lo + (hi - lo) * frac
    return float(hi)


def compute_eta_seconds(elapsed_seconds: float, pct: Optional[float]) -> Optional[float]:
    """Pure function: estimates remaining seconds from elapsed time and current
    completion percentage, assuming roughly constant throughput."""
    if pct is None or pct < 2.0 or pct >= 100.0 or elapsed_seconds <= 0:
        return None
    total_estimated = elapsed_seconds * (100.0 / pct)
    remaining = total_estimated - elapsed_seconds
    return max(0.0, remaining)


def format_eta(seconds: Optional[float]) -> str:
    """Pure function: formats an ETA in seconds (or None) into a short,
    human-readable string. No Tk/UI dependency - independently testable."""
    if seconds is None:
        return "Estimating time remaining..."
    if seconds < 1:
        return "Almost done..."
    total = int(round(seconds))
    m, s = divmod(total, 60)
    if m == 0:
        return f"About {s}s remaining"
    return f"About {m}m {s:02d}s remaining"


def format_elapsed(seconds: float) -> str:
    """Pure function: formats a completed elapsed duration for the
    'Completed in ...' message shown once the run finishes."""
    total = max(0, int(round(seconds)))
    m, s = divmod(total, 60)
    if m == 0:
        return f"{s}s"
    return f"{m}m {s:02d}s"


def desktop_dir() -> Path:
    home = Path(os.path.expanduser("~"))
    for c in [home / "Desktop", home / "OneDrive" / "Desktop"]:
        if c.exists():
            return c
    if os.name == "nt":
        for p in home.glob("OneDrive*/Desktop"):
            return p
    return home


def open_folder(path: Path):
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x720")
        self.minsize(760, 640)
        self.configure(background=COLOR_BG)

        # Force "clam" so custom button colours are honoured (see v2.2 note).
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        self._source_dir: Optional[Path] = None
        self._worker: Optional[threading.Thread] = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._enable_ocr = tk.BooleanVar(value=True)
        self._last_pct = 0.0
        self._run_start = 0.0
        self._last_output_dir: Optional[Path] = None

        self._build_ui()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        header = tk.Frame(self, background=COLOR_HEADER_BG, height=88)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text=APP_TITLE, background=COLOR_HEADER_BG,
                 foreground=COLOR_HEADER_TEXT, font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=24, pady=(18, 0))
        tk.Label(header, text=APP_SUBTITLE, background=COLOR_HEADER_BG,
                 foreground=COLOR_HEADER_SUB, font=("Segoe UI", 10)).pack(
            anchor="w", padx=24)

        body = tk.Frame(self, background=COLOR_BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # --- Source card ---
        card = tk.Frame(body, background=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 14))
        tk.Label(card, text="1. Choose the folder of PDFs", background=COLOR_CARD,
                 foreground=COLOR_TEXT, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=16, pady=(14, 6))
        row = tk.Frame(card, background=COLOR_CARD)
        row.pack(fill="x", padx=16, pady=(0, 14))
        self._path_var = tk.StringVar(value="No folder selected")
        tk.Entry(row, textvariable=self._path_var, state="readonly",
                 readonlybackground="#FFFFFF", relief="solid", bd=1).pack(
            side="left", fill="x", expand=True, ipady=4)
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left", padx=(10, 0))

        # --- Options card ---
        card2 = tk.Frame(body, background=COLOR_CARD, highlightbackground=COLOR_BORDER,
                         highlightthickness=1)
        card2.pack(fill="x", pady=(0, 14))
        tk.Label(card2, text="2. Options", background=COLOR_CARD, foreground=COLOR_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        tk.Checkbutton(card2, text="Read scanned PDFs with OCR (recommended)",
                       variable=self._enable_ocr, background=COLOR_CARD,
                       foreground=COLOR_TEXT, activebackground=COLOR_CARD,
                       font=("Segoe UI", 10)).pack(anchor="w", padx=16, pady=(0, 14))

        # --- Action row ---
        action = tk.Frame(body, background=COLOR_BG)
        action.pack(fill="x", pady=(0, 12))
        self._start_btn = tk.Button(action, text="Start", command=self._start,
                                    background=COLOR_ACCENT, foreground="#FFFFFF",
                                    activebackground=COLOR_ACCENT_DARK,
                                    activeforeground="#FFFFFF", relief="flat",
                                    font=("Segoe UI", 12, "bold"), padx=26, pady=10,
                                    cursor="hand2")
        self._start_btn.pack(side="left")
        self._open_btn = tk.Button(action, text="Open results", command=self._open_results,
                                   state="disabled", relief="flat", padx=18, pady=10,
                                   font=("Segoe UI", 11))
        self._open_btn.pack(side="left", padx=(10, 0))

        # --- Progress ---
        prog = tk.Frame(body, background=COLOR_BG)
        prog.pack(fill="x", pady=(0, 4))
        self._phase_var = tk.StringVar(value="Ready.")
        tk.Label(prog, textvariable=self._phase_var, background=COLOR_BG,
                 foreground=COLOR_TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._bar = ttk.Progressbar(prog, mode="determinate", maximum=100.0)
        self._bar.pack(fill="x", pady=(4, 2))
        self._eta_var = tk.StringVar(value="")
        tk.Label(prog, textvariable=self._eta_var, background=COLOR_BG,
                 foreground=COLOR_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        # --- Log ---
        logf = tk.Frame(body, background=COLOR_BG)
        logf.pack(fill="both", expand=True, pady=(8, 0))
        self._log = tk.Text(logf, background=COLOR_LOG_BG, foreground=COLOR_LOG_TEXT,
                            relief="flat", height=12, wrap="word", font=("Consolas", 9))
        self._log.pack(fill="both", expand=True)
        self._log.configure(state="disabled")

    # ------------------------------------------------------------- actions --
    def _browse(self):
        d = filedialog.askdirectory(title="Select the folder containing your PDFs")
        if d:
            self._source_dir = Path(d)
            self._path_var.set(d)

    def _log_line(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _start(self):
        if self._worker and self._worker.is_alive():
            return
        if not self._source_dir or not self._source_dir.exists():
            messagebox.showwarning("No folder", "Please choose a folder of PDFs first.")
            return
        self._start_btn.configure(state="disabled")
        self._open_btn.configure(state="disabled")
        self._last_pct = 0.0
        self._run_start = time.time()
        self._bar.configure(value=0)
        self._phase_var.set("Starting...")
        self._eta_var.set("")

        out_dir = desktop_dir() / f"LegalDocMigration_{_dt.datetime.now():%Y-%m-%d_%H%M%S}"
        self._last_output_dir = out_dir

        cfg = Config()
        cfg.paths.source_dir = self._source_dir
        cfg.paths.output_dir = out_dir
        cfg.ingestion.enable_ocr = bool(self._enable_ocr.get())
        cfg.dedup.near_dup_similarity = STRICTEST_SIMILARITY
        # Locate bundled OCR binaries (no-op if OCR disabled / not bundled).
        try:
            runtime_paths.apply_to_config(cfg)
        except Exception:
            pass

        def _log(*args):
            msg = " ".join(str(a) for a in args)
            self._queue.put(("log", msg))

        def _progress(phase, current, total, name=""):
            self._queue.put(("progress", (phase, current, total, name)))

        def _worker_main():
            try:
                summary = run_pipeline(cfg, copy_files=True, log=_log, progress=_progress)
                self._queue.put(("done", summary))
            except Exception as e:
                self._queue.put(("error", f"{e}\n{traceback.format_exc()}"))

        self._worker = threading.Thread(target=_worker_main, daemon=True)
        self._worker.start()

    def _apply_progress(self, phase, current, total, name=""):
        pct = compute_progress_percent(phase, current, total, self._last_pct)
        self._last_pct = pct
        self._bar.configure(value=pct)
        label = PHASE_META.get(phase, phase)
        if name:
            label = f"{label} — {name}"
        self._phase_var.set(label)
        eta = compute_eta_seconds(time.time() - self._run_start, pct)
        self._eta_var.set(format_eta(eta))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log_line(payload)
                elif kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _on_done(self, summary):
        self._bar.configure(value=100)
        self._phase_var.set("Finished.")
        self._eta_var.set(f"Completed in {format_elapsed(time.time() - self._run_start)}")
        self._start_btn.configure(state="normal")
        self._open_btn.configure(state="normal")
        try:
            if self._last_output_dir:
                open_folder(self._last_output_dir)
        except Exception:
            pass

    def _on_error(self, msg):
        self._phase_var.set("Something went wrong.")
        self._log_line("[ERROR] " + msg)
        self._start_btn.configure(state="normal")
        messagebox.showerror("Error", "The run did not finish. See the log for details.")

    def _open_results(self):
        if self._last_output_dir:
            open_folder(self._last_output_dir)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
