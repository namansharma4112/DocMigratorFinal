# Legal Document Migration & Deduplication Tool

A Windows desktop app that scans a folder of legal PDFs (native or scanned),
classifies each one (Contracts / Engagement Letters / Addendums / NDA / SOW /
Others), extracts key metadata, detects exact & near-duplicate documents, and
produces a ready-to-send migration package with an Excel tracker.

---

## What's new in this build (v1.4.0 — middle-path speed update)

This release adds **safe, accuracy-first speed** after a previous experimental
build regressed. The two problems from that build and how they are addressed:

| Previous problem | Root cause | This build |
|---|---|---|
| Multiple command-prompt windows / machine stalling | **Multiprocessing** relaunched the packaged `.exe` per worker | **Removed.** Extraction now uses a **thread pool** (same process → no window spawning, no `freeze_support`, no `app_gui.py` change). |
| Work Orders misfiled into Contracts / _REVIEW | **OCR DPI dropped to 200**, degrading messy scans | **Reverted to 300 DPI.** No accuracy compromise. |

### The two speed levers

1. **OCR page cap (proven, hardware-independent).** OCR only the first
   `Ingestion.ocr_max_pages` pages of each scanned document (default **15**).
   Classification reads page 1 and metadata reads pages 1–2, so trimming the
   tail of long scans removes pure wasted work. On 8-page scans this measured
   ~4.6x faster with **identical** classification; the win grows with page
   count. Set to `None` to OCR every page like before.

2. **Threaded extraction (correctness-proven; validate speed on your machine).**
   `Ingestion.extract_threads` (default **4**). Threads never relaunch the
   `.exe`, and each OCR job is pinned to one core (`OMP_THREAD_LIMIT=1`) so
   concurrent files don't fight over cores. Threaded output is byte-identical
   to serial (verified by an automated test). **Benchmark `--threads 4` vs
   `--threads 1` on a small sample; if it doesn't help on your hardware, set
   threads to 1 — the page-cap win still applies.**

Both levers live in `legal_pipeline/config.py → Ingestion`. **300 DPI, the full
pdfplumber fallback, and single-process safety are all preserved.**

> ⚠️ **After upgrading, delete any `extracted_text.cache.jsonl`** in previous
> output folders (or run into a fresh output folder). The cache refreshes only
> on file size/time changes, so a stale cache could otherwise reuse old text.

---

## Reliability — tested, not assumed

Automated tests run before every build, including:

- Full end-to-end pipeline on a 12-file fixture covering every code path
  (native, scanned/OCR, exact/near duplicates, corrupt, empty, tiny).
- **Threaded == serial** equivalence (all output counts identical).
- **OCR page cap preserves classification** vs uncapped.
- **Work Order regression guards** (a clear "WORK ORDER" heading must classify
  as `Work Order` / HIGH, never `Contracts`).
- Duplicate detection correctness (exact-file, exact-text, near-duplicate;
  different-client boilerplate must NOT merge).
- Corrupt/unreadable files never crash the run.
- Progress-bar / ETA pure-function math.

**OCR-toolchain note for CI:** the one test that requires a real Tesseract +
Poppler install (`test_scanned_pdfs_are_independently_ocr_processed`) **skips
itself automatically** when those binaries aren't available on the runner, so a
missing OCR engine never fails the build. OCR-independent behaviour (exact-file
dedupe, native-text classification, etc.) is always tested.

---

## For the end user (non-technical)

1. Download `LegalDocMigration.exe` (see **Getting the app** below).
2. Double-click it to launch.
3. Click **Browse…**, select the folder containing the PDFs, click **Start**.
4. Watch the progress bar + live ETA. Results open automatically on your Desktop
   when finished.

---

## Getting the app

Go to the **Actions** tab → open the latest successful
**"Build Windows EXE (with OCR bundled)"** run → download the
**LegalDocMigration-Windows** artifact. Or push a version tag (e.g. `v1.4.0`)
for a permanent Release download.

---

## Repository layout

```
legal_pipeline/
├── config.py, extract.py, ocr_support.py, runtime_paths.py
├── classify.py, metadata.py, dedupe.py, tracker.py
└── pipeline.py            (thread-pool extraction lives here)
app_gui.py                  (PyInstaller entry point; NO multiprocessing)
tests/
├── make_test_pdfs.py            — small curated fixture set (12 files)
├── make_large_fixture_set.py    — large synthetic corpus generator
├── test_pipeline.py             — integration/unit tests
└── test_progress_math.py        — progress-bar/ETA pure-function tests
legal_migration.spec, build_all.bat, get_ocr_helpers.bat
.github/workflows/build.yml
```

---

## Building it yourself

1. Install Python 3.11+.
2. Run `get_ocr_helpers.bat` once (downloads Tesseract + Poppler into `vendor\`).
3. Run `build_all.bat` (installs deps, runs tests, builds).
4. App is at `dist\LegalDocMigration\LegalDocMigration.exe` — copy the **entire**
   folder to share it.

### Running the test suite
```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -v
```

### Command-line usage (for benchmarking / automation)
```
python -m legal_pipeline.pipeline --source <folder> --output <folder> [options]
  --threads N        extraction threads (default 4; use 1 for serial)
  --no-ocr           flag scanned files for review instead of reading them
  --no-copy          analyse only; don't copy files
  --similarity F     near-duplicate cosine threshold
```

---

## Configuration reference

| Setting | Default | Purpose |
|---|---|---|
| `Ingestion.enable_ocr` | `True` | Toggle OCR for scanned PDFs |
| `Ingestion.ocr_dpi` | `300` | OCR render resolution (kept at 300 for accuracy) |
| `Ingestion.ocr_max_pages` | `15` | Max pages OCR'd per scanned doc (`None` = all) |
| `Ingestion.extract_threads` | `4` | Extraction thread count (`1` = serial) |
| `Dedup.near_dup_similarity` | `0.90` | TF-IDF cosine threshold (**GUI uses 0.98**) |
| `Dedup.block_by_type` | `True` | Only compare same-type docs for near-duplicates |
| `Dedup.require_entity_match / require_date_compatible` | `True` | Guards against false merges |

---

## Known limitations

- Near-duplicate matching is single-pass, order-dependent greedy clustering
  (fast, deterministic, not globally optimal).
- Entity/date/sector extraction is heuristic (regex + keyword based) — spot-check
  low-confidence tracker rows.
- The threaded speedup is hardware-dependent; validate on a sample before large
  runs (the page-cap speedup is not hardware-dependent).
