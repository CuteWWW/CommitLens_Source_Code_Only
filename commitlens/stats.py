"""小统计工具。

只依赖标准库，方便单测，也方便在报告里解释公式。
Mann-Kendall 是趋势检验的经典非参数方法：不假设数据正态分布，
对「这两周休假所以提交少」这类噪声比较稳健。
"""

from __future__ import annotations

import math
from collections import Counter


def mann_kendall(values: list[float]) -> tuple[float, float, float] | None:
    """返回 (S, Z, p)。样本不足时返回 None。

    H0：没有单调趋势；p < alpha 时可以认为存在显著趋势。
    """
    n = len(values)
    if n < 4:
        return None

    s = 0.0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            s += (diff > 0) - (diff < 0)

    # 结（并列值）修正
    var = n * (n - 1) * (2 * n + 5)
    for t in Counter(values).values():
        if t > 1:
            var -= t * (t - 1) * (2 * t + 5)
    var /= 18.0
    if var <= 0:
        return None

    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0

    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return s, z, p


def linear_slope(values: list[float]) -> float:
    """最小二乘斜率，仅用于展示「每周大约变化多少」。"""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    return num / denom


def change_pct(old: float, new: float) -> float:
    """相对变化率；old 为 0 时返回 0 或 ±1（表示从无到有）。"""
    if old == 0:
        return 0.0 if new == 0 else (1.0 if new > 0 else -1.0)
    return (new - old) / old
