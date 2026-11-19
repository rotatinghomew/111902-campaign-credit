import context  # noqa: F401  配置 sys.path

"""阶梯计价、留置拆分、保底素材款、预算上限与缺口、付款回查。"""

import unittest

from attribution import money
from attribution.scenarios import Award, Scenario
from tests.fixtures import contract, make_view, paid_row

C1 = contract("C1", "渠道甲", guarantee=100, period_cap=300, holdback=0.25,
              tiers=[{"upto": 1, "rate": 170}, {"upto": 10, "rate": 220}])
C2 = contract("C2", "渠道乙", guarantee=90, period_cap=400, holdback=0.3,
              tiers=[{"upto": 1, "rate": 180}, {"upto": 10, "rate": 230}])


def scenario(name, c1_count=0, c2_count=0):
    awards = []
    counts = {}
    if c1_count:
        counts["C1"] = c1_count
        awards.append(Award("A1", "C1", "渠道甲", "T1", "L1", "P1"))
    if c2_count:
        counts["C2"] = c2_count
        awards.append(Award("A2", "C2", "渠道乙", "T2", "L2", "P2"))
    return Scenario(name=name, awards=tuple(awards), counts=counts)


class MoneyTests(unittest.TestCase):
    def test_progressive_tiers(self):
        self.assertEqual(money.tier_gross(C1, 0), 0)
        self.assertEqual(money.tier_gross(C1, 1), 170)
        self.assertEqual(money.tier_gross(C1, 3), 170 + 2 * 220)

    def test_holdback_split_half_up(self):
        holdback, cash = money.split_holdback(C2, 180)   # 180*0.3=54
        self.assertEqual((holdback, cash), (54, 126))
        holdback, cash = money.split_holdback(C1, 170)   # 170*0.25=42.5 → 43
        self.assertEqual((holdback, cash), (43, 127))
        self.assertEqual(money.split_holdback(C1, 0), (0, 0))

    def test_guarantee_requires_delivery_evidence(self):
        view = make_view("2026-11-30", contracts=[C1, C2], paid=[
            paid_row("P01", "C1", 100, evidence_ref="素材交付一"),
        ])
        positions = {g["contract"]: g for g in money.guarantee_positions(view)}
        self.assertEqual(positions["C1"]["payable_now"], 0)
        self.assertEqual(positions["C2"]["contingent"], 90)
        self.assertIn("缺素材交付凭据", positions["C2"]["status"])

    def test_budget_breach_and_firm_gap_reports_all_debts(self):
        view = make_view(
            "2026-11-30",
            contracts=[C1, C2],
            budget_cap=200,
            paid=[paid_row("P01", "C1", 100), paid_row("P02", "C2", 80)],
        )
        priced = money.price_scenarios(view, [scenario("S1", c2_count=1)])
        budget = money.budget_positions(view, priced, view.paid)
        # 已占 180，再加 180 → 360 > 200
        self.assertEqual(budget["portfolio"]["breached_scenarios"], ["S1"])
        gap = money.firm_obligation_gap(
            [{"contract": "C1", "amount": 60}, {"contract": "C2", "amount": 70}],
            headroom=100,
        )
        self.assertEqual(gap["shortfall"], 30)
        self.assertTrue(gap["all_debts_listed"])

    def test_paid_trace_immutable_and_recovery(self):
        view = make_view(
            "2026-11-30",
            appointments=[{"appointment": "A9", "origin": "服务台", "area": "甲区", "date": "2026-10-15",
                           "cancel_until": "2026-10-17", "state": "撤销", "recorded": "2026-11-01"}],
            contracts=[C1, C2],
            paid=[
                paid_row("P02", "C2", 120, evidence_ref="A9"),
                paid_row("P03", "C2", 54, state="留置", evidence_ref="A9"),
                paid_row("P01", "C1", 100, evidence_ref="素材交付一"),
            ],
        )
        trace = {t["reference"]: t for t in money.paid_trace(view)}
        self.assertTrue(all(t["immutable"] for t in trace.values()))
        self.assertEqual(trace["P02"]["recovery_candidate"], 120)
        self.assertTrue(trace["P02"]["recovery_requirements"])
        self.assertEqual(trace["P03"]["release_blocked"], 54)
        self.assertTrue(any("取消窗口" in r for r in trace["P03"]["release_requirements"]))
        self.assertNotIn("recovery_candidate", trace["P01"])


if __name__ == "__main__":
    unittest.main()
