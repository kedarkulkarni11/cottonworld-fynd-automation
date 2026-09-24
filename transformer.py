"""
Cottonworld Logic ERP → Fynd Platform Template Transformer

Per the June 2026 client spec ("as-is" rules):
- Only Name and Item Code are derived. Every other field is passed through
  verbatim from Logic ERP — no title-casing, no display-name mapping, no
  blanking of (NIL).
- Cleanups (whitespace trim, .0 strip on numeric IDs) are LOGGED so the
  operator can audit what changed. Cleanups surface in the UI and as a
  downloadable CSV.
- Prices (MRP → Actual / Selling Price, RATE) preserve their decimal
  representation (e.g. 499.00, 499.50).
- HS Code looked up by raw (SECTION, DEPARTMENT) pair against
  data/hsn_lookup.csv. Misses emit a warning.

Returns (BytesIO, warnings, output_df, cleanup_df) so the UI can show
warnings AND a cleanup audit log.
"""

from __future__ import annotations

import json
import math
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd


DATA_DIR = Path(__file__).parent / "data"


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_hsn_lookup() -> dict[tuple[str, str], str]:
    df = pd.read_csv(DATA_DIR / "hsn_lookup.csv", dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    lookup: dict[tuple[str, str], str] = {}
    for _, row in df.iterrows():
        section = str(row["section"]).strip().upper()
        dept = str(row["department"]).strip().upper()
        hs_code = str(row["hs_code"]).strip()
        lookup[(section, dept)] = hs_code
    return lookup


STATIC = _load_json("static_values.json")
HSN_LOOKUP = _load_hsn_lookup()


# ---------------------------------------------------------------------------
# Fynd output schema
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Cleanup log
# ---------------------------------------------------------------------------

class CleanupLog:
    """Audit trail of every cell mutation the transformer made (whitespace
    trim, .0 stripping on numeric IDs). The operator gets this as a CSV so
    they can verify nothing semantic was changed."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, *, excel_row: int, column: str, original: str,
            cleaned: str, reason: str) -> None:
        self.entries.append({
            "Logic Row (Excel)": excel_row,
            "Column": column,
            "Original Value": original,
            "Cleaned Value": cleaned,
            "Reason": reason,
        })

    def to_dataframe(self) -> pd.DataFrame:
        if not self.entries:
            return pd.DataFrame(
                columns=["Logic Row (Excel)", "Column", "Original Value",
                         "Cleaned Value", "Reason"]
            )
        return pd.DataFrame(self.entries)


# ---------------------------------------------------------------------------
# Value handling — strict "as-is" with logged cleanups
# ---------------------------------------------------------------------------

def _is_na(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False


def passthrough(val: Any, excel_row: int, col_name: str,
                log: CleanupLog) -> str:
    """Trim trailing/leading whitespace; preserve everything else verbatim
    (including '(NIL)'). Log any whitespace trim that actually mutated the
    value."""
    if _is_na(val):
        return ""
    # Excel may store integer-typed cells as float (e.g. 17656.0).
    # That's a pandas artifact, not a semantic change — but it would corrupt
    # string fields, so coerce to int representation first.
    if isinstance(val, float) and not math.isnan(val) and val == int(val):
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
    """For numeric IDs (STYLE NO, FABRIC NO., ORDER NO, OEM_BARCODE) —
    strip trailing '.0' that pandas introduces when reading integer cells
    from Excel as floats. Logged because we're modifying the displayed
    representation."""
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
        # Non-whole float — preserve verbatim
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
    """Preserve price values exactly as Logic emits them.

    Logic always writes MRP with two decimal places (e.g. 499.00, 499.50).
    Pandas converts both to float and would render `499.0`; we restore the
    two-decimal display."""
    if _is_na(val):
        return ""
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return str(val).strip()


def format_packed_date(raw: Any) -> str:
    """Force Packed Date to text in mm-yyyy format (e.g. '10-2023').

    Confirmed by client (transcript): 'mm-yyyy format चाहिए'. Logic
    sometimes emits this as an Excel date object; sometimes as a string.
    We normalize either way."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    if isinstance(raw, pd.Timestamp):
        if pd.isna(raw):
            return ""
        return raw.strftime("%m-%Y")
    try:
        dt = pd.to_datetime(raw, errors="coerce", dayfirst=False)
        if pd.notna(dt):
            return dt.strftime("%m-%Y")
    except Exception:
        pass
    s = str(raw).strip() if raw is not None else ""
    if re.fullmatch(r"\d{1,2}-\d{4}", s):
        m, y = s.split("-")
        return f"{int(m):02d}-{y}"
    return s


# ---------------------------------------------------------------------------
# Name & Item Code builders (the ONLY derived fields)
# ---------------------------------------------------------------------------

def _is_nil(s: str) -> bool:
    return s.upper() == "(NIL)"


def build_name(section: str, department: str, fit: str, color: str) -> str:
    """Format: [SECTION] [DEPARTMENT] [FIT] [COLOR] — raw, verbatim from
    Logic. Per client: skip empty / (NIL) segments so the Name doesn't
    contain literal '(NIL)' or double spaces.

    Examples:
        MENS, TSHIRT, REGULAR FIT, BLACK -> 'MENS TSHIRT REGULAR FIT BLACK'
        MENS, TSHIRT, (NIL),       BLACK -> 'MENS TSHIRT BLACK'
    """
    parts: list[str] = []
    for v in (section, department, fit, color):
        s = (v or "").strip()
        if s and not _is_nil(s):
            parts.append(s)
    return " ".join(parts)


def build_fabric_composition(*comps: str) -> str:
    """Material = space-joined COMPOSITION1/2/3, skipping empty / (NIL)
    segments (mirrors build_name's segment handling)."""
    parts: list[str] = []
    for v in comps:
        s = (v or "").strip()
        if s and not _is_nil(s):
            parts.append(s)
    return " ".join(parts)


def build_item_code(section: str, department: str, style_no: str,
                    fabric_no: str, color: str) -> str:
    """Format: {first-letter-of-SECTION}-DEPARTMENT-STYLE_NO-FABRIC_NO-COLOR

    Per client (transcript): the section prefix is the first letter of the
    raw SECTION value (MENS -> M, LADIES -> L, BOYS -> B, GIRLS -> G,
    UNISEX -> U). Skip empty / (NIL) segments per the E2 decision.

    Examples:
        MENS, TSHIRT, 17656, 21646, BLACK -> 'M-TSHIRT-17656-21646-BLACK'
        MENS, TSHIRT, 17656, 21646, (NIL) -> 'M-TSHIRT-17656-21646'
    """
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


# ---------------------------------------------------------------------------
# HS Code lookup
# ---------------------------------------------------------------------------

def lookup_hs_code(section: str, department: str,
                   warnings: list[str]) -> str:
    sec = (section or "").strip().upper()
    dept = (department or "").strip().upper()
    if not sec or not dept or _is_nil(sec) or _is_nil(dept):
        return ""
    hs = HSN_LOOKUP.get((sec, dept))
    if hs is None:
        warnings.append(
            f"No HSN mapping for Section='{sec}', Department='{dept}'. "
            f"HS Code left blank — please add to data/hsn_lookup.csv."
        )
        return ""
    return hs


# ---------------------------------------------------------------------------
# Column discovery (Logic header names vary in case/spacing/punctuation)
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    norm_map = {_normalize(c): c for c in df.columns}
    for cand in candidates:
        n = _normalize(cand)
        if n in norm_map:
            return norm_map[n]
    return None


def find_header_row(df_raw: pd.DataFrame) -> int | None:
    for idx, row in df_raw.iterrows():
        for val in row.values:
            if str(val).strip().upper() == "OEM_BARCODE":
                return idx
    return None


# ---------------------------------------------------------------------------
# Main transform
# ---------------------------------------------------------------------------

def transform(input_file) -> tuple[BytesIO, list[str], pd.DataFrame, pd.DataFrame]:
    """Read Logic ERP xlsx → Fynd Platform bulk-upload xlsx.

    Returns:
        output_buf: BytesIO of the Fynd .xlsx
        warnings:   de-duped list of warning strings (missing HSN, etc.)
        output_df:  the output DataFrame (for UI preview)
        cleanup_df: audit log of every cell mutation (for UI / CSV download)
    """
    warnings: list[str] = []
    cleanup = CleanupLog()

    df_raw = pd.read_excel(input_file, header=None, sheet_name=0)
    header_idx = find_header_row(df_raw)
    if header_idx is None:
        raise ValueError("Could not find 'OEM_BARCODE' header row in input file.")

    if hasattr(input_file, "seek"):
        input_file.seek(0)
    df = pd.read_excel(input_file, header=header_idx, sheet_name=0)

    # Resolve Logic columns
    col_oem = find_col(df, "OEM_BARCODE")
    col_section = find_col(df, "SECTION")
    col_dept = find_col(df, "DEPARTMENT")
    col_style_no = find_col(df, "STYLE NO", "STYLE_NO", "STYLENO")
    col_fabric_no = find_col(df, "FABRIC NO.", "FABRIC_NO", "FABRICNO")
    col_color = find_col(df, "COLOR")
    col_size = find_col(df, "PACK / SIZE", "PACK_SIZE", "PACKSIZE", "PACK/SIZE")
    col_fab_main = find_col(df, "FABRIC MAIN DESC", "FABRIC_MAIN_DESC")
    col_fab_sub = find_col(df, "FABRIC SUB DESC", "FABRIC_SUB_DESC")
    col_fab_type = find_col(df, "FABRIC TYPE", "FABRIC_TYPE")
    col_fab_subtype = find_col(df, "FABRIC SUB TYPE", "FABRIC_SUB_TYPE")
    col_hl = find_col(df, "HL")
    col_sleeve = find_col(df, "SLEEVE TYPE", "SLEEVE_TYPE")
    col_fit = find_col(df, "FIT")
    col_occasion = find_col(df, "OCCASION")
    col_pockets = find_col(df, "POCKETS")
    col_neck = find_col(df, "NECK-COLLAR", "NECK_COLLAR", "NECKCOLLAR")
    col_length = find_col(df, "LENGTH")
    col_waist = find_col(df, "WAIST")
    col_closure = find_col(df, "CLOSURE")
    col_leg = find_col(df, "LEG")
    col_front = find_col(df, "FRONT")
    col_comp1 = find_col(df, "COMPOSITION1")
    col_comp2 = find_col(df, "COMPOSITION2")
    col_comp3 = find_col(df, "COMPOSITION3")
    col_packed_date = find_col(df, "PACKED DATE", "PACKED_DATE")
    col_cs = find_col(df, "CS")
    col_rate = find_col(df, "RATE")
    col_order_no = find_col(df, "ORDER NO", "ORDER_NO")
    col_mrp = find_col(df, "MRP")

    required = {
        "OEM_BARCODE": col_oem,
        "SECTION": col_section,
        "DEPARTMENT": col_dept,
        "STYLE NO": col_style_no,
        "FABRIC NO.": col_fabric_no,
        "COLOR": col_color,
        "PACK / SIZE": col_size,
        "MRP": col_mrp,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"Input file missing required columns: {', '.join(missing)}")

    df = df[df[col_oem].notna()].copy()

    # Excel row number for cleanup log: pandas index 0 maps to Excel row
    # (header_idx + 2) because header_idx is 0-based and Excel is 1-based +
    # we skip the header row itself.
    df["_excel_row"] = df.index + header_idx + 2

    # Grouping: one Fynd product per (STYLE_NO + FABRIC_NO + COLOR)
    df["_group_key"] = (
        df[col_style_no].astype(str).str.strip() + "|"
        + df[col_fabric_no].astype(str).str.strip() + "|"
        + df[col_color].astype(str).str.strip()
    )

    output_rows: list[dict] = []

    for _, group_df in df.groupby("_group_key", sort=False):
        first = group_df.iloc[0]
        first_row_excel = int(first["_excel_row"])

        def pt(col: str | None, key: str) -> str:
            if col is None:
                return ""
            return passthrough(first.get(col), first_row_excel, key, cleanup)

        def pid(col: str | None, key: str) -> str:
            if col is None:
                return ""
            return passthrough_id(first.get(col), first_row_excel, key, cleanup)

        # Product-level fields (read from first row of group)
        section = pt(col_section, "SECTION")
        department = pt(col_dept, "DEPARTMENT")
        style_no = pid(col_style_no, "STYLE NO")
        fabric_no = pid(col_fabric_no, "FABRIC NO.")
        color = pt(col_color, "COLOR")
        fit = pt(col_fit, "FIT")
        occasion = pt(col_occasion, "OCCASION")
        neck_collar = pt(col_neck, "NECK-COLLAR")
        sleeve_type = pt(col_sleeve, "SLEEVE TYPE")
        fabric_main = pt(col_fab_main, "FABRIC MAIN DESC")
        fabric_sub = pt(col_fab_sub, "FABRIC SUB DESC")
        fabric_type = pt(col_fab_type, "FABRIC TYPE")
        fabric_sub_type = pt(col_fab_subtype, "FABRIC SUB TYPE")
        hl = pt(col_hl, "HL")
        pockets = pt(col_pockets, "POCKETS")
        length_val = pt(col_length, "LENGTH")
        waist_val = pt(col_waist, "WAIST")
        closure = pt(col_closure, "CLOSURE")
        leg = pt(col_leg, "LEG")
        front = pt(col_front, "FRONT")
        composition1 = pt(col_comp1, "COMPOSITION1")
        composition2 = pt(col_comp2, "COMPOSITION2")
        composition3 = pt(col_comp3, "COMPOSITION3")
        packed_date = format_packed_date(first.get(col_packed_date)) if col_packed_date else ""
        cs = pt(col_cs, "CS")
        rate = format_price(first.get(col_rate)) if col_rate is not None else ""
        order_no = pid(col_order_no, "ORDER NO")

        # Derived (the only fields we synthesise)
        product_name = build_name(section, department, fit, color)
        item_code = build_item_code(section, department, style_no, fabric_no, color)
        hs_code = lookup_hs_code(section, department, warnings)

        for i, (_, row) in enumerate(group_df.iterrows()):
            is_first = (i == 0)
            row_excel = int(row["_excel_row"])

            oem_barcode = passthrough_id(row.get(col_oem), row_excel,
                                         "OEM_BARCODE", cleanup)
            pack_size = passthrough(row.get(col_size), row_excel,
                                    "PACK / SIZE", cleanup)
            mrp_str = format_price(row.get(col_mrp))

            out = {col: "" for col in FYND_COLUMNS}

            # Variant-level (every row)
            out["Item Code"] = item_code
            out["Brand"] = STATIC["brand"]
            out["Gtin Type"] = STATIC["gtin_type"]
            out["Gtin Value"] = oem_barcode
            out["Seller Identifier"] = oem_barcode
            out["Size"] = pack_size
            out["Actual Price"] = mrp_str
            out["Selling Price"] = mrp_str
            out["Currency"] = STATIC["currency"]
            out["Length (cm)"] = STATIC["length_cm"]
            out["Width (cm)"] = STATIC["width_cm"]
            out["Height (cm)"] = STATIC["height_cm"]
            out["Product Dead Weight (gram)"] = STATIC["weight_gram"]
            out["Net Quantity Value"] = STATIC["net_quantity_value"]
            out["Net Quantity Unit"] = STATIC["net_quantity_unit"]

            # Product-level (first row of each group only)
            if is_first:
                out["Name"] = product_name
                out["Category"] = STATIC["category"]
                out["Tax Rule Name"] = STATIC["tax_rule"]
                out["HS Code"] = hs_code
                out["Country of Origin"] = STATIC["country_of_origin"]
                out["Trader Type"] = STATIC["trader_type"]
                out["Trader Name"] = STATIC["trader_name"]
                out["Trader Address"] = STATIC["trader_address"]
                out["Return Time Limit"] = STATIC["return_time_limit"]
                out["Return Time Unit"] = STATIC["return_time_unit"]
                # Pass-through fields (no transformation)
                out["Colour"] = color
                out["Material"] = build_fabric_composition(
                    composition1, composition2, composition3)
                # Custom attributes — all pass-through
                out["Custom Attribute 1"] = department
                out["Custom Attribute 2"] = fit
                out["Custom Attribute 3"] = section
                out["Custom Attribute 4"] = occasion
                out["Custom Attribute 5"] = neck_collar
                # Custom Attribute 6 intentionally blank per spec
                out["Custom Attribute 7"] = sleeve_type
                out["Custom Attribute 8"] = order_no
                out["Custom Attribute 9"] = fabric_main
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

            output_rows.append(out)

    if not output_rows:
        raise ValueError("No valid product rows found in input file.")

    output_df = pd.DataFrame(output_rows, columns=FYND_COLUMNS)

    # De-duplicate warnings, preserve order
    seen: set[str] = set()
    unique_warnings: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique_warnings.append(w)

    cleanup_df = cleanup.to_dataframe()

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        output_df.to_excel(writer, sheet_name="Sheet1", index=False)
    buf.seek(0)
    return buf, unique_warnings, output_df, cleanup_df
