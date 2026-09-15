"""Outputs: per-line CSV, cluster summary CSV, markdown findings report.

Gross variance (screening estimate) and probability-weighted expected
recovery (band) are always reported separately and never blended.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict

from .clustering import Cluster
from .engine import Finding, cpt_family

LINE_COLUMNS = [
    "claim", "service_month", "cpt", "modifier", "revenue_code",
    "billed", "allowed", "expected_allowed", "gross_variance",
    "carc", "carc_desc", "rarc", "rarc_desc",
    "control_count", "control_realized_pct",
    "verdict", "verdict_label", "confidence_band", "band_p_low", "band_p_high",
    "payer_recovery_eligible", "expected_recovery_low", "expected_recovery_high",
    "action_owner", "grounds", "evidence_required", "risks_counterarguments",
    "warnings", "systemic_flags", "same_day_data_available", "rule_id",
]


def _pack(items) -> str:
    return " | ".join(items)


def write_lines_csv(findings: list[Finding], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(LINE_COLUMNS)
        for f in findings:
            if f.suppressed:
                continue
            l = f.line
            w.writerow([
                l.claim, l.month, l.cpt, l.modifier, l.revenue_code,
                l.billed, l.allowed, l.expected_allowed, l.variance,
                l.carc, l.carc_desc, l.rarc, l.rarc_desc,
                l.control_count, l.control_pct,
                f.verdict, f.label, f.band, f.band_range[0], f.band_range[1],
                f.payer_recovery_eligible,
                f.expected_recovery_low or "", f.expected_recovery_high or "",
                f.action_owner,
                _pack(f"[{g.rank}][{g.label}] {g.text}" for g in f.grounds),
                _pack(f.needs), _pack(f.risks), _pack(f.warnings),
                _pack(f.systemic_flags), f.same_day_data, f.rule_id,
            ])


def write_clusters_csv(clusters: list[Cluster], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["carc", "rarc", "cpt_family", "billed", "lines", "claims",
                    "total_gross_variance", "systemic", "claim_numbers"])
        for c in clusters:
            w.writerow([c.carc, c.rarc, c.family, c.billed, c.size,
                        len(c.claims), c.total_variance, c.systemic,
                        "; ".join(c.claims)])


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def write_markdown_report(findings: list[Finding], clusters: list[Cluster],
                          rules: dict, path: str) -> None:
    active = [f for f in findings if not f.suppressed]
    suppressed = [f for f in findings if f.suppressed]

    # Group by root cause: verdict + primary CARC.
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in active:
        primary = f.line.carc_codes()[0] if f.line.carc_codes() else "none"
        groups[(f.verdict, primary)].append(f)
    ordered = sorted(groups.items(),
                     key=lambda kv: -sum(x.line.variance or 0 for x in kv[1]))

    gross = sum(f.line.variance or 0 for f in active)
    rec_lo = sum(f.expected_recovery_low for f in active)
    rec_hi = sum(f.expected_recovery_high for f in active)
    pr_excluded = sum(
        f.line.variance or 0 for f in active
        if "PR" in f.line.carc_groups() and not f.payer_recovery_eligible
    )

    out = []
    out.append("# Denial triage findings — Oaklawn radiology (BCBSM commercial)\n")
    out.append("Grouped by root cause, ordered by dollars. Every statement is "
               "labelled **verified** (confirmed against data in hand or a cited "
               "source) or **hypothesis** (depends on a fact not yet available).\n")
    out.append("## Headline figures — reported separately, never blended\n")
    out.append(f"- **Gross variance (screening estimate only):** {_fmt_money(gross)} "
               f"across {len(active)} candidate lines. Not a recovery figure.")
    out.append(f"- **Probability-weighted expected recovery (band):** "
               f"{_fmt_money(rec_lo)} – {_fmt_money(rec_hi)}, payer-recovery-eligible "
               "verdicts only. Bands are directional judgment from general denial "
               "patterns, not measured payer overturn rates.")
    out.append(f"- **PR (patient responsibility) dollars excluded from recovery:** "
               f"{_fmt_money(pr_excluded)}.")
    out.append(f"- Suppressed lines (CO-45 at contract rate, normal write-off): "
               f"{len(suppressed)}.\n")

    sysd = [c for c in clusters if c.systemic]
    if sysd:
        out.append("## Systemic patterns (clusters)\n")
        out.append("| CARC | RARC | CPT family | Billed | Lines | Claims | Gross variance |")
        out.append("|---|---|---|---|---|---|---|")
        for c in sysd:
            out.append(f"| {c.carc or '—'} | {c.rarc or '—'} | {c.family} | "
                       f"{_fmt_money(c.billed)} | {c.size} | {len(c.claims)} | "
                       f"{_fmt_money(c.total_variance)} |")
        out.append("\nIdentical billed amounts across claims indicate a single "
                   "chargemaster item denied repeatedly — investigate the item, "
                   "not just the lines.\n")

    for (verdict, carc), fs in ordered:
        total = sum(f.line.variance or 0 for f in fs)
        out.append(f"## {verdict} · {carc} — {_fmt_money(total)} ({len(fs)} line"
                   f"{'s' if len(fs) != 1 else ''})\n")
        for f in sorted(fs, key=lambda x: -(x.line.variance or 0)):
            l = f.line
            out.append(
                f"### Claim {l.claim} · {l.month} · CPT {l.cpt} · "
                f"{_fmt_money(l.variance)} variance\n")
            out.append(f"- **Verdict:** {f.verdict} — **{f.label}** · confidence "
                       f"band **{f.band}** ({f.band_range[0]:.0%}–{f.band_range[1]:.0%})"
                       f" · owner: {f.action_owner or 'unassigned'}")
            out.append(f"- Billed {_fmt_money(l.billed)}, allowed {_fmt_money(l.allowed)}, "
                       f"expected {_fmt_money(l.expected_allowed)} · "
                       f"{l.carc or 'no CARC'}{(' / ' + l.rarc) if l.rarc else ''}")
            if l.control:
                out.append(f"- Control evidence: {l.control_count:.0f} line(s) at "
                           f"{l.control_pct}%")
            out.append(f"- Same-day line data: "
                       f"{'available' if f.same_day_data else 'NOT available'}")
            if f.payer_recovery_eligible:
                out.append(f"- Expected recovery band: "
                           f"{_fmt_money(f.expected_recovery_low)} – "
                           f"{_fmt_money(f.expected_recovery_high)}")
            out.append("- **Grounds (ranked):**")
            for g in f.grounds:
                out.append(f"  {g.rank}. [{g.label}] {g.text}")
            if f.needs:
                out.append("- **Evidence still required:** " + "; ".join(f.needs))
            if f.risks:
                out.append("- **Risk / counterargument:** " + "; ".join(f.risks))
            if f.warnings:
                out.append("- **Warnings:** " + "; ".join(f.warnings))
            if f.systemic_flags:
                out.append("- **Systemic:** " + "; ".join(sorted(set(f.systemic_flags))))
            out.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def write_all(findings, clusters, rules, out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "lines_csv": os.path.join(out_dir, "lines.csv"),
        "clusters_csv": os.path.join(out_dir, "clusters.csv"),
        "findings_md": os.path.join(out_dir, "findings.md"),
    }
    write_lines_csv(findings, paths["lines_csv"])
    write_clusters_csv(clusters, paths["clusters_csv"])
    write_markdown_report(findings, clusters, rules, paths["findings_md"])
    return paths
