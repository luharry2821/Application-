"""Required coverage per spec:
1. a PR line that must not enter the recovery figure
2. a bilateral code denied against a unilateral intervention
3. a CO-45 paid line that must not be flagged
4. a control line with trivial dollars that must trigger the rounding warning
5. a claim with no same-day data that must output a hypothesis, not a verdict
Plus the PHI guard and control-deviation flag.
"""

import unittest

from denial_triage import (
    Line, SameDayLine, Extract, PHIError,
    load_rules, evaluate_line,
)
from denial_triage.loader import phi_check_headers, phi_check_values

RULES = load_rules()


def line(**kw):
    base = dict(
        claim="90000001", month="2026-06", cpt="71045", modifier="none",
        billed=357.0, allowed=0.0, paid=0.0, expected_pct=42.54,
        expected_allowed=151.87, variance=151.87, line_status="denied",
        carc="CO-97", rarc="N19", control=True, control_count=3.0,
        control_pct=42.54, control_basis=1200.0,
    )
    base.update(kw)
    return Line(**base)


class TestPRExclusion(unittest.TestCase):
    def test_pr_line_never_enters_recovery(self):
        f = evaluate_line(line(carc="PR-204", rarc=""), None, RULES)
        self.assertEqual(f.verdict, "patient or COB issue")
        self.assertFalse(f.payer_recovery_eligible)
        self.assertEqual(f.expected_recovery_low, 0.0)
        self.assertEqual(f.expected_recovery_high, 0.0)

    def test_mixed_pr_line_not_recovery_eligible_unsplit(self):
        f = evaluate_line(line(carc="PR-204; CO-45", rarc=""), None, RULES)
        self.assertFalse(f.payer_recovery_eligible)


class TestBilateralVsUnilateral(unittest.TestCase):
    def test_partial_recovery_and_overcoding_warning(self):
        # Bilateral extremity angiography (75716) denied CO-97/N19 beside a
        # unilateral lower-extremity revascularization (37254).
        l = line(cpt="75716", billed=4629.0, expected_allowed=1969.18,
                 variance=1969.18)
        ex = Extract(claim=l.claim, lines=[
            SameDayLine(cpt="75716", billed=4629.0, allowed=0.0),
            SameDayLine(cpt="37254", billed=21000.0, allowed=8933.4, paid=8933.4),
            SameDayLine(cpt="71045", billed=357.0, allowed=151.87, paid=121.5),
        ])
        f = evaluate_line(l, ex, RULES)
        self.assertEqual(f.verdict, "appealable denial")
        self.assertTrue(f.same_day_data)
        self.assertTrue(any("Partial recovery" in g.text for g in f.grounds),
                        "expected a partial-recovery ground")
        self.assertTrue(any("Overcoding risk" in w for w in f.warnings),
                        "expected an overcoding risk warning")
        # payer paid another diagnostic on the claim -> consistency ground
        self.assertTrue(any("territory test" in g.text for g in f.grounds))

    def test_territory_mismatch_is_appealable(self):
        # Chest X-ray denied beside a lower-extremity intervention: territories differ.
        l = line(cpt="71045")
        ex = Extract(claim=l.claim, lines=[
            SameDayLine(cpt="71045", billed=357.0, allowed=0.0),
            SameDayLine(cpt="37254", billed=21000.0, allowed=8933.4),
        ])
        f = evaluate_line(l, ex, RULES)
        self.assertEqual(f.verdict, "appealable denial")
        self.assertEqual(f.label, "verified")
        self.assertTrue(any("does not match" in g.text for g in f.grounds))


class TestCO45(unittest.TestCase):
    def test_paid_at_contract_rate_not_flagged(self):
        # allowed = 42.54% of billed exactly: the normal contractual write-off.
        f = evaluate_line(
            line(carc="CO-45", rarc="", allowed=151.87, paid=121.50,
                 variance=0.0, line_status="paid"),
            None, RULES)
        self.assertTrue(f.suppressed, "CO-45 at the contract rate must not be flagged")

    def test_paid_minus_allowed_is_not_underpayment(self):
        # Same line: paid < allowed (deductible/coinsurance). Still suppressed —
        # the engine measures allowed, never paid.
        f = evaluate_line(
            line(carc="CO-45", rarc="", allowed=151.87, paid=0.0,
                 variance=0.0, line_status="paid"),
            None, RULES)
        self.assertTrue(f.suppressed)

    def test_below_contract_rate_is_flagged(self):
        f = evaluate_line(
            line(carc="CO-45", rarc="", allowed=100.0, variance=51.87,
                 line_status="paid"),
            None, RULES)
        self.assertFalse(f.suppressed)
        self.assertEqual(f.verdict, "appealable denial")
        self.assertTrue(f.payer_recovery_eligible)


class TestControlEvidence(unittest.TestCase):
    def test_trivial_control_dollars_trigger_rounding_warning(self):
        f = evaluate_line(line(control_basis=18.30), None, RULES)
        self.assertTrue(any("rounding noise" in w for w in f.warnings),
                        "trivial control dollars must trigger the rounding warning")

    def test_control_deviation_flagged(self):
        f = evaluate_line(line(control_pct=42.60), None, RULES)
        self.assertTrue(any("deviates" in w for w in f.warnings))

    def test_clean_control_no_warning(self):
        f = evaluate_line(line(), None, RULES)
        self.assertFalse(any("rounding noise" in w or "deviates" in w
                             for w in f.warnings))


class TestSameDayDataRequired(unittest.TestCase):
    def test_bundling_without_same_day_data_is_hypothesis(self):
        f = evaluate_line(line(), None, RULES)  # CO-97/N19, no extract
        self.assertEqual(f.verdict, "unresolved")
        self.assertEqual(f.label, "hypothesis")
        self.assertFalse(f.same_day_data)
        self.assertTrue(any("same-day" in n.lower() or "same day" in n.lower()
                            for n in f.needs),
                        "must name the same-day line list as required evidence")

    def test_co231_n20_also_requires_same_day_data(self):
        f = evaluate_line(line(carc="CO-231", rarc="N20"), None, RULES)
        self.assertEqual(f.verdict, "unresolved")
        self.assertEqual(f.label, "hypothesis")


class TestAssumedPreAprilRate(unittest.TestCase):
    def test_pre_april_recovery_finding_is_hypothesis(self):
        f = evaluate_line(
            line(month="2025-11", carc="CO-45", rarc="", allowed=100.0,
                 variance=47.0, line_status="paid", expected_pct=41.25),
            None, RULES)
        self.assertEqual(f.label, "hypothesis")
        self.assertTrue(any("assumption" in r for r in f.risks))


class TestRetiredAndNewCodes(unittest.TestCase):
    def test_new_code_systemic_flag(self):
        l = line(cpt="75716")
        ex = Extract(claim=l.claim, lines=[
            SameDayLine(cpt="75716", allowed=0.0),
            SameDayLine(cpt="37254", allowed=8933.4),
        ])
        f = evaluate_line(l, ex, RULES)
        self.assertTrue(any("37254" in s for s in f.systemic_flags))

    def test_retired_code_warning(self):
        l = line(cpt="75716", month="2026-06")
        ex = Extract(claim=l.claim, lines=[
            SameDayLine(cpt="75716", allowed=0.0),
            SameDayLine(cpt="37225", allowed=5000.0),  # retired 2026-01
        ])
        f = evaluate_line(l, ex, RULES)
        self.assertTrue(any("retired" in w for w in f.warnings))


class TestPHIGuard(unittest.TestCase):
    def test_phi_header_rejected(self):
        with self.assertRaises(PHIError):
            phi_check_headers(["Claim number", "Patient Name"], RULES)
        with self.assertRaises(PHIError):
            phi_check_headers(["Member ID"], RULES)

    def test_exact_date_of_service_rejected(self):
        with self.assertRaises(PHIError):
            phi_check_values(["12600627", "2026-04-12"], RULES)

    def test_service_month_accepted(self):
        phi_check_values(["12600627", "2026-04", 357.0], RULES)  # no raise


if __name__ == "__main__":
    unittest.main()
