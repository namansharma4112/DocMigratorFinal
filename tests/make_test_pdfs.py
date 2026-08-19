"""Generates a stress-test corpus of PDFs covering every code path in the
pipeline: native text, scanned/image-only (forces OCR), exact duplicates,
renamed duplicates, near-duplicates, a corrupt file, an empty PDF, and one
very short PDF.

CROSS-PLATFORM NOTE #1 (font dependency — fixed): the scanned-PDF generator
renders via reportlab's built-in Helvetica font (embedded metrics, no external
font file on any OS) and rasterizes with PyMuPDF, so OCR text quality no longer
depends on a Linux-only system font path.

CROSS-PLATFORM NOTE #2 (OCR non-determinism — fixed): file #08 is a byte-for-
byte COPY of #07 (like #02 copies #01), so the duplicate match happens via the
exact_file hash tier — evaluated on raw bytes BEFORE any OCR runs — making it
deterministic on every platform regardless of Tesseract build. Both files are
still independently OCR'd by the pipeline, so the real OCR path stays exercised;
only the dedupe MATCH no longer depends on OCR fidelity.
"""
import io
import shutil
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import fitz  # PyMuPDF

OUT = Path(__file__).resolve().parent / "test_pdfs"


def make_native_pdf(path: Path, lines):
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
        if y < 50:
            c.showPage()
            y = 800
    c.save()


def make_scanned_pdf(path: Path, lines, dpi: int = 200):
    """Render `lines` via reportlab's built-in Helvetica font, rasterize to a
    PNG with PyMuPDF, then embed ONLY the PNG into a fresh PDF (no text layer)
    to force the OCR fallback identically on every platform."""
    text_buf = io.BytesIO()
    c = canvas.Canvas(text_buf, pagesize=A4)
    c.setFont("Helvetica", 14)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 22
    c.save()
    text_buf.seek(0)
    src_doc = fitz.open(stream=text_buf.read(), filetype="pdf")
    page = src_doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    png_bytes = pix.tobytes("png")
    src_doc.close()
    img = ImageReader(io.BytesIO(png_bytes))
    out_c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    out_c.drawImage(img, 0, 0, width=width, height=height)
    out_c.save()


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1) Clear-cut CONTRACT, entity A, native text
    make_native_pdf(OUT / "01_contract_acme.pdf", [
        "SERVICES AGREEMENT",
        "",
        "This Agreement is made between Acme Holdings LLC and Ardent Advisory Ltd.",
        "Dated: 12 January 2024",
        "",
        "1. Scope of Services",
        "The consultant shall provide advisory services as described in Schedule A.",
        "This agreement is made in consideration of the fees set out herein.",
        "In witness whereof the parties have executed this agreement.",
    ])
    # 2) EXACT duplicate of #1 (byte-identical, renamed)
    shutil.copy(OUT / "01_contract_acme.pdf", OUT / "02_contract_acme_COPY.pdf")
    # 3) NEAR duplicate of #1 - same client, trivial wording tweak (native text)
    make_native_pdf(OUT / "03_contract_acme_v2.pdf", [
        "SERVICES AGREEMENT",
        "",
        "This Agreement is made between Acme Holdings LLC and Ardent Advisory Ltd.",
        "Dated: 12 January 2024",
        "",
        "1. Scope of Services",
        "The consultant will provide advisory services as described in Schedule A.",
        "This agreement is made in consideration of the fees set out herein.",
        "In witness whereof the parties have executed this agreement.",
    ])
    # 4) Similar CONTRACT boilerplate but DIFFERENT client - must NOT merge
    make_native_pdf(OUT / "04_contract_globex.pdf", [
        "SERVICES AGREEMENT",
        "",
        "This Agreement is made between Globex Trading FZE and Ardent Advisory Ltd.",
        "Dated: 03 March 2024",
        "",
        "1. Scope of Services",
        "The consultant shall provide advisory services as described in Schedule A.",
        "This agreement is made in consideration of the fees set out herein.",
        "In witness whereof the parties have executed this agreement.",
    ])
    # 5) ENGAGEMENT LETTER, distinct type
    make_native_pdf(OUT / "05_engagement_letter_beta.pdf", [
        "ENGAGEMENT LETTER",
        "",
        "Client: Beta Financial Services PJSC",
        "Dated: 20 February 2024",
        "",
        "We are pleased to confirm the terms of engagement between our firm and",
        "Beta Financial Services PJSC for the provision of advisory services.",
        "This letter sets out the scope of our services and fee arrangement.",
    ])
    # 6) ADDENDUM, distinct type
    make_native_pdf(OUT / "06_addendum_gamma.pdf", [
        "ADDENDUM NO. 1",
        "",
        "This Amendment amends the original agreement between Gamma Insurance",
        "Company and Ardent Advisory Ltd, dated 5 May 2023.",
        "This deed of variation modifies clause 4.2 of the original agreement.",
    ])
    # 7) SCANNED (image-only) version of a CONTRACT - forces OCR path
    make_scanned_pdf(OUT / "07_scanned_contract_delta.pdf", [
        "SERVICES AGREEMENT",
        "",
        "This Agreement is made between Delta Energy LLC and Ardent Advisory.",
        "Dated: 15 June 2024",
        "The consultant shall provide advisory services under this contract.",
        "In witness whereof the parties have executed this agreement.",
    ])
    # 8) EXACT byte-for-byte COPY of #7 (see NOTE #2) - matched via exact_file
    shutil.copy(OUT / "07_scanned_contract_delta.pdf", OUT / "08_scanned_contract_delta_rescan.pdf")
    # 9) OTHER / unclassifiable - no legal keywords at all
    make_native_pdf(OUT / "09_random_memo.pdf", [
        "Weekly Team Standup Notes",
        "",
        "Attendees: John, Priya, Wei.",
        "Discussed sprint velocity and upcoming holidays.",
        "No blockers reported this week.",
    ])
    # 10) Very short native text (below min_native_chars, NOT scanned - edge case)
    make_native_pdf(OUT / "10_tiny_stub.pdf", ["Hi"])
    # 11) Zero-byte / corrupt file (should fail extraction gracefully)
    (OUT / "11_corrupt.pdf").write_bytes(b"")
    # 12) Genuinely empty (blank page) native PDF
    make_native_pdf(OUT / "12_blank_page.pdf", [])
    print(f"Created {len(list(OUT.glob('*.pdf')))} test PDFs in {OUT}")


if __name__ == "__main__":
    main()
