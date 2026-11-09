from pathlib import Path

from po_pipeline.ocr import _lines_to_rows
from po_pipeline.text_extract import extract_text

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "synthetic"


def line(text, top, left):
    return {"BlockType": "LINE", "Text": text,
            "Geometry": {"BoundingBox": {"Top": top, "Left": left}}}


class FakeTextract:
    """Stands in for the real Textract client: no AWS call, no cost, predictable answer."""

    def __init__(self):
        self.calls = 0

    def detect_document_text(self, Document):
        self.calls += 1
        return {"Blocks": [
            line("PURCHASE ORDER", 0.05, 0.07),
            line("PO Number: CM-2026-0457", 0.09, 0.07),
            line("MR-9001", 0.30, 0.15),          # same row, three separate LINE blocks
            line("Portable Eyewash Station", 0.302, 0.25),
            line("17", 0.301, 0.60),
        ]}


def test_rows_are_rebuilt_left_to_right():
    blocks = FakeTextract().detect_document_text(Document={})["Blocks"]
    text = _lines_to_rows(blocks)
    assert "MR-9001   Portable Eyewash Station   17" in text.splitlines()


def test_scanned_pdf_uses_ocr_once():
    fake = FakeTextract()
    result = extract_text(SAMPLES / "PO-1003_contoso_scanned.pdf", textract=fake)
    assert fake.calls == 1
    assert result.method == "ocr"
    assert "CM-2026-0457" in result.text


def test_digital_pdf_never_calls_textract():
    fake = FakeTextract()
    result = extract_text(SAMPLES / "PO-1001_acme_native.pdf", textract=fake)
    assert fake.calls == 0          # proves we don't pay for OCR we don't need
    assert result.method == "pdf_text"
