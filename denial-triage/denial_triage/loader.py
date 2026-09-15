"""Input loading: rules file, workbook lines, optional same-day extracts.

The loader enforces the PHI guard before anything else sees the data:
input is rejected if a column header matches a forbidden pattern or any
cell contains an exact calendar date. Service month, claim number, codes
and amounts are acceptable.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import dataclass, field

import yaml

RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.yaml")


class PHIError(ValueError):
    """Raised when input appears to contain protected health information."""


def load_rules(path: str = RULES_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


@dataclass
class Line:
    """One radiology service line in canonical fields (see rules.yaml field_map)."""
    claim: str = ""
    month: str = ""
    rate_phase: str = ""
    cpt: str = ""
    modifier: str = ""
    revenue_code: str = ""
    units: float | None = None
    billed: float | None = None
    allowed: float | None = None
    paid: float | None = None
    expected_pct: float | None = None
    expected_allowed: float | None = None
    variance: float | None = None
    line_status: str = ""
    carc: str = ""
    carc_amounts: str = ""
    carc_desc: str = ""
    rarc: str = ""
    rarc_desc: str = ""
    control: bool = False
    control_count: float | None = None
    control_pct: float | None = None
    control_basis: float | None = None
    claim_charge: float | None = None
    claim_allowed: float | None = None
    claim_paid: float | None = None
    prior_cause: str = ""
    remit_type: str = ""
    payer_validation: str = ""

    def carc_codes(self) -> list[str]:
        return [c.strip() for c in self.carc.split(";") if c.strip()]

    def rarc_codes(self) -> list[str]:
        return [c.strip() for c in self.rarc.split(";") if c.strip()]

    def carc_group_of(self, code: str) -> str:
        return code.split("-")[0].strip().upper()

    def carc_groups(self) -> list[str]:
        return [self.carc_group_of(c) for c in self.carc_codes()]


@dataclass
class SameDayLine:
    """One line from a pasted dashboard extract — the authoritative view of
    everything billed on the claim, radiology or not."""
    cpt: str = ""
    modifier: str = ""
    billed: float | None = None
    allowed: float | None = None
    paid: float | None = None
    description: str = ""


@dataclass
class Extract:
    claim: str = ""
    lines: list[SameDayLine] = field(default_factory=list)


# --------------------------------------------------------------------------
# PHI guard
# --------------------------------------------------------------------------

def phi_check_headers(headers: list[str], rules: dict) -> None:
    pats = [re.compile(p, re.I) for p in rules["phi"]["forbidden_header_patterns"]]
    for h in headers:
        for p in pats:
            if p.search(h or ""):
                raise PHIError(
                    f"Input rejected: column '{h}' matches forbidden PHI pattern "
                    f"'{p.pattern}'. Remove PHI columns and resubmit."
                )


def phi_check_values(values, rules: dict) -> None:
    pats = [re.compile(p) for p in rules["phi"]["forbidden_value_patterns"]]
    for v in values:
        s = str(v) if v is not None else ""
        for p in pats:
            if p.search(s):
                raise PHIError(
                    f"Input rejected: value '{s}' looks like an exact date of service "
                    f"(pattern '{p.pattern}'). Use service month instead."
                )


# --------------------------------------------------------------------------
# Workbook / CSV loading
# --------------------------------------------------------------------------

_NUMERIC_FIELDS = {
    "units", "billed", "allowed", "paid", "expected_pct", "expected_allowed",
    "variance", "control_count", "control_pct", "control_basis",
    "claim_charge", "claim_allowed", "claim_paid",
}


def _to_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows_to_lines(header: list[str], data_rows: list[list], rules: dict) -> list[Line]:
    phi_check_headers(header, rules)
    colmap = rules["field_map"]["columns"]
    idx = {}
    for canonical, wb_header in colmap.items():
        if wb_header in header:
            idx[canonical] = header.index(wb_header)
    missing = [colmap[k] for k in colmap if k not in idx]
    if missing:
        warnings.warn(f"Workbook columns not found (fields left empty): {missing}")

    lines = []
    for row in data_rows:
        if not any(v not in (None, "") for v in row):
            continue
        phi_check_values(row, rules)
        kw = {}
        for canonical, i in idx.items():
            v = row[i] if i < len(row) else None
            if canonical in _NUMERIC_FIELDS:
                kw[canonical] = _to_num(v)
            elif canonical == "control":
                kw[canonical] = str(v).strip().lower() == "true"
            else:
                kw[canonical] = "" if v is None else str(v).strip()
        lines.append(Line(**kw))
    return lines


def load_workbook_lines(path: str, rules: dict) -> list[Line]:
    """Read the Line Detail sheet of the ops workbook."""
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True)
    sheet = rules["field_map"]["line_detail_sheet"]
    ws = wb[sheet]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    anchor = rules["field_map"]["columns"]["claim"]
    hdr_i = next(i for i, r in enumerate(rows) if anchor in r)
    header = ["" if v is None else str(v) for v in rows[hdr_i]]
    return _rows_to_lines(header, rows[hdr_i + 1:], rules)


def load_csv_lines(path: str, rules: dict, header_row: int | None = None) -> list[Line]:
    import csv
    with open(path) as f:
        rows = list(csv.reader(f))
    anchor = rules["field_map"]["columns"]["claim"]
    if header_row is None:
        header_row = next(i for i, r in enumerate(rows) if anchor in r)
    return _rows_to_lines(rows[header_row], rows[header_row + 1:], rules)


def load_extracts(directory: str, rules: dict) -> dict[str, Extract]:
    """Load pasted dashboard extracts: one JSON file per claim, named
    <claim>.json, shaped {"claim": "...", "lines": [{"cpt": ..., "modifier":
    ..., "billed": ..., "allowed": ..., "paid": ..., "description": ...}]}.
    These are the authoritative full same-day line list for the claim.
    """
    out: dict[str, Extract] = {}
    if not directory or not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name)) as f:
            raw = json.load(f)
        phi_check_values(
            [raw.get("claim", "")] + [str(l) for l in raw.get("lines", [])], rules
        )
        ex = Extract(claim=str(raw.get("claim", name[:-5])))
        for l in raw.get("lines", []):
            ex.lines.append(SameDayLine(
                cpt=str(l.get("cpt", "")).strip(),
                modifier=str(l.get("modifier", "") or "").strip(),
                billed=_to_num(l.get("billed")),
                allowed=_to_num(l.get("allowed")),
                paid=_to_num(l.get("paid")),
                description=str(l.get("description", "") or ""),
            ))
        out[ex.claim] = ex
    return out
