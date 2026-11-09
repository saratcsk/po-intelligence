from pathlib import Path

from po_pipeline.text_extract import extract_pdf_text

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "synthetic"


def test_native_pdf_returns_text():
    result = extract_pdf_text(SAMPLES / "PO-1001_acme_native.pdf")
    assert result.pages == 1
    assert result.needs_ocr is False
    assert "4500012345" in result.text  # the PO number
    assert "MR-" in result.text          # part numbers


def test_table_rows_stay_on_one_line():
    result = extract_pdf_text(SAMPLES / "PO-1001_acme_native.pdf")
    row = next(line for line in result.text.splitlines() if "Portable Eyewash Station" in line)
    assert "MR-9001" in row and "EA" in row  # part, description and UOM together


def test_multipage_pdf_reads_every_page():
    result = extract_pdf_text(SAMPLES / "PO-1002_northwind_multipage.pdf")
    assert result.pages == 2
    assert "--- page 2 ---" in result.text
    assert "NW-778812" in result.text


def test_scanned_pdf_is_flagged_for_ocr():
    result = extract_pdf_text(SAMPLES / "PO-1003_contoso_scanned.pdf")
    assert result.needs_ocr is True
