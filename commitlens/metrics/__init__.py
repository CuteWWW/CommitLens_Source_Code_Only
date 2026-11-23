"""8 个检查项的注册表。

要加第 9 个检查项：新建一个模块，实现 run(ctx) -> list[CheckResult]，
在这里 import 并把模块名加进 MODULES 即可，其它地方都不用改。
"""

from __future__ import annotations

from ..model import CheckResult
from .base import CHECKS, Context
from . import cadence, hotspot, message, ownership, size, trend

MODULES = [message, size, cadence, hotspot, ownership, trend]

CHECK_TITLES = {
    "message": "提交信息规范",
    "size": "单次改动规模",
    "giant": "巨型提交",
    "cadence_freq": "提交频率异常",
    "cadence_dormant": "长期未提交",
    "hotspot": "热点文件",
    "ownership": "知识集中度",
    "trend": "活跃度趋势",
}


def run_all(ctx: Context) -> list[CheckResult]:
    results: list[CheckResult] = []
    for module in MODULES:
        for result in module.run(ctx):
            if result.chart is None:
                result.chart = None
            results.append(result)
    results.sort(key=lambda r: r.order)
    return results


__all__ = ["run_all", "CHECK_TITLES", "Context", "CHECKS"]
