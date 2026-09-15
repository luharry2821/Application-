"""Rule-driven triage engine.

The engine interprets records from rules.yaml; it holds no CARC, CPT, or
policy knowledge of its own. Every finding carries:
  - a verdict from the fixed set,
  - a confidence band (never a point estimate),
  - ranked grounds each labelled verified/hypothesis,
  - evidence still required,
  - risks/counterarguments,
  - a data-completeness flag for same-day line data.

Gross variance is a screening estimate and is never aggregated into a
recovery figure; probability-weighted expected recovery is computed
separately as a band, and PR dollars never enter it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .loader import Line, Extract


@dataclass
class Ground:
    rank: int
    text: str
    label: str  # "verified" | "hypothesis"


@dataclass
class Finding:
    line: Line
    verdict: str
    label: str                       # verified | hypothesis (verdict-level)
    band: str                        # confidence band name
    band_range: tuple[float, float]  # (p_low, p_high)
    action_owner: str = ""
    grounds: list[Ground] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    systemic_flags: list[str] = field(default_factory=list)
    same_day_data: bool = False
    payer_recovery_eligible: bool = False
    expected_recovery_low: float = 0.0
    expected_recovery_high: float = 0.0
    suppressed: bool = False
    rule_id: str = ""

    def add_ground(self, text: str, label: str) -> None:
        self.grounds.append(Ground(len(self.grounds) + 1, text, label))


# --------------------------------------------------------------------------
# CPT metadata helpers (ranges like "37254-37299" are supported)
# --------------------------------------------------------------------------

def _cpt_lookup(cpt: str, cpt_meta: dict) -> dict | None:
    cpt = (cpt or "").strip()
    if not cpt:
        return None
    if cpt in cpt_meta:
        return cpt_meta[cpt]
    if cpt.isdigit():
        n = int(cpt)
        for key, meta in cpt_meta.items():
            if "-" in key:
                lo, hi = key.split("-")
                if lo.isdigit() and hi.isdigit() and int(lo) <= n <= int(hi):
                    return meta
    return None


def cpt_family(cpt: str, rules: dict) -> str:
    meta = _cpt_lookup(cpt, rules.get("cpt_meta", {}))
    if meta:
        return meta["family"]
    return f"CPT {cpt[:3]}xx" if cpt else "unknown"


def _in_range(cpt: str, rng: str) -> bool:
    if not cpt or not cpt.isdigit() or "-" not in rng:
        return False
    lo, hi = rng.split("-")
    return int(lo) <= int(cpt) <= int(hi)


# --------------------------------------------------------------------------
# Rate logic
# --------------------------------------------------------------------------

def applicable_rate(month: str, rules: dict) -> dict:
    """Return the rate record for a service month; records are sorted so the
    newest effective_from at or before the month wins."""
    cands = [r for r in rules["rates"] if r["effective_from"] <= (month or "")]
    if not cands:
        cands = rules["rates"]
    return max(cands, key=lambda r: r["effective_from"])


def _allowed_state(line: Line, rules: dict) -> str:
    """zero | below_rate | at_rate | above_rate for the line's allowed amount."""
    if line.allowed is None or line.billed in (None, 0):
        return "unknown"
    if line.allowed == 0:
        return "zero"
    rate = applicable_rate(line.month, rules)
    tol = rules["thresholds"]["at_rate_tolerance_pp"]
    realized = line.allowed / line.billed * 100.0
    if abs(realized - rate["pct"]) <= tol:
        return "at_rate"
    return "below_rate" if realized < rate["pct"] else "above_rate"


# --------------------------------------------------------------------------
# Primary CARC rule matching (data-driven)
# --------------------------------------------------------------------------

def _match_rule(rule: dict, line: Line, allowed_state: str) -> bool:
    m = rule.get("match", {})
    carcs = line.carc_codes()
    groups = line.carc_groups()
    rarcs = line.rarc_codes()
    if "carc" in m and m["carc"] not in carcs:
        return False
    if "carc_prefix" in m and not any(c.startswith(m["carc_prefix"]) for c in carcs):
        return False
    if "rarc" in m and m["rarc"] not in rarcs:
        return False
    if "groups_all" in m and (not groups or any(g != m["groups_all"] for g in groups)):
        return False
    if "groups_any" in m and m["groups_any"] not in groups:
        return False
    if "allowed" in m and m["allowed"] != "any" and m["allowed"] != allowed_state:
        return False
    if "line_status" in m and m["line_status"] != line.line_status:
        return False
    return True


# --------------------------------------------------------------------------
# Territory & laterality test — the core logic, parameterised by cpt_meta
# and the territory_test outcome records in rules.yaml.
# --------------------------------------------------------------------------

def territory_laterality_test(line: Line, extract: Extract, rules: dict) -> dict:
    """Evaluate a bundling denial against the authoritative same-day line
    list. Returns the outcome record (verdict/grounds/needs/warnings) chosen
    from rules['territory_test'], plus computed facts."""
    tt = rules["territory_test"]
    meta = rules.get("cpt_meta", {})
    denied = _cpt_lookup(line.cpt, meta)

    others = [l for l in extract.lines if l.cpt != line.cpt]
    interventions, paid_diagnostics = [], []
    for l in others:
        m = _cpt_lookup(l.cpt, meta)
        if m and m["modality"] == "intervention":
            interventions.append((l, m))
        elif m and m["modality"] in ("diagnostic", "screening") and (l.allowed or 0) > 0:
            paid_diagnostics.append((l, m))

    facts = {
        "denied_meta": denied,
        "interventions": [(l.cpt, m["territory"], m["laterality"]) for l, m in interventions],
        "paid_diagnostics": [(l.cpt, m["territory"]) for l, m in paid_diagnostics],
    }

    if denied is None:
        return {"outcome": "no_intervention_or_primary_found", **tt["no_intervention_or_primary_found"],
                "facts": facts,
                "extra_needs": [f"CPT metadata for {line.cpt} — add it to cpt_meta in rules.yaml"]}

    # Candidate bundling targets: interventions first; if none, a paid
    # higher-order diagnostic in the same territory can also be the target.
    targets = interventions or [
        (l, m) for l, m in paid_diagnostics if m["territory"] == denied["territory"]
    ]
    if not targets:
        return {"outcome": "no_intervention_or_primary_found", **tt["no_intervention_or_primary_found"], "facts": facts}

    territory_matches = [(l, m) for l, m in targets if m["territory"] == denied["territory"]]
    if not territory_matches:
        return {"outcome": "territory_mismatch", **tt["territory_mismatch"], "facts": facts}

    # Territory matches: laterality test.
    target_line, target_meta = territory_matches[0]
    if denied.get("laterality") == "bilateral" and target_meta.get("laterality") == "unilateral":
        return {"outcome": "laterality_partial", **tt["laterality_partial"], "facts": facts}

    has_distinct_modifier = any(
        tok in (line.modifier or "").upper()
        for tok in ("59", "XE", "XS", "XP", "XU")
    )
    key = "full_match_with_modifier" if has_distinct_modifier else "full_match_no_modifier"
    return {"outcome": key, **tt[key], "facts": facts}


# --------------------------------------------------------------------------
# Advisory checks (attach warnings/flags, never change the verdict)
# --------------------------------------------------------------------------

def control_evidence_checks(f: Finding, rules: dict) -> None:
    line, th = f.line, rules["thresholds"]
    if not line.control:
        return
    rate = applicable_rate(line.month, rules)
    if line.control_pct is not None and abs(line.control_pct - rate["pct"]) > th["control_deviation_pp"]:
        f.warnings.append(
            f"Control realized % ({line.control_pct}) deviates from expected "
            f"({rate['pct']}) by more than {th['control_deviation_pp']}pp — "
            "control evidence is suspect; verify before citing it."
        )
    if line.control_basis is not None and line.control_basis < th["control_trivial_dollars"]:
        f.warnings.append(
            f"Control lines cover trivial dollars (${line.control_basis:.2f} < "
            f"${th['control_trivial_dollars']:.2f}); the realized percentage is "
            "rounding noise, not rate evidence."
        )
    if not rate.get("verified", False):
        f.warnings.append(
            "Expected rate for this service month is an assumption "
            f"({rate['source']}); findings that depend on it are hypotheses."
        )


def code_set_checks(f: Finding, extract: Extract | None, rules: dict) -> None:
    changes = rules.get("code_set_changes", {})
    cpts = [f.line.cpt] + ([l.cpt for l in extract.lines] if extract else [])
    for rec in changes.get("retired", []):
        for c in cpts:
            if _in_range(c, rec["range"]) and (f.line.month or "") >= rec["retired_on"]:
                f.warnings.append(
                    f"CPT {c} is in a retired range ({rec['range']}, retired "
                    f"{rec['retired_on']}, replaced by {rec['replaced_by']}): {rec['note']}"
                )
    for rec in changes.get("introduced", []):
        for c in cpts:
            if _in_range(c, rec["range"]):
                f.systemic_flags.append(
                    f"CPT {c} introduced {rec['introduced_on']}: {rec['systemic_note']}"
                )


def angiography_criteria(f: Finding, rules: dict) -> None:
    cfg = rules.get("angiography_separate_criteria", {})
    fam = cpt_family(f.line.cpt, rules)
    if fam in cfg.get("applies_to_families", []):
        f.needs.append(
            "Medical-record confirmation of at least one separately-reportable "
            "criterion for diagnostic angiography beside an intervention: "
            + "; ".join(cfg.get("criteria", []))
        )


# --------------------------------------------------------------------------
# Main evaluation
# --------------------------------------------------------------------------

def evaluate_line(line: Line, extract: Extract | None, rules: dict) -> Finding:
    allowed_state = _allowed_state(line, rules)
    rule = next(
        (r for r in rules["carc_rules"] if _match_rule(r, line, allowed_state)),
        None,
    )
    bands = rules["confidence_bands"]

    def mk(verdict, label, band, owner="", rid=""):
        b = bands[band]
        return Finding(line=line, verdict=verdict, label=label, band=band,
                       band_range=(b["p_low"], b["p_high"]),
                       action_owner=owner, rule_id=rid,
                       same_day_data=extract is not None)

    if rule is None:  # defensive; default-unreviewed should always match
        f = mk("unresolved", "hypothesis", "low", "triage", "no-rule")
        f.add_ground("No rule matched.", "hypothesis")
        return f

    if rule.get("suppress"):
        f = mk("valid denial", "verified", "minimal", "", rule["id"])
        f.suppressed = True
        f.add_ground(rule.get("note", "Suppressed by rule."), "verified")
        return f

    if rule.get("territory_test"):
        if extract is None:
            f = mk(rule["hypothesis_verdict"], "hypothesis", rule["hypothesis_band"],
                   rule.get("action_owner", ""), rule["id"])
            for g in rule.get("hypothesis_grounds", []):
                f.add_ground(g, "hypothesis")
            f.needs.extend(rule.get("needs", []))
        else:
            res = territory_laterality_test(line, extract, rules)
            f = mk(res["verdict"], res["label"], res["band"],
                   rule.get("action_owner", ""), f"{rule['id']}:{res['outcome']}")
            for g in res.get("grounds", []):
                f.add_ground(g, res["label"])
            if res["facts"]["paid_diagnostics"] and res["outcome"] in (
                    "territory_mismatch", "laterality_partial"):
                f.add_ground(rules["territory_test"]["consistency_ground"]["ground"], "verified")
            f.needs.extend(res.get("needs", []))
            f.needs.extend(res.get("extra_needs", []))
            f.warnings.extend(res.get("warnings", []))
    else:
        f = mk(rule["verdict"], rule["label"], rule["band"],
               rule.get("action_owner", ""), rule["id"])
        for g in rule.get("grounds", []):
            f.add_ground(g, rule["label"])
        f.needs.extend(rule.get("needs", []))

    # Pre-April rate dependence downgrades the label to hypothesis.
    rate = applicable_rate(line.month, rules)
    if not rate.get("verified", False) and f.verdict in rules["recovery_verdicts"]:
        f.label = "hypothesis"
        f.risks.append(
            "Verdict depends on the pre-April inferred rate "
            f"({rate['pct']}%), which is an assumption, not a contract term."
        )

    control_evidence_checks(f, rules)
    code_set_checks(f, extract, rules)
    if f.verdict in ("appealable denial", "unresolved"):
        angiography_criteria(f, rules)

    # Payer-recovery eligibility & probability-weighted expected recovery.
    groups = set(line.carc_groups())
    group_ok = all(
        rules["carc_groups"].get(g, {}).get("payer_recovery") is True for g in groups
    ) if groups else False
    f.payer_recovery_eligible = (
        group_ok and f.verdict in rules["recovery_verdicts"] and not f.suppressed
    )
    if f.payer_recovery_eligible and line.variance:
        f.expected_recovery_low = round(line.variance * f.band_range[0], 2)
        f.expected_recovery_high = round(line.variance * f.band_range[1], 2)
    return f


def evaluate_all(lines: list[Line], extracts: dict[str, Extract], rules: dict) -> list[Finding]:
    return [evaluate_line(l, extracts.get(l.claim), rules) for l in lines]
