"""只追加的本地拟制台账。

存储布局（全部是本地 JSON / JSONL / Markdown，不接入外部系统）：
- runs/       每次运行的不可变快照，文件名带 as_of 与序号；
- proposals/  提案事件流 JSONL，只追加，当前状态由事件折叠得到；
- reports/    面向主管与出纳的 Markdown 报告；
- index.json  路径索引。

已确认付款在来源资料中是事实，台账永不删改；重判只追加新事件。
"""

from __future__ import annotations

import json
from pathlib import Path

from .engine import build_run
from .sources import canonical_hash, load_view


class LedgerError(RuntimeError):
    pass


def ensure_ledger(root: str | Path) -> Path:
    root = Path(root)
    for sub in ("runs", "proposals", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    index = root / "index.json"
    if not index.exists():
        index.write_text(json.dumps({"runs": [], "proposal_file": "proposals/proposals.jsonl"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _index(root: Path) -> dict:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def _write_index(root: Path, payload: dict) -> None:
    (root / "index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_run(
    root: str | Path,
    data_path: str,
    as_of: str,
    paid_through: str | None = None,
    exclude_paid_refs: frozenset[str] | None = None,
) -> tuple[Path, bool]:
    """保存一次运行快照；同一 as_of 内容未变则复用既有快照。返回(路径, 是否新建)。"""
    root = ensure_ledger(root)
    view = load_view(data_path, as_of)
    payload = build_run(view, paid_through, exclude_paid_refs)
    digest = payload["run_hash"]

    index = _index(root)
    for item in index["runs"]:
        if item["as_of"] == as_of and item["run_hash"] == digest:
            return root / item["path"], False

    sequence = sum(1 for item in index["runs"] if item["as_of"] == as_of) + 1
    name = f"run-{as_of}-{sequence}.json"
    path = root / "runs" / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    index["runs"].append({"as_of": as_of, "path": f"runs/{name}", "run_hash": digest})
    _write_index(root, index)
    return path, True


def load_run(root: str | Path, as_of: str | None = None) -> dict:
    root = Path(root)
    index = _index(root)
    runs = index["runs"]
    if not runs:
        raise LedgerError("台账还没有运行快照")
    chosen = runs[-1] if as_of is None else next((r for r in runs if r["as_of"] == as_of), None)
    if chosen is None:
        raise LedgerError(f"没有 as_of={as_of} 的运行快照")
    return json.loads((root / chosen["path"]).read_text(encoding="utf-8"))


def _proposal_path(root: Path) -> Path:
    return root / "proposals" / "proposals.jsonl"


def _read_events(root: Path) -> list[dict]:
    path = _proposal_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fold(root: Path) -> dict[str, dict]:
    proposals: dict[str, dict] = {}
    for event in _read_events(root):
        pid = event["proposal_id"]
        if event["event"] == "propose":
            proposals[pid] = {
                "proposal_id": pid,
                "status": "待审批",
                "basis": event["basis"],
                "amounts": event["amounts"],
                "created": event["as_of"],
                "history": [event],
            }
        else:
            target = proposals.get(pid)
            if target is None:
                raise LedgerError(f"提案 {pid} 不存在")
            target["history"].append(event)
            if event["event"] == "approve":
                target["status"] = "已批准待付"
            elif event["event"] == "pay":
                target["status"] = "已支付"
            elif event["event"] == "rejudge":
                target["status"] = "重判失效" if not event["still_holds"] else "重判仍成立"
                target["rejudgement"] = event
    return proposals


def list_proposals(root: str | Path) -> list[dict]:
    return sorted(_fold(Path(root)).values(), key=lambda p: p["proposal_id"])


def propose(root: str | Path, basis: dict, amounts: dict, as_of: str, run_hash: str) -> dict:
    """登记一笔尚未付款的提案，保留归属依据与留置安排。"""
    root = ensure_ledger(root)
    events = _read_events(root)
    next_id = 1 + max((int(e["proposal_id"].split("-")[1]) for e in events), default=0)
    pid = f"PR-{next_id:03d}"
    event = {
        "event": "propose",
        "proposal_id": pid,
        "as_of": as_of,
        "run_hash": run_hash,
        "basis": basis,
        "amounts": amounts,
    }
    with _proposal_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return _fold(root)[pid]


def _set_status(root: Path, pid: str, event_name: str, as_of: str, extra: dict | None = None) -> dict:
    proposals = _fold(root)
    if pid not in proposals:
        raise LedgerError(f"提案 {pid} 不存在")
    event = {"event": event_name, "proposal_id": pid, "as_of": as_of}
    if extra:
        event.update(extra)
    with _proposal_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return _fold(root)[pid]


def approve(root: str | Path, proposal_id: str, as_of: str) -> dict:
    return _set_status(Path(root), proposal_id, "approve", as_of)


def mark_paid(root: str | Path, proposal_id: str, as_of: str) -> dict:
    return _set_status(Path(root), proposal_id, "pay", as_of)


def rejudge(root: str | Path, proposal_id: str, new_run: dict, view) -> dict:
    """审批后收到撤销预约或同人回执证据时，重新判断提案及留置是否成立。

    只追加 rejudge 事件，不改写原提案；区分依据失效与预算未恢复两种情形。
    """
    root = Path(root)
    proposals = _fold(root)
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise LedgerError(f"提案 {proposal_id} 不存在")
    basis = proposal["basis"]
    appt_id = basis["appointment"]
    contract_id = basis["contract"]
    edge_id = basis["edge"]
    as_of = new_run["as_of"]

    verdict = next((a for a in new_run["appointments"] if a["appointment"] == appt_id), None)
    failures: list[str] = []
    if verdict is None:
        failures.append("预约在新视图中已不存在")
    else:
        if verdict["state"] != "有效":
            failures.append(f"预约状态变为{verdict['state']}，不再支持兑现")
        if not verdict["confirmable"]:
            failures.append(verdict["confirm_reason"])
        option = next((o for o in verdict["options"] if o["edge"] == edge_id), None)
        if option is None:
            failures.append(f"原关联边 {edge_id} 已被排除或修订消失")
        elif option["evidence_level"] != "confirmed":
            failures.append(f"原证据 {edge_id} 降为 {option['evidence_level']}，不能独立兑现")
        elif option["contract"] != contract_id or not option["window_ok"]:
            failures.append("原证据不再满足合约/窗口/地区条件")

        # 两份回执被确认属于同一个人：排他组内只允许一个渠道认领。
        same_person = option["same_person"] if option else None
        if same_person:
            rival = next(
                (o for o in verdict["options"]
                 if o["evidence_level"] == "confirmed"
                 and o["contract"] != contract_id
                 and o["same_person"] == same_person),
                None,
            )
            if rival is not None:
                failures.append(
                    f"同人 {same_person} 出现指向 {rival['contract']} 的第二份确认回执，排他争议未解除前不能兑现"
                )

    award_still_possible = any(
        any(a["appointment"] == appt_id and a["contract"] == contract_id and a["edge"] == edge_id for a in s["awards"])
        for s in new_run["scenarios"]
    )
    if not award_still_possible and not failures:
        failures.append("新视图的可辩护场景中已不存在该归属落点")

    still_holds = not failures
    reasons = list(dict.fromkeys(failures))
    budget = new_run["budget"]["portfolio"]
    budget_blocked = budget["headroom_min"] < 0
    extra = {
        "still_holds": still_holds,
        "reasons": reasons,
        "holdback_release": "留置继续冻结，待依据恢复且取消窗口结束" if not still_holds else "依据成立；留置仍需争议解除与窗口结束",
        "budget_status": (
            "共享预算未恢复，即使依据成立也暂不能支付"
            if still_holds and budget_blocked
            else "预算占用按当前场景重新计算"
        ),
        "new_run_hash": new_run["run_hash"],
        "evidence_resolved": still_holds,
    }
    return _set_status(root, proposal_id, "rejudge", as_of, extra)


def write_report(root: str | Path, filename: str, markdown: str) -> Path:
    root = ensure_ledger(root)
    path = root / "reports" / filename
    path.write_text(markdown, encoding="utf-8")
    return path


def ledger_hash(root: str | Path) -> str:
    """对台账全部不可变产物取摘要，便于发现本地文件被手工改动。"""
    root = Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return canonical_hash([str(p.relative_to(root)) for p in files]), files
