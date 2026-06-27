"""
Cottonworld Automation Tool - Streamlit Web App
Converts Logic ERP Item Master export → Fynd Platform upload template.
"""

import streamlit as st

from transformer import transform as transform_fynd
from shopify_transformer import transform as transform_shopify

st.set_page_config(
    page_title="Cottonworld Catalog Converter",
    page_icon="👕",
    layout="centered",
)


# Per-platform UI config. Keeps converter_page() branch-free.
PLATFORMS = {
    "Fynd": {
        "transform": transform_fynd,
        "title_col": "Name",
        "button": "Convert to Fynd Template",
        "out_suffix": "_fynd_upload.xlsx",
        "out_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "download_label": "Download Fynd Upload File",
        "key_cols": [
            "Name", "Item Code", "Brand", "Category", "HS Code",
            "Gtin Value", "Size", "Actual Price", "Currency",
            "Colour", "Material",
            "Custom Attribute 1", "Custom Attribute 2", "Custom Attribute 3",
            "Custom Attribute 5", "Custom Attribute 7",
            "Custom Attribute 14", "Custom Attribute 20",
        ],
    },
    "Shopify": {
        "transform": transform_shopify,
        "title_col": "Title",
        "button": "Convert to Shopify CSV",
        "out_suffix": "_shopify_import.csv",
        "out_mime": "text/csv",
        "download_label": "Download Shopify Import CSV",
        "key_cols": [
            "Handle", "Title", "Vendor",
            "Option1 Value", "Option2 Value", "Variant SKU", "Variant Price",
            "Gender (product.metafields.custom.gender)",
            "Product Type (product.metafields.custom.product_type)",
            "Fit Type (product.metafields.custom.fit_type)",
            "Fabric Composition (product.metafields.custom.fabric_composition)",
            "Style Code (product.metafields.custom.style_code)",
            "Status",
        ],
    },
}


# ---------------------------------------------------------------------------
# Page: Converter
# ---------------------------------------------------------------------------
def converter_page():
    st.title("Cottonworld Catalog Converter")
    st.markdown(
        "Upload the **Logic ERP Item Master** `.xlsx` file to generate a "
        "marketplace-ready upload file."
    )

    target = st.radio(
        "Target platform",
        list(PLATFORMS.keys()),
        horizontal=True,
        help="Fynd produces the Fynd Commerce bulk-upload .xlsx. "
             "Shopify produces a Shopify product-import .csv.",
    )
    cfg = PLATFORMS[target]

    if target == "Fynd":
        st.caption(
            "All sections (Mens, Ladies, Boys, Unisex) and departments are supported. "
            "HS Code is resolved from the Section + Department HSN lookup."
        )
    else:
        st.caption(
            "Produces a Shopify product-import CSV. One product per "
            "Style + Fabric + Color; sizes become variants."
        )

    st.warning(
        f"⚠️ **Always review the generated file before uploading to {target}.** "
        "This tool automates the mapping but does not guarantee correctness for every row — "
        "open the output, spot-check titles, prices, and any flagged "
        "warnings below before bulk upload.",
        icon="⚠️",
    )

    st.divider()

    st.caption("📎 Maximum file size: **10 MB**")
    uploaded_file = st.file_uploader(
        "Upload Logic ERP Item Master (.xlsx)",
        type=["xlsx"],
        help="This is the Item Master export from Logic ERP (PO file). Max size: 10 MB.",
    )

    if uploaded_file is not None:
        st.info(
            f"Uploaded: **{uploaded_file.name}** ({uploaded_file.size / 1024:.1f} KB)"
        )

        # Invalidate cached result if a new file OR a new target is selected
        current_key = f"{target}::{uploaded_file.name}::{uploaded_file.size}"
        if st.session_state.get("conv_source_key") != current_key:
            st.session_state.pop("conv_result", None)
            st.session_state["conv_source_key"] = current_key

        if st.button(cfg["button"], type="primary"):
            with st.spinner("Transforming data..."):
                try:
                    output_buf, warnings, output_df, cleanup_df = cfg["transform"](uploaded_file)
                    st.session_state["conv_result"] = {
                        "buf_bytes": output_buf.getvalue(),
                        "warnings": warnings,
                        "df": output_df,
                        "cleanup_df": cleanup_df,
                        "source_name": uploaded_file.name,
                        "target": target,
                    }
                except ValueError as e:
                    st.session_state.pop("conv_result", None)
                    st.error(f"Error: {e}")
                except Exception as e:
                    st.session_state.pop("conv_result", None)
                    st.error(f"Unexpected error: {e}")
                    st.exception(e)

        # Render preview/download from cached result so widget interactions
        # (e.g. Key-vs-All-columns radio) don't wipe the output.
        if "conv_result" in st.session_state:
            result = st.session_state["conv_result"]
            output_df = result["df"]
            warnings = result["warnings"]
            cleanup_df = result.get("cleanup_df")
            result_cfg = PLATFORMS[result.get("target", "Fynd")]

            st.success("Conversion complete!")

            # ---- Summary metrics ----
            total_rows = len(output_df)
            product_rows = output_df[result_cfg["title_col"]].astype(str).str.strip()
            num_products = (product_rows != "").sum()
            cleanup_count = 0 if cleanup_df is None else len(cleanup_df)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total rows (SKUs)", f"{total_rows}")
            col2.metric("Unique products", f"{num_products}")
            col3.metric("Warnings", f"{len(warnings)}")
            col4.metric("Cleanups logged", f"{cleanup_count}")

            # ---- Preview table ----
            st.divider()
            st.subheader("Preview")
            st.caption(
                f"First 20 rows of the generated file. Toggle below to "
                f"see only key columns or the full template "
                f"({len(output_df.columns)} columns)."
            )

            key_cols = result_cfg["key_cols"]
            view = st.radio(
                "View",
                ["Key columns only", "All columns"],
                horizontal=True,
                label_visibility="collapsed",
                key="preview_view",
            )
            # Cast to str so Streamlit/PyArrow doesn't choke on
            # mixed-type columns (e.g. Return Time Limit is int on
            # product rows, '' on variant rows).
            preview_df = output_df.head(20).fillna("").astype(str)
            if view == "Key columns only":
                preview_df = preview_df[key_cols]
            st.dataframe(preview_df, use_container_width=True, hide_index=True)

            st.caption(
                f"Showing 20 of {total_rows} rows. Download the full "
                "file below to see everything."
            )

            # ---- Download ----
            st.divider()
            output_filename = (
                result["source_name"].replace(".xlsx", "")
                + result_cfg["out_suffix"]
            )
            st.download_button(
                label=result_cfg["download_label"],
                data=result["buf_bytes"],
                file_name=output_filename,
                mime=result_cfg["out_mime"],
                type="primary",
            )

            st.info(
                f"📌 **Before uploading to {result.get('target', 'Fynd')}:** open the "
                "downloaded file, verify a few product rows (Title/Name, Price, key "
                "attributes), and review any warnings listed below."
            )

            if warnings:
                st.divider()
                st.warning(
                    f"**{len(warnings)} warning(s) during conversion** — "
                    "the file was generated, but review these before uploading to Fynd:"
                )
                with st.expander("View warnings", expanded=True):
                    for w in warnings:
                        st.markdown(f"- {w}")

            # ---- Cleanup audit log ----
            if cleanup_df is not None and len(cleanup_df) > 0:
                st.divider()
                st.info(
                    f"**{len(cleanup_df)} cell(s) cleaned up during conversion.** "
                    "These are minor mutations (whitespace trims, Excel `.0` "
                    "float artifacts on numeric IDs) — semantic values were "
                    "not changed. Review the table below or download as CSV "
                    "for your records."
                )
                with st.expander("View cleanup log", expanded=False):
                    st.dataframe(
                        cleanup_df.astype(str),
                        use_container_width=True,
                        hide_index=True,
                    )
                cleanup_csv = cleanup_df.to_csv(index=False).encode("utf-8")
                cleanup_filename = (
                    result["source_name"].replace(".xlsx", "")
                    + "_cleanup_log.csv"
                )
                st.download_button(
                    label="Download cleanup log (CSV)",
                    data=cleanup_csv,
                    file_name=cleanup_filename,
                    mime="text/csv",
                )

    st.divider()
    st.caption("Cottonworld Catalog Converter v4.0 | Fynd + Shopify | Pass-through mode (Name/Title derived only)")


# ---------------------------------------------------------------------------
# Page: How to use
# ---------------------------------------------------------------------------
def how_to_use_page():
    st.title("How to use this tool")
    st.caption("A step-by-step guide for the Cottonworld team.")

    st.warning(
        "⚠️ **Disclaimer:** This tool automates the Logic → Fynd **and** "
        "Logic → Shopify mappings, but you must **always verify the output file "
        "before uploading**. Open the file in Excel, check product names/titles, "
        "HS codes (Fynd), MRP, and any flagged warnings. The tool is an "
        "accelerator, not a substitute for a final human review.",
        icon="⚠️",
    )

    st.info(
        "🎯 **Pick a target platform first.** On the **Converter** page, use the "
        "**Target platform** toggle to choose **Fynd** (multi-sheet `.xlsx`) or "
        "**Shopify** (product-import `.csv`). The Logic export and review steps "
        "below are the same for both — only the output format and the upload "
        "destination differ.",
        icon="🎯",
    )

    st.divider()

    st.header("Step 1 — Export Item Master from Logic ERP")
    st.markdown(
        """
        1. Log in to **Logic ERP**.
        2. Go to **Reports → Item Master → GSL-PO** (or the equivalent PO report
           your team uses).
        3. Set the date range to the period you want to publish on Fynd.
        4. Export the report as an **`.xlsx`** file.
        5. Keep the file as-is — **do not rename columns or delete rows**.
        """
    )

    st.header("Step 2 — Upload the file here")
    st.markdown(
        """
        1. Open the **Converter** page (left sidebar).
        2. Choose your **Target platform** — **Fynd** or **Shopify**.
        3. Click **Browse files** and select the Logic `.xlsx` you exported.
        4. Click **Convert to Fynd Template** / **Convert to Shopify CSV**.
        5. Wait a few seconds while the tool processes the file.
        """
    )

    st.header("Step 3 — Review warnings and the cleanup log")
    st.markdown(
        """
        After conversion, the tool surfaces two things to review:

        **Warnings** (only fired when something needs your attention):
        - **No HSN mapping for (Section, Department)** — the tool doesn't have
          an HS Code for that combination. The HS Code field will be **blank**
          — fill it in manually before upload, and flag it so we can add the
          mapping permanently.

        **Cleanup log** (always shown when any cell was touched):
        - The tool only ever makes two mutations: (a) trimming leading/trailing
          whitespace, and (b) stripping the trailing `.0` Excel adds to
          integer ID columns (Style No, Fabric No., OEM Barcode, Order No).
        - Every such mutation is logged with the **Logic Excel row number,
          column name, original value, cleaned value, and reason**.
        - You can download the cleanup log as a CSV for your records.

        Both are **advisory** — the file is still generated. Treat them as a
        checklist of things to spot-check.
        """
    )

    st.header("Step 4 — Download and verify")
    st.markdown(
        """
        1. Click **Download Fynd Upload File**.
        2. Open the file in Excel or Google Sheets.
        3. Spot-check at least **5–10 products** across different sections and
           departments:
           - **Name** reads like *MENS TSHIRT REGULAR FIT BLACK* (raw, all
             caps from Logic, `(NIL)` segments skipped)
           - **Item Code** format: `M-TSHIRT-17656-21646-BLACK` (first letter
             of Section, then Dept-Style-Fabric-Color verbatim from Logic)
           - **HS Code** is 8 digits and matches the expected tariff code
           - **Actual Price / Selling Price** = Logic MRP (e.g. `499.00`,
             `499.50` — 2-decimal preserved)
           - **Size** is whatever Logic put in `PACK / SIZE`, verbatim
           - **Colour / Material** = Logic `COLOR` / `COMPOSITION1`, verbatim
           - **Custom Attribute 1** = Logic `DEPARTMENT`, verbatim
        4. If anything looks off, re-export from Logic and re-run — or fix in
           Excel directly.
        """
    )

    st.header("Step 5 — Upload to Fynd Commerce Platform")
    st.markdown(
        """
        1. Log in to **Fynd Commerce Platform** (Cottonworld company).
        2. Go to **Products → Bulk Upload** (or the equivalent path).
        3. Choose the **Supplementary Upload** template.
        4. Upload the file you downloaded from this tool.
        5. Watch the Fynd validation report — if any row fails, the error
           message will tell you which column is wrong. Fix it in the file
           and re-upload.
        """
    )

    st.divider()
    st.header("What the tool does automatically")
    st.markdown(
        """
        | Field | Rule |
        | --- | --- |
        | **Product Name** | `SECTION DEPARTMENT FIT COLOR` — raw verbatim from Logic, empty / `(NIL)` segments skipped (e.g. `MENS TSHIRT REGULAR FIT BLACK`) |
        | **Item Code** | `{FirstLetterOfSection}-DEPT-STYLE-FABRIC-COLOR` (e.g. `M-TSHIRT-17656-21646-BLACK`). LADIES → `L`. One code per product, shared across size variants |
        | **Brand** | `cottonworld` (fixed) |
        | **Category** | `Others level 3` (fixed) |
        | **Tax Rule** | `Tiered Tax Rule – 5% & 18% (Eff. 22 Sep 2025) (2)` |
        | **HS Code** | Looked up from the **Section + Department** HSN table |
        | **Country of Origin** | `India` |
        | **Dimensions** | 1 × 1 × 1 cm, 200 g (placeholder — update in Fynd if needed) |
        | **Trader / Marketer** | Lekhraj Corp Pvt Ltd (Colaba) |
        | **Return policy** | 30 Days |
        | **Net Quantity** | 1, unit `number` (fixed) |
        | **Prices (MRP / RATE)** | Pass-through, 2-decimal preserved (`499.00`, `499.50`) |
        | **Numeric IDs (Style No, Fabric No., Order No, OEM Barcode)** | Pass-through; trailing `.0` from Excel stripped and logged |
        | **All other fields** (Size, Colour, Material, Fit, Custom Attrs, Sleeve, Collar, etc.) | **Pass-through verbatim from Logic** — no title casing, no mapping, no blanking of `(NIL)` |
        | **Cleanup log** | Every whitespace trim / `.0` strip is logged with row + column + reason, exportable as CSV |
        """
    )

    st.divider()
    st.header("Common issues & fixes")
    st.markdown(
        """
        **Error: "Could not find 'OEM_BARCODE' header row in input file."**
        - You uploaded a file that isn't the Logic Item Master export. Re-export from Logic.

        **Error: "Input file missing required columns"**
        - Logic export is missing one of: `OEM_BARCODE`, `SECTION`, `DEPARTMENT`,
          `STYLE NO`, `FABRIC NO.`, `COLOR`, `PACK / SIZE`, `MRP`.
        - Don't rename or delete columns in Logic before exporting.

        **HS Code is blank for some rows**
        - That Section + Department combination is missing from the HSN table.
        - Tell the tool owner (or raise a PR on
          [GitHub](https://github.com/kedarkulkarni11/cottonworld-fynd-automation))
          to add it — one line in `data/hsn_lookup.csv`.
        - As a one-off, fill the HS Code manually in the downloaded file.

        **A field value looks "ugly" (all caps, weird spacing, `(NIL)`)**
        - That is by design — the tool now passes Logic values through verbatim
          so what you see on Fynd matches what's in Logic. If a value needs
          to be cleaned up, fix it at source in Logic (or fix in the output
          file before upload).

        **Cleanup log has lots of entries**
        - The tool only ever trims whitespace or strips Excel's `.0` artifact
          on integer ID columns. These are safe, mechanical cleanups — review
          them if you want, but no action is required.
        """
    )

    st.divider()
    st.header("Shopify flow (Logic → Shopify)")
    st.markdown(
        """
        Switch the **Target platform** toggle to **Shopify** to produce a
        Shopify **product-import `.csv`** instead of the Fynd `.xlsx`. The Logic
        export (Step 1) and the review habits (Steps 3–4) are identical — only
        these differ:

        - **Output:** a single `.csv` (Shopify's native import format), not a
          multi-sheet workbook. There is **no HS Code lookup** on this path.
        - **Grouping:** size variants are grouped into one product via a shared
          **`Handle`**; product-level fields (Title, metafields) are written on
          the first variant row, variant-level fields (Option values, SKU,
          Barcode, Price) on every row.
        - **What's derived:** `Title` and the `Style Code` metafield use the
          same `SECTION DEPARTMENT FIT COLOR` concatenation as the Fynd Name.
          Everything else is pass-through or a fixed Shopify default
          (`Vendor=Cottonworld`, `Status=draft`, option linkage to Shopify
          size/colour metafields, etc.).
        - **Upload:** in Shopify admin go to **Products → Import**, choose the
          downloaded `.csv`, and review Shopify's import preview before
          confirming. Products land as **draft** — publish after a spot-check.
        """
    )

    st.divider()
    st.caption(
        "Need help? Contact the Fynd team who owns this tool, or raise an "
        "issue on the GitHub repository."
    )


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
pg = st.navigation(
    [
        st.Page(converter_page, title="Converter", icon="📄", default=True),
        st.Page(how_to_use_page, title="How to use", icon="📖"),
    ]
)
pg.run()
