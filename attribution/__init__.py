"""本地拟制账：咨询预约归因与采购结算。

只处理 data/supplies.json 中的虚构资料，不接入任何外部系统。
模块划分：
- sources:   资料读取、按 recorded 折叠修订、as-of 视图
- rules:     预约资格、证据强弱、自然预约排除、排他与同人合并
- scenarios: 可辩护归属场景枚举与渠道上下限/合计边界
- money:     阶梯计价、保底素材款、留置、期间/共享预算与缺口
- engine:    汇总一次结算运行的完整结论
- ledger:    只追加的本地台账（运行快照、提案登记、重判、回查）
- report:    中文 Markdown 台账报告
"""

__all__ = [
    "sources",
    "rules",
    "scenarios",
    "money",
    "engine",
    "ledger",
    "report",
]
