"""归属资格规则：把资料事实翻译成可辩护的候选证据。

口径是合约约定的预约归属范围，不是因果增量。一条渠道证据要进入候选，
必须同时满足：同地区、触点在合约时间窗内、来源证据可接受。
证据等级：
- 独立触点 + 确认关联（state=确认）：可独立兑现的强证据；
- 独立触点 + 候选关联：只能进入“可能集合”，不能独立兑现；
- 汇总触点：只有按渠道/片区/时段的容量上界，永远不能独立兑现；
- 排除关联：不参与。
同一 same_person 的多条确认边把多个触点指向同一个人；同人证据跨到
排他组内不同渠道时，仍受“一笔转化只兑现一份合约贡献”约束，
不允许就近拼接关联。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources import SourceView, days_between

# 可参与归属的关联状态；“排除”不参与。
EDGE_USABLE = {"候选", "确认"}


@dataclass
class Option:
    """一条可辩护归属：把某个预约判给某合约/渠道。"""

    contract: str
    channel: str
    touch: str
    edge: str
    edge_state: str
    touch_kind: str
    same_person: str | None
    in_window: bool
    same_area: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def window_ok(self) -> bool:
        return self.in_window and self.same_area

    @property
    def evidence_level(self) -> str:
        """confirmed=可独立兑现；candidate=仅可能集合；blocked=不可用。"""
        if not self.window_ok:
            return "blocked"
        if self.touch_kind == "汇总":
            return "bound_only"
        if self.edge_state == "确认":
            return "confirmed"
        return "candidate"


@dataclass
class AppointmentVerdict:
    appointment: str
    area: str
    date: str
    state: str
    confirmable: bool
    confirm_reason: str
    options: list[Option]
    natural_excluded: bool
    natural_reason: str
    same_person_groups: dict[str, list[str]]

    @property
    def confirmed_options(self) -> list[Option]:
        return [o for o in self.options if o.evidence_level == "confirmed"]

    @property
    def candidate_options(self) -> list[Option]:
        return [o for o in self.options if o.evidence_level in ("confirmed", "candidate")]

    @property
    def bound_only_options(self) -> list[Option]:
        return [o for o in self.options if o.evidence_level == "bound_only"]

    @property
    def blocked_options(self) -> list[Option]:
        return [o for o in self.options if o.evidence_level == "blocked"]


def _is_confirmable(appointment: dict, as_of: str) -> tuple[bool, str]:
    """有效 + 取消窗口含截止时刻，as_of 严格晚于 cancel_until 才能确认。"""
    if appointment["state"] != "有效":
        return False, f"状态为{appointment['state']}，不能参与当期可兑现额"
    if as_of <= appointment["cancel_until"]:
        return False, f"取消窗口截至 {appointment['cancel_until']}（含当日），{as_of} 尚未走完，不能确认"
    return True, "预约仍有效且取消窗口已结束"


def evaluate(view: SourceView, appointment_id: str) -> AppointmentVerdict:
    appointment = view.appointment(appointment_id)
    confirmable, confirm_reason = _is_confirmable(appointment, view.as_of)

    options: list[Option] = []
    person_groups: dict[str, list[str]] = {}

    for edge in view.edges_for(appointment_id):
        if edge["state"] not in EDGE_USABLE:
            continue
        touch = view.touch(edge["touch"])
        contract = view.contract_for_channel(touch["channel"])
        if contract is None:
            continue
        gap = days_between(touch["date"], appointment["date"])
        same_area = touch["area"] == appointment["area"]
        in_window = 0 <= gap <= contract["max_days"]
        reasons = [
            f"触点 {touch['touch']}（{touch['channel']}/{touch['area']}/{touch['date']}，{touch['kind']}）",
            f"关联边 {edge['edge']} 状态={edge['state']}",
            f"自然日差 {gap} 天，合约 {contract['contract']} 窗口 {contract['max_days']} 天",
        ]
        if not same_area:
            reasons.append(f"地区不一致（触点 {touch['area']} ≠ 预约 {appointment['area']}），否决")
        elif not in_window:
            reasons.append("超出合约时间窗，否决")
        option = Option(
            contract=contract["contract"],
            channel=touch["channel"],
            touch=touch["touch"],
            edge=edge["edge"],
            edge_state=edge["state"],
            touch_kind=touch["kind"],
            same_person=edge["same_person"],
            in_window=in_window,
            same_area=same_area,
            reasons=reasons,
        )
        options.append(option)
        if edge["same_person"]:
            person_groups.setdefault(edge["same_person"], []).append(edge["edge"])

    # 自然入口只有两种确定方式：明确的非采购联系证明，或没有任何可归属渠道。
    confirmed_or_candidate = [o for o in options if o.evidence_level in ("confirmed", "candidate")]
    if appointment["origin"] != "自然入口":
        natural_excluded = False
        natural_reason = f"来源登记为{appointment['origin']}，不适用自然判定"
    elif confirmed_or_candidate:
        natural_excluded = True
        natural_reason = "存在可归属的采购触点证据，自然入口不能确定，须先解除渠道归属争议"
    else:
        natural_excluded = False
        natural_reason = "无任何可归属渠道，可计为确定自然，排除在付款之外"

    return AppointmentVerdict(
        appointment=appointment_id,
        area=appointment["area"],
        date=appointment["date"],
        state=appointment["state"],
        confirmable=confirmable,
        confirm_reason=confirm_reason,
        options=options,
        natural_excluded=natural_excluded,
        natural_reason=natural_reason,
        same_person_groups=person_groups,
    )


def evaluate_all(view: SourceView) -> dict[str, AppointmentVerdict]:
    return {appt: evaluate(view, appt) for appt in sorted(view.appointment_ids())}


def exclusive_groups(view: SourceView) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for contract in view.contracts:
        groups.setdefault(contract["exclusive_group"], []).append(contract["contract"])
    return groups
