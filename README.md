# baselmini v1.0.0

A minimal-yet-robust Basel III standardized-approach engine.  
Covers **credit risk RWA**, **capital ratios** (CET1/Tier1/Total + leverage + buffers), **liquidity** (LCR with composition caps), **NSFR (lite)**, and a **scenario engine**.  
Outputs are auditable to the per-exposure level with rich explainability.

---

## Quick start

Set up a virtualenv and install:

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -e .
```

Dependencies:  
- **PyYAML** (for YAML configs; JSON also supported)

After installation, example configs/data/scenarios/golden sets are placed under the package’s data directory.  

You can locate it like this:

```bash
python - <<'PY'
import sysconfig, pathlib
root = pathlib.Path(sysconfig.get_paths()["data"]) / "baselmini_examples"
print(root)  # browse this path for configs/, data/, scenarios/, golden/
PY
```

---

## CLI Commands

Additional convenience flags are available:

- **Version**
```bash
baselmini --version
```
Prints the installed version (from pyproject.toml).

- **Verbosity/Quiet**
```bash
baselmini -v run ...   # verbose, high-level progress
baselmini -vv run ...  # extra verbose, full detail
baselmini -q run ...   # quiet, shows warnings and errors only 
```

- **Strict Mode**
```bash
baselmini run ... --strict
```
Treat warnings as errors — run halts with exit code 2, no outputs written.

- **Skip validations**
```bash
baselmini run ... --no-validate
```
Skips schema/result validations.

- **Report to stdout**
```bash
baselmini run ... --stdout report
```
Prints the Markdown report to stdout instead of writing files.

- **Dry run**
```bash
baselmini run ... --dry-run
```
Loads data, computes, validates, and prints a one-screen summary. No files written.

- **List examples**
```bash
baselmini --list-examples
```
Shows paths of bundled sample files under configs/, data/, scenarios/, golden/inputs.

- **Show config**
```bash
baselmini --show-config --config-file configs/std_approach.yml
```
Echoes the parsed/merged config (JSON) to stdout.

---

## Run

Example run (with FX conversion + scenario):

```bash
baselmini run \
  --asof 2025-09-15 \
  --exposures data/exposures.csv \
  --capital   data/capital.csv \
  --liquidity data/liquidity.csv \
  --config    configs/std_approach.yml \
  --fx        data/fx.csv --fx-date-check \
  --nsfr      data/nsfr.csv \
  --scenario  scenarios/shock_fx_10pct.yml \
  --out       out_demo
```

---

## Outputs

Written to the chosen `--out` directory:

- **rwa_per_exposure.csv**  
  One row per exposure with:
  - `ead_gross`, `ead`, `rwa`, `risk_weight`  
  - `supporting_factor_applied` (e.g. SME/Infra factors)  
  - `collateral_mode`, `collateral_haircut_effect`  
  - `scenario_flags` (e.g. `{"ead_mult":1.1,"rating_notches":1}`)  
  - `rw_source` (e.g. `"Corporate:A"`)  
  - `crm_path` (e.g. `"advanced: base=0.02 + short=0.08"`)  

- **rwa_by_class.csv**  
  Totals by asset class.

- **rwa_kpis.json**  
  Portfolio KPIs: gross/net EAD, RWA, densities.

- **results.json**  
  Full structured results: capital, ratios, breaches, warnings, FX metadata, NSFR.

- **report.md**  
  Human-readable mini Pillar 3 style report:  
  - Breaches banner (⚠ CET1, LCR, NSFR, leverage)  
  - Capital components, ratios, buffers, headroom  
  - RWA totals + KPIs with reconciliation check  
  - LCR with Level-2 caps and composition table  
  - NSFR summary  
  - Top-5 RWA contributors (with RW source + CRM path)  
  - Non-blocking data warnings  

---

## Features

- **Credit Risk (SA style)**  
  - Risk weights by asset class and rating (AAA → NR).  
  - Mortgages with LTV banding.  
  - SME/Infrastructure supporting factors (stacking modes: multiply / min / priority).  

- **Collateral & CRM**  
  - Simple mode (haircuts per type / override).  
  - Advanced mode (supervisory haircuts + maturity & FX add-ons).  
  - Full trace via `crm_path`.

- **Capital stack**  
  - CET1, AT1, Tier 2, deductions.  
  - Ratios vs minimums + buffers.  
  - Leverage ratio (Tier1/Exposure).  
  - Breach signalling (ratios < requirement).  

- **Liquidity (LCR)**  
  - HQLA (Level 1, 2A, 2B) with 40%/15% caps.  
  - Outflow/inflow buckets with configurable rates.  
  - Net outflows ≤0 handled gracefully (LCR=0).  

- **NSFR (lite)**  
  - ASF and RSF buckets × factors.  
  - Ratio, % and breach signalling.  

- **FX support**  
  - Converts exposures + collateral to base CCY.  
  - Rejects zero/negative FX rates.  
  - Optional `--fx-date-check` to enforce `quote_date <= asof`.  

- **Scenario engine**  
  - EAD multipliers per asset class.  
  - Global / per-class rating notch-down.  
  - LCR multipliers: inflows/outflows/haircuts.  
  - HQLA haircuts bump (bps).  
  - Flags recorded per exposure for audit.  

- **Validation & auditability**  
  - Config schema checks (typos/unknown keys flagged).  
  - Per-row data hygiene: required fields, non-negatives, CCY whitelist.  
  - Aggregate vs per-exposure reconciliation logged.  
  - Breaches surfaced in JSON + report.  

- **Golden test set**  
  - A demo input + expected output bundle for reproducibility.  

---

## Tests

Run unit tests:

```bash
python -m unittest discover -s tests -v
```

---

# Data dictionaries

### Exposures (`exposures_*.csv`)
| Column | Required | Type | Notes |
|---|---|---|---|
| `id` | ✓ | string | Unique exposure identifier. |
| `asset_class` | ✓ | string | One of your configured classes (e.g., Bank, Corporate, Retail, Mortgage, Sovereign, SME, Infrastructure). |
| `rating` |  | string | AAA/AA/A/BBB/BB/B/CCC/NR; NR assumed if missing. |
| `ead` | ✓* | number | If `drawn`/`undrawn` not provided. |
| `drawn` | * | number | Used with `undrawn` + `commitment_type` to build EAD. |
| `undrawn` | * | number | See above. |
| `commitment_type` |  | string | Looks up CCF in config (e.g., `revocable`, `irrevocable_lt1y`). |
| `mortgage_ltv` | (Mortgage) | number | Used for LTV banding (e.g., 0.78). |
| `exposure_ccy` \| `ccy` |  | string | Exposure currency (FX conversion uses this). |
| `eligible_collateral` |  | number | Nominal collateral amount. |
| `collateral_type` |  | string | E.g., `cash`, `gov_bond_lvl1`, `corp_bond`. |
| `collateral_haircut` |  | number | Row override; otherwise type/default is used. |
| `collateral_residual_maturity_days` |  | integer | Advanced CRM short-maturity add-on trigger. |
| `collateral_ccy` |  | string | Used to detect FX mismatch in advanced CRM. |

\* Provide **either** `ead` **or** (`drawn`+`undrawn` with a matching `commitment_type`).  

---

### Liquidity (`liquidity_*.csv`)
| Column | Required | Type | Notes |
|---|---|---|---|
| `bucket` | ✓ | string | One of: `HQLA_L1`, `HQLA_L2A`, `HQLA_L2B`, `OUTFLOW`, `INFLOW`. |
| `amount_ccy` | ✓ | number | Nominal amount in the instrument currency (FX converts if provided). |
| `rate` | for flows | number | Flow rate (outflow or inflow). Backward-compatible aliases: `outflow_rate`, `inflow_rate`. |
| `haircuts` | for HQLA (opt) | number | Optional per-row HQLA haircuts if you choose to use it; caps still apply. |

_Notes:_  
- In v1.0.0, the column is normalized to `rate`. If you feed `outflow_rate`/`inflow_rate`, they are accepted as aliases and mapped to `rate`.  
- Caps from config: Level-2 total 40%, L2B 15%, inflow cap 75%.
- When scenarios apply inflow/outflow multipliers, they adjust the inflow_rate or outflow_rate fields as appropriate; at runtime these are normalized into the neutral rate column used by the LCR calculation.
---

### NSFR (`nsfr_*.csv`)
| Column | Required | Type | Notes |
|---|---|---|---|
| `bucket` | ✓ | string | Use `ASF` or `RSF`. |
| `amount_ccy` | ✓ | number | Nominal amount (pre-FX). |
| `factor` | ✓ | number | Available/Required Stable Funding factor (e.g., 0.85). |

The engine sums `ASF = Σ(amount×factor)` over `bucket=ASF`, `RSF = Σ(amount×factor)` over `bucket=RSF`, then reports `NSFR = ASF/RSF`.

---

# Config snippet — Supporting-factor stacking (v1.0.0)

```yaml
supporting_factors:
  enabled: true
  stacking: "min"                 # "multiply" (default), "min", or "priority"
  priority_order: ["sme","infra"] # used only when stacking: "priority"
  sme:
    on_asset_classes: ["SME"]
    factor: 0.7619
  infra:
    on_asset_classes: ["Infrastructure"]
    factor: 0.75
```

- `multiply` → SME and Infra both apply and **compound** (factor = 0.7619 × 0.75).  
- `min` → take the **most conservative** (smallest) factor.  
- `priority` → apply the **first** matching factor in `priority_order`.

_Add this under your existing config’s `supporting_factors:` block._

---

# Troubleshooting

- **Mortgage row without `mortgage_ltv`** → RWA falls back to `Mortgage.default`; warning emitted.  
  _Fix:_ fill `mortgage_ltv` (0–1).  
- **Unknown `asset_class`** → warning; RW falls back to class `default` if present, else to the global default.  
  _Fix:_ correct spelling or add the class in `risk_weights`.  
- **NR rating fallback** → rows without `rating` default to `NR`.  
  _Fix:_ add a rating if you intend a different RW.  
- **Non-positive FX rate** → hard error (conversion disabled).  
  _Fix:_ ensure `rate>0` (e.g., USD=1.0).  
- **FX quote date > as-of (with `--fx-date-check`)** → hard error.  
  _Fix:_ use quotes with `quote_date <= asof`.  
- **Currency whitelist mismatch (warning)** → currency not in config’s whitelist.  
  _Fix:_ add or correct the currency code.  
- **`--strict`** → any warnings cause exit code 2 and **no output files** (by design). Run without `--strict` to inspect warnings in `report.md`.

---

# Golden repro & tests

- **Unit tests**
  ```bash
  python -m unittest discover -s tests -v
  ```

- **Golden run (example)**
  ```bash
  baselmini run     --asof 2025-09-15     --exposures golden/inputs/exposures.csv     --capital   golden/inputs/capital.csv     --liquidity golden/inputs/liquidity.csv     --config    golden/inputs/config.yml     --nsfr      golden/inputs/nsfr.csv     --out       golden/out_golden
  ```
  Compare `golden/out_golden/results.json` against `golden/expected/results.json` using `diff` or your favourite JSON diff tool.

## License

MIT — free to use for demos, teaching, and interviews.
