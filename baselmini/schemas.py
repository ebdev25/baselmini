# schemas.py
# -----------------------------------------------------------------------------
# Lightweight config schema validation for early, descriptive failures.
# Focused on key trees used by the engine; permissive elsewhere.
# -----------------------------------------------------------------------------

from typing import Any, Dict
import difflib
import sys


def _expect(d: Dict[str, Any], key: str, type_, path: str):
    if key not in d:
        # Suggest the closest key at this level (edit distance)
        match = difflib.get_close_matches(key, list(d.keys()), n=1)
        hint = f" Did you mean '{match[0]}'?" if match else ""
        raise SystemExit(f"Config error: missing key '{path + key}'.{hint}")
    if type_ is not None and not isinstance(d[key], type_):
        raise SystemExit(f"Config error: '{path + key}' must be of type {type_.__name__}.")


def validate_config(cfg: Dict[str, Any]) -> None:
    if not isinstance(cfg, dict):
        raise SystemExit("Config error: top-level must be a mapping/object.")

    # --- Risk Weights (must be present) ---
    _expect(cfg, "risk_weights", dict, "")
    rw_root = cfg["risk_weights"]

    # Optional: assert allowed/known asset_class names to avoid silent typos.
    # Keep this as a WARNING (non-fatal) so users can extend if they want.
    _known_asset_classes = {
        "Sovereign", "Bank", "Corporate", "Retail", "Mortgage",
        "SME", "Infrastructure"  # often used with supporting factors
    }
    for k in rw_root.keys():
        if not isinstance(k, str):
            print(f"Warning: risk_weights key '{k}' is not a string.", file=sys.stderr)
            continue
        if k not in _known_asset_classes:
            print(f"Warning: unknown asset_class '{k}' in risk_weights. "
                  f"If this is intentional, ignore; otherwise check for typos.", file=sys.stderr)

    # --- LCR subtree and required knobs ---
    _expect(cfg, "lcr", dict, "")
    lcr = cfg["lcr"]
    for k in ("inflow_cap_pct", "level2_total_cap_pct", "level2b_cap_pct"):
        _expect(lcr, k, (int, float), "lcr.")

    # --- EAD / CCFs subtree ---
    _expect(cfg, "ead", dict, "")
    ead = cfg["ead"]
    _expect(ead, "ccf", dict, "ead.")
    _expect(ead, "default_ccf", (int, float), "ead.")

    # --- Collateral subtree (optional but validated if present) ---
    if "collateral" in cfg:
        col = cfg["collateral"]
        _expect(col, "enabled", (bool, int), "collateral.")
        if col.get("enabled", False):
            _expect(col, "mode", (str,), "collateral.")
            mode = (col.get("mode") or "simple").strip().lower()
            if mode not in ("simple", "advanced"):
                raise SystemExit("Config error: collateral.mode must be 'simple' or 'advanced'.")
            if mode == "simple":
                # If provided, haircuts should be a mapping
                if "haircuts" in col and not isinstance(col["haircuts"], dict):
                    raise SystemExit("Config error: collateral.haircuts must be a mapping.")
            if mode == "advanced":
                sup = col.get("supervisory", {})
                if not isinstance(sup, dict):
                    raise SystemExit("Config error: collateral.supervisory must be a mapping.")
                # Soft checks for presence; defaults tolerated
                for k in ("haircuts", "short_maturity_days", "short_maturity_addon", "fx_mismatch_addon"):
                    if k not in sup:
                        # No hard fail, but give a friendly nudge
                        print(f"Warning: collateral.supervisory missing '{k}' — using defaults.", file=sys.stderr)

    # --- Supporting factors (optional) ---
    if "supporting_factors" in cfg and not isinstance(cfg["supporting_factors"], dict):
        raise SystemExit("Config error: supporting_factors must be a mapping/object.")

    # --- Requirements subtree (optional, but if present must have numbers) ---
    if "requirements" in cfg:
        req = cfg["requirements"]
        if not isinstance(req, dict):
            raise SystemExit("Config error: requirements must be a mapping/object.")
        for k in ("cet1_min", "tier1_min", "total_min", "ccb", "ccyb", "gsib", "leverage_min"):
            if k in req and not isinstance(req[k], (int, float)):
                raise SystemExit(f"Config error: requirements.{k} must be a number.")

    # Specific friendly misspelling guard (acceptance case)
    if "lcr" in cfg and "level2b_cap_pct" not in cfg["lcr"]:
        # If a common misspelling exists, point to it
        for wrong in ("level2b_cap_pctt", "level2B_cap_pct", "level2b_cap_pct "):
            if wrong in cfg["lcr"]:
                raise SystemExit("Config error: 'lcr.level2b_cap_pct' is mis-typed. Hint: expected key is 'level2b_cap_pct'.")
