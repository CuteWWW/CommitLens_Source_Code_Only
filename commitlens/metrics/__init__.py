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
    "message": "Commit Message Quality",
    "size": "Change Size",
    "giant": "Oversized Commits",
    "cadence_freq": "Commit Cadence",
    "cadence_dormant": "Dormant Contributors",
    "hotspot": "Hotspot Files",
    "ownership": "Knowledge Concentration",
    "trend": "Activity Trend",
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
