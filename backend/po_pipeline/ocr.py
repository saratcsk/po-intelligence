"""Step B2: read scanned / faxed POs with Amazon Textract (OCR)."""

import boto3

# Textract gives each text line a position on the page (0 = top, 1 = bottom).
# Lines whose tops are closer than this are treated as the same table row.
SAME_ROW_TOLERANCE = 0.006


def _lines_to_rows(blocks: list[dict]) -> str:
    """Rebuild table rows: group LINE blocks that sit at the same height, left to right."""
    lines = [b for b in blocks if b["BlockType"] == "LINE"]
    lines.sort(key=lambda b: (b["Geometry"]["BoundingBox"]["Top"], b["Geometry"]["BoundingBox"]["Left"]))

    rows: list[list[dict]] = []
    for line in lines:
        top = line["Geometry"]["BoundingBox"]["Top"]
        if rows and abs(rows[-1][0]["Geometry"]["BoundingBox"]["Top"] - top) <= SAME_ROW_TOLERANCE:
            rows[-1].append(line)
        else:
            rows.append([line])

    return "\n".join(
        "   ".join(b["Text"] for b in sorted(row, key=lambda b: b["Geometry"]["BoundingBox"]["Left"]))
        for row in rows
    )


def ocr_document_bytes(data: bytes, textract=None) -> str:
    """OCR one page (PNG, JPEG or single-page PDF, up to 10 MB). Costs about $0.0015 per page."""
    textract = textract or boto3.client("textract")
    response = textract.detect_document_text(Document={"Bytes": data})
    return _lines_to_rows(response["Blocks"])