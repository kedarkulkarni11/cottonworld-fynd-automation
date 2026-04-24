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

st.title("Cottonworld → Fynd Platform Converter")
st.markdown(
    "Upload the **Logic ERP Item Master** `.xlsx` file to generate a "
    "Fynd Commerce-ready upload file."
)
st.caption(
    "All sections (Mens, Ladies, Boys, Unisex) and departments are supported. "
    "HS Code is resolved from the Section + Department HSN lookup."
)

st.divider()

uploaded_file = st.file_uploader(
    "Upload Logic ERP Item Master (.xlsx)",
    type=["xlsx"],
    help="This is the Item Master export from Logic ERP (PO file).",
)

if uploaded_file is not None:
    st.info(f"Uploaded: **{uploaded_file.name}** ({uploaded_file.size / 1024:.1f} KB)")

    if st.button("Convert to Fynd Template", type="primary"):
        with st.spinner("Transforming data..."):
            try:
                output_buf, warnings = transform(uploaded_file)

                st.success("Conversion complete!")

                output_filename = (
                    uploaded_file.name.replace(".xlsx", "") + "_fynd_upload.xlsx"
                )

                st.download_button(
                    label="Download Fynd Upload File",
                    data=output_buf,
                    file_name=output_filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
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

            except ValueError as e:
                st.error(f"Error: {e}")
            except Exception as e:
                st.error(f"Unexpected error: {e}")
                st.exception(e)

st.divider()
st.caption("Cottonworld Automation Tool v2.0 | All sections & departments")
