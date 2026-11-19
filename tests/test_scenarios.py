import context  # noqa: F401  配置 sys.path

"""可辩护场景枚举、排他兑付一次、条数上下限与不确定性登记。"""

import unittest

from attribution.rules import evaluate_all
from attribution.scenarios import (
    build_uncertainty_register,
    contract_envelopes,
    enumerate_scenarios,
    run_attribution,
)
from tests.fixtures import appointment, contract, link, make_view, touch

C1 = contract("C1", "渠道甲", max_days=7)
C2 = contract("C2", "渠道乙", max_days=7)


class ScenarioTests(unittest.TestCase):
    def test_exclusive_choice_pays_only_once(self):
        view = make_view(
            "2026-10-30",
            touches=[touch("T1", "渠道甲"), touch("T2", "渠道乙")],
            appointments=[appointment("A1", date="2026-10-15")],
            links=[
                link("L1", "T1", "A1", "确认", "P1"),
                link("L2", "T2", "A1", "确认", "P1"),  # 同一人 P1 两份回执
            ],
            contracts=[C1, C2],
        )
        verdicts = evaluate_all(view)
        scenarios = enumerate_scenarios(verdicts)
        self.assertEqual(len(scenarios), 2)
        for scenario in scenarios:
            self.assertEqual(len(scenario.awards), 1)   # 一笔转化只兑付一份
            self.assertEqual(sum(scenario.counts.values()), 1)
        contracts_used = {s.awards[0].contract for s in scenarios}
        self.assertEqual(contracts_used, {"C1", "C2"})

        env = contract_envelopes(scenarios, view.contracts)
        self.assertEqual((env["C1"]["min_count"], env["C1"]["max_count"]), (0, 1))
        self.assertEqual((env["C2"]["min_count"], env["C2"]["max_count"]), (0, 1))
        self.assertEqual((env["__joint__"]["min_count"], env["__joint__"]["max_count"]), (1, 1))

        register = build_uncertainty_register(view, verdicts)
        disputed = next(e for e in register if e.kind == "disputed")
        self.assertEqual(disputed.possible_contracts, ["C1", "C2"])
        self.assertTrue(disputed.narrowing)

    def test_two_independent_appointments_cartesian(self):
        view = make_view(
            "2026-10-30",
            touches=[touch("T1", "渠道甲"), touch("T2", "渠道乙"),
                     touch("T3", "渠道甲"), touch("T4", "渠道乙")],
            appointments=[
                appointment("A1", date="2026-10-15", cancel_until="2026-10-16"),
                appointment("A2", date="2026-10-15", cancel_until="2026-10-16"),
            ],
            links=[
                link("L1", "T1", "A1", "确认", "P1"), link("L2", "T2", "A1", "确认", "P1"),
                link("L3", "T3", "A2", "确认", "P3"), link("L4", "T4", "A2", "确认", "P4"),
            ],
            contracts=[C1, C2],
        )
        scenarios = enumerate_scenarios(evaluate_all(view))
        self.assertEqual(len(scenarios), 4)
        for scenario in scenarios:
            self.assertEqual(len(scenario.awards), 2)

    def test_unconfirmed_and_summary_bound_never_awarded(self):
        view = make_view(
            "2026-10-30",
            touches=[touch("T1", "渠道甲", kind="汇总", count=2)],
            appointments=[appointment("A1")],
            links=[link("L1", "T1", "A1", "候选")],
            contracts=[C1],
        )
        result = run_attribution(view)
        self.assertEqual(len(result["scenarios"]), 1)
        self.assertEqual(result["scenarios"][0].awards, ())
        entry = next(e for e in result["register"] if e.kind == "unconfirmed")
        self.assertEqual(entry.detail["bound_edges"], ["L1"])
        self.assertEqual(entry.capacity_bounds[0]["upper_bound_people"], 2)

    def test_pending_window_and_revoked_not_in_scenarios(self):
        view = make_view(
            "2026-10-17",
            touches=[touch("T1", "渠道甲"), touch("T2", "渠道乙")],
            appointments=[
                appointment("A1", cancel_until="2026-10-18"),
                appointment("A2", state="撤销", recorded="2026-10-16", cancel_until="2026-10-16"),
            ],
            links=[link("L1", "T1", "A1", "确认", "P1"), link("L2", "T2", "A2", "确认", "P2")],
            contracts=[C1, C2],
        )
        result = run_attribution(view)
        self.assertEqual(result["scenarios"][0].awards, ())
        kinds = {e.appointment: e.kind for e in result["register"]}
        self.assertEqual(kinds["A1"], "pending_window")
        self.assertEqual(kinds["A2"], "revoked")


if __name__ == "__main__":
    unittest.main()
