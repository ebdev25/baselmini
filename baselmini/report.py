from typing import Dict, Any, List # Typing helpers for signatures
import datetime as _dt            # Used for UTC timestamp in report header

# Render a Markdown report summarising Basel outputs (mini Pillar 3 style)
def render_markdown(results: Dict[str, Any], asof: str) -> str:
    # Extract sections from results blob
    rwa = results["rwa"]       # Risk-weighted assets section (per-exposure + totals)
    cap = results["capital"]   # Capital stack and ratios (+buffers, leverage)
    lcr = results["lcr"]       # Liquidity metrics
    nsfr = results.get("nsfr") # Optional NSFR section
    scenario_name = results.get("scenario_name") or "Base case"

    lines: List[str] = []  # collect markdown lines
    
    # --- Header ---
    lines.append(f"# BaselMini Results — {scenario_name}")
    lines.append("")
    lines.append(f"*As of {asof} — generated {_dt.datetime.utcnow().isoformat(timespec='seconds')}Z*")
    lines.append("")

    # --- Breaches banner (if any) ---
    if results.get("breaches"):
        lines.append("## ⚠ Breaches")
        lines.append("")
        for b in results["breaches"]:
            label = {
                "cet1_ratio": "CET1 ratio below requirement",
                "tier1_ratio": "Tier 1 ratio below requirement",
                "total_capital_ratio": "Total capital ratio below requirement",
                "leverage_ratio": "Leverage ratio below requirement",
                "lcr": "LCR below 100%",
                "nsfr": "NSFR below 100%",
            }.get(b, b)
            lines.append(f"- {label}")
        lines.append("")

    # --- Capital ratios section ---
    lines.append("## Capital & Ratios")
    lines.append("")
    c = cap["components"]
    lines.append("| CET1 | AT1 | Tier 2 | Deductions | Tier 1 | Total Capital |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    lines.append(f"| {c['cet1']:.2f} | {c['at1']:.2f} | {c['tier2']:.2f} | {c['deductions']:.2f} | {c['tier1']:.2f} | {c['total_capital']:.2f} |")
    lines.append("")
    r = cap["ratios"]
    lines.append("| CET1 Ratio | Tier 1 Ratio | Total Capital Ratio |")
    lines.append("|---:|---:|---:|")
    lines.append(f"| {r['cet1_ratio']:.4f} | {r['tier1_ratio']:.4f} | {r['total_capital_ratio']:.4f} |")
    lines.append("")
    if "requirements" in cap and "headroom" in cap:
        req = cap["requirements"]; hd = cap["headroom"]
        lines.append("### Requirements & Buffers")
        lines.append("")
        lines.append("| Metric | Required | Headroom (Actual − Required) |")
        lines.append("|---|---:|---:|")
        lines.append(f"| CET1 Ratio | {req['cet1_required']:.4f} | {hd['cet1']:.4f} |")
        lines.append(f"| Tier 1 Ratio | {req['tier1_required']:.4f} | {hd['tier1']:.4f} |")
        lines.append(f"| Total Capital Ratio | {req['total_required']:.4f} | {hd['total']:.4f} |")
        if "leverage" in cap and "leverage_required" in req and "leverage" in hd:
            lines.append(f"| Leverage Ratio | {req['leverage_required']:.4f} | {hd['leverage']:.4f} |")
        lines.append("")

    # Leverage
    if "leverage" in cap:
        lv = cap["leverage"]
        lines.append("### Leverage")
        lines.append("")
        lines.append("| Exposure Measure | Leverage Ratio |")
        lines.append("|---:|---:|")
        lines.append(f"| {lv['exposure_measure']:.2f} | {lv['ratio']:.4f} |")
        lines.append("")

    # --- RWA summary section ---
    lines.append("## RWA Summary")
    lines.append("")
    lines.append(f"**Total RWA:** {rwa['total_rwa']:.2f}")
    lines.append("")
    lines.append("| Asset Class | RWA |")
    lines.append("|---|---:|")
    for k, v in sorted(rwa["by_class"].items()):
        lines.append(f"| {k} | {v:.2f} |")
    lines.append("")
    lines.append("*Drivers:* per-exposure file includes `supporting_factor_applied`, "
                 "`collateral_mode`, `collateral_haircut_effect`, `scenario_flags`, "
                 "`rw_source`, and `crm_path` for auditability (see `rwa_per_exposure.csv`).")
    lines.append("")

    # KPIs: RWA density
    if "kpis" in rwa:
        lines.append("### Portfolio KPIs")
        lines.append("")
        lines.append("| Asset Class | EAD (Gross) | EAD (Net) | RWA | RWA Density |")
        lines.append("|---|---:|---:|---:|---:|")
        for ac, row in sorted(rwa["kpis"]["by_class"].items()):
            lines.append(f"| {ac} | {row['ead_gross']:.2f} | {row['ead']:.2f} | {row['rwa']:.2f} | {row['density']:.4f} |")
        tot = rwa["kpis"]["total"]
        lines.append(f"| **Total** | **{tot['ead_gross']:.2f}** | **{tot['ead']:.2f}** | **{tot['rwa']:.2f}** | **{tot['density']:.4f}** |")
        # Footnote: RWA reconciliation delta (if provided by validators)
        rec = (results.get("reconciliation") or {})
        if "rwa_per_exposure_delta" in rec:
            delta = rec["rwa_per_exposure_delta"]
            lines.append("")
            lines.append(f"*Reconciliation:* Sum of per-exposure RWA differs from total by **{delta:+.2f}** (tolerance ±0.05).")
        lines.append("")

    # --- Liquidity coverage ratio section ---
    lines.append("## Liquidity Coverage Ratio")
    lines.append("")
    if "breakdown" in lcr:
        b = lcr["breakdown"]
        lines.append("| Level 1 | Level 2A (allowed) | Level 2B (allowed) | Total HQLA |")
        lines.append("|---:|---:|---:|---:|")
        lines.append(f"| {b['level1']:.2f} | {b['level2a_allowed']:.2f} | {b['level2b_allowed']:.2f} | {lcr['hqla']:.2f} |")
        lines.append("")
        # Composition (% of HQLA) — useful for a quick chart-ready view
        if lcr["hqla"] > 0:
            l1p = b["level1"] / lcr["hqla"] * 100.0
            l2ap = b["level2a_allowed"] / lcr["hqla"] * 100.0
            l2bp = b["level2b_allowed"] / lcr["hqla"] * 100.0
            lines.append("**HQLA Composition (for charting):**")
            lines.append("")
            lines.append("| Component | Amount | Share of HQLA |")
            lines.append("|---|---:|---:|")
            lines.append(f"| Level 1 | {b['level1']:.2f} | {l1p:.2f}% |")
            lines.append(f"| Level 2A (allowed) | {b['level2a_allowed']:.2f} | {l2ap:.2f}% |")
            lines.append(f"| Level 2B (allowed) | {b['level2b_allowed']:.2f} | {l2bp:.2f}% |")
            lines.append("")

    lines.append("| HQLA | Outflows | Inflows | Inflow Cap | Net Outflows | LCR | LCR % |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| {lcr['hqla']:.2f} | "
        f"{lcr['outflows']:.2f} | "
        f"{lcr['inflows']:.2f} | "
        f"{lcr['inflow_cap_amount']:.2f} | "
        f"{lcr['net_outflows']:.2f} | "
        f"{lcr['lcr']:.4f} | "
        f"{lcr['lcr_percent']:.2f}% |"
    )
    lines.append("")
    if "caps" in lcr:
        lines.append(f"*Applied caps — Inflow cap: {lcr['caps']['inflow_cap_pct']:.2%}, "
                     f"Level-2 total cap: {lcr['caps']['level2_total_cap_pct']:.2%}, "
                     f"Level-2B cap: {lcr['caps']['level2b_cap_pct']:.2%}.*")
        lines.append("")

    # --- NSFR (optional) ------------------------------------------------------
    if nsfr:
        lines.append("## Net Stable Funding Ratio (NSFR)")
        lines.append("")
        lines.append("| ASF | RSF | NSFR | NSFR % |")
        lines.append("|---:|---:|---:|---:|")
        lines.append(f"| {nsfr['asf']:.2f} | {nsfr['rsf']:.2f} | {nsfr['nsfr']:.4f} | {nsfr['nsfr_percent']:.2f}% |")
        lines.append("")

    # --- Top-5 RWA contributors ----------------------------------------------
    try:
        rows = list(results["rwa"].get("per_exposure", []))
        if rows:
            top = sorted(rows, key=lambda r: float(r.get("rwa", 0.0) or 0.0), reverse=True)[:5]
            lines.append("## Top-5 RWA Contributors")
            lines.append("")
            # Added 'CRM Path' column to expose collateral treatment
            lines.append("| ID | Asset Class | EAD (Net) | Risk Weight | RWA | RW Source | CRM Path |")
            lines.append("|---|---|---:|---:|---:|---|---|")
            for rrow in top:
                lines.append(
                    f"| {rrow.get('id','')} | {rrow.get('asset_class','')} | "
                    f"{float(rrow.get('ead',0.0)):.2f} | {float(rrow.get('risk_weight',0.0)):.4f} | "
                    f"{float(rrow.get('rwa',0.0)):.2f} | {rrow.get('rw_source','')} | "
                    f"{rrow.get('crm_path','')} |"
                )
            lines.append("")
    except Exception:
        pass


    # --- Warnings (optional) --------------------------------------------------
    if results.get("warnings"):
        lines.append("## Warnings (non-blocking data checks)")
        lines.append("")
        for w in results["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)
