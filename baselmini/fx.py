# fx.py
# -----------------------------------------------------------------------------
# FX helpers: load FX quotes and convert exposures/collateral to base CCY.
# CSV expected columns (flexible):
#   - ccy          : currency code (e.g., EUR)
#   - rate         : BASE per CCY (preferred, e.g., USD per EUR)
#   - quote_date   : YYYY-MM-DD (optional, used by validators if --fx-date-check)
# Back-compat: also accepts 'rate_to_base' if 'rate' is absent.
#
# Exposures side:
#   - Preferred currency field: 'exposure_ccy' (legacy alias: 'ccy')
#   - Monetary fields converted (if present): 'ead', 'drawn', 'undrawn',
#       'collateral_value' (v3), 'eligible_collateral' (legacy)
#   - If collateral is converted, 'collateral_ccy' is normalized to base as well.
# -----------------------------------------------------------------------------

from typing import Dict, Any, List
import sys

def _s(x) -> str:
    return ("" if x is None else str(x)).strip()

def _f(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return float(default)
        return float(x)
    except Exception:
        return float(default)

def load_fx_table(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build { CCY: {rate: float, quote_date: str} } from FX rows.
    Prefers 'rate' but falls back to 'rate_to_base' for backward compatibility.

    Change: reject zero/negative rates early to avoid silent no-ops in conversion.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for i, r in enumerate(rows):
        c = _s(r.get("ccy")).upper()
        if not c:
            print(f"Warning: FX row {i+1} missing 'ccy' — skipping.", file=sys.stderr)
            continue
        # Prefer 'rate', fall back to legacy 'rate_to_base'
        rate = r.get("rate")
        if rate is None or rate == "":
            rate = r.get("rate_to_base", 1.0)
        rate_f = _f(rate, 0.0)
        if rate_f <= 0.0:
            raise SystemExit(f"FX error: non-positive rate for {c} at row {i+1} (value={rate}). "
                             f"Provide a strictly positive BASE-per-CCY rate.")
        out[c] = {
            "rate": rate_f,
            "quote_date": _s(r.get("quote_date")),
        }
    # Do not force-add a specific base here; caller provides base and validators handle presence.
    return out

def convert_exposures_to_base(exposures: List[Dict[str, Any]],
                              fx_table: Dict[str, Dict[str, Any]],
                              base_ccy: str) -> None:
    """
    In-place conversion to base_ccy for exposure rows.
      - If source currency equals base, no-op.
      - Else multiply monetary fields by rate = (BASE per CCY).
    """
    base = _s(base_ccy).upper()
    # Ensure a 1.0 sentinel for base (helps downstream introspection/validation)
    if base not in fx_table:
        fx_table[base] = {"rate": 1.0, "quote_date": ""}

    for e in exposures:
        # Determine source currency (prefer new 'exposure_ccy'; fall back to legacy 'ccy')
        src = _s(e.get("exposure_ccy")) or _s(e.get("ccy"))
        src = src.upper() if src else base

        if not src or src == base:
            # Normalize key even if no conversion
            if _s(e.get("exposure_ccy")) == "" and _s(e.get("ccy")) != "":
                e["exposure_ccy"] = _s(e.get("ccy")).upper()
            if _s(e.get("exposure_ccy")) == "":
                e["exposure_ccy"] = base
            continue

        q = fx_table.get(src)
        rate = _f(q["rate"], 0.0) if q else 0.0
        if rate <= 0.0:
            # After early reject above, this generally won't happen;
            # if it does (missing ccy in table), leave values as-is but flag the currency.
            e["exposure_ccy"] = src
            print(f"Warning: no usable FX rate for {src}; exposure left unconverted.", file=sys.stderr)
            continue

        # Convert principal exposure amounts
        for key in ("ead", "drawn", "undrawn"):
            if key in e and e[key] not in (None, "",):
                try:
                    e[key] = float(e[key]) * rate
                except Exception:
                    pass

        # Convert collateral amounts (support both new and legacy field names)
        converted_collateral = False
        if "collateral_value" in e and e["collateral_value"] not in (None, "",):
            try:
                e["collateral_value"] = float(e["collateral_value"]) * rate
                converted_collateral = True
            except Exception:
                pass
        if "eligible_collateral" in e and e["eligible_collateral"] not in (None, "",):
            try:
                e["eligible_collateral"] = float(e["eligible_collateral"]) * rate
                converted_collateral = True
            except Exception:
                pass

        # Normalize currency tags to base after conversion
        e["exposure_ccy"] = base
        # If we converted collateral and a collateral_ccy is present, align it too
        if converted_collateral and _s(e.get("collateral_ccy")):
            e["collateral_ccy"] = base
        # Also normalize legacy 'ccy' field if it exists
        if "ccy" in e:
            e["ccy"] = base
