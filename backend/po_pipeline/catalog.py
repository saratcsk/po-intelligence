"""A small, made-up product catalog the agent can look parts up in.

In a real system this would query the ERP item master and customer item cross-reference.
"""

CATALOG = {
    "MR-2001": {"description": "Pallet Jack 5500lb", "uom": "EA", "list_price": 845.00},
    "MR-3150": {"description": "Safety Harness Full Body", "uom": "EA", "list_price": 120.00},
    "MR-4410": {"description": "Stainless Bolt Kit 1000pc", "uom": "BX", "list_price": 760.00},
    "MR-5022": {"description": "Hydraulic Pump Repair Kit", "uom": "EA", "list_price": 495.00},
    "MR-7311": {"description": "Socket Wrench Set 1/2in", "uom": "SET", "list_price": 435.00},
    "MR-8120": {"description": "LED High Bay Light 150W", "uom": "EA", "list_price": 150.00},
    "MR-9001": {"description": "Portable Eyewash Station", "uom": "EA", "list_price": 500.00},
    # MR-6008 (Hydraulic Oil) is deliberately missing: it was discontinued.
}


def lookup_part(part_number: str) -> dict:
    """Return the catalog record for a part, or similar part numbers if it is not found."""
    key = (part_number or "").strip().upper()
    if key in CATALOG:
        return {"found": True, "part_number": key, **CATALOG[key]}
    prefix = key.split("-")[0] + "-" if "-" in key else key[:2]  # e.g. "MR-"
    return {"found": False, "part_number": key,
            "similar_parts": [p for p in CATALOG if prefix and p.startswith(prefix)][:5]}
