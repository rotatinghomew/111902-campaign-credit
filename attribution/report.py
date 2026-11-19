"""把运行快照渲染为面向主管与出纳的中文 Markdown 台账报告。"""

from __future__ import annotations

_LEVEL_CN = {
    "confirmed": "确认（可独立兑现）",
    "candidate": "候选（仅可能集合）",
    "bound_only": "汇总上界（不能独立兑现）",
    "blocked": "窗口/地区否决",
}


def _yuan(value: int) -> str:
    return f"{value:,} 元"


def render(run: dict) -> str:
    lines: list[str] = []
    lines.append("# 咨询预约归因与采购结算台账（本地拟制）")
    lines.append("")
    lines.append(f"- 截止视图 as_of：**{run['as_of']}**（含当日；取消窗口含截止时刻，次日才可确认）")
    lines.append(f"- 预算口径付款截至 paid_through：**{run.get('paid_through', run['as_of'])}**；付款回查仍列全部历史事实")
    if run.get("paid_refs_excluded_for_budget"):
        lines.append(f"- 预算重建显式排除（当时尚未发生）：{run['paid_refs_excluded_for_budget']}；这些付款在回查表中仍完整保留")
    lines.append(f"- 资料摘要：`{run['source_hash'][:12]}`，运行摘要：`{run['run_hash'][:12]}`")
    lines.append("- 口径：合约约定的预约归属范围，不等于渠道因果增量；同一有效转化最多兑现一份合约贡献")
    lines.append("")

    lines.append("## 一、逐预约归属证据")
    lines.append("")
    lines.append("| 预约 | 来源 | 地区 | 日期 | 状态 | 可否确认 | 证据判定 |")
    lines.append("|---|---|---|---|---|---|---|")
    for appt in run["appointments"]:
        levels = "；".join(
            f"{o['contract']}/{o['channel']} {o['edge']}={_LEVEL_CN[o['evidence_level']]}"
            for o in appt["options"]
        ) or "无可用关联"
        lines.append(
            f"| {appt['appointment']} | {appt['origin']} | {appt['area']} | {appt['date']} | "
            f"{appt['state']} | {'是：' + appt['confirm_reason'] if appt['confirmable'] else '否：' + appt['confirm_reason']} | {levels} |"
        )
    lines.append("")
    for appt in run["appointments"]:
        if appt["same_person_groups"]:
            groups = "；".join(f"{person}：{', '.join(edges)}" for person, edges in appt["same_person_groups"].items())
            lines.append(f"- {appt['appointment']} 同人关联（{groups}）：同组排他，不能拼接成两份贡献")
        if appt["origin"] == "自然入口":
            if appt["natural_excluded_from_payment"]:
                natural_note = "不能计为确定自然，暂不能排除在付款之外"
            else:
                natural_note = "可计为确定自然，排除在付款之外"
            lines.append(f"- {appt['appointment']} 自然入口判定：{appt['natural_reason']}；结论：{natural_note}")
    lines.append("")

    lines.append("## 二、可辩护归属场景（每个场景每预约只兑付一次）")
    lines.append("")
    lines.append("| 场景 | 落点（预约→合约/触点/同人） | 各合约条数 | 贡献总额 | 可付现金 | 留置 |")
    lines.append("|---|---|---|---|---|---|")
    for scenario in run["scenarios"]:
        awards = "；".join(f"{a['appointment']}→{a['contract']}({a['touch']}" + (f"/{a['same_person']}" if a["same_person"] else "") + ")" for a in scenario["awards"]) or "无可确认落点"
        counts = "，".join(f"{cid}:{p['count']} 条" for cid, p in scenario["per_contract"].items())
        lines.append(
            f"| {scenario['scenario']} | {awards} | {counts} | {_yuan(scenario['total_gross'])} | "
            f"{_yuan(scenario['total_cash_now'])} | {_yuan(scenario['total_holdback'])} |"
        )
    lines.append("")
    lines.append("> 各合约分档明细（保底之外的转化阶梯，留置按合约比例拆分）：")
    for scenario in run["scenarios"]:
        for cid, p in scenario["per_contract"].items():
            lines.append(
                f"> - {scenario['scenario']} / {cid}：{p['count']} 条，毛额 {_yuan(p['gross'])}，"
                f"可付 {_yuan(p['cash_now'])}，留置 {_yuan(p['holdback'])}"
            )
    lines.append("")

    lines.append("## 三、不确定性登记（可能集合与合计边界）")
    lines.append("")
    if not run["uncertainty_register"]:
        lines.append("无。")
    for entry in run["uncertainty_register"]:
        lines.append(f"### {entry['appointment']} · {entry['kind']}")
        lines.append(f"- 情形：{entry['summary']}")
        lines.append(f"- 可能集合：{entry['possible_contracts'] or '无合约候选'}")
        for bound in entry["capacity_bounds"]:
            lines.append(
                f"- 容量上界：{bound['contract']}/{bound['channel']} {bound['area']} {bound['touch_date']} "
                f"≤ {bound['upper_bound_people']} 人（{bound['note']}）"
            )
        lines.append("- 缩小范围所需材料：")
        for item in entry["narrowing"]:
            lines.append(f"  - {item}")
    lines.append("")

    env = run["count_envelopes"]
    lines.append("### 条数上下限（各渠道上限不可同时兑现）")
    lines.append("")
    lines.append("| 对象 | 下限 | 上限 | 说明 |")
    lines.append("|---|---|---|---|")
    for cid, value in env.items():
        if cid == "__joint__":
            lines.append(f"| 全组合计边界 | {value['min_count']} | {value['max_count']} | {value['note']} |")
        else:
            lines.append(f"| {cid}（{value['channel']}） | {value['min_count']} | {value['max_count']} | {value['attainable_together']} |")
    lines.append("")

    lines.append("## 四、保底素材款")
    lines.append("")
    lines.append("| 合约 | 保底金额 | 已凭交付支付 | 当前可付 | 或有部分 | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for g in run["guarantees"]:
        lines.append(
            f"| {g['contract']}（{g['channel']}） | {_yuan(g['guarantee_amount'])} | {_yuan(g['material_paid'])} | "
            f"{_yuan(g['payable_now'])} | {_yuan(g['contingent'])} | {g['status']} |"
        )
    lines.append("")

    lines.append("## 五、预算占用（as-of 重建：只含该时点已发生期间的付款；已付款不删改，追调收回前预算不恢复）")
    lines.append("")
    budget = run["budget"]
    lines.append("| 合约 | 期间上限 | 已占（含留置） | 各场景累计 | 最小余量 | 超限场景 |")
    lines.append("|---|---|---|---|---|---|")
    for cid, b in budget["contracts"].items():
        cumulative = "，".join(f"{s}={_yuan(v)}" for s, v in b["scenario_cumulative"].items())
        lines.append(
            f"| {cid}（{b['channel']}） | {_yuan(b['period_cap'])}（至 {b['cap_through']}） | {_yuan(b['paid_encumbered'])} | "
            f"{cumulative} | {_yuan(b['headroom_min'])} | {b['breached_scenarios'] or '无'} |"
        )
    p = budget["portfolio"]
    pc = "，".join(f"{s}={_yuan(v)}" for s, v in p["scenario_cumulative"].items())
    lines.append(
        f"| 共享预算 {p['scope']} | {_yuan(p['portfolio_cap'])} | {_yuan(p['paid_encumbered'])} | {pc} | "
        f"{_yuan(p['headroom_min'])} | {p['breached_scenarios'] or '无'} |"
    )
    lines.append("")
    gap = run["firm_obligation_gap"]
    lines.append(
        f"- 已明确应付款合计 {_yuan(gap['total_firm'])}，共享预算最小余量 {_yuan(gap['headroom'])}，"
        f"**缺口 {_yuan(gap['shortfall'])}**；全部债务并列列示，不按顺序抹账。"
    )
    lines.append("")

    lines.append("## 六、出纳三档")
    lines.append("")
    cashier = run["cashier"]
    lines.append("1. **可明确支付的素材款**")
    if cashier["material_payable_now"]:
        for item in cashier["material_payable_now"]:
            lines.append(f"   - {item['contract']}：{_yuan(item['amount'])}（{item['reason']}）")
    else:
        lines.append("   - 无新增；素材保底已凭交付结清或尚缺交付凭据。")
    lines.append("2. **无论合理归属如何都至少应付的贡献款（跨场景下限）**")
    for item in cashier["robust_contribution_minimum"]:
        lines.append(
            f"   - {item['contract']}（{item['channel']}）：毛额下限 {_yuan(item['min_gross_across_scenarios'])}，"
            f"现金下限 {_yuan(item['min_cash_across_scenarios'])}。{item['note']}"
        )
    lines.append("3. **争议准备（留置与冻结）**")
    lines.append("   - 原则：留置等归属争议解除且取消窗口结束才可释放；已释放的留置不能当作没放出去，释放后证据失效只能走追调。")
    for reserve in cashier["holdback_reserves_by_scenario"]:
        detail = "，".join(f"{cid}:{_yuan(v)}" for cid, v in reserve["per_contract"].items())
        lines.append(f"   - {reserve['scenario']} 留置 {detail}，合计 {_yuan(reserve['total'])}；释放条件：{reserve['release_condition']}")
    for blocked in cashier["blocked_holds"]:
        lines.append(f"   - 冻结 {blocked['reference']}（{blocked['contract']}）{_yuan(blocked['amount'])}：{blocked['reason']}")
    for item in cashier["contingent_guarantees"]:
        lines.append(f"   - 或有保底 {item['contract']} {_yuan(item['amount'])}：{item['reason']}")
    lines.append("")
    if cashier["recovery_candidates"]:
        lines.append("### 追调候选（历史已付款，事实更正后只提追调，不改历史款项）")
        trace_by_ref = {t["reference"]: t for t in run["paid_trace"]}
        for item in cashier["recovery_candidates"]:
            lines.append(f"- {item['reference']}（{item['contract']}）{_yuan(item['amount'])}：{item['reason']}")
            for requirement in trace_by_ref[item["reference"]].get("recovery_requirements", []):
                lines.append(f"  - 追调还缺：{requirement}")
        for blocked in cashier["blocked_holds"]:
            for requirement in trace_by_ref[blocked["reference"]].get("release_requirements", []):
                lines.append(f"  - {blocked['reference']} 释放前提：{requirement}")
        lines.append("")

    lines.append("## 七、已付款回查（全部历史付款事实，跨期间也列示，不可删改）")
    lines.append("")
    lines.append("| 付款 | 合约 | 期间 | 金额 | 状态 | 凭据类别 | 凭据现在是否支持 | 后续 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for t in run["paid_trace"]:
        lines.append(
            f"| {t['reference']} | {t['contract']} | {t['period']} | {_yuan(t['amount'])} | {t['state']} | "
            f"{t['kind']} | {t['support']} | {t.get('follow_up', '按常规复核')} |"
        )
    lines.append("")
    return "\n".join(lines)
