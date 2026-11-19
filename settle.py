#!/usr/bin/env python3
"""本地拟制账命令行入口（仅标准库；建议以 python3 -I settle.py 运行）。

子命令：
  run       生成/复用一次 as-of 运行快照并写 Markdown 报告
  proposals 列出提案事件流折叠后的当前状态
  propose   登记一笔尚未付款的结算提案（含归属依据与留置安排）
  approve   追加审批事件
  pay       追加支付事件（支付事实进入来源资料后仍以来源为准）
  rejudge   用新的 as-of 视图重判已审批提案（撤销/同人回执触发）
  trace     回查已付款与预算占用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attribution import ledger, report  # noqa: E402
from attribution.engine import build_run  # noqa: E402
from attribution.sources import load_view  # noqa: E402

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = str(ROOT / "data" / "supplies.json")
DEFAULT_LEDGER = str(ROOT / "ledger")


def cmd_run(args: argparse.Namespace) -> None:
    excluded = frozenset(args.exclude_paid or ())
    path, created = ledger.save_run(args.ledger, args.data, args.as_of, args.paid_through, excluded)
    run = ledger.load_run(args.ledger, args.as_of)
    report_path = ledger.write_report(args.ledger, f"report-{args.as_of}.md", report.render(run))
    print(f"{'新建' if created else '复用'}运行快照：{path}")
    print(f"报告：{report_path}")
    print(json.dumps({
        "as_of": run["as_of"],
        "scenarios": [s["scenario"] for s in run["scenarios"]],
        "portfolio_headroom_min": run["budget"]["portfolio"]["headroom_min"],
        "firm_shortfall": run["firm_obligation_gap"]["shortfall"],
    }, ensure_ascii=False, indent=2))


def cmd_proposals(args: argparse.Namespace) -> None:
    for proposal in ledger.list_proposals(args.ledger):
        print(json.dumps({k: v for k, v in proposal.items() if k != "history"}, ensure_ascii=False, indent=2))


def cmd_propose(args: argparse.Namespace) -> None:
    run = ledger.load_run(args.ledger, args.as_of)
    award = None
    for scenario in run["scenarios"]:
        for item in scenario["awards"]:
            if item["appointment"] == args.appointment and item["contract"] == args.contract:
                award = item
    if award is None:
        raise SystemExit("该预约→合约落点在任何可辩护场景中都不存在，拒绝登记提案（不得就近拼接关联）")
    amounts = next(
        p["per_contract"][args.contract]
        for p in run["scenarios"]
        if any(a["appointment"] == args.appointment and a["contract"] == args.contract for a in p["awards"])
    )
    proposal = ledger.propose(
        args.ledger,
        basis={
            "appointment": award["appointment"],
            "contract": award["contract"],
            "channel": award["channel"],
            "touch": award["touch"],
            "edge": award["edge"],
            "same_person": award["same_person"],
        },
        amounts=amounts,
        as_of=args.as_of,
        run_hash=run["run_hash"],
    )
    print(json.dumps({"proposal_id": proposal["proposal_id"], "status": proposal["status"]}, ensure_ascii=False))


def _simple_status(args: argparse.Namespace, event: str) -> None:
    proposal = getattr(ledger, event)(args.ledger, args.proposal_id, args.as_of)
    print(json.dumps({"proposal_id": proposal["proposal_id"], "status": proposal["status"]}, ensure_ascii=False))


def cmd_approve(args: argparse.Namespace) -> None:
    _simple_status(args, "approve")


def cmd_pay(args: argparse.Namespace) -> None:
    _simple_status(args, "mark_paid")


def cmd_rejudge(args: argparse.Namespace) -> None:
    view = load_view(args.data, args.as_of)
    new_run = build_run(view, args.paid_through, frozenset(args.exclude_paid or ()))
    proposal = ledger.rejudge(args.ledger, args.proposal_id, new_run, view)
    print(json.dumps(proposal.get("rejudgement", {}), ensure_ascii=False, indent=2))


def cmd_trace(args: argparse.Namespace) -> None:
    run = ledger.load_run(args.ledger, args.as_of)
    print(json.dumps(run["paid_trace"], ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地拟制账：咨询归因与采购结算")
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="生成运行快照与报告")
    p_run.add_argument("--as-of", required=True)
    p_run.add_argument(
        "--paid-through",
        help="视为已发生付款的截至日（默认=as-of；paid 无事件日期，历史重建按付款日历显式指定）",
    )
    p_run.add_argument(
        "--exclude-paid",
        nargs="*",
        default=[],
        help="预算重建时显式排除的付款 reference（付款事实仍保留在回查中，不删改）",
    )
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("proposals", help="列出提案")
    p_list.set_defaults(func=cmd_proposals)

    p_propose = sub.add_parser("propose", help="登记未付款提案")
    p_propose.add_argument("--as-of", required=True)
    p_propose.add_argument("--appointment", required=True)
    p_propose.add_argument("--contract", required=True)
    p_propose.set_defaults(func=cmd_propose)

    p_approve = sub.add_parser("approve", help="审批提案")
    p_approve.add_argument("--as-of", required=True)
    p_approve.add_argument("--proposal-id", required=True)
    p_approve.set_defaults(func=cmd_approve)

    p_pay = sub.add_parser("pay", help="标记支付")
    p_pay.add_argument("--as-of", required=True)
    p_pay.add_argument("--proposal-id", required=True)
    p_pay.set_defaults(func=cmd_pay)

    p_rejudge = sub.add_parser("rejudge", help="重判已审批提案")
    p_rejudge.add_argument("--as-of", required=True)
    p_rejudge.add_argument("--paid-through")
    p_rejudge.add_argument("--exclude-paid", nargs="*", default=[])
    p_rejudge.add_argument("--proposal-id", required=True)
    p_rejudge.set_defaults(func=cmd_rejudge)

    p_trace = sub.add_parser("trace", help="回查已付款")
    p_trace.add_argument("--as-of")
    p_trace.set_defaults(func=cmd_trace)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
