# Cottonworld → Fynd Converter

Streamlit web app that converts a Cottonworld **Logic ERP Item Master** export
into a **Fynd Commerce Platform** bulk-upload XLSX.

Designed to be embedded as a **private extension inside Fynd Commerce Platform**
(no authentication built in — rely on the platform's access control).

**Live URL:** https://cottonworld-fynd-automation.onrender.com/

## What it does

1. User uploads the Logic ERP Item Master `.xlsx`.
2. App groups rows by `STYLE NO + FABRIC NO + COLOR` (one product per group;
   size rows become variants).
3. Applies the mapping defined in
   *Cottonworld Logic Item master >> Fynd Platform Template.docx*:
   - Product Name: `Gender's Composition Fit Department Color`
   - Item Code: `{SectionPrefix}-{Dept}-{StyleNo}-{FabricNo}-{Color}`
   - HS Code: looked up from `data/hsn_lookup.csv` by `(Section, Department)`
   - Category: `Others Level 3` (fixed)
   - Tax Rule: `Tiered Tax Rule – 5% & 18% (Eff. 22 Sep 2025) (2)`
   - Custom Attributes 1–29 mapped to Logic columns per the doc
4. Surfaces warnings for unknown sleeve/collar values and missing HSN rows.
5. Returns a Fynd-ready `.xlsx` for download.

## Supported scope
- **Sections**: Mens, Ladies (→ Women), Boys, Unisex
- **Departments**: all 40+ Logic departments listed in `data/hsn_lookup.csv`.
- **Size standardization**: `SMALL/MEDIUM/LARGE/XLARGE` → `S/M/L/XL`.

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deployment (Render)
This repo is configured for one-click deploy to [Render](https://render.com)
via `render.yaml`.

1. In Render → **New +** → **Blueprint** → connect this GitHub repo.
2. Render reads `render.yaml` and provisions a Free web service.
3. Auto-deploys on every push to `main`.
4. Embed the resulting URL as a **Private Extension** in the Fynd Commerce
   Platform for Cottonworld's company ID.

No secrets or env vars needed. Startup command and Python version are defined
in `render.yaml`.

## Updating reference data
Edit files in `/data` and redeploy — no code changes required:
- `hsn_lookup.csv` — add new Section+Department HSN mappings
- `sleeve_map.json`, `collar_map.json`, `material_map.json` — add new
  abbreviations when Logic team introduces them
- `department_display.json` — control how departments appear in Product Name
- `static_values.json` — brand, trader, tax rule, return policy

## Warnings
The tool never blocks the file. When it encounters:
- Unknown sleeve or collar value → passes value through (title-cased) and flags.
- Missing HSN entry for `(Section, Department)` → leaves HS Code blank and flags.

Review the warnings panel before uploading to Fynd.

## File layout
```
cottonworld-automation/
├── app.py                          # Streamlit UI
├── transformer.py                  # Core transformation logic
├── requirements.txt
├── README.md
├── .streamlit/config.toml
└── data/
    ├── hsn_lookup.csv
    ├── sleeve_map.json
    ├── collar_map.json
    ├── material_map.json
    ├── section_gender.json
    ├── department_display.json
    └── static_values.json
```
