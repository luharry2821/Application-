# Oaklawn Recovery Model

Recovery-prioritization model built from `Oaklawn_Radiology_Ops_Review_2026-09-14.xlsm`
(BCBSM underpayment & denial review, single-remittance, PHI-minimized).

## Files

- `data.json` — normalized data model extracted from the workbook: 51 review claims /
  57 candidate lines, queue summary, CARC and service-month aggregates, and the
  half-rate (exact 21.27%) sample summary.
- `index.html` — self-contained interactive dashboard (data embedded). Open directly
  in a browser; no server needed.

## The model

Each candidate line gets a **scored recovery** value:

```
scoredRecovery = grossVariance × confidence

confidence = 0.45 (same-claim contract-rate control present)
           + 0.10 (≥5 control lines on the claim)
           + 0.30 (validated commercial-primary payer evidence;
                   −0.10 if source lineage is under investigation)
           + 0.10 (original remit, not a correction)
           + 0.05 (post-transition published 42.54% rate vs inferred pre-April 41.25%)
           clamped to [0.05, 0.95]
```

The worklist ranks lines by scored recovery and opens filtered to **P1 / CO-97**
(zero-allowed bundling denials with same-claim rate anchors), the lane review
started with. Confidence weights are heuristic triage inputs, not payer
determinations.

## P1 · CO-97 starting worklist

| Claim | Month | CPT | Variance | Note |
|---|---|---|---|---|
| 12669164 | 2026-08 | 75716 | $1,969.18 | Diagnostic angiography likely bundled into same-session intervention |
| 12631753 | 2026-06 | 71045 | $151.87 | Chest X-ray incidental to primary procedure (CO-97/N19) |
| 12631759 | 2026-06 | 71045 | $151.87 | Chest X-ray incidental to primary procedure (CO-97/N19) |
| 12600627 | 2026-04 | 71045 | $151.87 | Chest X-ray incidental to primary procedure (CO-97/N19) |

CO-97 playbook: check the NCCI PTP edit pair against the primary procedure; if the
study was distinct and separately documented, corrected claim with modifier 59/XU,
otherwise write off as a valid edit.

The half-rate exposure ($40,880 if the base rate applies to the 75 exact-21.27%
lines) is tracked separately — it is a suspected contract-level imaging carve-out
and should be confirmed at the contract level before individual appeals.
