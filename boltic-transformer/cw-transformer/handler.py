"""
Cottonworld Logic ERP -> Fynd Platform Transformer
Single-file Boltic serverless handler.
Uses ONLY Python stdlib + Flask (pre-installed by Boltic).
No openpyxl, no pandas -- xlsx reading/writing via zipfile + xml.etree.

Per the June 2026 client spec ("as-is" rules):
- Only Name and Item Code are derived. Every other Logic ERP value is
  passed through verbatim (no title-casing, no display-name mapping,
  no blanking of '(NIL)').
- Whitespace trim + Excel '.0' float artifact strip are LOGGED to a
  cleanup audit trail, returned as Sheet2 of the output xlsx.
- Prices (MRP, RATE) preserve 2-decimal display (e.g. 499.00, 499.50).
- HS Code looked up by raw (SECTION, DEPARTMENT) pair.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO, StringIO
from typing import Any

from flask import jsonify, make_response, redirect

# ---------------------------------------------------------------------------
# Reference data (all inlined)
# ---------------------------------------------------------------------------

STATIC = {
    "brand": "cottonworld",
    "category": "Others level 3",
    "tax_rule": "Tiered Tax Rule \u2013 5% & 18% (Eff. 22 Sep 2025) (2)",
    "country_of_origin": "India",
    "gtin_type": "EAN",
    "currency": "INR",
    "length_cm": 37, "width_cm": 48, "height_cm": 1.4, "weight_gram": 500,
    "trader_type": "Manufacturer",
    "trader_name": "Lekhraj Corp Pvt Ltd",
    "trader_address": "GALA-F, SIDHWA ESTATE, OLD BMP BUILDING, N.A. SAWANT MARG, Colaba, Mumbai City, Maharashtra, 400005",
    "return_time_limit": 30, "return_time_unit": "Days",
    "net_quantity_value": 1, "net_quantity_unit": "nos",
    "collection": "CWC",
}

HSN_LOOKUP = {
    ("BOYS", "MASK"): "62171010",
    ("LADIES", "BLOUSE"): "61061000",
    ("LADIES", "BOXERS"): "61034300",
    ("LADIES", "CANVAS BAGS"): "63052000",
    ("LADIES", "CLBAG"): "42022240",
    ("LADIES", "CULOTTE"): "61034200",
    ("LADIES", "DRESS"): "61044200",
    ("LADIES", "ETH DRESS"): "62044911",
    ("LADIES", "ETH KURTA"): "62113900",
    ("LADIES", "ETH KURTI"): "62113900",
    ("LADIES", "ETH PALLAZO"): "62041300",
    ("LADIES", "JACKET"): "61033200",
    ("LADIES", "JUMPER"): "61044200",
    ("LADIES", "JUMPSUIT"): "62114990",
    ("LADIES", "KAFTAN"): "62114210",
    ("LADIES", "KDRESS"): "61044200",
    ("LADIES", "KJUMPSUIT"): "61121100",
    ("LADIES", "KNIT SHRUG"): "61033200",
    ("LADIES", "KPANTS"): "61034200",
    ("LADIES", "KPYJAMA SUIT"): "62082100",
    ("LADIES", "KSHORTS"): "61046200",
    ("LADIES", "KSKIRT"): "61045200",
    ("LADIES", "KTIGHTS"): "61034200",
    ("LADIES", "KURTA"): "62114210",
    ("LADIES", "KURTI"): "61061000",
    ("LADIES", "KVEST"): "61091000",
    ("LADIES", "MASK"): "62171000",
    ("LADIES", "OVERLAY"): "61044990",
    ("LADIES", "PAJAMA"): "62082100",
    ("LADIES", "PALLAZO"): "62041300",
    ("LADIES", "PANTS"): "61034200",
    ("LADIES", "PYJAMA"): "62082100",
    ("LADIES", "PYJAMA SUIT"): "62082100",
    ("LADIES", "SHIRTS"): "62063000",
    ("LADIES", "SHORTS"): "61034300",
    ("LADIES", "SHRUG"): "61033200",
    ("LADIES", "SKIRT"): "61045200",
    ("LADIES", "SOCKS"): "62171010",
    ("LADIES", "SWEAT"): "61091000",
    ("LADIES", "TSHIRT"): "61091000",
    ("LADIES", "WAISTCOAT"): "62113200",
    ("MENS", "BOXERS"): "62071100",
    ("MENS", "ETHNI KURTA"): "62113990",
    ("MENS", "ETHNI PANTS"): "62031910",
    ("MENS", "JACKET"): "61033200",
    ("MENS", "JOGGERS"): "61121100",
    ("MENS", "KNIT JACKET"): "61033200",
    ("MENS", "KNIT SHRUG"): "61033200",
    ("MENS", "KPANTS"): "61034200",
    ("MENS", "KSHORTS"): "61034200",
    ("MENS", "KURTA"): "62114210",
    ("MENS", "KVEST"): "62079990",
    ("MENS", "PANTS"): "61034200",
    ("MENS", "SHIRTS"): "62052000",
    ("MENS", "SHORTS"): "61034200",
    ("MENS", "SOCKS"): "62171010",
    ("MENS", "SWEAT"): "61091000",
    ("MENS", "TRACK PANT"): "61034200",
    ("MENS", "TSHIRT"): "61091000",
    ("UNISEX", "TOTE BAG"): "42022220",
}

FYND_FIXED_COLUMNS = [
    "Name", "Slug", "Item Code", "Brand", "Category", "Description",
    "Short Description", "Tax Rule Name", "HS Code", "Country of Origin",
    "Media", "Multi Size", "Gtin Type", "Gtin Value", "Seller Identifier",
    "Meta", "Size Meta", "Size", "Actual Price", "Selling Price",
    "Currency", "Length (cm)", "Width (cm)", "Height (cm)",
    "Product Dead Weight (gram)", "Size Guide", "Available", "Highlights",
    "Unlisted Product", "Variant Type", "Variant Group ID", "Variant Media",
    "Trader Type", "Trader Name", "Trader Address", "Track Inventory",
    "Teaser Tag Name", "No of Boxes", "Manufacturing Time",
    "Manufacturing Time Unit", "Return Time Limit", "Return Time Unit",
    "Product Publishing Date", "Tags", "Net Quantity Value",
    "Net Quantity Unit", "Product Bundle", "Colour", "Material",
    "Package Contents", "Quantity Factor", "Priority",
]
FYND_CUSTOM_ATTRS = [f"Custom Attribute {i}" for i in range(1, 51)]
FYND_COLUMNS = FYND_FIXED_COLUMNS + FYND_CUSTOM_ATTRS

CLEANUP_COLUMNS = [
    "Logic Row (Excel)", "Column", "Original Value",
    "Cleaned Value", "Reason",
]

# ---------------------------------------------------------------------------
# Shopify reference data (inlined; mirrors data/shopify_static_values.json)
# ---------------------------------------------------------------------------

SHOPIFY_STATIC = {
    "vendor": "Cottonworld",
    "published": "FALSE",
    "option1_name": "Size",
    "option1_linked_to": "product.metafields.shopify.size",
    "option2_name": "color",
    "option2_linked_to": "product.metafields.shopify.color-pattern",
    "variant_grams": 200,
    "variant_inventory_tracker": "Shopify",
    "variant_inventory_policy": "Deny",
    "variant_fulfillment_service": "Manual",
    "variant_requires_shipping": "TRUE",
    "variant_taxable": "TRUE",
    "gift_card": "FALSE",
    "variant_weight_unit": "Kg",
    "included_india": "TRUE",
    "status": "draft",
}

# Shopify output schema (93 columns, exact Shopify product-import order)
SHOPIFY_COLUMNS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type",
    "Tags", "Published",
    "Option1 Name", "Option1 Value", "Option1 Linked To",
    "Option2 Name", "Option2 Value", "Option2 Linked To",
    "Option3 Name", "Option3 Value", "Option3 Linked To",
    "Variant SKU", "Variant Grams", "Variant Inventory Tracker",
    "Variant Inventory Policy", "Variant Fulfillment Service", "Variant Price",
    "Variant Compare At Price", "Variant Requires Shipping", "Variant Taxable",
    "Unit Price Total Measure", "Unit Price Total Measure Unit",
    "Unit Price Base Measure", "Unit Price Base Measure Unit", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Gift Card",
    "SEO Title", "SEO Description",
    "Google Shopping / Google Product Category", "Google Shopping / Gender",
    "Google Shopping / Age Group", "Google Shopping / MPN",
    "Google Shopping / Condition", "Google Shopping / Custom Product",
    "Google Shopping / Custom Label 0", "Google Shopping / Custom Label 1",
    "Google Shopping / Custom Label 2", "Google Shopping / Custom Label 3",
    "Google Shopping / Custom Label 4",
    "Availability (product.metafields.custom.availability)",
    "Category (product.metafields.custom.category)",
    "Collections (product.metafields.custom.collections)",
    "Colour (product.metafields.custom.colour)",
    "Dress Length (product.metafields.custom.dress_length)",
    "Fabric Type (product.metafields.custom.fabric)",
    "Fit Type (product.metafields.custom.fit_type)",
    "Gender (product.metafields.custom.gender)",
    "Hemline (product.metafields.custom.hemline)",
    "Leg (product.metafields.custom.leg)",
    "Length (product.metafields.custom.length)",
    "Model wearing size (product.metafields.custom.model_wearing_size)",
    "Neck Collar (product.metafields.custom.neck_collar)",
    "Occasion (product.metafields.custom.occasion)",
    "Opening (product.metafields.custom.opening)",
    "Pant Length (product.metafields.custom.pant_length)",
    "Pocket (product.metafields.custom.pocket)",
    "Print (product.metafields.custom.print)",
    "Fabric Composition (product.metafields.custom.fabric_composition)",
    "Product Type (product.metafields.custom.product_type)",
    "Size (product.metafields.custom.size)",
    "Sleeve (product.metafields.custom.sleeve)",
    "Style Code (product.metafields.custom.style_code)",
    "True to Size (product.metafields.custom.true_to_size)",
    "Waist (product.metafields.custom.waist)",
    "Wash Care Instruction (product.metafields.custom.wash_care_instruction)",
    "Woven or Knit (product.metafields.custom.woven_or_knit)",
    "Zodiac (product.metafields.custom.zodiac)",
    "Theme Festival (product.metafields.festival.theme_festival)",
    "Google: Custom Product (product.metafields.mm-google-shopping.custom_product)",
    "Theme Mood (product.metafields.mood.theme_mood)",
    "Theme Seasonal (product.metafields.seasonal.theme_seasonal)",
    "Complementary products (product.metafields.shopify--discovery--product_recommendation.complementary_products)",
    "Related products (product.metafields.shopify--discovery--product_recommendation.related_products)",
    "Related products settings (product.metafields.shopify--discovery--product_recommendation.related_products_display)",
    "Search product boosts (product.metafields.shopify--discovery--product_search_boost.queries)",
    "Theme Zodiac (product.metafields.zodiac.theme_zodiac)",
    "Variant Image", "Variant Weight Unit", "Variant Tax Code", "Cost per item",
    "Included / India", "Price / India", "Compare At Price / India", "Status",
]


# ---------------------------------------------------------------------------
# Stdlib xlsx reader
# ---------------------------------------------------------------------------

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _col_idx(ref: str) -> int:
    """'A' -> 0, 'B' -> 1, 'AA' -> 26, etc."""
    letters = "".join(c for c in ref if c.isalpha())
    n = 0
    for c in letters.upper():
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _read_xlsx(data: bytes) -> list[list]:
    """Return all rows from xlsx bytes as list of lists."""
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()

        # Shared strings
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                parts = [t.text or "" for t in si.iter(f"{{{_NS}}}t")]
                shared.append("".join(parts))

        # Worksheet
        ws_path = "xl/worksheets/sheet1.xml"
        if ws_path not in names:
            ws_path = next(
                (n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")),
                None,
            )
            if ws_path is None:
                raise ValueError("No worksheet found in xlsx file")

        root = ET.fromstring(zf.read(ws_path))
        all_rows: list[list] = []

        for row_el in root.iter(f"{{{_NS}}}row"):
            cells: dict[int, Any] = {}
            for c_el in row_el:
                ref = c_el.get("r", "")
                if not ref:
                    continue
                ci = _col_idx(ref)
                t = c_el.get("t", "")
                v_el = c_el.find(f"{{{_NS}}}v")

                if t == "s":
                    idx = int(v_el.text) if v_el is not None and v_el.text else 0
                    cells[ci] = shared[idx] if idx < len(shared) else ""
                elif t == "inlineStr":
                    t_el = c_el.find(f".//{{{_NS}}}t")
                    cells[ci] = t_el.text if t_el is not None else ""
                elif t == "b":
                    cells[ci] = bool(int(v_el.text)) if v_el is not None else False
                else:
                    if v_el is not None and v_el.text is not None:
                        try:
                            f = float(v_el.text)
                            cells[ci] = int(f) if f == int(f) else f
                        except ValueError:
                            cells[ci] = v_el.text
                    else:
                        cells[ci] = None

            if cells:
                width = max(cells.keys()) + 1
                all_rows.append([cells.get(i) for i in range(width)])

    return all_rows


# ---------------------------------------------------------------------------
# Stdlib xlsx writer (multi-sheet)
# ---------------------------------------------------------------------------

def _col_name(n: int) -> str:
    """0 -> 'A', 1 -> 'B', 26 -> 'AA', etc."""
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml_esc(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_sheet_xml(columns: list, rows: list[list]) -> str:
    all_rows = [columns] + rows
    sheet_parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for ri, row_vals in enumerate(all_rows, start=1):
        cells = []
        for ci, val in enumerate(row_vals):
            ref = f"{_col_name(ci)}{ri}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{_xml_esc(val)}</t></is></c>')
        sheet_parts.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet_parts += ["</sheetData>", "</worksheet>"]
    return "".join(sheet_parts)


def _write_xlsx(sheets: list[tuple[str, list, list[list]]]) -> bytes:
    """Write multi-sheet xlsx. Each sheet is (name, columns, rows)."""
    sheet_xmls = [_build_sheet_xml(cols, rows) for _, cols, rows in sheets]

    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets))
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{overrides}"
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    sheets_xml = "".join(
        f'<sheet name="{_xml_esc(name)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, (name, _, _) in enumerate(sheets)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets_xml}</sheets>'
        "</workbook>"
    )
    wb_rel_entries = "".join(
        f'<Relationship Id="rId{i+1}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i+1}.xml"/>'
        for i in range(len(sheets))
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{wb_rel_entries}"
        "</Relationships>"
    )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for i, sheet_xml in enumerate(sheet_xmls):
            zf.writestr(f"xl/worksheets/sheet{i+1}.xml", sheet_xml)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Cleanup log
# ---------------------------------------------------------------------------

class CleanupLog:
    """Audit trail of every cell mutation (whitespace trim, .0 strip)."""

    def __init__(self) -> None:
        self.entries: list[list] = []

    def add(self, *, excel_row: int, column: str, original: str,
            cleaned: str, reason: str) -> None:
        self.entries.append([excel_row, column, original, cleaned, reason])


# ---------------------------------------------------------------------------
# Value handling -- strict "as-is" with logged cleanups
# ---------------------------------------------------------------------------

def _is_na(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False


def _is_nil(s: str) -> bool:
    return s.upper() == "(NIL)"


def passthrough(val: Any, excel_row: int, col_name: str,
                log: CleanupLog) -> str:
    """Trim trailing/leading whitespace; preserve everything else verbatim.
    Logs any whitespace trim that actually mutated the value."""
    if _is_na(val):
        return ""
    # The xlsx reader gives us int for whole numbers, float for fractional.
    # Stringify cleanly.
    if isinstance(val, float) and val == int(val):
        s_raw = str(int(val))
    else:
        s_raw = str(val)
    s_stripped = s_raw.strip()
    if s_stripped != s_raw and s_stripped:
        log.add(
            excel_row=excel_row, column=col_name,
            original=repr(s_raw), cleaned=s_stripped,
            reason="Trimmed leading/trailing whitespace",
        )
    return s_stripped


def passthrough_id(val: Any, excel_row: int, col_name: str,
                   log: CleanupLog) -> str:
    """For numeric IDs (STYLE NO, FABRIC NO., ORDER NO, OEM_BARCODE) --
    strip trailing '.0' that Excel introduces when integer cells are
    stored as floats. Logged because we mutated the displayed string."""
    if _is_na(val):
        return ""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        f = float(val)
        if f == int(f):
            cleaned = str(int(f))
            original = str(val)
            if original != cleaned:
                log.add(
                    excel_row=excel_row, column=col_name,
                    original=original, cleaned=cleaned,
                    reason="Stripped trailing '.0' (Excel float artifact)",
                )
            return cleaned
        return str(val).strip()
    s_raw = str(val)
    s_stripped = s_raw.strip()
    if s_stripped != s_raw and s_stripped:
        log.add(
            excel_row=excel_row, column=col_name,
            original=repr(s_raw), cleaned=s_stripped,
            reason="Trimmed leading/trailing whitespace",
        )
    return s_stripped


def format_price(val: Any) -> str:
    """Preserve price as 2-decimal string (e.g. 499.00, 499.50)."""
    if _is_na(val):
        return ""
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return str(val).strip()


def format_packed_date(val: Any) -> str:
    """Excel-date-serial or 'mm-yyyy' string -> 'mm-yyyy'."""
    if _is_na(val):
        return ""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            from datetime import datetime, timedelta
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=float(val))
            return dt.strftime("%m-%Y")
        except Exception:
            pass
    s = str(val).strip() if val is not None else ""
    m = re.fullmatch(r"(\d{1,2})-(\d{4})", s)
    if m:
        return f"{int(m.group(1)):02d}-{m.group(2)}"
    return s


# ---------------------------------------------------------------------------
# Name & Item Code builders (the ONLY derived fields)
# ---------------------------------------------------------------------------

def build_name(section: str, department: str, fit: str, color: str) -> str:
    """[SECTION] [DEPARTMENT] [FIT] [COLOR] -- raw verbatim from Logic.
    Empty / (NIL) segments are skipped.

    Example: MENS, TSHIRT, REGULAR FIT, BLACK -> 'MENS TSHIRT REGULAR FIT BLACK'
    """
    parts: list[str] = []
    for v in (section, department, fit, color):
        s = (v or "").strip()
        if s and not _is_nil(s):
            parts.append(s)
    return " ".join(parts)


def build_item_code(section: str, department: str, style_no: str,
                    fabric_no: str, color: str) -> str:
    """{first-letter-of-SECTION}-DEPT-STYLE-FABRIC-COLOR.
    Empty / (NIL) segments are skipped (E2 decision)."""
    sec = (section or "").strip()
    prefix = sec[0].upper() if sec and not _is_nil(sec) else ""
    segments = [
        prefix,
        (department or "").strip(),
        (style_no or "").strip(),
        (fabric_no or "").strip(),
        (color or "").strip(),
    ]
    return "-".join(s for s in segments if s and not _is_nil(s))


def lookup_hs(section: str, department: str, warnings: list) -> str:
    sec = (section or "").strip().upper()
    dept = (department or "").strip().upper()
    if not sec or not dept or _is_nil(sec) or _is_nil(dept):
        return ""
    hs = HSN_LOOKUP.get((sec, dept))
    if hs is None:
        warnings.append(f"No HSN for Section='{sec}', Dept='{dept}' -- left blank.")
        return ""
    return hs


def build_fabric_composition(*comps: str) -> str:
    """Fabric Composition metafield = space-joined COMPOSITION1/2/3, skipping
    empty / (NIL) segments (mirrors build_name's segment handling)."""
    parts: list[str] = []
    for v in comps:
        s = (v or "").strip()
        if s and not _is_nil(s):
            parts.append(s)
    return " ".join(parts)


def _slug(value: str) -> str:
    out: list[str] = []
    prev_dash = False
    for ch in (value or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def shopify_handle(style_no: str, fabric_no: str, color: str) -> str:
    """Shopify groups variant rows into one product via a shared Handle.

    OPEN ITEM (pending Cottonworld team confirmation): the grouping rule.
    Default mirrors the Fynd flow -- one product per (Style No, Fabric No,
    Color). To change the grouping, change ONLY this function (and the group
    key in transform_shopify()).
    """
    return _slug(f"{style_no}-{fabric_no}-{color}")


# ---------------------------------------------------------------------------
# Column discovery
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def find_col(headers: list, *candidates: str):
    norm_map = {_normalize(h): i for i, h in enumerate(headers)}
    for cand in candidates:
        n = _normalize(cand)
        if n in norm_map:
            return norm_map[n]
    return None


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def transform(data: bytes) -> tuple[bytes, list[str], list[list]]:
    """Returns (xlsx_bytes, warnings, cleanup_entries).

    xlsx_bytes is a 2-sheet workbook: Sheet1=Fynd output, Sheet2=Cleanup Log.
    """
    warnings: list[str] = []
    cleanup = CleanupLog()

    all_rows = _read_xlsx(data)

    # Find header row
    header_idx = None
    for i, row in enumerate(all_rows):
        for val in row:
            if str(val).strip().upper() == "OEM_BARCODE":
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None:
        raise ValueError("Could not find 'OEM_BARCODE' header row in input file.")

    headers = [str(v).strip() if v is not None else "" for v in all_rows[header_idx]]
    data_rows = all_rows[header_idx + 1:]
    # Excel row number for cleanup log: header_idx is 0-based.
    # Data rows start at Excel row (header_idx + 2).
    first_data_excel_row = header_idx + 2

    col_oem       = find_col(headers, "OEM_BARCODE")
    col_section   = find_col(headers, "SECTION")
    col_dept      = find_col(headers, "DEPARTMENT")
    col_style_no  = find_col(headers, "STYLE NO", "STYLE_NO", "STYLENO")
    col_fabric_no = find_col(headers, "FABRIC NO.", "FABRIC_NO", "FABRICNO")
    col_color     = find_col(headers, "COLOR")
    col_size      = find_col(headers, "PACK / SIZE", "PACK_SIZE", "PACKSIZE", "PACK/SIZE")
    col_fab_main  = find_col(headers, "FABRIC MAIN DESC", "FABRIC_MAIN_DESC")
    col_fab_sub   = find_col(headers, "FABRIC SUB DESC", "FABRIC_SUB_DESC")
    col_fab_type  = find_col(headers, "FABRIC TYPE", "FABRIC_TYPE")
    col_fab_subtype = find_col(headers, "FABRIC SUB TYPE", "FABRIC_SUB_TYPE")
    col_hl        = find_col(headers, "HL")
    col_sleeve    = find_col(headers, "SLEEVE TYPE", "SLEEVE_TYPE")
    col_fit       = find_col(headers, "FIT")
    col_occasion  = find_col(headers, "OCCASION")
    col_pockets   = find_col(headers, "POCKETS")
    col_neck      = find_col(headers, "NECK-COLLAR", "NECK_COLLAR", "NECKCOLLAR")
    col_length    = find_col(headers, "LENGTH")
    col_waist     = find_col(headers, "WAIST")
    col_closure   = find_col(headers, "CLOSURE")
    col_leg       = find_col(headers, "LEG")
    col_front     = find_col(headers, "FRONT")
    col_comp1     = find_col(headers, "COMPOSITION1")
    col_comp2     = find_col(headers, "COMPOSITION2")
    col_comp3     = find_col(headers, "COMPOSITION3")
    col_packed_date = find_col(headers, "PACKED DATE", "PACKED_DATE")
    col_cs        = find_col(headers, "CS")
    col_rate      = find_col(headers, "RATE")
    col_order_no  = find_col(headers, "ORDER NO", "ORDER_NO")
    col_mrp       = find_col(headers, "MRP")

    required = {
        "OEM_BARCODE": col_oem, "SECTION": col_section, "DEPARTMENT": col_dept,
        "STYLE NO": col_style_no, "FABRIC NO.": col_fabric_no,
        "COLOR": col_color, "PACK / SIZE": col_size, "MRP": col_mrp,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"Input file missing required columns: {', '.join(missing)}")

    def get(row, idx):
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    # Attach Excel row to each data row for cleanup log
    indexed_rows = [
        (first_data_excel_row + i, r)
        for i, r in enumerate(data_rows)
        if not _is_na(get(r, col_oem))
    ]

    # Group by (style_no, fabric_no, color)
    groups: dict[str, list] = {}
    group_order: list[str] = []
    for excel_row, row in indexed_rows:
        sn = passthrough_id(get(row, col_style_no), excel_row, "STYLE NO", CleanupLog())
        fn = passthrough_id(get(row, col_fabric_no), excel_row, "FABRIC NO.", CleanupLog())
        co = passthrough(get(row, col_color), excel_row, "COLOR", CleanupLog())
        key = f"{sn}|{fn}|{co}"
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append((excel_row, row))

    output_rows: list[list] = []

    for key in group_order:
        group = groups[key]
        first_excel_row, first = group[0]

        def pt(col_idx, key_name: str) -> str:
            if col_idx is None:
                return ""
            return passthrough(get(first, col_idx), first_excel_row, key_name, cleanup)

        def pid(col_idx, key_name: str) -> str:
            if col_idx is None:
                return ""
            return passthrough_id(get(first, col_idx), first_excel_row, key_name, cleanup)

        section         = pt(col_section, "SECTION")
        department      = pt(col_dept, "DEPARTMENT")
        style_no        = pid(col_style_no, "STYLE NO")
        fabric_no       = pid(col_fabric_no, "FABRIC NO.")
        color           = pt(col_color, "COLOR")
        fit             = pt(col_fit, "FIT")
        occasion        = pt(col_occasion, "OCCASION")
        neck_collar     = pt(col_neck, "NECK-COLLAR")
        sleeve_type     = pt(col_sleeve, "SLEEVE TYPE")
        fabric_main     = pt(col_fab_main, "FABRIC MAIN DESC")
        fabric_sub      = pt(col_fab_sub, "FABRIC SUB DESC")
        fabric_type     = pt(col_fab_type, "FABRIC TYPE")
        fabric_sub_type = pt(col_fab_subtype, "FABRIC SUB TYPE")
        hl              = pt(col_hl, "HL")
        pockets         = pt(col_pockets, "POCKETS")
        length_val      = pt(col_length, "LENGTH")
        waist_val       = pt(col_waist, "WAIST")
        closure         = pt(col_closure, "CLOSURE")
        leg             = pt(col_leg, "LEG")
        front           = pt(col_front, "FRONT")
        composition1    = pt(col_comp1, "COMPOSITION1")
        composition2    = pt(col_comp2, "COMPOSITION2")
        composition3    = pt(col_comp3, "COMPOSITION3")
        packed_date     = format_packed_date(get(first, col_packed_date)) if col_packed_date is not None else ""
        cs              = pt(col_cs, "CS")
        rate            = format_price(get(first, col_rate)) if col_rate is not None else ""
        order_no        = pid(col_order_no, "ORDER NO")

        product_name = build_name(section, department, fit, color)
        item_code    = build_item_code(section, department, style_no, fabric_no, color)
        hs_code      = lookup_hs(section, department, warnings)

        for i, (row_excel, row) in enumerate(group):
            is_first = (i == 0)
            oem_barcode = passthrough_id(get(row, col_oem), row_excel,
                                         "OEM_BARCODE", cleanup)
            pack_size = passthrough(get(row, col_size), row_excel,
                                    "PACK / SIZE", cleanup)
            mrp_str = format_price(get(row, col_mrp))

            out = {col: "" for col in FYND_COLUMNS}
            out["Item Code"]                  = item_code
            out["Brand"]                      = STATIC["brand"]
            out["Gtin Type"]                  = STATIC["gtin_type"]
            out["Gtin Value"]                 = oem_barcode
            out["Seller Identifier"]          = oem_barcode
            out["Size"]                       = pack_size
            out["Actual Price"]               = mrp_str
            out["Selling Price"]              = mrp_str
            out["Currency"]                   = STATIC["currency"]
            out["Length (cm)"]                = STATIC["length_cm"]
            out["Width (cm)"]                 = STATIC["width_cm"]
            out["Height (cm)"]                = STATIC["height_cm"]
            out["Product Dead Weight (gram)"] = STATIC["weight_gram"]
            out["Net Quantity Value"]         = STATIC["net_quantity_value"]
            out["Net Quantity Unit"]          = STATIC["net_quantity_unit"]

            if is_first:
                out["Name"]              = product_name
                out["Category"]          = STATIC["category"]
                out["Tax Rule Name"]     = STATIC["tax_rule"]
                out["HS Code"]           = hs_code
                out["Country of Origin"] = STATIC["country_of_origin"]
                out["Trader Type"]       = STATIC["trader_type"]
                out["Trader Name"]       = STATIC["trader_name"]
                out["Trader Address"]    = STATIC["trader_address"]
                out["Return Time Limit"] = STATIC["return_time_limit"]
                out["Return Time Unit"]  = STATIC["return_time_unit"]
                out["Colour"]            = color
                out["Material"]          = build_fabric_composition(
                    composition1, composition2, composition3)
                out["Custom Attribute 1"]  = department
                out["Custom Attribute 2"]  = fit
                out["Custom Attribute 3"]  = section
                out["Custom Attribute 4"]  = occasion
                out["Custom Attribute 5"]  = neck_collar
                out["Custom Attribute 7"]  = sleeve_type
                out["Custom Attribute 8"]  = order_no
                out["Custom Attribute 9"]  = fabric_main
                out["Custom Attribute 10"] = fabric_sub
                out["Custom Attribute 11"] = fabric_sub_type
                out["Custom Attribute 12"] = hl
                out["Custom Attribute 13"] = pockets
                out["Custom Attribute 14"] = style_no
                out["Custom Attribute 20"] = fabric_no
                out["Custom Attribute 21"] = cs
                out["Custom Attribute 22"] = packed_date
                out["Custom Attribute 23"] = length_val
                out["Custom Attribute 24"] = waist_val
                out["Custom Attribute 25"] = closure
                out["Custom Attribute 26"] = leg
                out["Custom Attribute 27"] = front
                out["Custom Attribute 28"] = fabric_type
                out["Custom Attribute 29"] = rate
                out["Custom Attribute 30"] = composition1
                out["Custom Attribute 31"] = composition2
                out["Custom Attribute 32"] = composition3

            output_rows.append([out.get(col, "") for col in FYND_COLUMNS])

    if not output_rows:
        raise ValueError("No valid product rows found in input file.")

    # De-dup warnings
    seen: set = set()
    unique_warnings: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique_warnings.append(w)

    xlsx_bytes = _write_xlsx([
        ("Fynd Output", FYND_COLUMNS, output_rows),
        ("Cleanup Log", CLEANUP_COLUMNS, cleanup.entries),
    ])
    return xlsx_bytes, unique_warnings, cleanup.entries


# ---------------------------------------------------------------------------
# Transform -- Shopify (CSV)
# ---------------------------------------------------------------------------

def transform_shopify(data: bytes) -> tuple[bytes, list[str], list[list]]:
    """Returns (csv_bytes, warnings, cleanup_entries).

    csv_bytes is a Shopify product-import CSV (one product per group, sizes
    become variant rows). No HS Code lookup on this path. Cleanup is returned
    separately (a CSV can't carry a second sheet).
    """
    warnings: list[str] = []
    cleanup = CleanupLog()

    all_rows = _read_xlsx(data)

    header_idx = None
    for i, row in enumerate(all_rows):
        for val in row:
            if str(val).strip().upper() == "OEM_BARCODE":
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None:
        raise ValueError("Could not find 'OEM_BARCODE' header row in input file.")

    headers = [str(v).strip() if v is not None else "" for v in all_rows[header_idx]]
    data_rows = all_rows[header_idx + 1:]
    first_data_excel_row = header_idx + 2

    col_oem       = find_col(headers, "OEM_BARCODE")
    col_section   = find_col(headers, "SECTION")
    col_dept      = find_col(headers, "DEPARTMENT")
    col_style_no  = find_col(headers, "STYLE NO", "STYLE_NO", "STYLENO")
    col_fabric_no = find_col(headers, "FABRIC NO.", "FABRIC_NO", "FABRICNO")
    col_color     = find_col(headers, "COLOR")
    col_size      = find_col(headers, "PACK / SIZE", "PACK_SIZE", "PACKSIZE", "PACK/SIZE")
    col_fab_main  = find_col(headers, "FABRIC MAIN DESC", "FABRIC_MAIN_DESC")
    col_fab_type  = find_col(headers, "FABRIC TYPE", "FABRIC_TYPE")
    col_hl        = find_col(headers, "HL")
    col_sleeve    = find_col(headers, "SLEEVE TYPE", "SLEEVE_TYPE")
    col_fit       = find_col(headers, "FIT")
    col_occasion  = find_col(headers, "OCCASION")
    col_pockets   = find_col(headers, "POCKETS")
    col_neck      = find_col(headers, "NECK-COLLAR", "NECK_COLLAR", "NECKCOLLAR")
    col_length    = find_col(headers, "LENGTH")
    col_waist     = find_col(headers, "WAIST")
    col_leg       = find_col(headers, "LEG")
    col_front     = find_col(headers, "FRONT")
    col_comp1     = find_col(headers, "COMPOSITION1")
    col_comp2     = find_col(headers, "COMPOSITION2")
    col_comp3     = find_col(headers, "COMPOSITION3")
    col_mrp       = find_col(headers, "MRP")

    required = {
        "OEM_BARCODE": col_oem, "SECTION": col_section, "DEPARTMENT": col_dept,
        "STYLE NO": col_style_no, "FABRIC NO.": col_fabric_no,
        "COLOR": col_color, "PACK / SIZE": col_size, "MRP": col_mrp,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"Input file missing required columns: {', '.join(missing)}")

    def get(row, idx):
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    indexed_rows = [
        (first_data_excel_row + i, r)
        for i, r in enumerate(data_rows)
        if not _is_na(get(r, col_oem))
    ]

    # Group by (style_no, fabric_no, color) -- mirrors shopify_handle()
    groups: dict[str, list] = {}
    group_order: list[str] = []
    for excel_row, row in indexed_rows:
        sn = passthrough_id(get(row, col_style_no), excel_row, "STYLE NO", CleanupLog())
        fn = passthrough_id(get(row, col_fabric_no), excel_row, "FABRIC NO.", CleanupLog())
        co = passthrough(get(row, col_color), excel_row, "COLOR", CleanupLog())
        key = f"{sn}|{fn}|{co}"
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append((excel_row, row))

    output_rows: list[list] = []

    for key in group_order:
        group = groups[key]
        first_excel_row, first = group[0]

        def pt(col_idx, key_name: str) -> str:
            if col_idx is None:
                return ""
            return passthrough(get(first, col_idx), first_excel_row, key_name, cleanup)

        def pid(col_idx, key_name: str) -> str:
            if col_idx is None:
                return ""
            return passthrough_id(get(first, col_idx), first_excel_row, key_name, cleanup)

        section      = pt(col_section, "SECTION")
        department   = pt(col_dept, "DEPARTMENT")
        style_no     = pid(col_style_no, "STYLE NO")
        fabric_no    = pid(col_fabric_no, "FABRIC NO.")
        color        = pt(col_color, "COLOR")
        fit          = pt(col_fit, "FIT")
        occasion     = pt(col_occasion, "OCCASION")
        neck_collar  = pt(col_neck, "NECK-COLLAR")
        sleeve_type  = pt(col_sleeve, "SLEEVE TYPE")
        fabric_main  = pt(col_fab_main, "FABRIC MAIN DESC")
        fabric_type  = pt(col_fab_type, "FABRIC TYPE")
        hl           = pt(col_hl, "HL")
        pockets      = pt(col_pockets, "POCKETS")
        length_val   = pt(col_length, "LENGTH")
        waist_val    = pt(col_waist, "WAIST")
        leg          = pt(col_leg, "LEG")
        front        = pt(col_front, "FRONT")
        comp1        = pt(col_comp1, "COMPOSITION1")
        comp2        = pt(col_comp2, "COMPOSITION2")
        comp3        = pt(col_comp3, "COMPOSITION3")

        # Title = SECTION DEPARTMENT FIT COLOR; Handle and Style Code both use
        # the Fynd Item Code format ({FirstLetterOfSection}-DEPT-STYLE-FABRIC-COLOR).
        title = build_name(section, department, fit, color)
        item_code = build_item_code(section, department, style_no, fabric_no, color)
        style_code = item_code
        handle = item_code
        fabric_composition = build_fabric_composition(comp1, comp2, comp3)

        for i, (row_excel, row) in enumerate(group):
            is_first = (i == 0)
            oem_barcode = passthrough_id(get(row, col_oem), row_excel,
                                         "OEM_BARCODE", cleanup)
            pack_size = passthrough(get(row, col_size), row_excel,
                                    "PACK / SIZE", cleanup)
            mrp_str = format_price(get(row, col_mrp))

            out = {col: "" for col in SHOPIFY_COLUMNS}

            # Handle repeats on every variant row (groups the product)
            out["Handle"] = handle

            # Variant-level fields (every row)
            out["Option1 Value"]               = pack_size
            out["Option2 Value"]               = color
            out["Variant SKU"]                 = oem_barcode
            out["Variant Barcode"]             = oem_barcode
            out["Variant Price"]               = mrp_str
            out["Variant Compare At Price"]    = mrp_str
            out["Variant Grams"]               = SHOPIFY_STATIC["variant_grams"]
            out["Variant Inventory Tracker"]   = SHOPIFY_STATIC["variant_inventory_tracker"]
            out["Variant Inventory Policy"]    = SHOPIFY_STATIC["variant_inventory_policy"]
            out["Variant Fulfillment Service"] = SHOPIFY_STATIC["variant_fulfillment_service"]
            out["Variant Requires Shipping"]   = SHOPIFY_STATIC["variant_requires_shipping"]
            out["Variant Taxable"]             = SHOPIFY_STATIC["variant_taxable"]
            out["Variant Weight Unit"]         = SHOPIFY_STATIC["variant_weight_unit"]

            # Product-level fields (first row of each group only)
            if is_first:
                out["Title"]             = title
                out["Vendor"]            = SHOPIFY_STATIC["vendor"]
                out["Published"]         = SHOPIFY_STATIC["published"]
                out["Option1 Name"]      = SHOPIFY_STATIC["option1_name"]
                out["Option1 Linked To"] = SHOPIFY_STATIC["option1_linked_to"]
                out["Option2 Name"]      = SHOPIFY_STATIC["option2_name"]
                out["Option2 Linked To"] = SHOPIFY_STATIC["option2_linked_to"]
                out["Gift Card"]         = SHOPIFY_STATIC["gift_card"]
                out["Included / India"]  = SHOPIFY_STATIC["included_india"]
                out["Status"]            = SHOPIFY_STATIC["status"]

                out["Fabric Type (product.metafields.custom.fabric)"] = fabric_type
                out["Fit Type (product.metafields.custom.fit_type)"] = fit
                out["Gender (product.metafields.custom.gender)"] = section
                out["Hemline (product.metafields.custom.hemline)"] = hl
                out["Leg (product.metafields.custom.leg)"] = leg
                out["Length (product.metafields.custom.length)"] = length_val
                out["Neck Collar (product.metafields.custom.neck_collar)"] = neck_collar
                out["Occasion (product.metafields.custom.occasion)"] = occasion
                out["Opening (product.metafields.custom.opening)"] = front
                out["Pocket (product.metafields.custom.pocket)"] = pockets
                out["Print (product.metafields.custom.print)"] = fabric_main
                out["Fabric Composition (product.metafields.custom.fabric_composition)"] = fabric_composition
                out["Product Type (product.metafields.custom.product_type)"] = department
                out["Sleeve (product.metafields.custom.sleeve)"] = sleeve_type
                out["Style Code (product.metafields.custom.style_code)"] = style_code
                out["Waist (product.metafields.custom.waist)"] = waist_val
                out["Woven or Knit (product.metafields.custom.woven_or_knit)"] = fabric_type

            output_rows.append([out.get(col, "") for col in SHOPIFY_COLUMNS])

    if not output_rows:
        raise ValueError("No valid product rows found in input file.")

    seen: set = set()
    unique_warnings: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique_warnings.append(w)

    sio = StringIO()
    writer = csv.writer(sio)
    writer.writerow(SHOPIFY_COLUMNS)
    writer.writerows(output_rows)
    csv_bytes = sio.getvalue().encode("utf-8")

    return csv_bytes, unique_warnings, cleanup.entries


# ---------------------------------------------------------------------------
# Fynd private-extension OAuth handshake (stateless)
#
# This Boltic function doubles as a self-contained Fynd private extension:
# it serves its own HTML UI and handles the Fynd install/auth OAuth handshake
# so it can be installed on a company without the separate Render-hosted Node
# shim. Because the tool makes ZERO Fynd Platform API calls, the access token
# is never persisted -- the handshake is completed and the token discarded, so
# no session storage / database is required.
# ---------------------------------------------------------------------------

HANDLER_VERSION = "cw-transformer-v10"

_STATE_COOKIE = "cw_oauth_state"
_STATE_MAX_AGE = 600  # seconds the signed install state stays valid


def _ext_config() -> dict:
    return {
        "api_key": os.environ.get("EXTENSION_API_KEY", ""),
        "api_secret": os.environ.get("EXTENSION_API_SECRET", ""),
        "base_url": os.environ.get("EXTENSION_BASE_URL", "").rstrip("/"),
        "cluster": os.environ.get("FP_API_DOMAIN", "https://api.fynd.com").rstrip("/"),
        # Comma/space separated extension scopes, as configured in the Partner panel.
        "scope": os.environ.get("EXTENSION_SCOPE", ""),
    }


def _sign_state(payload: dict, secret: str) -> str:
    """HMAC-sign a small JSON payload into a tamper-proof cookie value."""
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_state(token: str, secret: str):
    """Return the payload if the signed state is valid and unexpired, else None."""
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + pad).decode())
    except Exception:
        return None
    if time.time() - payload.get("ts", 0) > _STATE_MAX_AGE:
        return None
    return payload


def _fp_install(request):
    """OAuth start: sign install state into a cookie, redirect to Fynd authorize."""
    cfg = _ext_config()
    if not (cfg["api_key"] and cfg["api_secret"] and cfg["base_url"]):
        return jsonify({"detail": "Extension not configured (set EXTENSION_API_KEY / "
                                  "EXTENSION_API_SECRET / EXTENSION_BASE_URL)."}), 500

    company_id = request.args.get("company_id", "")
    if not company_id:
        return jsonify({"detail": "Missing company_id"}), 400
    cluster = (request.args.get("cluster") or cfg["cluster"]).rstrip("/")

    # Sign the install context into the OAuth `state` itself. Because the token is
    # HMAC-signed with a short TTL it is tamper-proof in the URL, so the handshake
    # does not depend on a cookie surviving the cross-site (iframe) round-trip.
    signed_state = _sign_state(
        {"c": company_id, "cl": cluster, "ts": int(time.time())},
        cfg["api_secret"],
    )

    authorize_url = (
        f"{cluster}/service/panel/authentication/v1.0/company/{company_id}/oauth/authorize?"
        + urllib.parse.urlencode({
            "client_id": cfg["api_key"],
            "scope": cfg["scope"],
            "redirect_uri": f"{cfg['base_url']}/fp/auth",
            "state": signed_state,
            "response_type": "code",
            "access_mode": "online",
        })
    )
    resp = make_response(redirect(authorize_url, code=302))
    # Defense-in-depth cookie (SameSite=None to survive a third-party iframe); the
    # callback does not require it -- it verifies the signed state param instead.
    resp.set_cookie(_STATE_COOKIE, signed_state, max_age=_STATE_MAX_AGE,
                    httponly=True, secure=True, samesite="None")
    return resp


def _fp_auth(request):
    """OAuth callback: verify state, exchange code for a token, discard it, land on UI."""
    cfg = _ext_config()
    code = request.args.get("code", "")
    returned_state = request.args.get("state", "")
    cookie = request.cookies.get(_STATE_COOKIE, "")

    # Verify the signed state from the URL first (cookie-independent); fall back to
    # the cookie only if the platform stripped the state param.
    payload = _verify_state(returned_state, cfg["api_secret"]) if returned_state else None
    if payload is None and cookie:
        payload = _verify_state(cookie, cfg["api_secret"])

    if not payload or not code:
        return jsonify({"detail": "OAuth state validation failed."}), 400

    company_id = request.args.get("company_id", "") or payload.get("c", "")
    cluster = (payload.get("cl") or cfg["cluster"]).rstrip("/")

    token_url = (
        f"{cluster}/service/panel/authentication/v1.0/company/{company_id}/oauth/token"
    )
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": cfg["api_key"],
        "client_secret": cfg["api_secret"],
        "redirect_uri": f"{cfg['base_url']}/fp/auth",
    }).encode()
    basic = base64.b64encode(f"{cfg['api_key']}:{cfg['api_secret']}".encode()).decode()
    req = urllib.request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()  # token obtained then intentionally discarded -- no API calls made
    except Exception as e:  # noqa: BLE001 - surface any handshake failure to the installer
        return jsonify({"detail": f"Token exchange failed: {e}"}), 502

    landing = f"{cfg['base_url']}/?company_id={urllib.parse.quote(company_id)}"
    resp = make_response(redirect(landing, code=302))
    resp.set_cookie(_STATE_COOKIE, "", max_age=0)  # clear the one-shot state cookie
    return resp


def _fp_uninstall(request):
    # Stateless tool -- nothing persistent to clean up.
    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Static UI (served at GET / -- replaces the Render Streamlit app)
# ---------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Cottonworld Catalog Converter</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; color: #1a1a2e; min-height: 100vh; }
    .layout { display: flex; min-height: 100vh; }
    .sidebar { width: 220px; flex-shrink: 0; background: #fff; border-right: 1px solid #e5e7eb; padding: 24px 14px; }
    .sidebar .brand { font-size: 13px; font-weight: 700; color: #111827; margin-bottom: 18px; padding: 0 8px; }
    .nav-item { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 9px 10px; border-radius: 8px; font-size: 14px; font-weight: 500; color: #374151; background: none; border: none; cursor: pointer; margin-bottom: 4px; }
    .nav-item:hover { background: #f3f4f6; }
    .nav-item.active { background: #eff6ff; color: #1d4ed8; font-weight: 600; }
    .main { flex: 1; padding: 32px 28px 48px; }
    .container { max-width: 820px; margin: 0 auto; }
    h1 { font-size: 26px; font-weight: 700; margin-bottom: 8px; }
    h2 { font-size: 18px; font-weight: 700; margin: 22px 0 8px; }
    h3.sub { font-size: 18px; font-weight: 700; margin: 0 0 6px; }
    p.intro { font-size: 14px; color: #374151; margin-bottom: 18px; line-height: 1.55; }
    .caption { font-size: 12px; color: #6b7280; line-height: 1.5; margin: 6px 0; }
    .md { font-size: 14px; color: #374151; line-height: 1.6; }
    .md ul, .md ol { padding-left: 22px; margin: 6px 0 12px; }
    .md li { margin-bottom: 5px; }
    .md code { background: #f3f4f6; padding: 1px 5px; border-radius: 4px; font-size: 12px; font-family: ui-monospace, monospace; }
    .md table { border-collapse: collapse; width: 100%; margin: 10px 0 16px; font-size: 12.5px; }
    .md table th, .md table td { border: 1px solid #e5e7eb; padding: 6px 9px; text-align: left; vertical-align: top; }
    .md table th { background: #f9fafb; font-weight: 600; }
    .md a { color: #2563eb; }
    .divider { height: 1px; background: #e5e7eb; border: none; margin: 22px 0; }
    .alert { border-radius: 8px; padding: 12px 16px; font-size: 13px; margin: 14px 0; line-height: 1.5; }
    .alert.warn { background: #fffbeb; border: 1px solid #f59e0b; color: #92400e; }
    .alert.info { background: #eff6ff; border: 1px solid #93c5fd; color: #1e40af; }
    .alert.ok   { background: #ecfdf5; border: 1px solid #10b981; color: #065f46; }
    .alert strong { font-weight: 600; }
    .toggle-row { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
    .toggle-label { font-size: 13px; font-weight: 600; color: #374151; }
    .seg { display: inline-flex; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; }
    .seg button { padding: 8px 18px; font-size: 13px; font-weight: 600; background: #fff; border: none; cursor: pointer; color: #374151; }
    .seg button.active { background: #2563eb; color: #fff; }
    .drop-zone { border: 2px dashed #d1d5db; border-radius: 10px; background: #fff; padding: 32px 24px; text-align: center; cursor: pointer; transition: border-color .2s, background .2s; margin-bottom: 12px; }
    .drop-zone:hover, .drop-zone.drag-over { border-color: #2563eb; background: #eff6ff; }
    .drop-zone.has-file { border-color: #10b981; background: #ecfdf5; }
    .drop-icon { font-size: 34px; margin-bottom: 6px; }
    .drop-label { font-size: 15px; font-weight: 500; color: #374151; }
    .drop-sub { font-size: 12px; color: #9ca3af; margin-top: 4px; }
    .drop-filename { font-size: 13px; color: #10b981; font-weight: 600; margin-top: 8px; }
    input[type="file"] { display: none; }
    .btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; border: none; transition: opacity .15s; }
    .btn:disabled { opacity: .45; cursor: not-allowed; }
    .btn-primary { background: #2563eb; color: #fff; }
    .btn-primary:hover:not(:disabled) { background: #1d4ed8; }
    .btn-success { background: #10b981; color: #fff; }
    .btn-success:hover { background: #059669; }
    .btn-ghost { background: #fff; color: #374151; border: 1px solid #d1d5db; }
    .btn-ghost:hover { background: #f9fafb; }
    .action-row { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
    .status { display: none; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 8px; font-size: 13px; margin: 14px 0; }
    .status.loading { display: flex; background: #eff6ff; color: #1d4ed8; }
    .status.error { display: flex; background: #fef2f2; color: #991b1b; }
    .spinner { width: 18px; height: 18px; border: 3px solid #bfdbfe; border-top-color: #2563eb; border-radius: 50%; animation: spin .7s linear infinite; flex-shrink: 0; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 14px 0; }
    .metric { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; }
    .metric .label { font-size: 12px; color: #6b7280; margin-bottom: 4px; }
    .metric .value { font-size: 24px; font-weight: 700; color: #111827; }
    .warnings ul { padding-left: 18px; margin-top: 6px; }
    .warnings li { margin-bottom: 3px; line-height: 1.4; }
    .table-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; max-height: 440px; overflow-y: auto; }
    table.data { border-collapse: collapse; min-width: 100%; font-size: 12px; }
    table.data th { background: #f9fafb; padding: 8px 12px; text-align: left; font-weight: 600; color: #374151; white-space: nowrap; border-bottom: 1px solid #e5e7eb; position: sticky; top: 0; z-index: 1; }
    table.data td { padding: 7px 12px; border-bottom: 1px solid #f3f4f6; white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; color: #4b5563; }
    table.data tr:last-child td { border-bottom: none; }
    table.data tr:hover td { background: #f9fafb; }
    details { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 4px 14px; margin: 10px 0; }
    details summary { cursor: pointer; font-size: 13px; font-weight: 600; color: #374151; padding: 8px 0; }
    .hidden { display: none !important; }
    @media (max-width: 720px) {
      .layout { flex-direction: column; }
      .sidebar { width: 100%; border-right: none; border-bottom: 1px solid #e5e7eb; display: flex; gap: 8px; padding: 12px; }
      .sidebar .brand { display: none; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
<div class="layout">
  <nav class="sidebar">
    <div class="brand">👕 Cottonworld</div>
    <button class="nav-item active" data-page="converter">📄 Converter</button>
    <button class="nav-item" data-page="howto">📖 How to use</button>
  </nav>
  <div class="main">

    <!-- ===================== CONVERTER PAGE ===================== -->
    <div class="container" id="page-converter">
      <h1>Cottonworld Catalog Converter</h1>
      <p class="intro">Upload the <strong>Logic ERP Item Master</strong> <code>.xlsx</code> file to generate a marketplace-ready upload file.</p>

      <div class="toggle-row">
        <span class="toggle-label">Target platform</span>
        <div class="seg" id="seg">
          <button data-target="fynd" class="active">Fynd</button>
          <button data-target="shopify">Shopify</button>
        </div>
      </div>
      <p class="caption" id="targetCaption"></p>

      <div class="alert warn" id="targetWarn"></div>

      <hr class="divider" />

      <p class="caption">📎 Maximum file size: <strong>10 MB</strong></p>
      <div class="drop-zone" id="dropZone">
        <div class="drop-icon">📂</div>
        <div class="drop-label">Drag &amp; drop Logic ERP Item Master (.xlsx)</div>
        <div class="drop-sub">or click to browse · max 10 MB</div>
        <div class="drop-filename" id="dropFilename"></div>
      </div>
      <input type="file" id="fileInput" accept=".xlsx" />

      <div class="alert info hidden" id="uploadInfo"></div>

      <div class="action-row">
        <button class="btn btn-primary" id="convertBtn" disabled>Convert to Fynd Template</button>
      </div>

      <div class="status" id="status"><div class="spinner"></div><span id="statusText">Transforming data…</span></div>

      <!-- ===== Results ===== -->
      <div id="results" class="hidden">
        <div class="alert ok">✅ Conversion complete!</div>

        <div class="metrics">
          <div class="metric"><div class="label">Total rows (SKUs)</div><div class="value" id="mTotal">0</div></div>
          <div class="metric"><div class="label">Unique products</div><div class="value" id="mProducts">0</div></div>
          <div class="metric"><div class="label">Warnings</div><div class="value" id="mWarnings">0</div></div>
          <div class="metric"><div class="label">Cleanups logged</div><div class="value" id="mCleanups">0</div></div>
        </div>

        <hr class="divider" />
        <h3 class="sub">Preview</h3>
        <p class="caption" id="previewCaption"></p>
        <div class="toggle-row">
          <div class="seg" id="viewSeg">
            <button data-view="key" class="active">Key columns only</button>
            <button data-view="all">All columns</button>
          </div>
        </div>
        <div class="table-wrap"><table class="data" id="previewTable"></table></div>
        <p class="caption" id="previewNote"></p>

        <hr class="divider" />
        <div class="action-row">
          <button class="btn btn-primary" id="downloadBtn">Download Output File</button>
        </div>
        <div class="alert info" id="beforeUpload"></div>

        <!-- Warnings -->
        <div id="warningsBlock" class="hidden">
          <hr class="divider" />
          <div class="alert warn" id="warningsSummary"></div>
          <details open class="warnings">
            <summary>View warnings</summary>
            <ul id="warningList"></ul>
          </details>
        </div>

        <!-- Cleanup log -->
        <div id="cleanupBlock" class="hidden">
          <hr class="divider" />
          <div class="alert info" id="cleanupSummary"></div>
          <details class="warnings">
            <summary>View cleanup log</summary>
            <div class="table-wrap" style="margin-top:8px;"><table class="data" id="cleanupTable"></table></div>
          </details>
          <div class="action-row">
            <button class="btn btn-ghost" id="cleanupDownloadBtn">Download cleanup log (CSV)</button>
          </div>
        </div>
      </div>

      <hr class="divider" />
      <p class="caption">Cottonworld Catalog Converter v4.0 | Fynd + Shopify | Pass-through mode (Name/Title derived only)</p>
    </div>

    <!-- ===================== HOW-TO PAGE ===================== -->
    <div class="container hidden" id="page-howto">
      <h1>How to use this tool</h1>
      <p class="caption">A step-by-step guide for the Cottonworld team.</p>

      <div class="alert warn">⚠️ <strong>Disclaimer:</strong> This tool automates the Logic → Fynd <strong>and</strong> Logic → Shopify mappings, but you must <strong>always verify the output file before uploading</strong>. Open the file in Excel, check product names/titles, HS codes (Fynd), MRP, and any flagged warnings. The tool is an accelerator, not a substitute for a final human review.</div>

      <div class="alert info">🎯 <strong>Pick a target platform first.</strong> On the <strong>Converter</strong> page, use the <strong>Target platform</strong> toggle to choose <strong>Fynd</strong> (multi-sheet <code>.xlsx</code>) or <strong>Shopify</strong> (product-import <code>.csv</code>). The Logic export and review steps below are the same for both — only the output format and the upload destination differ.</div>

      <hr class="divider" />
      <div class="md">
        <h2>Step 1 — Export Item Master from Logic ERP</h2>
        <ol>
          <li>Log in to <strong>Logic ERP</strong>.</li>
          <li>Go to <strong>Reports → Item Master → GSL-PO</strong> (or the equivalent PO report your team uses).</li>
          <li>Set the date range to the period you want to publish on Fynd.</li>
          <li>Export the report as an <strong><code>.xlsx</code></strong> file.</li>
          <li>Keep the file as-is — <strong>do not rename columns or delete rows</strong>.</li>
        </ol>

        <h2>Step 2 — Upload the file here</h2>
        <ol>
          <li>Open the <strong>Converter</strong> page (left sidebar).</li>
          <li>Choose your <strong>Target platform</strong> — <strong>Fynd</strong> or <strong>Shopify</strong>.</li>
          <li>Click <strong>Browse files</strong> and select the Logic <code>.xlsx</code> you exported.</li>
          <li>Click <strong>Convert to Fynd Template</strong> / <strong>Convert to Shopify CSV</strong>.</li>
          <li>Wait a few seconds while the tool processes the file.</li>
        </ol>

        <h2>Step 3 — Review warnings and the cleanup log</h2>
        <p>After conversion, the tool surfaces two things to review:</p>
        <p><strong>Warnings</strong> (only fired when something needs your attention):</p>
        <ul>
          <li><strong>No HSN mapping for (Section, Department)</strong> — the tool doesn't have an HS Code for that combination. The HS Code field will be <strong>blank</strong> — fill it in manually before upload, and flag it so we can add the mapping permanently.</li>
        </ul>
        <p><strong>Cleanup log</strong> (always shown when any cell was touched):</p>
        <ul>
          <li>The tool only ever makes two mutations: (a) trimming leading/trailing whitespace, and (b) stripping the trailing <code>.0</code> Excel adds to integer ID columns (Style No, Fabric No., OEM Barcode, Order No).</li>
          <li>Every such mutation is logged with the <strong>Logic Excel row number, column name, original value, cleaned value, and reason</strong>.</li>
          <li>You can download the cleanup log as a CSV for your records.</li>
        </ul>
        <p>Both are <strong>advisory</strong> — the file is still generated. Treat them as a checklist of things to spot-check.</p>

        <h2>Step 4 — Download and verify</h2>
        <ol>
          <li>Click <strong>Download Fynd Upload File</strong>.</li>
          <li>Open the file in Excel or Google Sheets.</li>
          <li>Spot-check at least <strong>5–10 products</strong> across different sections and departments:
            <ul>
              <li><strong>Name</strong> reads like <em>MENS TSHIRT REGULAR FIT BLACK</em> (raw, all caps from Logic, <code>(NIL)</code> segments skipped)</li>
              <li><strong>Item Code</strong> format: <code>M-TSHIRT-17656-21646-BLACK</code> (first letter of Section, then Dept-Style-Fabric-Color verbatim from Logic)</li>
              <li><strong>HS Code</strong> is 8 digits and matches the expected tariff code</li>
              <li><strong>Actual Price / Selling Price</strong> = Logic MRP (e.g. <code>499.00</code>, <code>499.50</code> — 2-decimal preserved)</li>
              <li><strong>Size</strong> is whatever Logic put in <code>PACK / SIZE</code>, verbatim</li>
              <li><strong>Colour / Material</strong> = Logic <code>COLOR</code> / <code>COMPOSITION1</code>, verbatim</li>
              <li><strong>Custom Attribute 1</strong> = Logic <code>DEPARTMENT</code>, verbatim</li>
            </ul>
          </li>
          <li>If anything looks off, re-export from Logic and re-run — or fix in Excel directly.</li>
        </ol>

        <h2>Step 5 — Upload to Fynd Commerce Platform</h2>
        <ol>
          <li>Log in to <strong>Fynd Commerce Platform</strong> (Cottonworld company).</li>
          <li>Go to <strong>Products → Bulk Upload</strong> (or the equivalent path).</li>
          <li>Choose the <strong>Supplementary Upload</strong> template.</li>
          <li>Upload the file you downloaded from this tool.</li>
          <li>Watch the Fynd validation report — if any row fails, the error message will tell you which column is wrong. Fix it in the file and re-upload.</li>
        </ol>

        <hr class="divider" />
        <h2>What the tool does automatically</h2>
        <table>
          <thead><tr><th>Field</th><th>Rule</th></tr></thead>
          <tbody>
            <tr><td><strong>Product Name</strong></td><td><code>SECTION DEPARTMENT FIT COLOR</code> — raw verbatim from Logic, empty / <code>(NIL)</code> segments skipped (e.g. <code>MENS TSHIRT REGULAR FIT BLACK</code>)</td></tr>
            <tr><td><strong>Item Code</strong></td><td><code>{FirstLetterOfSection}-DEPT-STYLE-FABRIC-COLOR</code> (e.g. <code>M-TSHIRT-17656-21646-BLACK</code>). LADIES → <code>L</code>. One code per product, shared across size variants</td></tr>
            <tr><td><strong>Brand</strong></td><td><code>cottonworld</code> (fixed)</td></tr>
            <tr><td><strong>Category</strong></td><td><code>Others level 3</code> (fixed)</td></tr>
            <tr><td><strong>Tax Rule</strong></td><td><code>Tiered Tax Rule – 5% &amp; 18% (Eff. 22 Sep 2025) (2)</code></td></tr>
            <tr><td><strong>HS Code</strong></td><td>Looked up from the <strong>Section + Department</strong> HSN table</td></tr>
            <tr><td><strong>Country of Origin</strong></td><td><code>India</code></td></tr>
            <tr><td><strong>Dimensions</strong></td><td>1 × 1 × 1 cm, 200 g (placeholder — update in Fynd if needed)</td></tr>
            <tr><td><strong>Trader / Marketer</strong></td><td>Lekhraj Corp Pvt Ltd (Colaba)</td></tr>
            <tr><td><strong>Return policy</strong></td><td>30 Days</td></tr>
            <tr><td><strong>Net Quantity</strong></td><td>1, unit <code>number</code> (fixed)</td></tr>
            <tr><td><strong>Prices (MRP / RATE)</strong></td><td>Pass-through, 2-decimal preserved (<code>499.00</code>, <code>499.50</code>)</td></tr>
            <tr><td><strong>Numeric IDs (Style No, Fabric No., Order No, OEM Barcode)</strong></td><td>Pass-through; trailing <code>.0</code> from Excel stripped and logged</td></tr>
            <tr><td><strong>All other fields</strong> (Size, Colour, Material, Fit, Custom Attrs, Sleeve, Collar, etc.)</td><td><strong>Pass-through verbatim from Logic</strong> — no title casing, no mapping, no blanking of <code>(NIL)</code></td></tr>
            <tr><td><strong>Cleanup log</strong></td><td>Every whitespace trim / <code>.0</code> strip is logged with row + column + reason, exportable as CSV</td></tr>
          </tbody>
        </table>

        <hr class="divider" />
        <h2>Common issues &amp; fixes</h2>
        <p><strong>Error: "Could not find 'OEM_BARCODE' header row in input file."</strong></p>
        <ul><li>You uploaded a file that isn't the Logic Item Master export. Re-export from Logic.</li></ul>
        <p><strong>Error: "Input file missing required columns"</strong></p>
        <ul>
          <li>Logic export is missing one of: <code>OEM_BARCODE</code>, <code>SECTION</code>, <code>DEPARTMENT</code>, <code>STYLE NO</code>, <code>FABRIC NO.</code>, <code>COLOR</code>, <code>PACK / SIZE</code>, <code>MRP</code>.</li>
          <li>Don't rename or delete columns in Logic before exporting.</li>
        </ul>
        <p><strong>HS Code is blank for some rows</strong></p>
        <ul>
          <li>That Section + Department combination is missing from the HSN table.</li>
          <li>Tell the tool owner (or raise a PR on <a href="https://github.com/kedarkulkarni11/cottonworld-fynd-automation" target="_blank" rel="noopener">GitHub</a>) to add it — one line in <code>data/hsn_lookup.csv</code>.</li>
          <li>As a one-off, fill the HS Code manually in the downloaded file.</li>
        </ul>
        <p><strong>A field value looks "ugly" (all caps, weird spacing, <code>(NIL)</code>)</strong></p>
        <ul><li>That is by design — the tool now passes Logic values through verbatim so what you see on Fynd matches what's in Logic. If a value needs to be cleaned up, fix it at source in Logic (or fix in the output file before upload).</li></ul>
        <p><strong>Cleanup log has lots of entries</strong></p>
        <ul><li>The tool only ever trims whitespace or strips Excel's <code>.0</code> artifact on integer ID columns. These are safe, mechanical cleanups — review them if you want, but no action is required.</li></ul>

        <hr class="divider" />
        <h2>Shopify flow (Logic → Shopify)</h2>
        <p>Switch the <strong>Target platform</strong> toggle to <strong>Shopify</strong> to produce a Shopify <strong>product-import <code>.csv</code></strong> instead of the Fynd <code>.xlsx</code>. The Logic export (Step 1) and the review habits (Steps 3–4) are identical — only these differ:</p>
        <ul>
          <li><strong>Output:</strong> a single <code>.csv</code> (Shopify's native import format), not a multi-sheet workbook. There is <strong>no HS Code lookup</strong> on this path.</li>
          <li><strong>Grouping:</strong> size variants are grouped into one product via a shared <strong><code>Handle</code></strong>; product-level fields (Title, metafields) are written on the first variant row, variant-level fields (Option values, SKU, Barcode, Price) on every row.</li>
          <li><strong>What's derived:</strong> <code>Title</code> and the <code>Style Code</code> metafield use the same <code>SECTION DEPARTMENT FIT COLOR</code> concatenation as the Fynd Name. Everything else is pass-through or a fixed Shopify default (<code>Vendor=Cottonworld</code>, <code>Status=draft</code>, option linkage to Shopify size/colour metafields, etc.).</li>
          <li><strong>Upload:</strong> in Shopify admin go to <strong>Products → Import</strong>, choose the downloaded <code>.csv</code>, and review Shopify's import preview before confirming. Products land as <strong>draft</strong> — publish after a spot-check.</li>
        </ul>
      </div>

      <hr class="divider" />
      <p class="caption">Need help? Contact the Fynd team who owns this tool, or raise an issue on the GitHub repository.</p>
    </div>

  </div>
</div>

<script>
  const TRANSFORMER_URL = '';            // same origin -- POST to /transform
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const TARGETS = {
    fynd: {
      name: 'Fynd',
      btn: 'Convert to Fynd Template',
      download_label: 'Download Fynd Upload File',
      out_suffix: '_fynd_upload.xlsx',
      title_col: 'Name',
      kind: 'xlsx',
      caption: 'All sections (Mens, Ladies, Boys, Unisex) and departments are supported. HS Code is resolved from the Section + Department HSN lookup.',
      key_cols: ['Name','Item Code','Brand','Category','HS Code','Gtin Value','Size','Actual Price','Currency','Colour','Material','Custom Attribute 1','Custom Attribute 2','Custom Attribute 3','Custom Attribute 5','Custom Attribute 7','Custom Attribute 14','Custom Attribute 20'],
    },
    shopify: {
      name: 'Shopify',
      btn: 'Convert to Shopify CSV',
      download_label: 'Download Shopify Import CSV',
      out_suffix: '_shopify_import.csv',
      title_col: 'Title',
      kind: 'csv',
      caption: 'Produces a Shopify product-import CSV. One product per Style + Fabric + Color; sizes become variants.',
      key_cols: ['Handle','Title','Vendor','Option1 Value','Option2 Value','Variant SKU','Variant Price','Gender (product.metafields.custom.gender)','Product Type (product.metafields.custom.product_type)','Fit Type (product.metafields.custom.fit_type)','Fabric Composition (product.metafields.custom.fabric_composition)','Style Code (product.metafields.custom.style_code)','Status'],
    },
  };

  let target = 'fynd';
  let selectedFile = null;
  let result = null;       // { rows, header, fileBlob, warnings, cleanup, target, sourceName }
  let previewView = 'key';

  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  // ----- Page navigation -----
  document.querySelectorAll('.nav-item').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n === b));
    const page = b.dataset.page;
    $('page-converter').classList.toggle('hidden', page !== 'converter');
    $('page-howto').classList.toggle('hidden', page !== 'howto');
    window.scrollTo(0, 0);
  }));

  // ----- Target toggle -----
  const seg = $('seg');
  seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    target = b.dataset.target;
    [...seg.children].forEach(c => c.classList.toggle('active', c === b));
    applyTarget();
    resetResults();
  });

  function applyTarget() {
    const t = TARGETS[target];
    $('targetCaption').textContent = t.caption;
    $('targetWarn').innerHTML = '⚠️ <strong>Always review the generated file before uploading to ' + t.name + '.</strong> This tool automates the mapping but does not guarantee correctness for every row — open the output, spot-check titles, prices, and any flagged warnings below before bulk upload.';
    $('convertBtn').textContent = t.btn;
  }

  // ----- File handling -----
  const dropZone = $('dropZone'), fileInput = $('fileInput');
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); });

  function handleFile(file) {
    if (!file || !file.name.endsWith('.xlsx')) { alert('Please select a .xlsx file.'); return; }
    if (file.size > MAX_FILE_BYTES) { alert('File is too large (' + (file.size/1048576).toFixed(1) + ' MB). Max 10 MB.'); fileInput.value = ''; return; }
    selectedFile = file;
    $('dropFilename').textContent = '📄 ' + file.name;
    dropZone.classList.add('has-file');
    $('uploadInfo').innerHTML = 'Uploaded: <strong>' + esc(file.name) + '</strong> (' + (file.size/1024).toFixed(1) + ' KB)';
    $('uploadInfo').classList.remove('hidden');
    $('convertBtn').disabled = false;
    resetResults();
  }

  function resetResults() {
    result = null;
    $('status').className = 'status';
    $('results').classList.add('hidden');
  }

  // ----- Convert -----
  $('convertBtn').addEventListener('click', async () => {
    if (!selectedFile) return;
    resetResults();
    const reqTarget = target;
    $('convertBtn').disabled = true;
    $('status').className = 'status loading';
    $('statusText').textContent = 'Transforming data…';

    const form = new FormData();
    form.append('file', selectedFile);
    form.append('target', reqTarget);
    form.append('mode', 'json');

    try {
      const res = await fetch(TRANSFORMER_URL + '/transform', { method: 'POST', body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'HTTP ' + res.status }));
        throw new Error(err.detail || ('HTTP ' + res.status));
      }
      const data = await res.json();
      const bytes = Uint8Array.from(atob(data.file_b64), c => c.charCodeAt(0));
      const fileBlob = new Blob([bytes], { type: data.content_type });

      // Parse the generated file into rows for preview + metrics.
      let rows;
      if (TARGETS[reqTarget].kind === 'csv') {
        const wb = XLSX.read(new TextDecoder().decode(bytes), { type: 'string' });
        rows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: '' });
      } else {
        const wb = XLSX.read(bytes, { type: 'array' });
        rows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: '' });
      }

      result = {
        rows: rows.slice(1),
        header: rows[0] || [],
        fileBlob,
        warnings: data.warnings || [],
        cleanup: data.cleanup || [],
        target: reqTarget,
        sourceName: selectedFile.name,
      };
      $('status').className = 'status';
      renderResults();
    } catch (err) {
      $('status').className = 'status error';
      $('statusText').textContent = '❌ ' + err.message;
    } finally {
      $('convertBtn').disabled = false;
    }
  });

  function renderResults() {
    const t = TARGETS[result.target];
    const totalRows = result.rows.length;
    const titleIdx = result.header.indexOf(t.title_col);
    let products = 0;
    if (titleIdx >= 0) result.rows.forEach(r => { if (String(r[titleIdx] ?? '').trim() !== '') products++; });

    $('mTotal').textContent = totalRows;
    $('mProducts').textContent = products;
    $('mWarnings').textContent = result.warnings.length;
    $('mCleanups').textContent = result.cleanup.length;

    $('previewCaption').textContent = 'First 20 rows of the generated file. Toggle below to see only key columns or the full template (' + result.header.length + ' columns).';
    previewView = 'key';
    [...$('viewSeg').children].forEach(c => c.classList.toggle('active', c.dataset.view === 'key'));
    renderPreview();
    $('previewNote').textContent = 'Showing ' + Math.min(20, totalRows) + ' of ' + totalRows + ' rows. Download the full file below to see everything.';

    $('beforeUpload').innerHTML = '📌 <strong>Before uploading to ' + t.name + ':</strong> open the downloaded file, verify a few product rows (Title/Name, Price, key attributes), and review any warnings listed below.';

    // Warnings
    if (result.warnings.length) {
      $('warningsSummary').innerHTML = '<strong>' + result.warnings.length + ' warning(s) during conversion</strong> — the file was generated, but review these before uploading to Fynd:';
      $('warningList').innerHTML = result.warnings.map(w => '<li>' + esc(w) + '</li>').join('');
      $('warningsBlock').classList.remove('hidden');
    } else {
      $('warningsBlock').classList.add('hidden');
    }

    // Cleanup log
    if (result.cleanup.length) {
      $('cleanupSummary').innerHTML = '<strong>' + result.cleanup.length + ' cell(s) cleaned up during conversion.</strong> These are minor mutations (whitespace trims, Excel <code>.0</code> float artifacts on numeric IDs) — semantic values were not changed. Review the table below or download as CSV for your records.';
      buildCleanupTable();
      $('cleanupBlock').classList.remove('hidden');
    } else {
      $('cleanupBlock').classList.add('hidden');
    }

    $('results').classList.remove('hidden');
  }

  // ----- Preview view toggle -----
  $('viewSeg').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    previewView = b.dataset.view;
    [...$('viewSeg').children].forEach(c => c.classList.toggle('active', c === b));
    renderPreview();
  });

  function renderPreview() {
    if (!result) return;
    const t = TARGETS[result.target];
    let cols = result.header.map((h, i) => i);
    if (previewView === 'key') {
      const keyIdx = t.key_cols.map(c => result.header.indexOf(c)).filter(i => i >= 0);
      if (keyIdx.length) cols = keyIdx;
    }
    const table = $('previewTable');
    table.innerHTML = '';
    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    cols.forEach(i => { const th = document.createElement('th'); const v = result.header[i]; th.textContent = v; th.title = v; hrow.appendChild(th); });
    thead.appendChild(hrow); table.appendChild(thead);
    const tbody = document.createElement('tbody');
    result.rows.slice(0, 20).forEach(row => {
      const tr = document.createElement('tr');
      cols.forEach(i => { const td = document.createElement('td'); const v = String(row[i] ?? ''); td.textContent = v; td.title = v; tr.appendChild(td); });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }

  function buildCleanupTable() {
    const header = ['Excel Row', 'Column', 'Original', 'Cleaned', 'Reason'];
    const table = $('cleanupTable');
    table.innerHTML = '';
    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    header.forEach(h => { const th = document.createElement('th'); th.textContent = h; hrow.appendChild(th); });
    thead.appendChild(hrow); table.appendChild(thead);
    const tbody = document.createElement('tbody');
    result.cleanup.forEach(entry => {
      const tr = document.createElement('tr');
      entry.forEach(c => { const td = document.createElement('td'); const v = String(c ?? ''); td.textContent = v; td.title = v; tr.appendChild(td); });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }

  // ----- Downloads -----
  $('downloadBtn').addEventListener('click', () => {
    if (!result) return;
    const t = TARGETS[result.target];
    const name = result.sourceName.replace(/\\.xlsx$/i, '') + t.out_suffix;
    triggerDownload(result.fileBlob, name);
  });

  $('cleanupDownloadBtn').addEventListener('click', () => {
    if (!result || !result.cleanup.length) return;
    const header = ['Excel Row', 'Column', 'Original', 'Cleaned', 'Reason'];
    const csv = [header, ...result.cleanup].map(row =>
      row.map(c => { const v = String(c ?? ''); return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(',')
    ).join('\\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const name = result.sourceName.replace(/\\.xlsx$/i, '') + '_cleanup_log.csv';
    triggerDownload(blob, name);
  });

  function triggerDownload(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  applyTarget();
</script>
</body>
</html>"""


def _serve_ui():
    resp = make_response(INDEX_HTML)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://*.fynd.com https://*.fyndx0.com "
        "https://*.fyndx1.com https://*.fyndx5.com;"
    )
    return resp


# ---------------------------------------------------------------------------
# Boltic handler (path-routed)
# ---------------------------------------------------------------------------

def _cors_preflight():
    res = make_response("", 204)
    res.headers["Access-Control-Allow-Origin"] = "*"
    res.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    res.headers["Access-Control-Allow-Headers"] = "Content-Type"
    res.headers["Access-Control-Expose-Headers"] = "X-Warnings, X-Cleanup-Count, Content-Disposition"
    return res


def _handle_transform(request):
    if "file" not in request.files:
        return jsonify({"detail": "No file uploaded. Send xlsx as field 'file'."}), 400

    f = request.files["file"]
    if not f.filename.endswith(".xlsx"):
        return jsonify({"detail": "Only .xlsx files are supported"}), 400

    # Target platform: 'fynd' (default) | 'shopify' -- from form field or query.
    target = (request.form.get("target")
              or request.args.get("target")
              or "fynd").strip().lower()
    if target not in ("fynd", "shopify"):
        return jsonify({"detail": "Invalid 'target'. Use 'fynd' or 'shopify'."}), 400

    try:
        if target == "shopify":
            body_bytes, warnings, cleanup_entries = transform_shopify(f.read())
        else:
            body_bytes, warnings, cleanup_entries = transform(f.read())
    except ValueError as e:
        return jsonify({"detail": str(e)}), 400
    except Exception as e:
        return jsonify({"detail": f"Transform failed: {str(e)}"}), 500

    # JSON envelope mode (used by the bundled UI): returns the file as base64
    # plus the full warnings list and the cleanup audit log, so the page can
    # render metrics + the cleanup table + a cleanup CSV. Default stays the raw
    # file download for any other/legacy caller.
    mode = (request.form.get("mode") or request.args.get("mode") or "").strip().lower()
    if mode == "json":
        payload = {
            "target": target,
            "filename": "shopify_import.csv" if target == "shopify" else "fynd_catalog_output.xlsx",
            "content_type": "text/csv" if target == "shopify" else
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "file_b64": base64.b64encode(body_bytes).decode("ascii"),
            "warnings": warnings,
            # Each entry: [excel_row, column, original, cleaned, reason]
            "cleanup": cleanup_entries,
        }
        resp = jsonify(payload)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    warnings_safe = "||".join(warnings).encode("latin-1", errors="replace").decode("latin-1")

    response = make_response(body_bytes)
    if target == "shopify":
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="shopify_import.csv"'
    else:
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = 'attachment; filename="fynd_catalog_output.xlsx"'
    response.headers["X-Warnings"] = warnings_safe
    response.headers["X-Cleanup-Count"] = str(len(cleanup_entries))
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "X-Warnings, X-Cleanup-Count, Content-Disposition"
    return response


def handler(request):
    method = request.method
    path = (getattr(request, "path", "/") or "/").rstrip("/") or "/"

    if method == "OPTIONS":
        return _cors_preflight()

    if path == "/healthz":
        res = jsonify({"status": "ok", "version": HANDLER_VERSION})
        res.headers["Access-Control-Allow-Origin"] = "*"
        return res

    # Fynd extension OAuth handshake
    if path == "/fp/install" and method == "GET":
        return _fp_install(request)
    if path == "/fp/auth" and method == "GET":
        return _fp_auth(request)
    if path == "/fp/uninstall":
        return _fp_uninstall(request)

    # Transform API (accept at /transform and at base for backward compatibility)
    if path in ("/transform", "/") and method == "POST":
        return _handle_transform(request)

    # UI
    if path == "/" and method == "GET":
        return _serve_ui()

    # Any other GET -> version/health JSON (kept for existing callers/monitors)
    if method == "GET":
        res = jsonify({"status": "ok", "version": HANDLER_VERSION})
        res.headers["Access-Control-Allow-Origin"] = "*"
        return res

    return jsonify({"detail": "Not found"}), 404
