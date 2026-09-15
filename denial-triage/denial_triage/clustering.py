"""Denial clustering.

Group by CARC + RARC + CPT family + billed amount. Identical billed amounts
across claims indicate a single chargemaster item denied repeatedly — a
systemic issue rather than isolated errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import Finding, cpt_family


@dataclass
class Cluster:
    carc: str
    rarc: str
    family: str
    billed: float | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.findings)

    @property
    def claims(self) -> list[str]:
        return sorted({f.line.claim for f in self.findings})

    @property
    def total_variance(self) -> float:
        return round(sum(f.line.variance or 0 for f in self.findings), 2)

    @property
    def systemic(self) -> bool:
        return len(self.claims) > 1


def cluster_findings(findings: list[Finding], rules: dict) -> list[Cluster]:
    buckets: dict[tuple, Cluster] = {}
    for f in findings:
        if f.suppressed:
            continue
        key = (f.line.carc, f.line.rarc, cpt_family(f.line.cpt, rules), f.line.billed)
        if key not in buckets:
            buckets[key] = Cluster(carc=key[0], rarc=key[1], family=key[2], billed=key[3])
        buckets[key].findings.append(f)
    clusters = sorted(buckets.values(), key=lambda c: -c.total_variance)
    min_size = rules.get("clustering", {}).get("systemic_min_size", 2)
    for c in clusters:
        if c.size >= min_size and c.systemic:
            note = (
                f"Systemic pattern: {c.size} lines across {len(c.claims)} claims, "
                f"same CARC/RARC/CPT family and identical billed amount "
                f"(${c.billed:.2f}) — one chargemaster item denied repeatedly."
                if c.billed is not None else
                f"Systemic pattern: {c.size} lines across {len(c.claims)} claims."
            )
            for f in c.findings:
                if note not in f.systemic_flags:
                    f.systemic_flags.append(note)
    return clusters
