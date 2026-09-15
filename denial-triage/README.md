# Denial triage engine — Oaklawn radiology (BCBSM commercial)

Scores every denied radiology line for appealability and outputs the grounds,
not just a category. Rules are data (`denial_triage/rules.yaml`); the engine
only interprets them, so a CARC rule, CPT, or bundling relationship is added
by editing YAML, not code.

## Inferred workbook schema — confirm this mapping first

Workbook: `Oaklawn_Radiology_Ops_Review_2026-09-14.xlsm`, five tabs.

| Tab | Shape | Role |
|---|---|---|
| Ops Summary | header + queue table | totals: 51 claims / 57 candidate lines / $29,746.85 gross variance; rate 42.54% post-Apr, 41.25% inferred pre-Apr |
| Claim Review | 51 rows, header on sheet row 5 | one row per claim |
| **Line Detail** | 57 rows, header on sheet row 5 | **one row per radiology line — the engine's input** |
| Half-rate Samples | 75 rows | exact-21.27% lines; contract-level track, not triaged here |
| Field Guide | definitions | operational definitions and cautions |

Line Detail columns as found (left) → canonical fields (right). This mapping
is the `field_map` block at the top of `rules.yaml` — correct any header there
and rerun; no code changes.

| Workbook header | Canonical | Notes |
|---|---|---|
| Claim number | claim | |
| Service month | month | string `YYYY-MM`; exact dates are rejected by the PHI guard |
| Rate phase | rate_phase | pre-/post-transition |
| CPT/HCPCS | cpt | |
| Modifier | modifier | literal `none` when absent |
| Revenue code | revenue_code | |
| Units / Billed / Allowed / Paid | units, billed, allowed, paid | allowed is measured; paid never is |
| Expected % / Expected allowed / Gross variance | expected_pct, expected_allowed, variance | variance = expected − actual allowed |
| Line status | line_status | denied / paid |
| CARC code(s), CARC adjustment amount(s), CARC description(s) | carc, carc_amounts, carc_desc | `;`-separated when multiple |
| RARC code(s), RARC description(s) | rarc, rarc_desc | |
| Same-claim rate control / Control line count / Control realized % | control, control_count, control_pct | evidence strength, not a selector |
| Other allowed on claim | control_basis | **proxy** for control-line dollars — the workbook has no per-control-line billed; the trivial-dollars rounding test runs on this. Flag if you have a better column. |
| Claim total charge / allowed / paid | claim_charge, claim_allowed, claim_paid | |
| Prior likely cause | prior_cause | carried through as context |
| Remit transaction type / Payer validation | remit_type, payer_validation | original vs correction; lineage status |

Columns present but unmapped (payer names/IDs, type of bill, filing indicator,
assignee, source counts) are ignored by the engine; add them to `field_map`
if a rule needs them.

## Run

```bash
python3 -m denial_triage --workbook ops_review.xlsm --out output
python3 -m denial_triage --workbook ops_review.xlsm --extracts extracts/ --out output
python3 -m unittest discover -s tests        # test suite
```

Outputs: `output/lines.csv` (per-line), `output/clusters.csv` (cluster
summary), `output/findings.md` (report grouped by root cause, ordered by
dollars).

## Same-day extracts (authoritative view)

The workbook holds only radiology candidate lines and cannot answer same-day
bundling questions. CO-97/N19 and CO-231/N20 lines therefore output a
**hypothesis** with the evidence required, never a verdict, until a full
same-day line list is supplied: one JSON file per claim in the extracts
directory, named `<claim>.json`:

```json
{
  "claim": "12669164",
  "lines": [
    {"cpt": "75716", "modifier": "", "billed": 4629.0, "allowed": 0.0, "paid": 0.0},
    {"cpt": "37254", "modifier": "", "billed": 21000.0, "allowed": 8933.4, "paid": 8933.4}
  ]
}
```

With an extract present the territory & laterality test runs:
1. does the denied imaging territory match the intervention's territory
   (treated territory bundles; outside it generally does not);
2. does laterality match (a unilateral intervention cannot fully bundle a
   bilateral study — partial recovery, plus an overcoding-risk warning);
3. was any other diagnostic imaging line paid (the payer is applying a
   territory test and can be held to it consistently).

## Guarantees encoded

- Allowed is measured, never paid — paid-minus-allowed is patient liability.
- PR dollars never enter a payer-recovery figure; OA is COB-conditional.
- CO-45 at the contract rate is suppressed; below it, it's an underpayment.
- Control realized % deviating > 0.01pp from expected is flagged; trivial
  control dollars trigger a rounding-noise warning.
- The pre-April 41.25% rate is an assumption: findings that depend on it are
  downgraded to hypothesis with the risk stated.
- 37220–37235 retired 2026-01-01 (replaced by 37254–37299); new-code denials
  are surfaced as a systemic pattern, not just line items.
- Clusters: CARC + RARC + CPT family + identical billed amount across claims
  = one chargemaster item denied repeatedly.
- Confidence is a band, stated as directional judgment from general denial
  patterns — not measured payer overturn rates.
- Verified vs hypothesis is never blurred: verified means confirmed against
  data in hand or a cited source.
- No fabricated citations: NCCI/policy checks are named as required evidence
  with the source to consult (CMS NCCI PTP tables), never asserted.
- PHI guard: input with patient names, DOB, member IDs, MRNs, or exact dates
  of service is rejected before processing.
