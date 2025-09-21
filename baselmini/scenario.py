# scenario.py
# -----------------------------------------------------------------------------
# Scenario engine: apply in-place adjustments to exposures and liquidity rows.
# Supports:
#   - EAD multipliers by asset class (applied to drawn/undrawn when present, else to ead)
#   - Rating migrations (global notch-down and per-asset-class notches)
#   - HQLA haircut additive bump in basis points (bps)
#   - LCR parameter multipliers (outflow/inflow rates, HQLA haircuts)
#   - Explainability: set per-exposure _scenario_flags that calc.compute_rwa forwards
# -----------------------------------------------------------------------------

from typing import Dict, Any, List  # Typing helpers
from .ratings import notch_down  # Rating utility: downgrade by N notches


def _flags(e: Dict[str, Any]) -> Dict[str, Any]:
    f = e.get("_scenario_flags")
    if not isinstance(f, dict):
        f = {}
        e["_scenario_flags"] = f
    return f


# Apply scenario adjustments in-place to exposures + liquidity inputs
def apply_scenario(exposures: List[Dict[str, Any]],
                   liquidity: List[Dict[str, Any]],
                   scenario: Dict[str, Any]) -> None:
    sc = scenario.get("scenario", {})  # Read scenario dict root

    # -------------------------------------------------------------------------
    # Exposure-side shocks
    # -------------------------------------------------------------------------

    # EAD multipliers by asset class (e.g., stress corporates +10%)
    ead_mult = sc.get("ead_multipliers", {})
    if ead_mult:
        for e in exposures:
            ac = e.get("asset_class")
            mult = float(ead_mult.get(ac, 1.0))
            if mult != 1.0:
                # Record flag
                _flags(e)["ead_mult"] = mult
                try:
                    if e.get("drawn") not in (None, "",) or e.get("undrawn") not in (None, "",):
                        if e.get("drawn") not in (None, "",):
                            e["drawn"] = float(e.get("drawn", 0.0)) * mult
                        if e.get("undrawn") not in (None, "",):
                            e["undrawn"] = float(e.get("undrawn", 0.0)) * mult
                    else:
                        e["ead"] = float(e.get("ead", 0.0)) * mult
                except Exception:
                    pass

    # Rating migration: apply uniform downgrade by N notches (if specified)
    n_down = int(sc.get("rating_notches_down", 0) or 0)
    if n_down > 0:
        for e in exposures:
            e["rating"] = notch_down(e.get("rating", "NR"), n_down)
            _flags(e)["rating_notches"] = _flags(e).get("rating_notches", 0) + n_down

    # Optional: per-asset-class rating notch-down (in addition to global)
    class_notches = sc.get("rating_notches_by_class", {})
    if class_notches:
        for e in exposures:
            n = int(class_notches.get(e.get("asset_class"), 0) or 0)
            if n > 0:
                e["rating"] = notch_down(e.get("rating", "NR"), n)
                _flags(e)["rating_notches"] = _flags(e).get("rating_notches", 0) + n

    # Liquidity-side: additive haircut bump (bps)
    haircut_bps = float(sc.get("hqlahaircut_bps_add", 0.0))
    if haircut_bps:
        bump = haircut_bps / 10000.0
        for r in liquidity:
            if (r.get("bucket") or "").upper().startswith("HQLA"):
                try:
                    h = float(r.get("haircuts", 0.0) or 0.0) + bump
                    r["haircuts"] = max(0.0, min(1.0, h))
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Liquidity-side shocks (LCR knobs)
    # -------------------------------------------------------------------------
    outflow_mult = float(sc.get("lcr_outflow_multiplier", 1.0))
    inflow_mult  = float(sc.get("lcr_inflow_multiplier", 1.0))
    hair_mult    = float(sc.get("hqlahaircut_multiplier", 1.0))

    if outflow_mult != 1.0 or inflow_mult != 1.0 or hair_mult != 1.0:
        for r in liquidity:
            b = (r.get("bucket") or "").upper()
            try:
                if b == "OUTFLOW":
                    r["outflow_rate"] = float(r.get("outflow_rate", 0.0)) * outflow_mult
                elif b == "INFLOW":
                    r["outflow_rate"] = float(r.get("outflow_rate", 0.0)) * inflow_mult
                elif b.startswith("HQLA"):
                    h = float(r.get("haircuts", 0.0)) * hair_mult
                    r["haircuts"] = max(0.0, min(1.0, h))
            except Exception:
                pass
