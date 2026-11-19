"""读取本地合成资料，按事件时间折叠修订，提供 as-of 视图。

约定（见 README 与 data/fields.json）：
- appointments / links 同一主键可有多行，后到的 recorded 是修订；
- as-of 视图只取 recorded <= as_of 的最新一行，更早的行不删除、可回查；
- touches / contract_terms / settlement_budget / paid 没有事件日期，
  其中 paid 是结算事实，任何视图都原样保留，绝不改写。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def days_between(start: str, end: str) -> int:
    """触点日期到预约日期的自然日差。"""
    return (parse_day(end) - parse_day(start)).days


def canonical_hash(payload: object) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _fold_revisions(rows: tuple[dict, ...], key: str, as_of: str) -> tuple[dict, ...]:
    """每个主键只保留 recorded 不晚于 as_of 的最新一行。"""
    latest: dict[str, dict] = {}
    for row in rows:
        if row["recorded"] > as_of:
            continue
        old = latest.get(row[key])
        if old is None or row["recorded"] > old["recorded"]:
            latest[row[key]] = dict(row)
    return tuple(latest.values())


@dataclass(frozen=True)
class SourceView:
    as_of: str
    source: dict
    touches: tuple[dict, ...]
    appointments: tuple[dict, ...]
    links: tuple[dict, ...]
    contracts: tuple[dict, ...]
    budget: dict
    paid: tuple[dict, ...]
    source_hash: str

    def contract_for_channel(self, channel: str) -> dict | None:
        for contract in self.contracts:
            if contract["channel"] == channel:
                return contract
        return None

    def touch(self, touch_id: str) -> dict | None:
        for row in self.touches:
            if row["touch"] == touch_id:
                return row
        return None

    def appointment(self, appointment_id: str) -> dict | None:
        for row in self.appointments:
            if row["appointment"] == appointment_id:
                return row
        return None

    def appointment_ids(self) -> set[str]:
        return {row["appointment"] for row in self.appointments}

    def edges_for(self, appointment_id: str) -> tuple[dict, ...]:
        return tuple(edge for edge in self.links if edge["appointment"] == appointment_id)


def paid_as_of(
    view: SourceView,
    paid_through: str | None = None,
    exclude_refs: frozenset[str] | None = None,
) -> tuple[dict, ...]:
    """as-of 重建时视为已经发生的付款。

    paid 表没有事件日期：默认只能按期间（YYYY-MM）近似过滤；
    调用方依据外部付款日历用 exclude_refs 显式裁掉当时尚未发生的付款。
    全部付款事实仍保留在 view.paid 中用于不可变回查，绝不静默删除。
    """
    cutoff = paid_through or view.as_of
    period = cutoff[:7]
    exclude_refs = exclude_refs or frozenset()
    return tuple(
        row
        for row in view.paid
        if row["period"] <= period and row["reference"] not in exclude_refs
    )


def load_view(path: str | Path, as_of: str) -> SourceView:
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    tables = source["tables"]
    return SourceView(
        as_of=as_of,
        source=source,
        touches=tuple(tables["touches"]),
        appointments=_fold_revisions(tuple(tables["appointments"]), "appointment", as_of),
        links=_fold_revisions(tuple(tables["links"]), "edge", as_of),
        contracts=tuple(tables["contract_terms"]),
        budget=tables["settlement_budget"][0],
        paid=tuple(tables["paid"]),
        source_hash=canonical_hash(source),
    )
