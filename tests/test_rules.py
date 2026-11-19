import context  # noqa: F401  配置 sys.path

"""窗口、地区、证据等级、同人合并与自然入口排除规则。"""

import unittest

from attribution.rules import evaluate
from tests.fixtures import appointment, contract, link, make_view, touch

C1 = contract("C1", "渠道甲", max_days=7)
C2 = contract("C2", "渠道乙", max_days=3)


class RuleTests(unittest.TestCase):
    def test_window_and_area(self):
        view = make_view(
            "2026-10-30",
            touches=[
                touch("T1", "渠道甲", "甲区", "2026-10-10"),       # 差5天，窗内
                touch("T2", "渠道乙", "甲区", "2026-10-10"),       # 差5天，C2窗3天外
                touch("T3", "渠道甲", "乙区", "2026-10-12"),       # 地区不符
            ],
            appointments=[appointment("A1", date="2026-10-15", cancel_until="2026-10-16")],
            links=[
                link("L1", "T1", "A1", "确认", "P1"),
                link("L2", "T2", "A1", "确认", "P2"),
                link("L3", "T3", "A1", "确认", "P3"),
            ],
            contracts=[C1, C2],
        )
        verdict = evaluate(view, "A1")
        self.assertTrue(verdict.confirmable)
        levels = {o.edge: o.evidence_level for o in verdict.options}
        self.assertEqual(levels["L1"], "confirmed")
        self.assertEqual(levels["L2"], "blocked")   # 超窗
        self.assertEqual(levels["L3"], "blocked")   # 跨地区
        self.assertEqual(verdict.same_person_groups, {"P1": ["L1"], "P2": ["L2"], "P3": ["L3"]})

    def test_summary_touch_is_bound_only_and_candidate_never_confirms(self):
        view = make_view(
            "2026-10-30",
            touches=[
                touch("T1", "渠道甲", kind="汇总", count=3),
                touch("T2", "渠道甲", kind="独立"),
            ],
            appointments=[appointment("A1")],
            links=[link("L1", "T1", "A1", "候选"), link("L2", "T2", "A1", "候选")],
            contracts=[C1],
        )
        verdict = evaluate(view, "A1")
        levels = {o.edge: o.evidence_level for o in verdict.options}
        self.assertEqual(levels["L1"], "bound_only")
        self.assertEqual(levels["L2"], "candidate")
        self.assertEqual(verdict.confirmed_options, [])

    def test_excluded_edge_does_not_participate(self):
        view = make_view(
            "2026-10-30",
            touches=[touch("T1", "渠道甲")],
            appointments=[appointment("A1")],
            links=[link("L1", "T1", "A1", "排除")],
            contracts=[C1],
        )
        verdict = evaluate(view, "A1")
        self.assertEqual(verdict.options, [])

    def test_cancel_window_inclusive_and_revoked(self):
        view = make_view(
            "2026-10-17",  # 等于 cancel_until，窗口含当日
            touches=[touch("T1", "渠道甲")],
            appointments=[
                appointment("A1", cancel_until="2026-10-17"),
                appointment("A2", state="撤销", recorded="2026-10-16", cancel_until="2026-10-16"),
            ],
            links=[link("L1", "T1", "A1", "确认", "P1")],
            contracts=[C1],
        )
        self.assertFalse(evaluate(view, "A1").confirmable)
        revoked = evaluate(view, "A2")
        self.assertFalse(revoked.confirmable)
        self.assertIn("撤销", revoked.confirm_reason)

    def test_natural_exclusion_rules(self):
        view = make_view(
            "2026-10-30",
            touches=[touch("T1", "渠道甲")],
            appointments=[
                appointment("A1", origin="自然入口"),
                appointment("A2", origin="自然入口"),
            ],
            links=[link("L1", "T1", "A1", "候选")],  # A1 有采购证据
            contracts=[C1],
        )
        a1 = evaluate(view, "A1")
        a2 = evaluate(view, "A2")
        self.assertTrue(a1.natural_excluded)          # 有采购证据：不能计确定自然
        self.assertFalse(a2.natural_excluded)         # 无渠道可归属：确定自然，排除付款
        self.assertIn("确定自然", a2.natural_reason)


if __name__ == "__main__":
    unittest.main()
