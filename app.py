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

    uploaded_file = st.file_uploader(
        "Upload Logic ERP Item Master (.xlsx)",
        type=["xlsx"],
        help="This is the Item Master export from Logic ERP (PO file).",
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
                    output_buf, warnings, output_df = transform(uploaded_file)
                    st.session_state["conv_result"] = {
                        "buf_bytes": output_buf.getvalue(),
                        "warnings": warnings,
                        "df": output_df,
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

            st.success("Conversion complete!")

            # ---- Summary metrics ----
            total_rows = len(output_df)
            product_rows = output_df["Name"].astype(str).str.strip()
            num_products = (product_rows != "").sum()
            col1, col2, col3 = st.columns(3)
            col1.metric("Total rows (SKUs)", f"{total_rows}")
            col2.metric("Unique products", f"{num_products}")
            col3.metric("Warnings", f"{len(warnings)}")

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

    st.divider()
    st.caption("Cottonworld Automation Tool v2.1 | All sections & departments")


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

    st.header("Step 3 — Review the warnings")
    st.markdown(
        """
        After conversion, the tool may show a list of warnings such as:

        - **Unknown sleeve type / neck-collar** — Logic has a new abbreviation
          the tool doesn't recognise yet. The value is passed through as-is, so
          just check whether it's acceptable on Fynd.
        - **No HSN mapping for (Section, Department)** — the tool doesn't have
          an HS Code for that combination. The HS Code field will be **blank**
          — fill it in manually before upload, and flag it so we can add the
          mapping permanently.

        Warnings are **advisory** — the file is still generated. Treat them as
        a checklist of things to manually verify.
        """
    )

    st.header("Step 4 — Download and verify")
    st.markdown(
        """
        1. Click **Download Fynd Upload File**.
        2. Open the file in Excel or Google Sheets.
        3. Spot-check at least **5–10 products** across different sections and
           departments:
           - **Name** reads like *Men's Cotton Regular Fit T-shirt Black*
           - **Item Code** format: `M-TSHIRT-17656-21646-BLACK`
           - **HS Code** is 8 digits and matches the expected tariff code
           - **Actual Price / Selling Price** = Logic MRP
           - **Size** is `S/M/L/XL/XXL` (short forms)
           - **Colour / Material** filled in
           - **Custom Attribute 1** = Department (e.g. T-shirt, Shirt, Pant)
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
        | **Product Name** | `Gender's Composition Fit Department Color` — no percentages, no "Cottonworld" prefix |
        | **Item Code** | `{SectionPrefix}-{Dept}-{StyleNo}-{FabricNo}-{Color}` — one code per product, shared across size variants |
        | **Brand** | `cottonworld` (fixed) |
        | **Category** | `Others level 3` (fixed) |
        | **Tax Rule** | `Tiered Tax Rule – 5% & 18% (Eff. 22 Sep 2025) (2)` |
        | **HS Code** | Looked up from the **Section + Department** HSN table |
        | **Country of Origin** | `India` |
        | **Dimensions** | 1 × 1 × 1 cm, 200 g (placeholder — update in Fynd if needed) |
        | **Trader / Marketer** | Lekhraj Corp Pvt Ltd (Colaba) |
        | **Return policy** | 30 Days |
        | **Sleeve / Collar** | Abbreviations expanded (FS → Full Sleeves, HS → Half Sleeves, etc.) |
        | **Size** | Standardized (SMALL → S, MEDIUM → M, XLARGE → XL) |
        | **Gender** | `Ladies` → `Women` |
        | **Custom Attributes 1–29** | Mapped from Logic columns per the approved mapping doc |
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

        **A sleeve / collar value isn't expanding**
        - Same cause — new abbreviation not yet in the mapping file.
        - Manually correct in the output file, and report so it can be added.
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
