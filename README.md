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
├── data/
│   ├── hsn_lookup.csv
│   ├── sleeve_map.json
│   ├── collar_map.json
│   ├── material_map.json
│   ├── section_gender.json
│   ├── department_display.json
│   └── static_values.json
└── fynd-extension/                 # Node.js OAuth shim for Fynd Private Extension
    ├── server.js
    ├── index.js
    ├── public/index.html
    ├── package.json
    ├── Dockerfile
    └── README.md
```

## Deploy as Fynd Private Extension

Architecture: **two** Render services, both defined in this repo's
`render.yaml`.

1. **`cottonworld-fynd-automation`** — the Streamlit tool (already live).
2. **`cottonworld-fynd-extension`** — a small Node.js shim (in
   [`fynd-extension/`](./fynd-extension)) that handles Fynd's OAuth
   (`/fp/install`, `/fp/auth`) and iframes the Streamlit app after
   successful install.

### Step 1 — Create the extension in Fynd Partner panel
1. Go to `console.fynd.com` → **Partners** → **Extensions** → **Create Extension** → **Create Here**.
2. Fill in Basic Details (name, icon, description).
3. Set **Extension URL** to a placeholder for now (e.g.
   `https://cottonworld-fynd-extension.onrender.com`). You'll confirm this
   after the service is deployed.
4. Select the **Permissions** the tool actually needs. This tool does not
   call any Platform API, so *Products* alone is typically sufficient (or
   even none).
5. Save. Open the extension again → copy the **API Key** and **API Secret**.

### Step 2 — Deploy the extension shim to Render
1. In Render, the existing Blueprint picks up the new service automatically.
   (Render → Blueprint → Sync — it sees `cottonworld-fynd-extension` in
   `render.yaml`.)
2. Render provisions the Docker service but it will fail to start until
   credentials are set. Open the service → **Environment** and add:
   - `EXTENSION_API_KEY` — from Fynd Partner panel
   - `EXTENSION_API_SECRET` — from Fynd Partner panel
   - `EXTENSION_BASE_URL` — the Render URL of *this* service, e.g.
     `https://cottonworld-fynd-extension.onrender.com`
3. Trigger a redeploy (Render → Manual Deploy → Deploy latest commit).

### Step 3 — Wire up Fynd Partner panel
1. Go back to the extension in Fynd Partner panel.
2. Update **Extension URL** to the final Render URL of the extension shim
   (must exactly match `EXTENSION_BASE_URL`).
3. Save.

### Step 4 — Install on Cottonworld's company
1. In Fynd, go to the extension's **Preview** / **Install** page.
2. Choose Cottonworld's company and click **Install**.
3. Fynd redirects to `https://cottonworld-fynd-extension.onrender.com/fp/install`,
   which kicks off OAuth.
4. Approve permissions.
5. On success, Fynd returns you to the extension → you see the Streamlit
   converter loaded in an iframe.

### Troubleshooting
- **"Invalid api_key or api_secret"** — double-check the env vars in
  Render. They must match exactly what Fynd shows under Credentials.
- **Iframe doesn't render** — verify `EXTENSION_BASE_URL` in Render matches
  the Extension URL saved in Fynd exactly (including `https://` and no
  trailing slash).
- **Streamlit app blocked in iframe** — check the browser console for
  `X-Frame-Options` or CSP errors. The extension shim sets a permissive
  `frame-ancestors` header for `*.fynd.com` already; if you see the
  Streamlit app itself refusing to render, the Streamlit Dockerfile may
  need `--server.enableCORS=false` added.
