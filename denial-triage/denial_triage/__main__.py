"""CLI:  python -m denial_triage --workbook ops.xlsm [--extracts DIR] --out out/"""

import argparse
import sys

from . import (
    load_rules, load_workbook_lines, load_csv_lines, load_extracts,
    evaluate_all, cluster_findings, write_all, PHIError,
)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="denial_triage",
                                 description="Denial triage engine for radiology claims")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--workbook", help="ops review workbook (.xlsx/.xlsm), Line Detail tab")
    src.add_argument("--csv", help="Line Detail exported as CSV")
    ap.add_argument("--extracts", help="directory of per-claim same-day line JSON extracts")
    ap.add_argument("--rules", help="alternate rules.yaml")
    ap.add_argument("--out", default="output", help="output directory")
    args = ap.parse_args(argv)

    rules = load_rules(args.rules) if args.rules else load_rules()
    try:
        if args.workbook:
            lines = load_workbook_lines(args.workbook, rules)
        else:
            lines = load_csv_lines(args.csv, rules)
        extracts = load_extracts(args.extracts, rules) if args.extracts else {}
    except PHIError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    findings = evaluate_all(lines, extracts, rules)
    clusters = cluster_findings(findings, rules)
    paths = write_all(findings, clusters, rules, args.out)

    active = [f for f in findings if not f.suppressed]
    print(f"{len(lines)} lines read, {len(active)} findings "
          f"({len(findings) - len(active)} suppressed), "
          f"{len([c for c in clusters if c.systemic])} systemic clusters, "
          f"{len(extracts)} same-day extracts.")
    for k, p in paths.items():
        print(f"  {k}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
