"""Generate synthetic (made-up) purchase order PDFs for testing.

All companies, people, part numbers and addresses are fictional.
Real customer POs must never be committed; put those in samples/private/ (git-ignored).

Usage (from the repo root, with the venv active):
    pip install reportlab pillow
    python samples/make_synthetic_pos.py

Creates in samples/synthetic/:
    PO-1001_acme_native.pdf        1 page, 3 lines, digital text (normal case)
    PO-1002_northwind_multipage.pdf 2 pages, 28 lines, digital text (multi-page, multi-line)
    PO-1003_contoso_scanned.pdf    1 page, image only, no text layer (needs OCR)
    PO-1004_fabrikam_problems.pdf  1 page, missing PO number, zero/blank quantities (should fail validation)
"""

from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "synthetic"

# Fictional maintenance, repair and operations (MRO) supplies. Last value = list price.
PARTS = [
    ("MR-2001", "Pallet Jack 5500lb", "EA", 845.00),
    ("MR-3150", "Safety Harness Full Body", "EA", 120.00),
    ("MR-4410", "Stainless Bolt Kit 1000pc", "BX", 760.00),
    ("MR-5022", "Hydraulic Pump Repair Kit", "EA", 495.00),
    ("MR-6008", "Hydraulic Oil ISO 46 1gal", "GAL", 32.00),   # not in the app's catalog on purpose
    ("MR-7311", "Socket Wrench Set 1/2in", "SET", 435.00),
    ("MR-8120", "LED High Bay Light 150W", "EA", 150.00),
    ("MR-9001", "Portable Eyewash Station", "EA", 500.00),
]


def header_lines(po):
    lines = [
        "PURCHASE ORDER",
        "",
        f"PO Number: {po['po_number']}" if po["po_number"] else "PO Number:",
        f"PO Date: {po['date']}        Requested Ship Date: {po['ship_date']}",
        f"Buyer: {po['buyer']}",
        "",
        f"Bill To: {po['bill_to']}",
        f"Ship To: {po['ship_to']}",
        "",
        "Vendor: Brightline Industrial Supply, 500 Commerce Pkwy, Dayton OH 45402",
        "",
    ]
    return lines


def draw_pdf(path, po, lines_per_page=20):
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    items = po["items"]
    pages = [items[i:i + lines_per_page] for i in range(0, len(items), lines_per_page)] or [[]]
    for page_no, page_items in enumerate(pages, start=1):
        y = height - 60
        if page_no == 1:
            for text in header_lines(po):
                c.setFont("Helvetica-Bold" if text == "PURCHASE ORDER" else "Helvetica",
                          16 if text == "PURCHASE ORDER" else 10)
                c.drawString(50, y, text)
                y -= 16
        else:
            c.setFont("Helvetica", 10)
            c.drawString(50, y, f"PO Number: {po['po_number']} (continued)")
            y -= 24
        c.setFont("Helvetica-Bold", 9)
        for x, col in [(50, "Line"), (90, "Part #"), (170, "Description"), (390, "Qty"),
                       (430, "UOM"), (480, "Unit Price")]:
            c.drawString(x, y, col)
        y -= 14
        c.setFont("Helvetica", 9)
        for it in page_items:
            c.drawString(50, y, str(it["line"]))
            c.drawString(90, y, it["part"])
            c.drawString(170, y, it["desc"])
            c.drawString(390, y, str(it["qty"]))
            c.drawString(430, y, it["uom"])
            c.drawString(480, y, f"${it['price']:.2f}")
            y -= 14
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(50, 40, f"Page {page_no} of {len(pages)}")
        if page_no == len(pages):
            c.setFont("Helvetica", 9)
            c.drawString(50, 60, f"Notes: {po['notes']}")
        c.showPage()
    c.save()


def draw_scanned_pdf(path, po):
    """Render the PO as a slightly noisy grayscale image and save it as an image-only PDF."""
    img = Image.new("L", (1700, 2200), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
    except OSError:
        font = big = ImageFont.load_default()
    y = 120
    for text in header_lines(po):
        d.text((120, y), text, fill=0, font=big if text == "PURCHASE ORDER" else font)
        y += 70 if text == "PURCHASE ORDER" else 44
    d.text((120, y), "Line  Part #     Description                     Qty   UOM   Unit Price", fill=0, font=font)
    y += 50
    for it in po["items"]:
        d.text((120, y), f"{it['line']:<5} {it['part']:<10} {it['desc']:<30} {it['qty']:<5} {it['uom']:<5} ${it['price']:.2f}",
               fill=0, font=font)
        y += 44
    d.text((120, y + 40), f"Notes: {po['notes']}", fill=0, font=font)
    img = img.rotate(0.6, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))  # slight skew/blur like a fax
    img.save(path, "PDF", resolution=200)


def make_items(n, seed, qty_override=None, wild_prices=False):
    """Prices sit within a few percent of list price, like a real order.
    wild_prices=True gives random prices, so the price check has something to catch."""
    rnd = random.Random(seed)
    items = []
    for i in range(1, n + 1):
        part, desc, uom, list_price = rnd.choice(PARTS)
        qty = rnd.randint(1, 40)
        factor = rnd.uniform(0.97, 1.06)
        price = round(15 + factor * 400, 2) if wild_prices else round(list_price * factor, 2)
        items.append({"line": i, "part": part, "desc": desc, "uom": uom, "qty": qty, "price": price})
    if qty_override:
        for idx, q in qty_override.items():
            items[idx]["qty"] = q
    return items


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pos = [
        ("PO-1001_acme_native.pdf", dict(
            po_number="4500012345", date="10/27/2026", ship_date="11/10/2026", buyer="Jane Miller",
            bill_to="Acme Supply Co, 100 Main St, Columbus OH 43215",
            ship_to="Acme Supply Co - Warehouse 2, 2200 Rail Dr, Columbus OH 43207",
            notes="Deliver to dock 4. Call 24h before delivery.", items=make_items(3, 1)), "native"),
        ("PO-1002_northwind_multipage.pdf", dict(
            po_number="NW-778812", date="10/20/2026", ship_date="12/01/2026", buyer="Raj Patel",
            bill_to="Northwind HVAC Distributors, 75 Commerce Blvd, Pittsburgh PA 15222",
            ship_to="Northwind Branch 17, 9 Harbor Rd, Erie PA 16501",
            notes="Partial shipments allowed. Reference PO on all packing slips.",
            items=make_items(28, 2)), "native"),
        ("PO-1003_contoso_scanned.pdf", dict(
            po_number="CM-2026-0457", date="10/25/2026", ship_date="11/15/2026", buyer="Luis Gomez",
            bill_to="Contoso Mechanical LLC, 410 Elm St, Louisville KY 40202",
            ship_to="Contoso Mechanical LLC, 410 Elm St, Louisville KY 40202",
            notes="Faxed order. Confirm receipt by email.", items=make_items(5, 3)), "scanned"),
        ("PO-1004_fabrikam_problems.pdf", dict(
            po_number="", date="10/28/2026", ship_date="10/01/2026", buyer="Unknown",
            bill_to="Fabrikam Building Services, 12 Lake Ave, Cleveland OH 44114",
            ship_to="",
            notes="Ship date is before PO date; PO number missing; line 2 qty 0.",
            items=make_items(4, 4, qty_override={1: 0}, wild_prices=True)), "native"),
    ]
    for name, po, kind in pos:
        path = OUT / name
        if kind == "scanned":
            draw_scanned_pdf(path, po)
        else:
            draw_pdf(path, po)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
