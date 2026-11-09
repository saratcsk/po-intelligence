"""Steps B1 + B2: get the text out of a purchase order (PDF, scan or image)."""

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from po_pipeline.ocr import ocr_document_bytes

# If a page has fewer characters than this, it is probably a scan or fax
# (a picture of text with no real text inside), so it will need OCR (step B2).
MIN_CHARS_PER_PAGE = 50


@dataclass
class ExtractedText:
    text: str        # all pages joined, with "--- page N ---" markers
    pages: int       # number of pages in the PDF
    method: str      # how we got the text: "pdf_text" or "ocr"
    needs_ocr: bool  # True when the PDF has (almost) no real text


def _clean(raw: str | None) -> str:
    """Drop blank lines and trailing spaces that layout mode adds."""
    lines = [line.rstrip() for line in (raw or "").splitlines()]
    return "\n".join(line for line in lines if line.strip())


def extract_pdf_text(path: str | Path) -> ExtractedText:
    reader = PdfReader(str(path))
    page_texts = [_clean(page.extract_text(extraction_mode="layout")) for page in reader.pages]

    text = "\n\n".join(
        f"--- page {number} ---\n{page_text}"
        for number, page_text in enumerate(page_texts, start=1)
    )
    average_chars = sum(len(t) for t in page_texts) / max(len(page_texts), 1)

    return ExtractedText(
        text=text,
        pages=len(page_texts),
        method="pdf_text",
        needs_ocr=average_chars < MIN_CHARS_PER_PAGE,
    )


IMAGE_TYPES = {".png", ".jpg", ".jpeg"}


def extract_text(path: str | Path, textract=None) -> ExtractedText:
    """Free PDF text first; pay for OCR only when the file is a scan or an image."""
    path = Path(path)

    if path.suffix.lower() in IMAGE_TYPES:
        text = ocr_document_bytes(path.read_bytes(), textract)
        return ExtractedText(text=f"--- page 1 ---\n{text}", pages=1, method="ocr", needs_ocr=False)

    result = extract_pdf_text(path)
    if not result.needs_ocr:
        return result  # digital PDF: no AWS call, no cost

    if result.pages > 1:
        raise NotImplementedError("Multi-page scanned PDFs need asynchronous Textract (added in B6).")

    text = ocr_document_bytes(path.read_bytes(), textract)
    return ExtractedText(text=f"--- page 1 ---\n{text}", pages=1, method="ocr", needs_ocr=False)


if __name__ == "__main__":
    import sys

    result = extract_text(sys.argv[1])
    print(f"pages={result.pages}  method={result.method}  needs_ocr={result.needs_ocr}")
    print(result.text)