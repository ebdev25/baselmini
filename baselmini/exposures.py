# exposures.py
# -----------------------------------------------------------------------------
# Helpers for building Exposure-at-Default (EAD) from components and applying
# collateral netting (simple mode) or supervisory haircuts (advanced mode).
# Backward-compatible with column name variants used across your CSVs.
# Adds a CRM trace string (e['_crm_path']) for auditability.
# -----------------------------------------------------------------------------

from typing import Dict, Any
from decimal import Decimal, ROUND_HALF_UP  # local to avoid circular import with calc.py


# --- Decimal helpers (match style/rounding used in calc.py) -------------------

def d(x) -> Decimal:
    """Convert any input 'x' (int, float, str) into a Decimal (via str() to avoid float noise)."""
    return Decimal(str(x))


def quantize(v: float, places: str = "0.01") -> float:
    """Round numeric value 'v' to fixed decimals using banker-friendly ROUND_HALF_UP."""
    return float(Decimal(v).quantize(Decimal(places), rounding=ROUND_HALF_UP))


# --- Small parsing helpers ----------------------------------------------------

def _to_float(val, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return float(default)
        return float(val)
    except Exception:
        return float(default)


def _to_int(val, default: int = 0):
    try:
        if val is None or val == "":
            return default
        return int(val)
    except Exception:
        return default


def _s(val) -> str:
    return ("" if val is None else str(val)).strip()


def _clip01(x: float) -> float:
    """Clamp to [0, 1]."""
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


# --- EAD construction ---------------------------------------------------------

def compute_ead_row(e: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    """
    EAD determination (robust to input variants):

    Priority:
      1) If explicit 'ead' present (and not blank), use it.
      2) Else, use drawn + CCF * undrawn:
         - CCF key read from 'commitment_type' (preferred) or 'ccf_type' (alias).
         - Falls back to cfg['ead']['default_ccf'] when missing/unknown.

    Config:
      cfg['ead']['ccf']        : { ccf_key -> factor }
      cfg['ead']['default_ccf']: number
    """
    # Explicit EAD
    if _s(e.get("ead")) != "":
        try:
            return quantize(float(e.get("ead", 0.0)))
        except Exception:
            return 0.0

    # Drawn + CCF * undrawn
    drawn = None
    try:
        drawn = float(e.get("drawn", "")) if e.get("drawn") not in (None, "",) else None
        undrawn = float(e.get("undrawn", "")) if e.get("undrawn") not in (None, "",) else 0.0
    except Exception:
        drawn = None
        undrawn = 0.0

    if drawn is not None:
        ead_cfg = (cfg.get("ead") or {})
        ccfs = (ead_cfg.get("ccf") or {})
        default_ccf = float(ead_cfg.get("default_ccf", 1.0))
        # prefer 'commitment_type', accept 'ccf_type' as an alias
        ctype = _s(e.get("commitment_type")) or _s(e.get("ccf_type"))
        ccf = float(ccfs.get(ctype, default_ccf)) if ctype else float(default_ccf)
        ead = d(drawn) + d(undrawn) * d(ccf)
        return quantize(float(ead))

    # Nothing usable ⇒ 0.0
    return 0.0


# --- Collateral value & field resolution -------------------------------------
#   amount  : 'eligible_collateral' | 'collateral_value'
#   days    : 'collateral_residual_maturity_days' | 'residual_maturity_days'
#   ctype   : 'collateral_type'
#   haircut : 'collateral_haircut' (row override)
#   CCYs    : 'exposure_ccy', 'collateral_ccy'

def _resolve_collateral_amount(e: Dict[str, Any]) -> float:
    amt = e.get("eligible_collateral", None)
    if amt in (None, "",):
        amt = e.get("collateral_value", None)
    return _to_float(amt, 0.0)


def _resolve_residual_days(e: Dict[str, Any]):
    days = e.get("collateral_residual_maturity_days", None)
    if days in (None, "",):
        days = e.get("residual_maturity_days", None)
    try:
        return int(days) if days not in (None, "",) else None
    except Exception:
        return None


# --- Collateral netting (simple) ----------------------------------------------

def _apply_simple_collateral(ead: float, e: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    """
    Simple haircut deduction:
      EAD_net = max(0, EAD - collateral_amount * (1 - haircut))

    Haircut selection order:
      row override 'collateral_haircut' → collateral.haircuts[type] → collateral.default_haircut

    Trace:
      e['_crm_path'] = "simple: override haircut=0.10"  (if row override used)
                    or "simple: type haircut=0.15"      (if type/default used)
    """
    coll_cfg = (cfg.get("collateral") or {})
    c_amt = _resolve_collateral_amount(e)
    if c_amt <= 0.0:
        e["_crm_path"] = "simple: none"
        return ead

    # Row-level override
    row_hair = e.get("collateral_haircut")
    hair = None
    try:
        hair = float(row_hair) if row_hair not in (None, "",) else None
    except Exception:
        hair = None

    src = "override" if hair is not None else "type"
    if hair is None:
        ctype = _s(e.get("collateral_type")).lower()
        hair = float((coll_cfg.get("haircuts", {}) or {}).get(ctype, coll_cfg.get("default_haircut", 0.15)))

    hair = _clip01(hair)
    e["_crm_path"] = f"simple: {src} haircut={hair:.2f}"
    ead_net = max(0.0, ead - c_amt * (1.0 - hair))
    return quantize(ead_net)


# --- Collateral netting (advanced/supervisory) --------------------------------

def _apply_supervisory_collateral(ead: float, e: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    """
    Supervisory haircut approach (simplified but auditable):

      effective_haircut = base_haircut(ctype)
                        + short_maturity_addon   if residual_maturity_days < short_maturity_days
                        + fx_mismatch_addon      if exposure_ccy != collateral_ccy
      EAD_net = max(0, EAD - collateral_amount * (1 - effective_haircut))

    Inputs expected (if relevant):
      eligible_collateral / collateral_value,
      collateral_type,
      collateral_residual_maturity_days / residual_maturity_days,
      exposure_ccy, collateral_ccy

    Config (cfg['collateral']['supervisory']):
      haircuts: { ctype -> base }
      short_maturity_days: int
      short_maturity_addon: float
      fx_mismatch_addon: float

    Trace:
      e['_crm_path'] = "advanced: base=0.15 + short=0.08 + fx=0.08 → effective=0.31"
      (components appear only when applied)
    """
    coll_root = (cfg.get("collateral") or {})
    sup = (coll_root.get("supervisory") or {})

    c_amt = _resolve_collateral_amount(e)
    if c_amt <= 0.0:
        e["_crm_path"] = "advanced: none"
        return ead  # nothing to net

    ctype = _s(e.get("collateral_type")).lower()
    base_tbl = (sup.get("haircuts") or {})
    # Fallback path: try configured default_haircut if type absent
    base = float(base_tbl.get(ctype, coll_root.get("default_haircut", 0.15)))

    short_thr = _to_int(sup.get("short_maturity_days", 365), 365)
    short_add = _to_float(sup.get("short_maturity_addon", 0.0), 0.0)
    fx_add    = _to_float(sup.get("fx_mismatch_addon", 0.0), 0.0)

    parts = [f"base={base:.2f}"]

    residual_days = _resolve_residual_days(e)
    if residual_days is not None and residual_days < short_thr:
        base += short_add
        parts.append(f"short={short_add:.2f}")

    exp_ccy = _s(e.get("exposure_ccy")).upper()
    col_ccy = _s(e.get("collateral_ccy")).upper()
    if exp_ccy and col_ccy and exp_ccy != col_ccy:
        base += fx_add
        parts.append(f"fx={fx_add:.2f}")

    hair = _clip01(base)
    parts.append(f"→ effective={hair:.2f}")
    e["_crm_path"] = "advanced: " + " + ".join(parts)

    ead_net = max(0.0, ead - c_amt * (1.0 - hair))
    return quantize(ead_net)


# --- Collateral selector ------------------------------------------------------

def apply_collateral_netting(ead: float, e: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    """
    Dispatcher:
      - If collateral.enabled is false/missing: return EAD unchanged
      - If mode == "advanced": apply supervisory model
      - Else: use simple haircut deduction
    """
    coll_root = (cfg.get("collateral") or {})
    if not bool(coll_root.get("enabled", False)):
        e["_crm_path"] = "disabled"
        return ead

    mode = _s(coll_root.get("mode") or "simple").lower()
    if mode == "advanced":
        return _apply_supervisory_collateral(ead, e, cfg)
    return _apply_simple_collateral(ead, e, cfg)
