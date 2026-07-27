"""
Cottonworld Logic ERP → Shopify Template Transformer

Per the June 2026 Cottonworld spec ("Logic ERP >>> Shopify Template"):
- Only Title and Style Code are DERIVED (both are the same concatenation
  [Section] [Department] [Fit] [Color]). Every other Logic value is passed
  through verbatim, exactly like the Fynd flow.
- Output is a Shopify product-import CSV (one product per group, sizes become
  variant rows; product-level fields appear on the first row of each product,
  variant-level fields on every row).
- Cleanups (whitespace trim, .0 strip on numeric IDs) are LOGGED so the
  operator can audit what changed — surfaced in the UI and as a CSV.

The mapping engine (header detection, column resolution, pass-through +
cleanup logging, Name/Title concatenation) is shared with the Fynd flow and
imported from transformer.py — only the output schema differs.

Returns (BytesIO, warnings, output_df, cleanup_df) — same shape as the Fynd
transform so the Streamlit app wires up symmetrically.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pandas as pd

from transformer import (
    CleanupLog,
    _is_nil,
    build_item_code,
    build_name,
    find_col,
    find_header_row,
    format_price,
    passthrough,
    passthrough_id,
)

DATA_DIR = Path(__file__).parent / "data"


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


SHOPIFY_STATIC = _load_json("shopify_static_values.json")


# ---------------------------------------------------------------------------
# Shopify output schema (93 columns, exact Shopify product-import order)
# ---------------------------------------------------------------------------

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
# Shopify-specific derived helpers
# ---------------------------------------------------------------------------

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
    out = []
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
    Default mirrors the Fynd flow — one product per (Style No, Fabric No,
    Color) — so the Handle is a slug of those three. To change the grouping,
    change ONLY this function (and the group key in transform()).
    """
    return _slug(f"{style_no}-{fabric_no}-{color}")


# ---------------------------------------------------------------------------
# Main transform
# ---------------------------------------------------------------------------

def transform(input_file) -> tuple[BytesIO, list[str], pd.DataFrame, pd.DataFrame]:
    """Read Logic ERP xlsx → Shopify product-import CSV.

    Returns:
        output_buf: BytesIO of the Shopify .csv (UTF-8)
        warnings:   de-duped list of warning strings
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
    col_fab_type = find_col(df, "FABRIC TYPE", "FABRIC_TYPE")
    col_hl = find_col(df, "HL")
    col_sleeve = find_col(df, "SLEEVE TYPE", "SLEEVE_TYPE")
    col_fit = find_col(df, "FIT")
    col_occasion = find_col(df, "OCCASION")
    col_pockets = find_col(df, "POCKETS")
    col_neck = find_col(df, "NECK-COLLAR", "NECK_COLLAR", "NECKCOLLAR")
    col_length = find_col(df, "LENGTH")
    col_waist = find_col(df, "WAIST")
    col_leg = find_col(df, "LEG")
    col_front = find_col(df, "FRONT")
    col_comp1 = find_col(df, "COMPOSITION1")
    col_comp2 = find_col(df, "COMPOSITION2")
    col_comp3 = find_col(df, "COMPOSITION3")
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

    # Excel row number for cleanup log (pandas index 0 -> Excel row header_idx+2)
    df["_excel_row"] = df.index + header_idx + 2

    # Grouping: one Shopify product per (STYLE_NO + FABRIC_NO + COLOR).
    # Change shopify_handle() (and this key) to change the grouping rule.
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

        # Product-level values (read from first row of group)
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
        fabric_type = pt(col_fab_type, "FABRIC TYPE")
        hl = pt(col_hl, "HL")
        pockets = pt(col_pockets, "POCKETS")
        length_val = pt(col_length, "LENGTH")
        waist_val = pt(col_waist, "WAIST")
        leg = pt(col_leg, "LEG")
        front = pt(col_front, "FRONT")
        comp1 = pt(col_comp1, "COMPOSITION1")
        comp2 = pt(col_comp2, "COMPOSITION2")
        comp3 = pt(col_comp3, "COMPOSITION3")

        # Derived. Title = SECTION DEPARTMENT FIT COLOR; Handle and Style Code
        # both use the Fynd Item Code format ({FirstLetterOfSection}-DEPT-STYLE-FABRIC-COLOR).
        title = build_name(section, department, fit, color)
        item_code = build_item_code(section, department, style_no, fabric_no, color)
        style_code = item_code
        handle = item_code
        fabric_composition = build_fabric_composition(comp1, comp2, comp3)

        for i, (_, row) in enumerate(group_df.iterrows()):
            is_first = (i == 0)
            row_excel = int(row["_excel_row"])

            oem_barcode = passthrough_id(row.get(col_oem), row_excel,
                                         "OEM_BARCODE", cleanup)
            pack_size = passthrough(row.get(col_size), row_excel,
                                    "PACK / SIZE", cleanup)
            mrp_str = format_price(row.get(col_mrp))

            out = {col: "" for col in SHOPIFY_COLUMNS}

            # Handle repeats on every variant row (groups the product)
            out["Handle"] = handle

            # Variant-level fields (every row)
            out["Option1 Value"] = pack_size
            out["Option2 Value"] = color
            out["Variant SKU"] = oem_barcode
            out["Variant Barcode"] = oem_barcode
            out["Variant Price"] = mrp_str
            out["Variant Compare At Price"] = mrp_str
            out["Variant Grams"] = SHOPIFY_STATIC["variant_grams"]
            out["Variant Inventory Tracker"] = SHOPIFY_STATIC["variant_inventory_tracker"]
            out["Variant Inventory Policy"] = SHOPIFY_STATIC["variant_inventory_policy"]
            out["Variant Fulfillment Service"] = SHOPIFY_STATIC["variant_fulfillment_service"]
            out["Variant Requires Shipping"] = SHOPIFY_STATIC["variant_requires_shipping"]
            out["Variant Taxable"] = SHOPIFY_STATIC["variant_taxable"]
            out["Variant Weight Unit"] = SHOPIFY_STATIC["variant_weight_unit"]

            # Product-level fields (first row of each group only)
            if is_first:
                out["Title"] = title
                out["Vendor"] = SHOPIFY_STATIC["vendor"]
                out["Published"] = SHOPIFY_STATIC["published"]
                out["Option1 Name"] = SHOPIFY_STATIC["option1_name"]
                out["Option1 Linked To"] = SHOPIFY_STATIC["option1_linked_to"]
                out["Option2 Name"] = SHOPIFY_STATIC["option2_name"]
                out["Option2 Linked To"] = SHOPIFY_STATIC["option2_linked_to"]
                out["Gift Card"] = SHOPIFY_STATIC["gift_card"]
                out["Included / India"] = SHOPIFY_STATIC["included_india"]
                out["Status"] = SHOPIFY_STATIC["status"]

                # Pass-through metafields
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

            output_rows.append(out)

    if not output_rows:
        raise ValueError("No valid product rows found in input file.")

    output_df = pd.DataFrame(output_rows, columns=SHOPIFY_COLUMNS)

    # De-duplicate warnings, preserve order
    seen: set[str] = set()
    unique_warnings: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique_warnings.append(w)

    cleanup_df = cleanup.to_dataframe()

    buf = BytesIO()
    buf.write(output_df.to_csv(index=False).encode("utf-8"))
    buf.seek(0)
    return buf, unique_warnings, output_df, cleanup_df
