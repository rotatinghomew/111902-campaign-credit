"""构造内存合成资料，供引擎与台账测试使用；不触碰 data/supplies.json。"""

from __future__ import annotations

from attribution.sources import SourceView, canonical_hash


def make_view(
    as_of: str,
    *,
    touches=None,
    appointments=None,
    links=None,
    contracts=None,
    budget_cap=1000,
    paid=None,
) -> SourceView:
    touches = list(touches or [])
    appointments = list(appointments or [])
    links = list(links or [])
    contracts = list(contracts or [])
    paid = list(paid or [])
    source = {
        "synthetic": True,
        "tables": {
            "touches": touches,
            "appointments": appointments,
            "links": links,
            "contract_terms": contracts,
            "settlement_budget": [{"scope": "测试组", "portfolio_cap": budget_cap}],
            "paid": paid,
        },
    }
    return SourceView(
        as_of=as_of,
        source=source,
        touches=tuple(touches),
        appointments=tuple(appointments),
        links=tuple(links),
        contracts=tuple(contracts),
        budget={"scope": "测试组", "portfolio_cap": budget_cap},
        paid=tuple(paid),
        source_hash=canonical_hash(source),
    )


def touch(tid, channel, area="甲区", date="2026-10-10", kind="独立", count=1):
    return {"touch": tid, "channel": channel, "area": area, "date": date, "kind": kind, "count": count}


def appointment(aid, origin="服务台", area="甲区", date="2026-10-15",
                cancel_until="2026-10-17", state="有效", recorded="2026-10-15"):
    return {
        "appointment": aid, "origin": origin, "area": area, "date": date,
        "cancel_until": cancel_until, "state": state, "recorded": recorded,
    }


def link(eid, tid, aid, state="候选", same_person=None, recorded="2026-10-16"):
    return {
        "edge": eid, "touch": tid, "appointment": aid, "state": state,
        "same_person": same_person, "recorded": recorded,
    }


def contract(cid="C1", channel="渠道甲", max_days=7, group="测试组",
             guarantee=100, period_cap=1000, holdback=0.2,
             tiers=None, cap_through="2026-12-31"):
    return {
        "contract": cid, "channel": channel, "max_days": max_days,
        "exclusive_group": group, "guarantee": guarantee, "period_cap": period_cap,
        "cap_through": cap_through, "holdback": holdback,
        "tiers": tiers or [{"upto": 1, "rate": 100}, {"upto": 10, "rate": 150}],
    }


def paid_row(ref, contract, amount, state="已结", delivery=True, evidence_ref="素材"):
    return {
        "reference": ref, "contract": contract, "period": "2026-10",
        "amount": amount, "state": state, "delivery": delivery, "evidence_ref": evidence_ref,
    }
