# calc.py
# -----------------------------------------------------------------------------
# Core calculations: Risk Weights, RWA aggregation, Capital ratios (+buffers),
# Liquidity Coverage Ratio (with Level-2 composition caps), and NSFR (lite).
# -----------------------------------------------------------------------------

from typing import Dict, Any, List, Tuple
from decimal import Decimal, ROUND_HALF_UP
import json

from .ratings import normalize_rating
from .exposures import compute_ead_row, apply_collateral_netting


# --- Decimal helpers ----------------------------------------------------------

def d(x) -> Decimal:
    return Decimal(str(x))

def quantize(v: float, places: str = "0.01") -> float:
    return float(Decimal(v).quantize(Decimal(places), rounding=ROUND_HALF_UP))


# --- Risk Weights (with trace) ------------------------------------------------

def _select_risk_weight_with_trace(exposure: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, str]:
    """
    Returns (risk_weight, rw_source_string)
      rw_source examples:
        - "Corporate:A"
        - "Mortgage:LTV<=0.80→0.35"
        - "Retail:default 0.75"
    """
    ac = (exposure.get("asset_class") or "").strip()
    rating = normalize_rating(exposure.get("rating") or "NR")

    rules = cfg.get("risk_weights", {})
    ac_rules = rules.get(ac, {})

    # Mortgage LTV bands
    try:
        ltv = float(exposure.get("mortgage_ltv")) if exposure.get("mortgage_ltv") not in (None, "",) else None
    except Exception:
        ltv = None

    if "ltv_thresholds" in ac_rules and ltv is not None:
        for r in ac_rules["ltv_thresholds"]:
            thr = float(r["lte"])
            w = float(r["weight"])
            if ltv <= thr:
                return w, f"Mortgage:LTV<={thr:.2f}→{w:.2f}"
        w = float(ac_rules.get("default", 1.0))
        return w, f"Mortgage:default {w:.2f}"

    # Rating map (generic)
    if rating in ac_rules:
        w = float(ac_rules[rating])
        return w, f"{ac}:{rating}"
    w = float(ac_rules.get("default", 1.0))
    return w, f"{ac}:default {w:.2f}"


# --- RWA Aggregation ----------------------------------------------------------

def compute_rwa(exposures: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
      {
        "per_exposure": [... rows incl. explainability + traces ...],
        "by_class": {class: RWA},
        "total_rwa": float,
        "kpis": {
           "by_class": {class: {"ead_gross":..., "ead":..., "rwa":..., "density":...}},
           "total": {"ead_gross":..., "ead":..., "rwa":..., "density":...}
        }
      }
    """
    per_exposure: List[Dict[str, Any]] = []
    by_class: Dict[str, float] = {}
    total_rwa: float = 0.0

    # KPI aggregation
    kpi_ead_gross: Dict[str, float] = {}
    kpi_ead_net: Dict[str, float] = {}

    def _flag(v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "y"}
        return bool(v)

    coll_root = (cfg.get("collateral") or {})
    coll_enabled = bool(coll_root.get("enabled", False))
    coll_mode = (coll_root.get("mode") or "simple").strip().lower() if coll_enabled else "disabled"

    sf_cfg = (cfg.get("supporting_factors") or {})
    sme_cfg = (sf_cfg.get("sme") or {})
    infra_cfg = (sf_cfg.get("infra") or {})

    # --- Supporting-factor stacking policy -----------------------------------
    # New switch:
    #   cfg['supporting_factors']['stacking'] in {"multiply","min","priority"}
    #   - "multiply" (default): SME and Infra both apply and compound (current behavior).
    #   - "min": apply only the smallest factor among applicable ones (no stacking).
    #   - "priority": choose the first match in 'priority_order' (default ["sme","infra"]).
    sf_mode = (sf_cfg.get("stacking") or "multiply").strip().lower()
    priority_order = sf_cfg.get("priority_order") or ["sme", "infra"]

    # Helper to stringify scenario flags if present on row (set by scenario.apply_scenario)
    def _scenario_flags(row: Dict[str, Any]) -> Dict[str, Any]:
        flags = row.get("_scenario_flags") or {}
        out = {}
        for k, v in flags.items():
            try:
                float(v)
                out[k] = v
            except Exception:
                out[k] = v
        return out

    for e in exposures:
        ac = (e.get("asset_class") or "").strip()

        # Gross EAD then CRM netting (also writes e["_crm_path"] in exposures.py)
        ead_gross = compute_ead_row(e, cfg)
        ead = apply_collateral_netting(ead_gross, e, cfg)
        haircut_effect = quantize(max(0.0, ead_gross - ead))
        crm_path = e.get("_crm_path", "")  # explainability string

        # Risk weight + trace
        rw, rw_src = _select_risk_weight_with_trace(e, cfg)
        rwa = ead * rw

        # Supporting factors (with stacking guard)
        supporting_factor_applied = 1.0
        if sf_cfg.get("enabled", False):
            ac_list = lambda lst: [s.strip() for s in (lst or [])]

            sme_hit = (ac in ac_list(sme_cfg.get("on_asset_classes"))) or _flag(e.get("is_sme", False))
            infra_hit = (ac in ac_list(infra_cfg.get("on_asset_classes"))) or _flag(e.get("is_infra", False))

            factors: List[float] = []
            if sme_hit:
                factors.append(float(sme_cfg.get("factor", 1.0)))
            if infra_hit:
                factors.append(float(infra_cfg.get("factor", 1.0)))

            if factors:
                if sf_mode == "multiply":
                    f_val = 1.0
                    for f in factors:
                        f_val *= f
                    supporting_factor_applied = f_val
                elif sf_mode == "min":
                    # pick a single (most conservative to capital) factor; no stacking
                    supporting_factor_applied = min(factors)
                elif sf_mode == "priority":
                    chosen = 1.0
                    # honor priority_order; first applicable wins
                    for key in priority_order:
                        if key == "sme" and sme_hit:
                            chosen = float(sme_cfg.get("factor", 1.0))
                            break
                        if key == "infra" and infra_hit:
                            chosen = float(infra_cfg.get("factor", 1.0))
                            break
                    supporting_factor_applied = chosen
                else:
                    # Fallback to old behavior if unknown mode
                    f_val = 1.0
                    for f in factors:
                        f_val *= f
                    supporting_factor_applied = f_val

                rwa *= supporting_factor_applied

        # Compose explainability row
        flags = _scenario_flags(e)
        flags_json = json.dumps(flags, separators=(",", ":"), sort_keys=True) if flags else ""

        row = {
            "id": e.get("id"),
            "asset_class": ac,
            "rating": e.get("rating"),
            "ead_gross": quantize(ead_gross),
            "ead": quantize(ead),
            "risk_weight": quantize(rw, "0.0001"),
            "rwa": quantize(rwa),

            # --- Explainability additions ---
            "supporting_factor_applied": quantize(supporting_factor_applied, "0.0001"),
            "collateral_mode": coll_mode,  # simple / advanced / disabled
            "collateral_haircut_effect": haircut_effect,  # gross - net EAD
            "scenario_flags": flags_json,  # e.g., {"ead_mult":1.1,"rating_notches":1}

            # --- New trace fields ---
            "rw_source": rw_src,
            "crm_path": crm_path,
        }
        per_exposure.append(row)

        # Aggregators
        by_class.setdefault(ac, 0.0)
        by_class[ac] += rwa
        total_rwa += rwa

        kpi_ead_gross[ac] = kpi_ead_gross.get(ac, 0.0) + ead_gross
        kpi_ead_net[ac] = kpi_ead_net.get(ac, 0.0) + ead

    # Finalise numbers (rounding)
    total_rwa_q = quantize(total_rwa)
    by_class_q = {k: quantize(v) for k, v in by_class.items()}

    for row in per_exposure:
        row["risk_weight"] = quantize(row["risk_weight"], "0.0001")
        row["rwa"] = quantize(row["rwa"])
        row["ead"] = quantize(row["ead"])
        row["ead_gross"] = quantize(row["ead_gross"])
        row["collateral_haircut_effect"] = quantize(row["collateral_haircut_effect"])

    # Build KPI block with densities (RWA / net EAD)
    kpis_by_class: Dict[str, Dict[str, float]] = {}
    total_ead_gross = 0.0
    total_ead_net = 0.0
    for ac, rwaval in by_class.items():
        eg = kpi_ead_gross.get(ac, 0.0)
        en = kpi_ead_net.get(ac, 0.0)
        total_ead_gross += eg
        total_ead_net += en
        density = 0.0 if en == 0 else rwaval / en
        kpis_by_class[ac] = {
            "ead_gross": quantize(eg),
            "ead": quantize(en),
            "rwa": quantize(rwaval),
            "density": quantize(density, "0.0001"),
        }
    total_density = 0.0 if total_ead_net == 0 else total_rwa / total_ead_net
    kpis_total = {
        "ead_gross": quantize(total_ead_gross),
        "ead": quantize(total_ead_net),
        "rwa": total_rwa_q,
        "density": quantize(total_density, "0.0001"),
    }

    return {
        "per_exposure": per_exposure,
        "by_class": by_class_q,
        "total_rwa": total_rwa_q,
        "kpis": {
            "by_class": kpis_by_class,
            "total": kpis_total,
        }
    }


# --- Capital Ratios (+ buffers & leverage) ------------------------------------

def compute_capital_ratios(capital: Dict[str, float], total_rwa: float, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Build capital stack + ratios and (optionally) overlay requirements:
      - CET1/Tier1/Total ratios
      - Requirements (min + buffers) if cfg['requirements'] present
      - Leverage ratio if 'leverage_exposure' present in 'capital' and requirement in cfg
    """
    cfg = cfg or {}
    cet1 = float(capital.get("cet1", 0.0))
    at1 = float(capital.get("at1", 0.0))
    tier2 = float(capital.get("tier2", 0.0))
    deductions = float(capital.get("deductions", 0.0))
    lev_exp = float(capital.get("leverage_exposure", 0.0))

    tier1 = cet1 + at1
    total_capital = tier1 + tier2 - deductions

    ratios = {
        "cet1_ratio": 0.0 if total_rwa == 0 else cet1 / total_rwa,
        "tier1_ratio": 0.0 if total_rwa == 0 else tier1 / total_rwa,
        "total_capital_ratio": 0.0 if total_rwa == 0 else total_capital / total_rwa,
    }

    # Requirements overlay
    req = (cfg.get("requirements") or {})
    cet1_min = float(req.get("cet1_min", 0.0))
    tier1_min = float(req.get("tier1_min", 0.0))
    total_min = float(req.get("total_min", 0.0))
    ccb = float(req.get("ccb", 0.0))
    ccyb = float(req.get("ccyb", 0.0))
    gsib = float(req.get("gsib", 0.0))

    cet1_req = cet1_min + ccb + ccyb + gsib
    tier1_req = tier1_min + ccb + ccyb + gsib
    total_req = total_min + ccb + ccyb + gsib

    # Leverage ratio
    leverage_ratio = 0.0 if lev_exp <= 0 else tier1 / lev_exp
    leverage_min = float(req.get("leverage_min", 0.0))

    return {
        "components": {
            "cet1": round(cet1, 2),
            "at1": round(at1, 2),
            "tier2": round(tier2, 2),
            "deductions": round(deductions, 2),
            "tier1": round(tier1, 2),
            "total_capital": round(total_capital, 2),
        },
        "ratios": {k: round(v, 4) for k, v in ratios.items()},
        "requirements": {
            "cet1_required": round(cet1_req, 4),
            "tier1_required": round(tier1_req, 4),
            "total_required": round(total_req, 4),
            "leverage_required": round(leverage_min, 4),
        },
        "headroom": {
            "cet1": round(ratios["cet1_ratio"] - cet1_req, 4),
            "tier1": round(ratios["tier1_ratio"] - tier1_req, 4),
            "total": round(ratios["total_capital_ratio"] - total_req, 4),
            "leverage": round(leverage_ratio - leverage_min, 4),
        },
        "leverage": {
            "exposure_measure": round(lev_exp, 2),
            "ratio": round(leverage_ratio, 4),
        }
    }


# --- Liquidity Coverage Ratio (with Level-2 caps) -----------------------------

def compute_lcr(liquidity_rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute LCR with Level-2 composition caps:
      - Level 2B ≤ level2b_cap_pct of total HQLA
      - Total Level 2 (2A+2B) ≤ level2_total_cap_pct of total HQLA
    Algebraic implementation (post-haircut amounts):
      L2B_cap -> L2B_allowed = min(L2B, 0.17647 * (L1 + L2A))  # from  L2B ≤ 15% of total  ⇒ L2B ≤ 0.17647*(L1+L2A)
      L2_total_cap -> L2_total_allowed ≤ (2/3) * L1             # from  L2_total ≤ 40% of total ⇒ L2_total ≤ 2/3 * L1

    Rate field naming:
      - Preferred neutral field: 'rate' on both OUTFLOW and INFLOW rows.
      - Backward-compat: 'outflow_rate' still accepted.
      - Optional clarity: 'inflow_rate' for INFLOW rows (overrides 'rate' if present).
    """
    lcr_cfg = cfg.get("lcr", {}) or {}
    inflow_cap = float(lcr_cfg.get("inflow_cap_pct", 0.75))
    level2_total_cap_pct = float(lcr_cfg.get("level2_total_cap_pct", 0.40))
    level2b_cap_pct = float(lcr_cfg.get("level2b_cap_pct", 0.15))

    # Buckets
    L1 = L2A = L2B = 0.0
    outflows = inflows = 0.0

    for r in liquidity_rows:
        bucket = (r.get("bucket") or "").upper()
        amt = float(r.get("amount_ccy", 0.0) or 0.0)
        haircut = float(r.get("haircuts", 0.0) or 0.0)

        # Neutral rate with legacy/override support
        base_rate = r.get("rate", None)
        rate = float(base_rate) if base_rate not in (None, "",) else 0.0
        # Legacy alias for historical files
        if rate == 0.0 and r.get("outflow_rate") not in (None, "",):
            try:
                rate = float(r.get("outflow_rate"))
            except Exception:
                rate = 0.0
        # Specific overrides for clarity
        if bucket == "INFLOW" and r.get("inflow_rate") not in (None, "",):
            try:
                rate = float(r.get("inflow_rate"))
            except Exception:
                pass
        if bucket == "OUTFLOW" and r.get("outflow_rate") not in (None, "",):
            try:
                rate = float(r.get("outflow_rate"))
            except Exception:
                pass

        if bucket == "HQLA_L1":
            L1 += amt * (1.0 - haircut)
        elif bucket == "HQLA_L2A":
            L2A += amt * (1.0 - haircut)
        elif bucket == "HQLA_L2B":
            L2B += amt * (1.0 - haircut)
        elif bucket == "OUTFLOW":
            outflows += amt * rate
        elif bucket == "INFLOW":
            inflows += amt * rate

    # Apply Level-2B 15% cap: solve L2B ≤ level2b_cap_pct * (L1 + L2A + L2B)
    # => L2B ≤ [level2b_cap_pct / (1 - level2b_cap_pct)] * (L1 + L2A)
    if level2b_cap_pct >= 1.0:
        l2b_allowed = L2B  # degenerate config
    else:
        factor = level2b_cap_pct / (1.0 - level2b_cap_pct)
        l2b_allowed = min(L2B, factor * (L1 + L2A))

    # Apply Level-2 total 40% cap: L2_total ≤ level2_total_cap_pct * (L1 + L2_total)
    # -> L2_total ≤ [level2_total_cap_pct / (1 - level2_total_cap_pct)] * L1
    if level2_total_cap_pct >= 1.0:
        l2_total_cap = L2A + l2b_allowed
    else:
        factor2 = level2_total_cap_pct / (1.0 - level2_total_cap_pct)  # default 0.4/(0.6)=2/3
        l2_total_cap = min(L2A + l2b_allowed, factor2 * L1)

    # If total L2 exceeds cap, trim L2A first (L2B already capped)
    if L2A + l2b_allowed > l2_total_cap:
        l2a_allowed = max(0.0, l2_total_cap - l2b_allowed)
    else:
        l2a_allowed = L2A

    hqla = L1 + l2a_allowed + l2b_allowed

    inflow_cap_amount = min(inflows, inflow_cap * outflows)
    net_outflows = outflows - inflow_cap_amount
    lcr = 0.0 if net_outflows <= 0 else hqla / net_outflows

    return {
        "hqla": round(hqla, 2),
        "breakdown": {
            "level1": round(L1, 2),
            "level2a_allowed": round(l2a_allowed, 2),
            "level2b_allowed": round(l2b_allowed, 2),
        },
        "outflows": round(outflows, 2),
        "inflows": round(inflows, 2),
        "inflow_cap_amount": round(inflow_cap_amount, 2),
        "net_outflows": round(net_outflows, 2),
        "lcr": round(lcr, 4),
        "lcr_percent": round(lcr * 100.0, 2),
        "caps": {
            "inflow_cap_pct": round(inflow_cap, 4),
            "level2_total_cap_pct": round(level2_total_cap_pct, 4),
            "level2b_cap_pct": round(level2b_cap_pct, 4),
        }
    }


# --- NSFR (lite) --------------------------------------------------------------

def compute_nsfr(nsfr_rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    NSFR (lite), same input style as LCR:
      Each row: bucket ∈ {ASF, RSF}, amount_ccy, factor (0..1)
      ASF = Σ amount_ccy × factor  ; RSF = Σ amount_ccy × factor
      NSFR = ASF / RSF  (0 if RSF ≤ 0)
    """
    asf = 0.0
    rsf = 0.0
    for r in nsfr_rows:
        bucket = (r.get("bucket") or "").upper()
        amt = float(r.get("amount_ccy", 0.0) or 0.0)
        factor = float(r.get("factor", 0.0) or 0.0)
        val = amt * factor
        if bucket == "ASF":
            asf += val
        elif bucket == "RSF":
            rsf += val

    nsfr = 0.0 if rsf <= 0.0 else asf / rsf
    return {
        "asf": round(asf, 2),
        "rsf": round(rsf, 2),
        "nsfr": round(nsfr, 4),
        "nsfr_percent": round(nsfr * 100.0, 2),
    }
