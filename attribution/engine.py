"""一次结算运行的完整结论，供台账快照与报告使用。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from . import money
from .rules import evaluate_all
from .scenarios import (
    Award,
    build_uncertainty_register,
    contract_envelopes,
    enumerate_scenarios,
)
from .sources import SourceView, canonical_hash, load_view, paid_as_of


def _verdict_payload(view: SourceView, verdict) -> dict:
    return {
        "appointment": verdict.appointment,
        "origin": view.appointment(verdict.appointment)["origin"],
        "area": verdict.area,
        "date": verdict.date,
        "state": verdict.state,
        "confirmable": verdict.confirmable,
        "confirm_reason": verdict.confirm_reason,
        "natural_excluded_from_payment": verdict.natural_excluded,
        "natural_reason": verdict.natural_reason,
        "same_person_groups": verdict.same_person_groups,
        "options": [
            {
                "contract": o.contract,
                "channel": o.channel,
                "touch": o.touch,
                "edge": o.edge,
                "edge_state": o.edge_state,
                "touch_kind": o.touch_kind,
                "same_person": o.same_person,
                "window_ok": o.window_ok,
                "evidence_level": o.evidence_level,
                "reasons": o.reasons,
            }
            for o in verdict.options
        ],
    }


def build_run(
    view: SourceView,
    paid_through: str | None = None,
    exclude_paid_refs: frozenset[str] | None = None,
) -> dict:
    verdicts = evaluate_all(view)
    scenarios = enumerate_scenarios(verdicts)
    priced = money.price_scenarios(view, scenarios)
    envelopes = contract_envelopes(scenarios, view.contracts)
    register = build_uncertainty_register(view, verdicts)
    trace = money.paid_trace(view)
    in_period_paid = paid_as_of(view, paid_through, exclude_paid_refs)
    guarantees = money.guarantee_positions(view, in_period_paid)
    cashier = money.cashier(view, priced, trace, guarantees)
    budget = money.budget_positions(view, priced, in_period_paid)

    firm = [
        {"contract": item["contract"], "amount": item["amount"]}
        for item in cashier["material_payable_now"]
    ] + [
        {"contract": item["contract"], "amount": item["min_cash_across_scenarios"]}
        for item in cashier["robust_contribution_minimum"]
        if item["min_cash_across_scenarios"] > 0
    ]
    gap = money.firm_obligation_gap(firm, max(0, budget["portfolio"]["headroom_min"]))

    payload = {
        "schema": "attribution-run/v1",
        "as_of": view.as_of,
        "paid_through": paid_through or view.as_of,
        "paid_refs_excluded_for_budget": sorted(exclude_paid_refs or []),
        "generated_date": date.today().isoformat(),
        "source_hash": view.source_hash,
        "appointments": [_verdict_payload(view, verdicts[a]) for a in sorted(verdicts)],
        "scenarios": [
            {
                **p,
                "awards": [a.__dict__ if isinstance(a, Award) else a for a in p["awards"]],
            }
            for p in priced
        ],
        "count_envelopes": envelopes,
        "uncertainty_register": [asdict(entry) for entry in register],
        "guarantees": guarantees,
        "budget": budget,
        "firm_obligation_gap": gap,
        "cashier": cashier,
        "paid_trace": trace,
    }
    payload["run_hash"] = canonical_hash(payload)
    return payload


def run_from_file(
    path: str,
    as_of: str,
    paid_through: str | None = None,
    exclude_paid_refs: frozenset[str] | None = None,
) -> dict:
    return build_run(load_view(path, as_of), paid_through, exclude_paid_refs)
