"""资金计算：阶梯贡献款、素材保底、留置、预算上限与付款回查。

金额单位为元（整数，合同资料保留到分的约定在本合成数据中均为整数元）。
留置份额按合同 holdback 四舍五入到元；同一笔贡献拆成“可付现金”和
“留置”两部分，避免把有凭据的部分全部压到争议结束。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .scenarios import Scenario
from .sources import SourceView


def tier_gross(contract: dict, count: int) -> int:
    """按数量累进计价：upto 为本档最高计数，rate 为本档单条金额。"""
    if count <= 0:
        return 0
    total = 0
    floor = 0
    for tier in contract["tiers"]:
        take = max(0, min(count, tier["upto"]) - floor)
        total += take * tier["rate"]
        floor = tier["upto"]
    return total


def split_holdback(contract: dict, gross: int) -> tuple[int, int]:
    """返回 (留置, 可付现金)；留置等争议解除且取消窗口结束才可释放。"""
    if gross <= 0:
        return 0, 0
    holdback = int(
        (Decimal(gross) * Decimal(str(contract["holdback"]))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return holdback, gross - holdback


def _appointment_ids(view: SourceView) -> set[str]:
    return view.appointment_ids()


def guarantee_positions(view: SourceView, paid_rows: tuple[dict, ...] | None = None) -> list[dict]:
    """保底是独立的素材交付款：有交付证据且不超金额上限才支付。"""
    paid_rows = view.paid if paid_rows is None else paid_rows
    appt_ids = _appointment_ids(view)
    positions = []
    for contract in view.contracts:
        material_paid = sum(
            row["amount"]
            for row in paid_rows
            if row["contract"] == contract["contract"]
            and row["evidence_ref"] not in appt_ids
        )
        evidence = [
            row["reference"]
            for row in paid_rows
            if row["contract"] == contract["contract"] and row["evidence_ref"] not in appt_ids
        ]
        guarantee = contract["guarantee"]
        positions.append(
            {
                "contract": contract["contract"],
                "channel": contract["channel"],
                "guarantee_amount": guarantee,
                "material_paid": material_paid,
                "evidence_refs": evidence,
                "payable_now": max(0, guarantee - material_paid) if evidence else 0,
                "contingent": max(0, guarantee - material_paid) if not evidence else 0,
                "status": "已凭素材交付支付"
                if evidence and material_paid >= guarantee
                else "已部分支付"
                if evidence
                else "缺素材交付凭据，暂不可付",
            }
        )
    return positions


def price_scenarios(view: SourceView, scenarios: list[Scenario]) -> list[dict]:
    priced = []
    for scenario in scenarios:
        per_contract = {}
        for contract in view.contracts:
            cid = contract["contract"]
            count = scenario.counts.get(cid, 0)
            gross = tier_gross(contract, count)
            holdback, cash = split_holdback(contract, gross)
            per_contract[cid] = {
                "count": count,
                "gross": gross,
                "holdback": holdback,
                "cash_now": cash,
            }
        priced.append(
            {
                "scenario": scenario.name,
                "awards": [a.__dict__ for a in scenario.awards],
                "per_contract": per_contract,
                "total_gross": sum(p["gross"] for p in per_contract.values()),
                "total_holdback": sum(p["holdback"] for p in per_contract.values()),
                "total_cash_now": sum(p["cash_now"] for p in per_contract.values()),
            }
        )
    return priced


def _paid_by_contract(rows: tuple[dict, ...]) -> dict[str, int]:
    used: dict[str, int] = {}
    for row in rows:
        used[row["contract"]] = used.get(row["contract"], 0) + row["amount"]
    return used


def budget_positions(view: SourceView, priced: list[dict], paid_rows: tuple[dict, ...]) -> dict:
    """预算占用按给定付款行计算。

    历史 as-of 重建只传期间内付款（期间外付款在当时尚未发生）；
    当前视图传入全部付款事实。已付款本身永不删改，回查见 paid_trace。
    """
    paid_used = _paid_by_contract(paid_rows)
    total_paid = sum(paid_used.values())
    portfolio_cap = view.budget["portfolio_cap"]

    contracts = {}
    for contract in view.contracts:
        cid = contract["contract"]
        scenario_gross = [p["per_contract"][cid]["gross"] for p in priced]
        cumulative = [paid_used.get(cid, 0) + gross for gross in scenario_gross]
        contracts[cid] = {
            "channel": contract["channel"],
            "period_cap": contract["period_cap"],
            "cap_through": contract["cap_through"],
            "paid_encumbered": paid_used.get(cid, 0),
            "scenario_cumulative": dict(zip([p["scenario"] for p in priced], cumulative)),
            "headroom_min": contract["period_cap"] - max(cumulative),
            "breached_scenarios": [
                p["scenario"] for p in priced if paid_used.get(cid, 0) + p["per_contract"][cid]["gross"] > contract["period_cap"]
            ],
        }

    portfolio_cumulative = [total_paid + p["total_gross"] for p in priced]
    portfolio = {
        "scope": view.budget["scope"],
        "portfolio_cap": portfolio_cap,
        "paid_encumbered": total_paid,
        "scenario_cumulative": dict(zip([p["scenario"] for p in priced], portfolio_cumulative)),
        "headroom_min": portfolio_cap - max(portfolio_cumulative),
        "breached_scenarios": [
            p["scenario"] for p in priced if total_paid + p["total_gross"] > portfolio_cap
        ],
    }
    return {"contracts": contracts, "portfolio": portfolio}


def firm_obligation_gap(obligations: list[dict], headroom: int) -> dict:
    """已明确应付款超过可用预算时报告缺口；全部债务并列，不按顺序抹账。"""
    total = sum(item["amount"] for item in obligations)
    return {
        "obligations": obligations,
        "total_firm": total,
        "headroom": headroom,
        "shortfall": max(0, total - headroom),
        "all_debts_listed": True,
    }


def paid_trace(view: SourceView) -> list[dict]:
    """从已付款回查：占过哪段预算、凭据是否仍支持、差额与追调还缺什么。"""
    appt_ids = _appointment_ids(view)
    trace = []
    for row in view.paid:
        ref = row["evidence_ref"]
        entry = {
            "reference": row["reference"],
            "contract": row["contract"],
            "period": row["period"],
            "amount": row["amount"],
            "state": row["state"],
            "delivery": row["delivery"],
            "evidence_ref": ref,
            "kind": "转化贡献款" if ref in appt_ids else "素材交付款",
            "immutable": True,
        }
        if ref in appt_ids:
            appt = view.appointment(ref)
            entry["appointment_state_now"] = appt["state"]
            if appt["state"] != "有效":
                entry["support"] = "凭据不再支持：预约已撤销/被拒"
                if row["state"] == "已结":
                    entry["follow_up"] = "历史付款不删改，列入追调范围；预算占用在追调收回前不恢复"
                    entry["recovery_candidate"] = row["amount"]
                    entry["recovery_requirements"] = [
                        "撤销/被拒确认文书归档并与原预约核对一致",
                        "追调审批单及供应商确认（或约定抵扣下次请款）",
                        "收回入账后该段共享预算才恢复",
                    ]
                else:
                    entry["follow_up"] = "留置不得释放，留作争议准备；已释放的留置不能当作没放出去"
                    entry["release_blocked"] = row["amount"]
                    entry["release_requirements"] = [
                        "依据恢复（预约重新有效或新的可独立兑现确认关联）",
                        "取消窗口已结束且归属争议解除",
                        "若此前已释放，不可回冲，只能按追调流程处理",
                    ]
            else:
                entry["support"] = "预约仍有效，按当前归属场景复核"
        else:
            entry["support"] = "素材交付凭据存在，支持保底付款"
        trace.append(entry)
    return trace


def cashier(view: SourceView, priced: list[dict], trace: list[dict], guarantees: list[dict] | None = None) -> dict:
    """出纳三档：明确可付的素材款 / 至少应付的贡献款下限 / 争议准备。"""
    guarantees = guarantee_positions(view) if guarantees is None else guarantees
    material_payable = [
        {"contract": g["contract"], "amount": g["payable_now"], "reason": g["status"]}
        for g in guarantees
        if g["payable_now"] > 0
    ]

    robust_minimum = []
    for contract in view.contracts:
        cid = contract["contract"]
        min_cash = min(p["per_contract"][cid]["cash_now"] for p in priced)
        min_gross = min(p["per_contract"][cid]["gross"] for p in priced)
        robust_minimum.append(
            {
                "contract": cid,
                "channel": contract["channel"],
                "min_gross_across_scenarios": min_gross,
                "min_cash_across_scenarios": min_cash,
                "note": "任何可辩护归属下都至少应付；为 0 表示全部贡献价值仍在争议区，不得预支",
            }
        )

    holdback_reserves = [
        {
            "scenario": p["scenario"],
            "per_contract": {cid: p["per_contract"][cid]["holdback"] for cid in p["per_contract"]},
            "total": p["total_holdback"],
            "release_condition": "归属争议解除且取消窗口结束",
        }
        for p in priced
    ]

    blocked_holds = [
        {"reference": t["reference"], "contract": t["contract"], "amount": t["release_blocked"], "reason": t["follow_up"]}
        for t in trace
        if "release_blocked" in t
    ]
    recovery = [
        {"reference": t["reference"], "contract": t["contract"], "amount": t["recovery_candidate"], "reason": t["follow_up"]}
        for t in trace
        if "recovery_candidate" in t
    ]

    return {
        "material_payable_now": material_payable,
        "robust_contribution_minimum": robust_minimum,
        "holdback_reserves_by_scenario": holdback_reserves,
        "blocked_holds": blocked_holds,
        "recovery_candidates": recovery,
        "contingent_guarantees": [
            {"contract": g["contract"], "amount": g["contingent"], "reason": g["status"]}
            for g in guarantees
            if g["contingent"] > 0
        ],
    }