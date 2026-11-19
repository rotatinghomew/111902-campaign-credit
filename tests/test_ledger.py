import context  # noqa: F401  配置 sys.path

"""as-of 折叠、只追加台账、提案审批与两类重判（撤销 / 同人回执 / 预算未恢复）。"""

import json
import tempfile
import unittest
from pathlib import Path

from attribution import ledger
from attribution.engine import build_run
from attribution.sources import load_view

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "supplies.json"


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_root = Path(self.tmp.name) / "ledger"

    def tearDown(self):
        self.tmp.cleanup()

    def test_revision_folding_by_recorded(self):
        early = load_view(DATA, "2026-10-25")
        current = load_view(DATA, "2026-11-19")
        a2_early = early.appointment("A2")
        a2_now = current.appointment("A2")
        self.assertEqual(a2_early["state"], "有效")     # 撤销记录 11-02 尚未发生
        self.assertEqual(a2_now["state"], "撤销")
        l1_early = next(e for e in early.links if e["edge"] == "L1")
        self.assertEqual(l1_early["state"], "候选")      # 11-01 才确认
        self.assertEqual(next(e for e in current.links if e["edge"] == "L1")["state"], "确认")

    def test_snapshot_immutability_and_idempotent_rerun(self):
        path1, created1 = ledger.save_run(self.ledger_root, DATA, "2026-11-19")
        path2, created2 = ledger.save_run(self.ledger_root, DATA, "2026-11-19")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(path1, path2)
        run = json.loads(path1.read_text(encoding="utf-8"))
        self.assertEqual({s["scenario"] for s in run["scenarios"]}, {"S1", "S2"})

    def test_proposal_append_only_lifecycle_and_revocation_rejudge(self):
        ledger.save_run(self.ledger_root, DATA, "2026-10-25",
                        exclude_paid_refs=frozenset({"P01", "P02", "P03"}))
        proposal = ledger.propose(
            self.ledger_root,
            basis={"appointment": "A2", "contract": "C2", "channel": "图文号",
                   "touch": "T2", "edge": "L3", "same_person": "P2"},
            amounts={"count": 1, "gross": 180, "holdback": 54, "cash_now": 126},
            as_of="2026-10-25",
            run_hash=ledger.load_run(self.ledger_root, "2026-10-25")["run_hash"],
        )
        self.assertEqual(proposal["status"], "待审批")
        ledger.approve(self.ledger_root, "PR-001", "2026-10-26")

        new_run = build_run(load_view(DATA, "2026-11-19"))
        result = ledger.rejudge(self.ledger_root, "PR-001", new_run, load_view(DATA, "2026-11-19"))
        judgement = result["rejudgement"]
        self.assertFalse(judgement["still_holds"])
        self.assertIn("撤销", judgement["reasons"][0])
        self.assertIn("留置继续冻结", judgement["holdback_release"])

        events = [json.loads(line) for line in
                  (self.ledger_root / "proposals" / "proposals.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["propose", "approve", "rejudge"])
        # 原 propose 事件内容不被改写
        self.assertEqual(events[0]["amounts"]["gross"], 180)
        self.assertEqual(ledger.list_proposals(self.ledger_root)[0]["status"], "重判失效")

    def test_same_person_second_receipt_blocks_release(self):
        # 先以 10-25 视图提案 A2→C2（L3 确认 P2）
        ledger.save_run(self.ledger_root, DATA, "2026-10-25",
                        exclude_paid_refs=frozenset({"P01", "P02", "P03"}))
        ledger.propose(
            self.ledger_root,
            basis={"appointment": "A2", "contract": "C2", "channel": "图文号",
                   "touch": "T2", "edge": "L3", "same_person": "P2"},
            amounts={"count": 1, "gross": 180, "holdback": 54, "cash_now": 126},
            as_of="2026-10-25",
            run_hash=ledger.load_run(self.ledger_root, "2026-10-25")["run_hash"],
        )
        # 构造新视图：A2 仍有效，但出现同一人 P2 指向 C1 的第二份确认回执
        source = json.loads(Path(DATA).read_text(encoding="utf-8"))
        tables = dict(source["tables"])
        # 让 A2 保持有效：剔除 11-02 的撤销修订行
        tables["appointments"] = [r for r in tables["appointments"]
                                  if not (r["appointment"] == "A2" and r["state"] == "撤销")]
        tables["touches"] = tables["touches"] + [
            {"touch": "T5", "channel": "社区报", "area": "甲区", "date": "2026-10-17",
             "kind": "独立", "count": 1}
        ]
        tables["links"] = [e for e in tables["links"] if e["appointment"] != "A2" or e["edge"] != "L3"]
        tables["links"] = tables["links"] + [
            {"edge": "L3", "touch": "T2", "appointment": "A2", "state": "确认",
             "same_person": "P2", "recorded": "2026-10-20"},
            {"edge": "L5", "touch": "T5", "appointment": "A2", "state": "确认",
             "same_person": "P2", "recorded": "2026-11-05"},
        ]
        source["tables"] = tables
        new_data = Path(self.tmp.name) / "altered.json"
        new_data.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

        new_view = load_view(new_data, "2026-11-19")
        new_run = build_run(new_view)
        result = ledger.rejudge(self.ledger_root, "PR-001", new_run, new_view)
        judgement = result["rejudgement"]
        self.assertFalse(judgement["still_holds"])
        self.assertTrue(any("第二份确认回执" in r for r in judgement["reasons"]))

    def test_budget_not_restored_is_distinct_from_evidence_resolved(self):
        # 依据成立但共享预算为负：主管能分辨“证据争议解决”与“预算未恢复”
        ledger.save_run(self.ledger_root, DATA, "2026-10-25",
                        exclude_paid_refs=frozenset({"P01", "P02", "P03"}))
        ledger.propose(
            self.ledger_root,
            basis={"appointment": "A2", "contract": "C2", "channel": "图文号",
                   "touch": "T2", "edge": "L3", "same_person": "P2"},
            amounts={"count": 1, "gross": 180, "holdback": 54, "cash_now": 126},
            as_of="2026-10-25",
            run_hash=ledger.load_run(self.ledger_root, "2026-10-25")["run_hash"],
        )
        source = json.loads(Path(DATA).read_text(encoding="utf-8"))
        source["tables"]["settlement_budget"] = [{"scope": "宜居十月", "portfolio_cap": 10}]
        tight_data = Path(self.tmp.name) / "tight.json"
        tight_data.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        tight_view = load_view(tight_data, "2026-10-25")
        tight_run = build_run(tight_view, exclude_paid_refs=frozenset({"P01", "P02", "P03"}))
        result = ledger.rejudge(self.ledger_root, "PR-001", tight_run, tight_view)
        judgement = result["rejudgement"]
        self.assertTrue(judgement["still_holds"])
        self.assertTrue(judgement["evidence_resolved"])
        self.assertIn("共享预算未恢复", judgement["budget_status"])


if __name__ == "__main__":
    unittest.main()
