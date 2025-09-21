# validators.py
# -----------------------------------------------------------------------------
# Validations over aggregated results + non-blocking per-row data warnings.
# -----------------------------------------------------------------------------

from typing import Dict, Any, List
from datetime import datetime


# --- Tiny date helper ---------------------------------------------------------

def _parse_date(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


# --- Warning-level checks (non-blocking) -------------------------------------

def collect_warnings(exposures: List[Dict[str, Any]],
                     liquidity: List[Dict[str, Any]],
                     cfg: Dict[str, Any],
                     fx_meta: Dict[str, Any] | None) -> List[str]:
    warnings: List[str] = []

    # Currency whitelist from fx_meta (if provided)
    base = ((cfg.get("fx") or {}).get("base_ccy") or "").upper()
    allowed_ccy = set()
    if fx_meta and isinstance(fx_meta.get("quotes"), dict):
        allowed_ccy = {k.upper() for k in fx_meta["quotes"].keys()}
    if base:
        allowed_ccy.add(base)

    # Allowed asset classes derived from config (warn on unknown data rows)
    allowed_asset_classes = set((cfg.get("risk_weights") or {}).keys())

    # Supporting factor config (for hints)
    sf = (cfg.get("supporting_factors") or {})
    sf_enabled = bool(sf.get("enabled", False))

    # Exposure-level checks
    for i, e in enumerate(exposures):
        row_id = e.get("id") or f"row{i+1}"
        ac = (e.get("asset_class") or "").strip()
        rating = e.get("rating")

        # Unknown/typo asset_class at data level → warn (non-blocking)
        if ac and allowed_asset_classes and ac not in allowed_asset_classes:
            warnings.append(f"[{row_id}] asset_class='{ac}' not present in config.risk_weights keys {sorted(allowed_asset_classes)}")

        # Required by class
        if ac == "Mortgage":
            if e.get("mortgage_ltv") in (None, "",):
                warnings.append(f"[{row_id}] Mortgage without mortgage_ltv")

        # SME/Infra labeling vs flags
        if sf_enabled:
            if ac == "SME" and str(e.get("is_sme", "")).strip() not in {"1", "true", "True"}:
                warnings.append(f"[{row_id}] SME asset_class without is_sme flag set (ok, but note)")
            if ac == "Infrastructure" and str(e.get("is_infra", "")).strip() not in {"1", "true", "True"}:
                warnings.append(f"[{row_id}] Infrastructure asset_class without is_infra flag set (ok, but note)")

        # Non-negative numeric fields
        for k in ("ead","drawn","undrawn","collateral_value","eligible_collateral"):
            if k in e and e[k] not in (None, "",):
                try:
                    if float(e[k]) < 0:
                        warnings.append(f"[{row_id}] {k} is negative")
                except Exception:
                    warnings.append(f"[{row_id}] {k} not numeric")

        # Currency whitelist (only when we have FX info)
        if allowed_ccy:
            for cfield in ("exposure_ccy","collateral_ccy"):
                cc = (e.get(cfield) or "").upper()
                if cc and cc not in allowed_ccy:
                    warnings.append(f"[{row_id}] {cfield}='{cc}' not in FX whitelist {sorted(allowed_ccy)}")

        # Basic hygiene
        if not ac:
            warnings.append(f"[{row_id}] missing asset_class")
        if rating in (None, "",):
            warnings.append(f"[{row_id}] missing rating (NR will be used)")

    # --- Liquidity row checks --------------------------------------------------
    for j, r in enumerate(liquidity):
        rid = r.get("id") or f"L{j+1}"
        bucket = (r.get("bucket") or "").strip().upper()

        # Known buckets
        if bucket not in {"HQLA_L1","HQLA_L2A","HQLA_L2B","OUTFLOW","INFLOW"}:
            warnings.append(f"[{rid}] unknown bucket '{bucket}'")

        # amount_ccy present & numeric & non-negative
        amt_raw = r.get("amount_ccy")
        try:
            amt = float(amt_raw) if amt_raw not in (None, "",) else None
        except Exception:
            amt = None
        if amt is None:
            warnings.append(f"[{rid}] liquidity missing/invalid amount_ccy for bucket='{bucket}'")
        elif amt < 0:
            warnings.append(f"[{rid}] liquidity amount_ccy < 0 for bucket='{bucket}'")

        # Optional HQLA haircut sanity (if present)
        try:
            hc = float(r.get("haircuts", 0.0) or 0.0)
            if hc < 0 or hc > 1:
                warnings.append(f"[{rid}] haircuts out of [0,1]")
        except Exception:
            if r.get("haircuts") not in (None, "",):
                warnings.append(f"[{rid}] haircuts not numeric")

        # Neutral rate validation (with legacy aliases)
        is_flow = bucket in ("OUTFLOW", "INFLOW")
        rate_field_used = None
        rate_val = None
        # Prefer 'rate', accept legacy 'outflow_rate' / 'inflow_rate'
        for fname in ("rate", "outflow_rate", "inflow_rate"):
            if r.get(fname) not in (None, "",):
                rate_field_used = fname
                try:
                    rate_val = float(r.get(fname))
                except Exception:
                    rate_val = None
                break

        if is_flow:
            # Require rate for flows
            if rate_val is None:
                if rate_field_used is None:
                    warnings.append(f"[{rid}] {bucket} row missing rate (accepts 'rate', legacy 'outflow_rate'/'inflow_rate')")
                else:
                    warnings.append(f"[{rid}] {bucket} row has non-numeric {rate_field_used}='{r.get(rate_field_used)}'")
            else:
                # Non-negative
                if rate_val < 0:
                    warnings.append(f"[{rid}] {bucket} rate < 0 ({rate_val})")
                # Suspiciously large (e.g., 75 instead of 0.75)
                if rate_val > 2.0:
                    warnings.append(f"[{rid}] {bucket} rate unusually high ({rate_val}); did you mean {rate_val/100:.4f}?")

            # Soft deprecation nudge for legacy names
            if rate_field_used in ("outflow_rate", "inflow_rate"):
                warnings.append(f"[{rid}] {bucket} uses legacy '{rate_field_used}'; prefer unified 'rate'")
        else:
            # HQLA buckets shouldn't need a rate; nudge if supplied
            if rate_field_used is not None:
                warnings.append(f"[{rid}] {bucket} row supplied '{rate_field_used}' which is ignored for HQLA")

    return warnings


# --- Aggregate results validation (blocking for hard errors) ------------------

def validate_results(results: Dict[str, Any]) -> None:
    EPS = 1e-9

    # --- Basic constraints on RWA ---
    rwa_total = float(results["rwa"]["total_rwa"])
    assert rwa_total >= 0.0, "RWA must be ≥ 0"

    # Optional: per-exposure RWA sum ≈ total
    per_sum = None
    try:
        per_sum = sum(float(row.get("rwa", 0.0) or 0.0) for row in results["rwa"].get("per_exposure", []))
        delta = per_sum - rwa_total
        # Store reconciliation delta for report footnote (non-blocking)
        results.setdefault("reconciliation", {})["rwa_per_exposure_delta"] = round(delta, 2)
        assert abs(delta) <= 0.05 + EPS, "Sum(per-exposure RWA) does not reconcile to total RWA (±5 cents)"
    except Exception:
        pass

    # --- Capital ratios ---
    ratios = results["capital"]["ratios"]
    for k, v in ratios.items():
        assert float(v) >= 0.0, f"{k} ratio must be non-negative: {v}"

    # --- Capital structure relationships ---
    cap = results["capital"]["components"]
    assert float(cap["cet1"]) <= float(cap["total_capital"]) + EPS, "CET1 cannot exceed total capital"
    assert float(cap["tier1"]) + EPS >= float(cap["cet1"]), "Tier 1 cannot be < CET1"
    assert float(cap["total_capital"]) + EPS >= float(cap["tier1"]), "Total capital cannot be < Tier 1"

    # --- LCR outputs ---
    lcr = results["lcr"]
    assert float(lcr["hqla"]) >= 0.0, "HQLA must be ≥ 0"
    assert float(lcr["outflows"]) >= 0.0, "Outflows must be ≥ 0"
    assert float(lcr["inflows"]) >= 0.0, "Inflows must be ≥ 0"
    assert float(lcr["inflow_cap_amount"]) <= float(lcr["inflows"]) + EPS, "Inflow cap amount exceeds inflows"
    assert abs(float(lcr["net_outflows"]) - (float(lcr["outflows"]) - float(lcr["inflow_cap_amount"]))) <= 0.02 + EPS, \
        "Net outflows do not reconcile to outflows - inflow cap amount"
    assert float(lcr["lcr"]) >= 0.0, "LCR must be non-negative"
    if float(lcr["net_outflows"]) > 0.0:
        implied = float(lcr["hqla"]) / float(lcr["net_outflows"])
        assert abs(float(lcr["lcr"]) - implied) <= 0.01 + EPS, "Reported LCR not consistent with HQLA / Net Outflows"
    else:
        assert float(lcr["lcr"]) == 0.0, "LCR should be 0 when net outflows ≤ 0"

    # --- NSFR outputs (if provided) ---
    if "nsfr" in results and results["nsfr"] is not None:
        ns = results["nsfr"]
        assert float(ns["asf"]) >= 0.0, "ASF must be ≥ 0"
        assert float(ns["rsf"]) >= 0.0, "RSF must be ≥ 0"
        if float(ns["rsf"]) > 0.0:
            implied = float(ns["asf"]) / float(ns["rsf"])
            assert abs(float(ns["nsfr"]) - implied) <= 0.01 + EPS, "NSFR not consistent with ASF / RSF"
        else:
            assert float(ns["nsfr"]) == 0.0, "NSFR should be 0 when RSF ≤ 0"

    # --- Optional FX checks (only when user asked for date validation) ---
    fx = results.get("fx_meta")
    if fx:
        asof_d = _parse_date(results["asof"])
        for ccy, info in (fx.get("quotes") or {}).items():
            qd = _parse_date((info or {}).get("quote_date", ""))
            if asof_d and qd:
                assert qd <= asof_d, f"FX quote_date for {ccy} ({qd}) > as-of ({asof_d})"

    # --- Breach signalling (capital/leverage; LCR; NSFR) ----------------------
    breaches = []
    try:
        hd = results["capital"]["headroom"]
        if float(hd.get("cet1", 0.0)) < 0: breaches.append("cet1_ratio")
        if float(hd.get("tier1", 0.0)) < 0: breaches.append("tier1_ratio")
        if float(hd.get("total", 0.0)) < 0: breaches.append("total_capital_ratio")
        if float(hd.get("leverage", 0.0)) < 0: breaches.append("leverage_ratio")
    except Exception:
        pass

    try:
        if float(results["lcr"].get("lcr", 0.0)) < 1.0:
            breaches.append("lcr")
    except Exception:
        pass

    try:
        if "nsfr" in results and results["nsfr"] is not None and float(results["nsfr"].get("nsfr", 0.0)) < 1.0:
            breaches.append("nsfr")
    except Exception:
        pass

    if breaches:
        results["breaches"] = sorted(set(breaches))
