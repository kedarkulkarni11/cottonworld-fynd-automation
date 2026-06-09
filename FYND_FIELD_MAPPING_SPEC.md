# Cottonworld → Fynd Field Mapping Specification

**Audience:** Cottonworld business team — for review & sign-off
**Purpose:** Document every transformation applied to Logic ERP Item Master data when generating the Fynd Commerce Platform bulk-upload file.

**Version:** 3.0 — Pass-through rules (June 2026 client revision)

---

## 1. How the transformer works (at a glance)

1. **Input:** Logic ERP "Item Master" export (`.xlsx`). The transformer auto-detects the header row by looking for the `OEM_BARCODE` cell, so the report's leading title rows don't matter.
2. **Grouping:** Rows are grouped into "products" by the composite key **`STYLE NO + FABRIC NO. + COLOR`**. Each product becomes one Fynd product with one row per size/variant.
3. **Output:** Fynd Platform bulk-upload `.xlsx` with **51 standard columns** + **50 custom-attribute columns** (`Custom Attribute 1` through `Custom Attribute 50`).
4. **Two row types in the output:**
   - **Product-level fields** (Name, Category, HS Code, Material, Trader info, etc.) — written **only on the first row** of each product group.
   - **Variant-level fields** (Size, GTIN, Price, Dimensions, Weight, Net Quantity) — written on **every row**.
5. **Warnings:** Missing HSN mappings surface as operator warnings.
6. **Cleanup log:** Every cell mutation (whitespace trim, Excel `.0` artifact strip on numeric IDs) is recorded with row/column/reason and surfaced to the operator as a downloadable CSV.

---

## 2. Core principle — pass-through by default

The transformer applies **only two derived fields**: `Name` and `Item Code`. **Every other Logic ERP value is passed through verbatim** — no title-casing, no display-name mapping, no blanking of `(NIL)`, no fabric-blend collapsing, no size standardization, no sleeve/collar abbreviation expansion.

The only mutations the transformer ever performs on a value are:

| Mutation | Where | Why |
|---|---|---|
| **Whitespace trim** | All fields | Strip leading/trailing whitespace from Logic exports. Logged. |
| **`.0` strip** | Numeric ID columns (`STYLE NO`, `FABRIC NO.`, `ORDER NO`, `OEM_BARCODE`) | Excel reads integer-typed cells as floats (`17656` → `17656.0`). The transformer restores the integer representation. Logged. |
| **2-decimal price formatting** | `MRP`, `RATE` | Logic emits `499.00` and `499.50`; pandas would render `499.0`. The transformer preserves 2-decimal display. |
| **`mm-yyyy` date formatting** | `PACKED DATE` | Excel-date serial → `mm-yyyy` text. Per client transcript. |

That's it. No other transformations are applied. If a value in Logic is `(NIL)`, `NaN`, all caps, or has weird spacing — that's what shows up on Fynd.

---

## 3. Field-by-field mapping

### 3.1 Derived fields (the only two)

| Fynd column | Rule | Example |
|---|---|---|
| **Name** | `SECTION DEPARTMENT FIT COLOR` — raw verbatim from Logic, joined by single spaces. Empty / `(NIL)` segments are skipped. | `MENS, TSHIRT, REGULAR FIT, BLACK` → **`MENS TSHIRT REGULAR FIT BLACK`** |
| **Item Code** | `{first-letter-of-SECTION}-DEPARTMENT-STYLE_NO-FABRIC_NO-COLOR`. Empty / `(NIL)` segments skipped (no dangling hyphens). | `MENS, TSHIRT, 17656, 21646, BLACK` → **`M-TSHIRT-17656-21646-BLACK`**<br>`LADIES, KURTI, 17656, 21646, RED` → **`L-KURTI-17656-21646-RED`** |

**Note on `LADIES` → `L`:** the prefix is now the first character of the raw `SECTION`, not the historical "W" (Women). Confirmed by client.

---

### 3.2 Static fields

| Fynd column | Value |
|---|---|
| Brand | `cottonworld` |
| Category | `Others level 3` |
| Tax Rule Name | `Tiered Tax Rule – 5% & 18% (Eff. 22 Sep 2025) (2)` |
| Country of Origin | `India` |
| GTIN Type | `EAN` |
| Currency | `INR` |
| Length / Width / Height (cm) | `1` / `1` / `1` |
| Product Dead Weight (gram) | `200` |
| Trader Type | `Manufacturer` |
| Trader Name | `Lekhraj Corp Pvt Ltd` |
| Trader Address | `GALA-F, SIDHWA ESTATE, OLD BMP BUILDING, N.A. SAWANT MARG, Colaba, Mumbai City, Maharashtra, 400005` |
| Return Time Limit / Unit | `30` / `Days` |
| **Net Quantity Value** | `1` |
| **Net Quantity Unit** | `number` (platform doesn't have `Pcs`) |

---

### 3.3 HS Code

Looked up from `data/hsn_lookup.csv` (60 entries) keyed on raw `(SECTION, DEPARTMENT)`. Misses → blank HS Code + operator warning.

Coverage today:

| Section | Mappings | Status |
|---|---|---|
| LADIES | 40 | ✅ Strong coverage |
| MENS | 18 | ✅ Good coverage |
| BOYS | 1 (only `MASK`) | ⚠️ Sparse |
| GIRLS | 0 | ❌ No mappings |
| UNISEX | 1 (`TOTE BAG`) | OK if scope is limited |

---

### 3.4 Identifier & variant fields

| Fynd column | Logic source | Treatment |
|---|---|---|
| Seller Identifier | `OEM_BARCODE` | Pass-through + `.0` strip + logged |
| Gtin Value | `OEM_BARCODE` | Same as Seller Identifier |
| Size | `PACK / SIZE` | **Pass-through verbatim** (no SMALL → S mapping) |
| Actual Price | `MRP` | 2-decimal preserved (`499.00`, `499.50`) |
| Selling Price | `MRP` | Same as Actual Price |

---

### 3.5 Color & material

| Fynd column | Logic source | Treatment |
|---|---|---|
| Colour | `COLOR` | **Pass-through verbatim** (no title-casing) |
| Material | `COMPOSITION1` | **Pass-through verbatim** (no fabric-blend collapsing, no percentage stripping) |

---

### 3.6 Custom Attributes

| Custom Attribute | Logic source | Treatment |
|---|---|---|
| CA 1 — Department | `DEPARTMENT` | Pass-through verbatim |
| CA 2 — Fit | `FIT` | Pass-through verbatim |
| CA 3 — Gender / Section | `SECTION` | Pass-through verbatim (no `LADIES` → `Women` map) |
| CA 4 — Occasion | `OCCASION` | Pass-through verbatim |
| CA 5 — Neck / Collar | `NECK-COLLAR` | Pass-through verbatim (no abbreviation expansion) |
| CA 6 | _(intentionally blank)_ | — |
| CA 7 — Sleeve | `SLEEVE TYPE` | Pass-through verbatim (no abbreviation expansion) |
| CA 8 — Order No. | `ORDER NO` | Pass-through + `.0` strip + logged |
| CA 9 — Fabric Main Description | `FABRIC MAIN DESC` | Pass-through verbatim |
| CA 10 — Fabric Sub Description | `FABRIC SUB DESC` | Pass-through verbatim |
| CA 11 — Fabric Sub Type | `FABRIC SUB TYPE` | Pass-through verbatim |
| CA 12 — Highlights (HL) | `HL` | Pass-through verbatim |
| CA 13 — Pockets | `POCKETS` | Pass-through verbatim |
| CA 14 — Style No. | `STYLE NO` | Pass-through + `.0` strip + logged |
| CA 20 — Fabric No. | `FABRIC NO.` | Pass-through + `.0` strip + logged |
| CA 21 — CS | `CS` | Pass-through verbatim |
| CA 22 — Packed Date | `PACKED DATE` | Reformatted to `mm-yyyy` |
| CA 23 — Length | `LENGTH` | Pass-through verbatim |
| CA 24 — Waist | `WAIST` | Pass-through verbatim |
| CA 25 — Closure | `CLOSURE` | Pass-through verbatim |
| CA 26 — Leg | `LEG` | Pass-through verbatim |
| CA 27 — Front | `FRONT` | Pass-through verbatim |
| CA 28 — Fabric Type | `FABRIC TYPE` | Pass-through verbatim |
| CA 29 — Rate | `RATE` | 2-decimal preserved |

CAs 15–19, 30–50 are intentionally left blank.

---

## 4. Cleanup log

Every mutation (whitespace trim, `.0` strip on numeric IDs) is recorded with:

| Column | Description |
|---|---|
| Logic Row (Excel) | The 1-based Excel row number in the original Logic file |
| Column | The Logic column name (e.g. `STYLE NO`, `OEM_BARCODE`) |
| Original Value | The value as it appeared in Logic |
| Cleaned Value | The value as written to the Fynd output |
| Reason | E.g. `Trimmed leading/trailing whitespace`, `Stripped trailing '.0' (Excel float artifact)` |

The Streamlit UI displays a metric (`Cleanups logged`), an expandable table, and a CSV download button. The Boltic handler returns the log as **Sheet 2** of the output `.xlsx` (`Cleanup Log`).

---

## 5. Operator warnings

The transformer only ever raises **one type of warning**:

> "No HSN mapping for Section='X', Department='Y'. HS Code left blank — please add to data/hsn_lookup.csv."

Unknown sleeve/collar/material/department values are no longer warnings — they're just passed through verbatim.

---

## 6. Columns intentionally left blank

`Slug`, `Description`, `Short Description`, `Media`, `Multi Size`, `Meta`, `Size Meta`, `Size Guide`, `Available`, `Highlights`, `Unlisted Product`, `Variant Type`, `Variant Group ID`, `Variant Media`, `Track Inventory`, `Teaser Tag Name`, `No of Boxes`, `Manufacturing Time`, `Manufacturing Time Unit`, `Product Publishing Date`, `Tags`, `Product Bundle`, `Package Contents`, `Quantity Factor`, `Priority`, plus Custom Attributes `6`, `15`–`19`, `30`–`50`.

---

## 7. Change-management process

- The HSN lookup (`data/hsn_lookup.csv`) and static values (`data/static_values.json`) live in the `/data` directory and can be updated centrally without code changes.
- All other mapping files (`collar_map.json`, `sleeve_map.json`, `material_map.json`, `section_gender.json`, `department_display.json`) have been **removed** — they are no longer used.

---

_End of specification — please return with marked-up feedback or section-by-section approval._
