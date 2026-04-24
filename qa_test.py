"""
QA test suite for Cottonworld → Fynd transformer and app flow.
Covers happy path, edge cases, preview rendering (PyArrow), and xlsx output.
Run: python3 qa_test.py
"""
from __future__ import annotations

import sys
import traceback
from io import BytesIO

import pandas as pd
import pyarrow as pa


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def ok(name: str) -> None:
    PASSED.append(name)
    print(f"  ✓ {name}")


def fail(name: str, err: str) -> None:
    FAILED.append((name, err))
    print(f"  ✗ {name}\n      {err}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# Core happy path
# ---------------------------------------------------------------------------
def test_happy_path() -> pd.DataFrame:
    section("Happy path — test_input.xlsx")
    from transformer import transform, FYND_COLUMNS

    with open("test_input.xlsx", "rb") as f:
        buf, warnings, df = transform(f)

    try:
        assert isinstance(buf, BytesIO), "buf not BytesIO"
        ok("transform returns BytesIO")
    except AssertionError as e:
        fail("transform returns BytesIO", str(e))

    try:
        assert isinstance(warnings, list), "warnings not list"
        ok(f"warnings is list (len={len(warnings)})")
    except AssertionError as e:
        fail("warnings is list", str(e))

    try:
        assert isinstance(df, pd.DataFrame), "df not DataFrame"
        ok(f"output_df is DataFrame (shape={df.shape})")
    except AssertionError as e:
        fail("output_df is DataFrame", str(e))

    try:
        assert df.shape[1] == 102, f"expected 102 cols, got {df.shape[1]}"
        ok("output has 102 columns (52 fixed + 50 custom)")
    except AssertionError as e:
        fail("column count", str(e))

    try:
        assert list(df.columns) == FYND_COLUMNS, "column order mismatch"
        ok("column order matches FYND_COLUMNS")
    except AssertionError as e:
        fail("column order", str(e))

    try:
        assert df.shape[0] > 0, "no rows produced"
        ok(f"produced {df.shape[0]} SKU rows")
    except AssertionError as e:
        fail("row count > 0", str(e))

    try:
        products = df[df["Name"].astype(str).str.strip() != ""]
        assert len(products) > 0, "no product (first) rows"
        ok(f"{len(products)} unique products")
    except AssertionError as e:
        fail("product row count", str(e))

    return df


# ---------------------------------------------------------------------------
# Preview rendering (PyArrow) — the bug we're fixing
# ---------------------------------------------------------------------------
def test_preview_rendering(df: pd.DataFrame) -> None:
    section("Preview rendering — PyArrow conversion")

    # Raw dataframe — expected to fail without cast
    raw_preview = df.head(20).fillna("")
    try:
        pa.Table.from_pandas(raw_preview)
        ok("raw df.head(20).fillna('') — PyArrow accepts")
    except Exception as e:
        # this IS expected to fail before the fix; note for record
        print(f"  (note) raw preview fails PyArrow: {type(e).__name__}")

    # Cast-to-str preview — this is what the app now does
    str_preview = df.head(20).fillna("").astype(str)
    try:
        pa.Table.from_pandas(str_preview)
        ok("astype(str) preview — PyArrow accepts (fixes All-columns bug)")
    except Exception as e:
        fail("astype(str) preview", f"{type(e).__name__}: {e}")

    # Key columns view
    key_cols = [
        "Name", "Item Code", "Brand", "Category", "HS Code",
        "Gtin Value", "Size", "Actual Price", "Currency",
        "Colour", "Material",
        "Custom Attribute 1", "Custom Attribute 2", "Custom Attribute 3",
        "Custom Attribute 5", "Custom Attribute 7",
        "Custom Attribute 14", "Custom Attribute 20",
    ]
    try:
        pa.Table.from_pandas(str_preview[key_cols])
        ok("Key columns view renders")
    except Exception as e:
        fail("Key columns view", str(e))


# ---------------------------------------------------------------------------
# Mapping correctness
# ---------------------------------------------------------------------------
def test_mappings(df: pd.DataFrame) -> None:
    section("Mapping correctness")

    products = df[df["Name"].astype(str).str.strip() != ""].copy()

    # Name format
    first = products.iloc[0]
    try:
        name = first["Name"]
        assert name.startswith(("Men's", "Women's", "Boys'", "Girls'", "Unisex")), \
            f"Name doesn't start with gender: {name!r}"
        ok(f"Name starts with gender: {name!r}")
    except AssertionError as e:
        fail("Name format", str(e))

    # Item Code format: prefix-dept-style-fabric-color
    try:
        code = first["Item Code"]
        parts = code.split("-")
        assert len(parts) >= 5, f"Item Code has <5 parts: {code!r}"
        assert parts[0] in ("M", "W", "B", "G", "U"), f"prefix not M/W/B/G/U: {parts[0]}"
        ok(f"Item Code format: {code!r}")
    except AssertionError as e:
        fail("Item Code format", str(e))

    # Category always "Others Level 3"
    try:
        cats = set(products["Category"].unique())
        assert cats == {"Others Level 3"}, f"unexpected categories: {cats}"
        ok("Category = 'Others Level 3' on all products")
    except AssertionError as e:
        fail("Category constant", str(e))

    # Brand always "cottonworld"
    try:
        brands = set(df[df["Brand"].astype(str).str.strip() != ""]["Brand"].unique())
        assert brands == {"cottonworld"}, f"unexpected brands: {brands}"
        ok("Brand = 'cottonworld'")
    except AssertionError as e:
        fail("Brand constant", str(e))

    # HS Code is 8 digits (or blank)
    try:
        for hs in products["HS Code"].astype(str):
            if hs.strip():
                assert hs.isdigit() and len(hs) == 8, f"bad HS Code: {hs!r}"
        ok("All non-blank HS Codes are 8 digits")
    except AssertionError as e:
        fail("HS Code format", str(e))

    # Size standardization: should not contain SMALL/MEDIUM/LARGE/XLARGE
    try:
        sizes = set(df["Size"].astype(str).unique())
        bad = sizes & {"SMALL", "MEDIUM", "LARGE", "XLARGE", "XSMALL"}
        assert not bad, f"non-standardized sizes found: {bad}"
        ok(f"sizes standardized: {sorted(s for s in sizes if s)}")
    except AssertionError as e:
        fail("Size standardization", str(e))

    # Section → Gender mapping (Custom Attribute 3)
    try:
        genders = set(products["Custom Attribute 3"].unique())
        allowed = {"Men", "Women", "Boys", "Girls", "Unisex"}
        unexpected = genders - allowed
        assert not unexpected, f"unexpected genders: {unexpected}"
        ok(f"Gender values OK: {sorted(genders)}")
    except AssertionError as e:
        fail("Gender mapping", str(e))

    # Ladies → Women specifically
    try:
        ladies_rows = products[products["Item Code"].str.startswith("W-")]
        if len(ladies_rows):
            ladies_genders = set(ladies_rows["Custom Attribute 3"].unique())
            assert ladies_genders == {"Women"}, f"Ladies not renamed: {ladies_genders}"
            ok("LADIES → Women rename working")
    except AssertionError as e:
        fail("Ladies → Women", str(e))

    # Sleeve: FS should be expanded to 'Full Sleeves' (per spec)
    try:
        sleeves = set(products["Custom Attribute 7"].astype(str).unique())
        assert "FS" not in sleeves, "FS not expanded"
        if "Full Sleeves" in sleeves:
            ok("'FS' → 'Full Sleeves' expansion applied")
    except AssertionError as e:
        fail("Sleeve expansion", str(e))

    # Custom Attribute 1 = Department (display form, not CAPS)
    try:
        depts = set(products["Custom Attribute 1"].astype(str).unique())
        caps_depts = {d for d in depts if d.isupper() and d}
        assert not caps_depts, f"Department display form still in CAPS: {caps_depts}"
        ok(f"Departments in display form: e.g. {sorted(depts)[:5]}")
    except AssertionError as e:
        fail("Department display form", str(e))


# ---------------------------------------------------------------------------
# Variant rows
# ---------------------------------------------------------------------------
def test_variant_rows(df: pd.DataFrame) -> None:
    section("Variant rows")

    # Variant rows: Name is blank, but Item Code + GTIN + Size + Price present
    variants = df[df["Name"].astype(str).str.strip() == ""]
    try:
        assert len(variants) > 0, "no variant rows"
        ok(f"{len(variants)} variant rows")
    except AssertionError as e:
        fail("variant rows exist", str(e))

    try:
        missing_code = variants["Item Code"].astype(str).str.strip() == ""
        assert not missing_code.any(), f"{missing_code.sum()} variant rows missing Item Code"
        ok("all variants carry Item Code")
    except AssertionError as e:
        fail("variants carry Item Code", str(e))

    try:
        missing_gtin = variants["Gtin Value"].astype(str).str.strip() == ""
        assert not missing_gtin.any(), f"{missing_gtin.sum()} variants missing GTIN"
        ok("all variants carry GTIN")
    except AssertionError as e:
        fail("variants carry GTIN", str(e))

    try:
        missing_size = variants["Size"].astype(str).str.strip() == ""
        assert not missing_size.any(), f"{missing_size.sum()} variants missing Size"
        ok("all variants carry Size")
    except AssertionError as e:
        fail("variants carry Size", str(e))


# ---------------------------------------------------------------------------
# XLSX output integrity
# ---------------------------------------------------------------------------
def test_xlsx_output() -> None:
    section("XLSX output integrity")
    from transformer import transform

    with open("test_input.xlsx", "rb") as f:
        buf, _, _ = transform(f)

    try:
        buf.seek(0)
        df = pd.read_excel(buf, sheet_name="Sheet1", dtype=str)
        ok(f"xlsx reopens; {df.shape[0]} rows × {df.shape[1]} cols")
    except Exception as e:
        fail("xlsx reopen", str(e))
        return

    try:
        assert "Sheet1" in pd.ExcelFile(BytesIO(buf.getvalue())).sheet_names
        ok("sheet name = 'Sheet1'")
    except Exception as e:
        fail("sheet name", str(e))

    # HS Code doesn't have trailing .0
    try:
        hs_vals = [v for v in df["HS Code"].astype(str).unique() if v and v != "nan"]
        bad = [v for v in hs_vals if "." in v]
        assert not bad, f"HS codes with decimal: {bad}"
        ok("HS Code cells are integer strings (no .0)")
    except AssertionError as e:
        fail("HS Code decimal", str(e))


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------
def test_error_paths() -> None:
    section("Error handling")
    from transformer import transform

    # Case 1: empty xlsx
    buf_in = BytesIO()
    pd.DataFrame({"A": [1, 2, 3]}).to_excel(buf_in, index=False)
    buf_in.seek(0)
    try:
        transform(buf_in)
        fail("missing OEM_BARCODE", "no exception raised")
    except ValueError as e:
        if "OEM_BARCODE" in str(e):
            ok(f"raises ValueError on missing OEM_BARCODE header: {e}")
        else:
            fail("missing OEM_BARCODE", f"wrong error: {e}")
    except Exception as e:
        fail("missing OEM_BARCODE", f"{type(e).__name__}: {e}")

    # Case 2: has OEM_BARCODE but missing other required cols
    buf_in = BytesIO()
    pd.DataFrame({"OEM_BARCODE": ["123"], "foo": ["bar"]}).to_excel(buf_in, index=False)
    buf_in.seek(0)
    try:
        transform(buf_in)
        fail("missing required cols", "no exception raised")
    except ValueError as e:
        if "missing required columns" in str(e):
            ok(f"raises ValueError on missing required cols: {e}")
        else:
            fail("missing required cols", f"wrong error: {e}")


# ---------------------------------------------------------------------------
# Reference data integrity
# ---------------------------------------------------------------------------
def test_reference_data() -> None:
    section("Reference data")
    from transformer import HSN_LOOKUP, SLEEVE_MAP, COLLAR_MAP, STATIC

    try:
        assert len(HSN_LOOKUP) >= 50, f"HSN too small: {len(HSN_LOOKUP)}"
        ok(f"HSN_LOOKUP loaded: {len(HSN_LOOKUP)} entries")
    except AssertionError as e:
        fail("HSN_LOOKUP size", str(e))

    try:
        assert HSN_LOOKUP.get(("MENS", "TSHIRT")) == "61091000"
        ok("MENS+TSHIRT → 61091000")
    except AssertionError as e:
        fail("MENS+TSHIRT", str(e))

    try:
        assert HSN_LOOKUP.get(("LADIES", "PANTS")) == "61034200"
        ok("LADIES+PANTS → 61034200")
    except AssertionError as e:
        fail("LADIES+PANTS", str(e))

    try:
        assert SLEEVE_MAP.get("FS") == "Full Sleeves"
        ok("FS → Full Sleeves")
    except AssertionError as e:
        fail("FS mapping", str(e))

    try:
        assert SLEEVE_MAP.get("HS") == "Half Sleeves"
        ok("HS → Half Sleeves")
    except AssertionError as e:
        fail("HS mapping", str(e))

    try:
        assert STATIC["category"] == "Others Level 3"
        ok("Category = Others Level 3")
    except AssertionError as e:
        fail("Category constant", str(e))


# ---------------------------------------------------------------------------
# app.py import smoke test
# ---------------------------------------------------------------------------
def test_app_import() -> None:
    section("app.py import smoke test")
    import os
    # set env so streamlit doesn't try to open browser
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    try:
        import ast
        with open("app.py") as f:
            ast.parse(f.read())
        ok("app.py parses (syntax OK)")
    except Exception as e:
        fail("app.py syntax", str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 60)
    print(" Cottonworld → Fynd Converter — QA Test Suite")
    print("=" * 60)

    df = test_happy_path()
    test_preview_rendering(df)
    test_mappings(df)
    test_variant_rows(df)
    test_xlsx_output()
    test_error_paths()
    test_reference_data()
    test_app_import()

    print()
    print("=" * 60)
    print(f" Passed: {len(PASSED)}   Failed: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        print("\nFAILURES:")
        for name, err in FAILED:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
