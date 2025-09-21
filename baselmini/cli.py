# cli.py
# -----------------------------------------------------------------------------
# Command-line entrypoint for the Basel III Mini Engine (capital, liquidity, NSFR)
# - Loads input CSV/JSON, applies optional scenario overlays
# - Runs credit risk (RWA), capital ratios, LCR, and (optionally) NSFR
# - Performs validations and emits CSV/JSON/Markdown outputs
# -----------------------------------------------------------------------------

import argparse, os, sys, json
from typing import Dict, Any, List, Tuple

# Load YAML/JSON config files (risk weights, LCR/NSFR params, scenarios)
from .config import load_config, get_version
# CSV/JSON helpers and output directory creation
from .io_utils import read_csv, write_csv, write_json, ensure_dir
# Core calculators: credit risk (RWA), capital ratios, liquidity (LCR), and NSFR
from .calc import compute_rwa, compute_capital_ratios, compute_lcr, compute_nsfr
# Scenario engine: apply shocks (EAD scalers, rating notches, LCR multipliers)
from .scenario import apply_scenario
# Validations (blocking) + data-quality warnings (non-blocking)
from .validators import validate_results, collect_warnings
# Markdown report renderer (mini Pillar-3-style output)
from .report import render_markdown
# FX helpers (optional): convert exposures to base CCY and optionally validate quote dates
from .fx import load_fx_table, convert_exposures_to_base
# Config schema checks (fail-fast on typos/wrong keys)
from .schemas import validate_config


# Read the first row of capital.csv and return a normalized dict of own-funds components
def parse_capital_csv(path: str) -> Dict[str, float]:
    """
    Read the first row of capital CSV and return own-funds components.
    Supports optional 'leverage_exposure' (defaults to 0.0 if missing).
    """
    rows = read_csv(path)
    if not rows:  # deterministic zeros if file is empty
        return {
            "cet1": 0.0,
            "at1": 0.0,
            "tier2": 0.0,
            "deductions": 0.0,
            "leverage_exposure": 0.0,
        }
    r = rows[0]
    return {
        "cet1": float(r.get("cet1", 0.0)),
        "at1": float(r.get("at1", 0.0)),
        "tier2": float(r.get("tier2", 0.0)),
        "deductions": float(r.get("deductions", 0.0)),
        "leverage_exposure": float(r.get("leverage_exposure", 0.0)),
    }


def _print_examples(verbosity: int) -> int:
    """
    Print paths under common example folders if present.
    """
    roots = [("configs", ".yml"), ("data", ".csv"), ("scenarios", ".yml"), ("golden/inputs", ".*")]
    found = False
    for root, ext in roots:
        if os.path.isdir(root):
            files = sorted([os.path.join(root, f) for f in os.listdir(root) if not f.startswith(".")])
            if files:
                print(f"[{root}]")
                for p in files:
                    print("  ", p)
                found = True
    if not found and verbosity > 0:
        print("No example folders found (configs/, data/, scenarios/, golden/inputs).")
    return 0


def _one_screen_summary(results: Dict[str, Any]) -> str:
    """
    Build a concise single-screen text summary for --dry-run.
    """
    lines: List[str] = []
    lines.append(f"As-of: {results.get('asof')}")
    if results.get("scenario_name"):
        lines.append(f"Scenario: {results.get('scenario_name')}")
    # RWA / Capital
    rwa = results["rwa"]; cap = results["capital"]; lcr = results["lcr"]
    lines.append(f"RWA total: {rwa['total_rwa']:.2f}")
    ratios = cap["ratios"]
    lines.append(f"CET1/T1/Total: {ratios['cet1_ratio']:.4f} / {ratios['tier1_ratio']:.4f} / {ratios['total_capital_ratio']:.4f}")
    # LCR
    lines.append(f"LCR: {lcr['lcr']:.4f} ({lcr['lcr_percent']:.2f}%)  HQLA={lcr['hqla']:.2f}  NetOut={lcr['net_outflows']:.2f}")
    # NSFR (if present)
    if results.get("nsfr"):
        ns = results["nsfr"]
        lines.append(f"NSFR: {ns['nsfr']:.4f} ({ns['nsfr_percent']:.2f}%)  ASF={ns['asf']:.2f}  RSF={ns['rsf']:.2f}")
    # Breaches / Warnings
    if results.get("breaches"):
        lines.append("Breaches: " + ", ".join(results["breaches"]))
    if results.get("warnings"):
        lines.append(f"Warnings: {len(results['warnings'])} (use --strict to fail on warnings)")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(prog="baselmini", description="Basel III Mini Engine (capital, liquidity, NSFR)")
    sub = p.add_subparsers(dest="cmd")

    # Global flags (apply to subcommands where relevant)
    p.add_argument("--version", action="store_true", help="Print version and exit")
    p.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (repeatable)")
    p.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (suppresses normal output)")

    # NEW: global utilities that do not require the 'run' subcommand
    p.add_argument("--list-examples", action="store_true", help="List bundled example paths and exit")
    p.add_argument("--show-config", action="store_true", help="Print the parsed config (post-parse) to stdout and exit")
    p.add_argument("--config-file", help="Path to a config file for --show-config (YAML or JSON)")

    # Define the `run` subcommand and its required inputs
    run = sub.add_parser("run", help="Run calculation")
    run.add_argument("--asof", required=True, help="As-of date, e.g., 2024-12-31")
    run.add_argument("--exposures", required=True)
    run.add_argument("--capital", required=True)
    run.add_argument("--liquidity", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--scenario", required=False)  # Optional stress/scenario file

    # --out not required
    run.add_argument("--out", required=False, help="Output directory (required unless --dry-run or --stdout report)")

    run.add_argument("--fx", required=False, help="Optional FX rates CSV")
    run.add_argument("--fx-date-check", action="store_true", help="Validate FX quote_date <= asof")
    run.add_argument("--nsfr", required=False, help="Optional NSFR CSV (ASF/RSF rows: bucket,amount_ccy,factor)")

    # UX flags (run-specific)
    run.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    run.add_argument("--no-validate", action="store_true", help="Skip hard validations (schema + results)")
    run.add_argument("--stdout", choices=["report"], help="Print artifact to stdout instead of writing files (e.g., 'report')")
    run.add_argument("--dry-run", action="store_true", help="Load, compute, validate; print a one-screen summary; do not write files")

    args = p.parse_args()  # Parse CLI args

    # Global: --version
    if args.version:
        print(f"baselmini {get_version()}")
        return

    # Verbosity helpers
    verbosity = int(getattr(args, "verbose", 0) or 0)
    quiet = bool(getattr(args, "quiet", False))

    def vprint(*msg, level: int = 1, **kw):
        if not quiet and verbosity >= level:
            print(*msg, **kw)

    # Global utilities that do not require 'run'
    if args.list_examples:
        _print_examples(verbosity)
        return

    if args.show_config:
        if not args.config_file:
            print("error: --show-config requires --config-file <path>", file=sys.stderr)
            sys.exit(2)
        cfg = load_config(args.config_file)
        print(json.dumps(cfg, indent=2, sort_keys=True))
        return

    if args.cmd == "run":
        # enforce --out unless --dry-run or --stdout report
        if not args.dry_run and not args.stdout and not args.out:
            p.error("--out is required unless --dry-run or --stdout report is set")

        # Load config first so we can optionally validate it
        cfg = load_config(args.config)

        # Schema validation (fail fast on typos) unless --no-validate
        if not args.no_validate:
            validate_config(cfg)

        # Ensure output directory only when we're going to write files
        if not args.dry_run and not args.stdout:
            ensure_dir(args.out)

        # Load input datasets
        exposures = read_csv(args.exposures)
        capital = parse_capital_csv(args.capital)
        liquidity = read_csv(args.liquidity)
        nsfr_rows = read_csv(args.nsfr) if args.nsfr else []

        # Optional FX conversion to base CCY
        base_ccy = ((cfg.get("fx") or {}).get("base_ccy") or "").upper() or None
        if base_ccy and args.fx:
            fx_rows = read_csv(args.fx)
            fx_table = load_fx_table(fx_rows)
            convert_exposures_to_base(exposures, fx_table, base_ccy)
            # When --fx-date-check is on, the validator will enforce quote_date <= asof
            fx_meta = {"quotes": fx_table, "base": base_ccy} if args.fx_date_check else None
        else:
            fx_meta = None

        # Optional scenario overlay
        scenario_name = "Base case"
        if args.scenario:
            sc = load_config(args.scenario)
            scenario_name = sc.get("scenario", {}).get("name", os.path.basename(args.scenario))
            apply_scenario(exposures, liquidity, sc)
            vprint(f"Applied scenario: {scenario_name}", level=1)

        # Non-blocking per-row data checks (schema hints, non-negatives, currency whitelist, etc.)
        warnings = collect_warnings(exposures, liquidity, cfg, fx_meta)
        if warnings and args.strict:
            # Treat warnings as errors in strict mode
            print("Strict mode: warnings encountered:", file=sys.stderr)
            for w in warnings[:50]:
                print(" -", w, file=sys.stderr)
            if len(warnings) > 50:
                print(f" ...and {len(warnings)-50} more.", file=sys.stderr)
            sys.exit(2)

        # --- Compute core outputs ---
        rwa = compute_rwa(exposures, cfg)
        cap = compute_capital_ratios(capital, rwa["total_rwa"], cfg)  # pass cfg so requirements/leverage_min apply
        lcr = compute_lcr(liquidity, cfg)
        nsfr = compute_nsfr(nsfr_rows, cfg) if nsfr_rows else None

        # Collect results for validation + reporting
        results: Dict[str, Any] = {
            "asof": args.asof,
            "scenario_name": scenario_name,
            "rwa": rwa,
            "capital": cap,
            "lcr": lcr,
            "fx_meta": fx_meta,
            # always include warnings, even if empty
            "warnings": warnings or [],
        }
        if nsfr is not None:
            results["nsfr"] = nsfr

        # Validate aggregate outputs (also annotates breaches when present) unless --no-validate
        if not args.no_validate:
            validate_results(results)
        else:
            vprint("Skipping validations (--no-validate).", level=2)

        # --dry-run: print one-screen summary and exit (no writes)
        if args.dry_run:
            print(_one_screen_summary(results))
            return

        # Markdown report
        md = render_markdown(results, args.asof)

        # --stdout report: print report to stdout and exit (no writes)
        if args.stdout == "report":
            print(md)
            return

        # Otherwise, write outputs to files
        write_csv(os.path.join(args.out, "rwa_per_exposure.csv"), rwa["per_exposure"])
        write_csv(
            os.path.join(args.out, "rwa_by_class.csv"),
            [{"asset_class": k, "rwa": v} for k, v in rwa["by_class"].items()],
        )
        # KPIs file
        if "kpis" in rwa:
            write_json(os.path.join(args.out, "rwa_kpis.json"), rwa["kpis"])
        # Full results blob (includes breaches, warnings, fx metadata, nsfr when present)
        write_json(os.path.join(args.out, "results.json"), results)

        # Write report.md to file
        with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as f:
            f.write(md)

        if not quiet:
            print("Done. Outputs written to", args.out)
    else:
        if getattr(args, "version", False):
            # Handled above; just return. (Parsers may put cmd=None when only --version is used.)
            return
        p.print_help()
