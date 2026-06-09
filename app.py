"""
Cottonworld Automation Tool - Streamlit Web App
Converts Logic ERP Item Master export → Fynd Platform upload template.
"""

import streamlit as st

from transformer import transform

st.set_page_config(
    page_title="Cottonworld → Fynd Converter",
    page_icon="👕",
    layout="centered",
)


# ---------------------------------------------------------------------------
# Page: Converter
# ---------------------------------------------------------------------------
def converter_page():
    st.title("Cottonworld → Fynd Platform Converter")
    st.markdown(
        "Upload the **Logic ERP Item Master** `.xlsx` file to generate a "
        "Fynd Commerce-ready upload file."
    )
    st.caption(
        "All sections (Mens, Ladies, Boys, Unisex) and departments are supported. "
        "HS Code is resolved from the Section + Department HSN lookup."
    )

    st.warning(
        "⚠️ **Always review the generated file before uploading to Fynd Commerce Platform.** "
        "This tool automates the mapping but does not guarantee correctness for every row — "
        "open the output in Excel, spot-check names, HS codes, prices, and any flagged "
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

        # Invalidate cached result if a new file is uploaded
        current_key = f"{uploaded_file.name}::{uploaded_file.size}"
        if st.session_state.get("conv_source_key") != current_key:
            st.session_state.pop("conv_result", None)
            st.session_state["conv_source_key"] = current_key

        if st.button("Convert to Fynd Template", type="primary"):
            with st.spinner("Transforming data..."):
                try:
                    output_buf, warnings, output_df, cleanup_df = transform(uploaded_file)
                    st.session_state["conv_result"] = {
                        "buf_bytes": output_buf.getvalue(),
                        "warnings": warnings,
                        "df": output_df,
                        "cleanup_df": cleanup_df,
                        "source_name": uploaded_file.name,
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

            st.success("Conversion complete!")

            # ---- Summary metrics ----
            total_rows = len(output_df)
            product_rows = output_df["Name"].astype(str).str.strip()
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
                "First 20 rows of the generated file. Toggle below to "
                "see only key columns or the full template (102 columns)."
            )

            key_cols = [
                "Name", "Item Code", "Brand", "Category", "HS Code",
                "Gtin Value", "Size", "Actual Price", "Currency",
                "Colour", "Material",
                "Custom Attribute 1",  # Department
                "Custom Attribute 2",  # Fit
                "Custom Attribute 3",  # Gender
                "Custom Attribute 5",  # Collar
                "Custom Attribute 7",  # Sleeve
                "Custom Attribute 14", # Style No
                "Custom Attribute 20", # Fabric No
            ]
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
                + "_fynd_upload.xlsx"
            )
            st.download_button(
                label="Download Fynd Upload File",
                data=result["buf_bytes"],
                file_name=output_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )

            st.info(
                "📌 **Before uploading to Fynd:** open the downloaded file, "
                "verify a few product rows (Name, HS Code, Price, Custom Attributes), "
                "and review any warnings listed below."
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
    st.caption("Cottonworld Automation Tool v3.0 | Pass-through mode (Name + Item Code derived only)")


# ---------------------------------------------------------------------------
# Page: How to use
# ---------------------------------------------------------------------------
def how_to_use_page():
    st.title("How to use this tool")
    st.caption("A step-by-step guide for the Cottonworld team.")

    st.warning(
        "⚠️ **Disclaimer:** This tool automates the Logic → Fynd mapping, but "
        "you must **always verify the output file before uploading to the Fynd "
        "Commerce Platform**. Open the file in Excel, check product names, HS "
        "codes, MRP, and any flagged warnings. The tool is an accelerator, not "
        "a substitute for a final human review.",
        icon="⚠️",
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
        2. Click **Browse files** and select the Logic `.xlsx` you exported.
        3. Click **Convert to Fynd Template**.
        4. Wait a few seconds while the tool processes the file.
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
