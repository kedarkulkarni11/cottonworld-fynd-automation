"""
Cottonworld Logic ERP → Fynd Platform Template Transformer
Handles Top Wear (T-Shirt, Shirt) transformations.
"""

import pandas as pd
import re
from io import BytesIO

# ---------------------------------------------------------------------------
# Constants & Mappings
# ---------------------------------------------------------------------------

TOPWEAR_DEPARTMENTS = {"TSHIRT", "SHIRTS"}

# Department → Fynd Category
DEPARTMENT_CATEGORY_MAP = {
    "TSHIRT": "T-Shirts",
    "SHIRTS": "Casual Shirts",
}

# Department → HS Code (static for now, maintained at our end)
DEPARTMENT_HS_CODE_MAP = {
    "TSHIRT": 61091000,
    "SHIRTS": 62052000,
}

# Section → Gender
SECTION_GENDER_MAP = {
    "MENS": "Men",
    "LADIES": "Women",
}

# Section → prefix for Item Code
SECTION_PREFIX_MAP = {
    "MENS": "M",
    "LADIES": "W",
}

# Department → short name for Item Code
DEPARTMENT_CODE_MAP = {
    "TSHIRT": "TSHIRT",
    "SHIRTS": "SHIRTS",
}

# Sleeve type expansion
SLEEVE_TYPE_MAP = {
    "FS": "Long Sleeve (Regular)",
    "HE FS": "Long Sleeve (Regular)",
    "HS": "Half Sleeves",
    "HE HS": "Half Sleeves",
    "3/4 SLEEVE WITH FOLD": "3/4 Sleeve with Fold",
    "3/4 SLV WITH BIG CUFF": "3/4 Sleeve with Big Cuff",
    "BAT SLEEVE": "Bat Sleeve",
    "ELBOW LENGTH SLV": "Elbow Length Sleeve",
    "EXTENDED SHORT SLEEVE": "Extended Short Sleeve",
    "(NIL)": "",
}

# Neck-Collar mapping to Fynd values
COLLAR_STYLE_MAP = {
    "ROUND NECK": "Rounded",
    "HOODED": "Hooded",
    "SHIRT COLLAR": "Shirt Collar",
    "BUTTON DOWN": "Button Down",
    "V NECK": "V Neck",
    "MANDARIN COLLAR": "Mandarin Collar",
    "POLO COLLAR": "Polo Collar",
    "BAND COLLAR": "Band Collar",
    "(NIL)": "",
}

# Primary material extraction from FABRIC SUB TYPE
MATERIAL_MAP = {
    "COTTON/BAMBOO/ELASTANE": "Cotton",
    "COTTON/BAM": "Cotton",
    "COTTON": "Cotton",
    "LINEN": "Linen",
    "VISCOSE": "Viscose",
    "POLYESTER": "Polyester",
    "SILK": "Silk",
}

# Static values
STATIC = {
    "brand": "cottonworld",
    "tax_rule": "Tiered Tax Rule – 5% & 18% (Eff. 22 Sep 2025) (2)",
    "country_of_origin": "India",
    "gtin_type": "EAN",
    "currency": "INR",
    "length_cm": 1,
    "width_cm": 1,
    "height_cm": 1,
    "weight_gram": 200,
    "trader_type": "Manufacturer",
    "trader_name": "Lekhraj Corp Pvt Ltd",
    "trader_address": "GALA-F, SIDHWA ESTATE, OLD BMP BUILDING, N.A. SAWANT MARG, Colaba, Mumbai City, Maharashtra, 400005",
    "net_quantity": "1 N",
}

# Fynd output column order (Sheet1)
FYND_COLUMNS = [
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
    "Net Quantity Unit", "Product Bundle", "Marketer Name",
    "Marketer Address", "Age Group", "Style", "Generic Name", "Collection",
    "Import Month & Year", "Season", "Gender", "Item Details", "Colour",
    "Primary Colour Hex Code", "Primary Colour", "Fabric Description",
    "Material", "Primary Material", "Outer Material", "Pattern",
    "Product Fit", "Design Styling", "Occasion", "Topwear Length",
    "Neck Type", "Collar Style", "Sleeve Length", "Sleeve Type",
    "Embroidery Type", "Embellishment", "Embellishment Description",
    "Technique", "Technique Description", "Hemline", "Closure Type",
    "Cuff Style", "Waist Rise", "Lifestyle", "Model Info", "Net Quantity",
    "Care Instructions", "Features", "Package Contents", "Sustainable",
    "Custom Attribute 1", "Custom Attribute 2", "Custom Attribute 3",
    "Custom Attribute 4", "Custom Attribute 5", "Custom Attribute 6",
    "Custom Attribute 7", "GSM",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def clean_val(val):
    """Return empty string for (NIL) or NaN values. Convert float ints to int strings."""
    if pd.isna(val):
        return ""
    # If it's a float that's actually an integer, convert cleanly
    if isinstance(val, float) and val == int(val):
        val = int(val)
    s = str(val).strip()
    if s.upper() == "(NIL)":
        return ""
    return s


def extract_primary_material(fabric_sub_type_raw):
    """Extract the first/primary material from FABRIC SUB TYPE."""
    val = clean_val(fabric_sub_type_raw)
    if not val:
        return ""
    # Try direct map first
    upper = val.upper()
    for key, mapped in MATERIAL_MAP.items():
        if key in upper:
            return mapped
    # Fallback: take first word before /
    first = val.split("/")[0].strip()
    return first.title()


def build_product_name(section, composition1, fit, department, color):
    """
    Format: Men's Cotton Bamboo Elastin Regular Fit T-shirt Black
    Rules: no percentages, no 'Cottonworld' prefix, expand abbreviations.
    """
    gender = SECTION_GENDER_MAP.get(section.upper(), section.title())
    gender_possessive = f"{gender}'s"

    # Clean composition - remove percentages
    comp = clean_val(composition1)
    comp = re.sub(r'\d+%\s*', '', comp).strip()
    comp = comp.title()

    fit_clean = clean_val(fit)
    if fit_clean:
        fit_clean = fit_clean.title()

    dept = clean_val(department)
    dept_display = dept.title()
    if dept.upper() == "TSHIRT":
        dept_display = "T-shirt"
    elif dept.upper() == "SHIRTS":
        dept_display = "Shirt"

    color_clean = clean_val(color).title()

    parts = [gender_possessive]
    if comp:
        parts.append(comp)
    if fit_clean:
        parts.append(fit_clean)
    if dept_display:
        parts.append(dept_display)
    if color_clean:
        parts.append(color_clean)

    return " ".join(parts)


def build_item_code(section, department, style_no, fabric_no, color):
    """Format: M-TSHIRT-17656-21646-BLACK"""
    prefix = SECTION_PREFIX_MAP.get(section.upper(), "X")
    dept = DEPARTMENT_CODE_MAP.get(department.upper(), department.upper())
    style = clean_val(style_no)
    fabric = clean_val(fabric_no)
    col = clean_val(color).upper().replace(" ", "-")
    return f"{prefix}-{dept}-{style}-{fabric}-{col}"


def map_sleeve_type(sleeve_raw):
    """Map Logic sleeve abbreviation to Fynd value."""
    val = clean_val(sleeve_raw).upper()
    if not val:
        return ""
    for key, mapped in SLEEVE_TYPE_MAP.items():
        if key == val:
            return mapped
    # Partial match
    for key, mapped in SLEEVE_TYPE_MAP.items():
        if key in val:
            return mapped
    return val.title()


def map_collar_style(collar_raw):
    """Map NECK-COLLAR to Fynd Collar Style."""
    val = clean_val(collar_raw).upper()
    if not val:
        return ""
    for key, mapped in COLLAR_STYLE_MAP.items():
        if key == val:
            return mapped
    return val.title()


def format_size(size_raw):
    """Sizes in UPPERCASE."""
    val = clean_val(size_raw)
    return val.upper()


# ---------------------------------------------------------------------------
# Main transformation
# ---------------------------------------------------------------------------

def find_header_row(df_raw):
    """Find the row index that contains 'OEM_BARCODE' as header."""
    for idx, row in df_raw.iterrows():
        for val in row.values:
            if str(val).strip().upper() == "OEM_BARCODE":
                return idx
    return None


def transform(input_file) -> BytesIO:
    """
    Read Logic ERP xlsx, transform to Fynd Platform template.
    Returns a BytesIO object containing the output xlsx.
    """
    # Read raw to find header row
    df_raw = pd.read_excel(input_file, header=None, sheet_name=0)
    header_idx = find_header_row(df_raw)
    if header_idx is None:
        raise ValueError("Could not find 'OEM_BARCODE' header row in input file.")

    # Re-read with proper header
    input_file.seek(0) if hasattr(input_file, 'seek') else None
    df = pd.read_excel(input_file, header=header_idx, sheet_name=0)

    # Normalize column names
    df.columns = [str(c).strip().upper().replace(" ", "_") for c in df.columns]

    # Rename known columns for consistency
    col_renames = {
        "OEM_BARCODE": "OEM_BARCODE",
        "PACK_/_SIZE": "PACK_SIZE",
        "FABRIC_MAIN_DESC": "FABRIC_MAIN_DESC",
        "FABRIC_SUB_DESC": "FABRIC_SUB_DESC",
        "FABRIC_SUB_TYPE": "FABRIC_SUB_TYPE",
        "SLEEVE_TYPE": "SLEEVE_TYPE",
        "NECK-COLLAR": "NECK_COLLAR",
        "STYLE_NO": "STYLE_NO",
        "FABRIC_NO.": "FABRIC_NO",
        "STYLE_NAME": "STYLE_NAME",
        "PACKED_DATE": "PACKED_DATE",
        "ORDER_NO": "ORDER_NO",
        "RATE_ORDER_DATE": "RATE_ORDER_DATE",
        "GST_TAX_GROUP": "GST_TAX_GROUP",
        "SUPPLIER_CODE": "SUPPLIER_CODE",
    }
    new_cols = {}
    for old, new in col_renames.items():
        for c in df.columns:
            if c == old or c.replace("-", "_") == old.replace("-", "_"):
                new_cols[c] = new
                break
    df.rename(columns=new_cols, inplace=True)

    # Filter for top wear departments only
    df["DEPARTMENT_UPPER"] = df["DEPARTMENT"].astype(str).str.strip().str.upper()
    df = df[df["DEPARTMENT_UPPER"].isin(TOPWEAR_DEPARTMENTS)].copy()

    if df.empty:
        raise ValueError("No Top Wear (TSHIRT/SHIRTS) rows found in input.")

    # Group by product: STYLE_NO + FABRIC_NO + COLOR = one product, sizes are variants
    # Determine grouping key using available columns
    style_col = "STYLE_NO" if "STYLE_NO" in df.columns else "STYLE NO"
    fabric_col = "FABRIC_NO" if "FABRIC_NO" in df.columns else "FABRIC_NO."
    # Try alternate names
    for c in df.columns:
        if "STYLE" in c and "NAME" not in c and "NO" in c:
            style_col = c
        if "FABRIC" in c and "NO" in c and "DESC" not in c and "TYPE" not in c and "MAIN" not in c and "SUB" not in c:
            fabric_col = c

    df["_group_key"] = (
        df[style_col].astype(str).str.strip() + "|" +
        df[fabric_col].astype(str).str.strip() + "|" +
        df["COLOR"].astype(str).str.strip()
    )

    groups = df.groupby("_group_key", sort=False)

    output_rows = []

    for group_key, group_df in groups:
        first = group_df.iloc[0]
        section = clean_val(first.get("SECTION", ""))
        department = clean_val(first.get("DEPARTMENT", ""))
        style_no = clean_val(first.get(style_col, ""))
        fabric_no = clean_val(first.get(fabric_col, ""))
        color = clean_val(first.get("COLOR", ""))
        fit = clean_val(first.get("FIT", ""))
        occasion = clean_val(first.get("OCCASION", ""))
        neck_collar = clean_val(first.get("NECK_COLLAR", first.get("NECK-COLLAR", "")))
        sleeve_type = clean_val(first.get("SLEEVE_TYPE", ""))
        fabric_main = clean_val(first.get("FABRIC_MAIN_DESC", ""))
        fabric_sub = clean_val(first.get("FABRIC_SUB_DESC", ""))
        fabric_sub_type = clean_val(first.get("FABRIC_SUB_TYPE", ""))
        hl = clean_val(first.get("HL", ""))
        pockets = clean_val(first.get("POCKETS", ""))
        cs = clean_val(first.get("CS", ""))
        packed_date = clean_val(first.get("PACKED_DATE", ""))
        order_no = clean_val(first.get("ORDER_NO", ""))
        composition1 = clean_val(first.get("COMPOSITION1", ""))
        mrp = first.get("MRP", "")

        # Build product-level fields
        product_name = build_product_name(section, composition1, fit, department, color)
        item_code_base = build_item_code(section, department, style_no, fabric_no, color)
        category = DEPARTMENT_CATEGORY_MAP.get(department.upper(), department.title())
        hs_code = DEPARTMENT_HS_CODE_MAP.get(department.upper(), "")
        gender = SECTION_GENDER_MAP.get(section.upper(), section.title())
        primary_colour = color.title()
        primary_material = extract_primary_material(fabric_sub_type)
        collar_mapped = map_collar_style(neck_collar)
        sleeve_mapped = map_sleeve_type(sleeve_type)
        fit_display = fit.title() if fit else ""
        # Remove "Fit" suffix if present for Product Fit field
        fit_for_field = re.sub(r'\s*fit\s*$', '', fit_display, flags=re.IGNORECASE).strip()
        if fit_for_field:
            fit_for_field = fit_for_field.title()

        for i, (_, row) in enumerate(group_df.iterrows()):
            is_first = (i == 0)
            oem_barcode = clean_val(row.get("OEM_BARCODE", ""))
            pack_size = clean_val(row.get("PACK_SIZE", row.get("PACK_/_SIZE", "")))
            row_mrp = row.get("MRP", mrp)
            if pd.isna(row_mrp):
                row_mrp = mrp

            size = format_size(pack_size)

            out = {col: "" for col in FYND_COLUMNS}

            # Variant-level fields (every row)
            out["Item Code"] = item_code_base
            out["Gtin Type"] = STATIC["gtin_type"]
            out["Gtin Value"] = str(int(float(oem_barcode))) if oem_barcode else ""
            out["Seller Identifier"] = str(int(float(oem_barcode))) if oem_barcode else ""
            out["Size"] = size
            out["Actual Price"] = int(float(row_mrp)) if row_mrp else ""
            out["Selling Price"] = int(float(row_mrp)) if row_mrp else ""
            out["Currency"] = STATIC["currency"]
            out["Length (cm)"] = STATIC["length_cm"]
            out["Width (cm)"] = STATIC["width_cm"]
            out["Height (cm)"] = STATIC["height_cm"]
            out["Product Dead Weight (gram)"] = STATIC["weight_gram"]

            # Product-level fields (first row of each group only)
            if is_first:
                out["Name"] = product_name
                out["Brand"] = STATIC["brand"]
                out["Category"] = category
                out["Tax Rule Name"] = STATIC["tax_rule"]
                out["HS Code"] = hs_code
                out["Country of Origin"] = STATIC["country_of_origin"]
                out["Trader Type"] = STATIC["trader_type"]
                out["Trader Name"] = STATIC["trader_name"]
                out["Trader Address"] = STATIC["trader_address"]
                out["Marketer Name"] = STATIC["trader_name"]
                out["Marketer Address"] = STATIC["trader_address"]
                out["Style"] = style_no
                out["Generic Name"] = fabric_no
                out["Collection"] = cs if cs else "CWC"
                out["Import Month & Year"] = packed_date
                out["Gender"] = gender
                out["Primary Colour"] = primary_colour
                out["Primary Material"] = primary_material
                out["Product Fit"] = fit_for_field
                out["Occasion"] = occasion.upper() if occasion else ""
                out["Collar Style"] = collar_mapped
                out["Sleeve Type"] = sleeve_mapped
                out["Net Quantity"] = STATIC["net_quantity"]
                out["Custom Attribute 1"] = order_no
                out["Custom Attribute 2"] = fabric_main
                out["Custom Attribute 3"] = fabric_sub
                out["Custom Attribute 4"] = fabric_sub_type
                out["Custom Attribute 5"] = hl
                out["Custom Attribute 7"] = pockets

            output_rows.append(out)

    output_df = pd.DataFrame(output_rows, columns=FYND_COLUMNS)

    # Write to Excel
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        output_df.to_excel(writer, sheet_name="Sheet1", index=False)
    buf.seek(0)
    return buf
