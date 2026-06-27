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

import csv
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO, StringIO
from typing import Any

from flask import jsonify, make_response

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
    "length_cm": 1, "width_cm": 1, "height_cm": 1, "weight_gram": 200,
    "trader_type": "Manufacturer",
    "trader_name": "Lekhraj Corp Pvt Ltd",
    "trader_address": "GALA-F, SIDHWA ESTATE, OLD BMP BUILDING, N.A. SAWANT MARG, Colaba, Mumbai City, Maharashtra, 400005",
    "return_time_limit": 30, "return_time_unit": "Days",
    "net_quantity_value": 1, "net_quantity_unit": "number",
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
    ("LADIES", "WAIST COAT"): "62113200",
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
    "published": "0",
    "option1_name": "Size",
    "option1_linked_to": "product.metafields.shopify.size",
    "option2_name": "color",
    "option2_linked_to": "product.metafields.shopify.color-pattern",
    "variant_grams": 200,
    "variant_inventory_tracker": "Shopify",
    "variant_inventory_policy": "Deny",
    "variant_fulfillment_service": "Manual",
    "variant_requires_shipping": "1",
    "variant_taxable": "1",
    "gift_card": "0",
    "variant_weight_unit": "Kg",
    "included_india": "1",
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
                out["Material"]          = composition1
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

        title = build_name(section, department, fit, color)
        style_code = title
        handle = shopify_handle(style_no, fabric_no, color)
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
# Boltic handler
# ---------------------------------------------------------------------------

def handler(request):
    if request.method == "OPTIONS":
        res = make_response("", 204)
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type"
        res.headers["Access-Control-Expose-Headers"] = "X-Warnings, X-Cleanup-Count, Content-Disposition"
        return res

    if request.method == "GET":
        res = jsonify({"status": "ok", "version": "cw-transformer-v5"})
        res.headers["Access-Control-Allow-Origin"] = "*"
        return res

    if request.method != "POST":
        return jsonify({"detail": "Method not allowed"}), 405

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
