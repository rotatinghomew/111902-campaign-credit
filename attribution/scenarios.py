"""可辩护归属场景枚举与不确定性登记。

每个仍有效且过了取消窗口的预约，若有“独立触点+确认关联”的强证据，
就形成一个可判落点；同一预约多个强证据分属不同合约时，每个落点都是
一个可辩护场景，但任何场景里该预约只兑现一份合约贡献。
没有强证据的预约不进入任何可兑现场景，只登记可能集合与合计边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .rules import AppointmentVerdict, evaluate_all
from .sources import SourceView


@dataclass(frozen=True)
class Award:
    appointment: str
    contract: str
    channel: str
    touch: str
    edge: str
    same_person: str | None


@dataclass
class Scenario:
    name: str
    awards: tuple[Award, ...]
    counts: dict[str, int] = field(default_factory=dict)

    def contracts(self) -> set[str]:
        return set(self.counts)


@dataclass
class UncertaintyEntry:
    appointment: str
    kind: str  # disputed / unconfirmed / pending_window / revoked / natural
    summary: str
    possible_contracts: list[str]
    capacity_bounds: list[dict]
    narrowing: list[str]
    detail: dict


def _confirmed_choices(verdict: AppointmentVerdict) -> list[Award]:
    """同一预约的强证据按合约去重：排他组内一笔转化只允许一个渠道认领。"""
    seen: set[str] = set()
    choices: list[Award] = []
    for option in verdict.confirmed_options:
        if option.contract in seen:
            continue
        seen.add(option.contract)
        choices.append(
            Award(
                appointment=verdict.appointment,
                contract=option.contract,
                channel=option.channel,
                touch=option.touch,
                edge=option.edge,
                same_person=option.same_person,
            )
        )
    return choices


def enumerate_scenarios(verdicts: dict[str, AppointmentVerdict]) -> list[Scenario]:
    decision_points: list[tuple[str, list[Award]]] = []
    for appt in sorted(verdicts):
        verdict = verdicts[appt]
        if not verdict.confirmable:
            continue
        choices = _confirmed_choices(verdict)
        if choices:
            decision_points.append((appt, choices))

    if not decision_points:
        return [Scenario(name="S1", awards=(), counts={})]

    scenarios: list[Scenario] = []
    for index, combo in enumerate(product(*(choices for _, choices in decision_points)), start=1):
        awards = tuple(combo)
        counts: dict[str, int] = {}
        for award in awards:
            counts[award.contract] = counts.get(award.contract, 0) + 1
        scenarios.append(Scenario(name=f"S{index}", awards=awards, counts=counts))
    return scenarios


def _capacity_bounds(view: SourceView, verdict: AppointmentVerdict) -> list[dict]:
    """汇总触点只给容量上界，按渠道/片区/触点日期登记，不落到具体预约。"""
    bounds: list[dict] = []
    for option in verdict.bound_only_options:
        touch = view.touch(option.touch)
        if not option.window_ok:
            continue
        bounds.append(
            {
                "contract": option.contract,
                "channel": option.channel,
                "area": touch["area"],
                "touch_date": touch["date"],
                "upper_bound_people": touch["count"],
                "note": "汇总人数仅为容量上界，不能独立兑现，需落地为独立触点确认关联",
            }
        )
    return bounds


def build_uncertainty_register(view: SourceView, verdicts: dict[str, AppointmentVerdict]) -> list[UncertaintyEntry]:
    register: list[UncertaintyEntry] = []
    for appt in sorted(verdicts):
        verdict = verdicts[appt]
        possible = sorted({o.contract for o in verdict.candidate_options if o.window_ok})
        bounds = _capacity_bounds(view, verdict)

        if not verdict.confirmable:
            kind = "revoked" if verdict.state != "有效" else "pending_window"
            register.append(
                UncertaintyEntry(
                    appointment=appt,
                    kind=kind,
                    summary=verdict.confirm_reason,
                    possible_contracts=possible,
                    capacity_bounds=bounds,
                    narrowing=["重新有效或窗口结束后再评估；已撤销预约不得参与当期可兑现额"],
                    detail={"state": verdict.state},
                )
            )
            continue

        choices = _confirmed_choices(verdict)
        if len(choices) > 1:
            register.append(
                UncertaintyEntry(
                    appointment=appt,
                    kind="disputed",
                    summary=f"同一人确认关联同时指向 {len(choices)} 个排他组内渠道，只能兑现一份",
                    possible_contracts=[a.contract for a in choices],
                    capacity_bounds=bounds,
                    narrowing=[
                        "补充排他组内渠道优先级/排他约定凭据，或能区分实际来源的同人确认",
                        "任一渠道证据被排除后，可能集合立即缩小",
                    ],
                    detail={"same_person_groups": verdict.same_person_groups},
                )
            )
        elif not choices:
            register.append(
                UncertaintyEntry(
                    appointment=appt,
                    kind="unconfirmed",
                    summary="只有候选关联或汇总上界，没有可独立兑现的确认证据",
                    possible_contracts=possible,
                    capacity_bounds=bounds,
                    narrowing=[
                        "把候选关联升级为 same_person 确认关联，或补充独立触点回执",
                        "汇总上界需逐人落地，不能直接补给最缺业绩的渠道",
                    ],
                    detail={
                        "candidate_edges": [
                            o.edge for o in verdict.options if o.evidence_level == "candidate"
                        ],
                        "bound_edges": [
                            o.edge for o in verdict.options if o.evidence_level == "bound_only"
                        ],
                    },
                )
            )

        if view.appointment(appt)["origin"] == "自然入口":
            register.append(
                UncertaintyEntry(
                    appointment=appt,
                    kind="natural",
                    summary=verdict.natural_reason,
                    possible_contracts=possible,
                    capacity_bounds=bounds,
                    narrowing=[
                        "若要排除付款，需明确的非采购联系证明，或确认所有渠道均不可归属",
                    ],
                    detail={"natural_excluded_from_payment": verdict.natural_excluded},
                )
            )
    return register


def contract_envelopes(scenarios: list[Scenario], contracts: tuple[dict, ...]) -> dict[str, dict]:
    """各渠道在所有可辩护场景下的条数上下限。

    各渠道上限来自各自最有利场景，不能同时兑现；合计边界另行给出。
    """
    envelopes: dict[str, dict] = {}
    for contract in contracts:
        cid = contract["contract"]
        counts = [scenario.counts.get(cid, 0) for scenario in scenarios]
        envelopes[cid] = {
            "channel": contract["channel"],
            "min_count": min(counts),
            "max_count": max(counts),
            "attainable_together": "各渠道上限取自不同场景，不得相加承诺",
        }
    joint = [sum(scenario.counts.values()) for scenario in scenarios]
    envelopes["__joint__"] = {
        "min_count": min(joint),
        "max_count": max(joint),
        "note": "同一预算组内全部可辩护场景的合计边界",
    }
    return envelopes


def run_attribution(view: SourceView) -> dict:
    verdicts = evaluate_all(view)
    scenarios = enumerate_scenarios(verdicts)
    register = build_uncertainty_register(view, verdicts)
    envelopes = contract_envelopes(scenarios, view.contracts)
    return {
        "verdicts": verdicts,
        "scenarios": scenarios,
        "register": register,
        "envelopes": envelopes,
    }
