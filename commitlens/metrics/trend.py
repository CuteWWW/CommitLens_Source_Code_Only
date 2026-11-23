"""检查项 8：项目活跃度是否在下降。"""

from __future__ import annotations

from collections import defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from ..stats import change_pct, linear_slope, mann_kendall
from .base import Context, mean, register


def run(ctx: Context) -> list[CheckResult]:
    weekly = ctx.weekly()
    if not weekly:
        return [_empty("窗口内没有提交，无法评估")]

    min_weeks = ctx.cfg("trend", "min_weeks")
    if len(weekly) < min_weeks:
        return [
            CheckResult(
                check="trend",
                title="活跃度趋势",
                severity=Severity.INFO,
                summary=f"窗口只有 {len(weekly)} 周数据（少于 {min_weeks} 周），暂不做趋势判断",
                order=8,
                stats={"weeks": len(weekly)},
                chart={
                    "type": "line",
                    "labels": [d.strftime("%m-%d") for d, _ in weekly],
                    "values": [v for _, v in weekly],
                    "unit": "次/周",
                },
            )
        ]

    labels = [d.strftime("%m-%d") for d, _ in weekly]
    values = [float(v) for _, v in weekly]
    recent_w = ctx.cfg("trend", "recent_weeks")
    alpha = ctx.cfg("trend", "alpha")
    drop_pct = ctx.cfg("trend", "drop_pct")

    recent = values[-recent_w:]
    prev = values[-2 * recent_w:-recent_w] if len(values) >= 2 * recent_w else values[: len(values) - recent_w]
    baseline_label = f"此前 {len(prev)} 个连续周"
    if all(v == 0 for v in recent) and not any(prev):
        # When both adjacent windows are zero, a 0% 'change' falsely implies
        # stability. Compare with the most recent active weeks, and label the
        # non-adjacent reference unambiguously.
        active = [v for v in values[:-recent_w] if v > 0][-recent_w:]
        if active:
            prev = active
            baseline_label = f"此前最近 {len(active)} 个活跃周"
    change = change_pct(mean(prev), mean(recent))
    slope = linear_slope(values)
    mk = mann_kendall(values)
    p_value = mk[2] if mk else 1.0
    significant = bool(mk) and p_value < alpha and slope < 0
    trailing_zero = 0
    for v in reversed(values):
        if v:
            break
        trailing_zero += 1
    # A prolonged all-zero tail is a clear operational inactivity signal even
    # when the immediate previous comparison window is ALSO entirely zero.
    prolonged_inactivity = trailing_zero >= recent_w and any(values[:-trailing_zero])

    severity = (
        Severity.ALERT if prolonged_inactivity or (significant and change <= -drop_pct) else
        Severity.WARN if significant else
        Severity.INFO if change <= -drop_pct else Severity.OK
    )

    base = mean(prev)
    now = mean(recent)
    if base < 1.0:
        # 基线太小时百分比会失真（0.2 → 1.8 就是 +600%），直接说绝对量
        trend_word = f"从每周 {base:.1f} 次变为 {now:.1f} 次"
    elif change < 0:
        trend_word = f"下降 {abs(change):.0%}"
    elif change > 0:
        trend_word = f"上升 {change:.0%}"
    else:
        trend_word = "基本持平"

    summary = (
        f"最近 {recent_w} 周平均每周 {mean(recent):.1f} 次提交，"
        f"{baseline_label}均值为 {mean(prev):.1f} 次，{trend_word}"
    )
    p_label = "p < 0.001" if p_value < 0.001 else f"p = {p_value:.3f}"
    if significant:
        summary += f"（Mann-Kendall 趋势检验 {p_label}，统计显著）"
    else:
        summary += "（趋势尚未达到统计显著，可能是正常波动）"
    if trailing_zero:
        summary += f"；截至分析日已连续 {trailing_zero} 周零提交"

    findings: list[Finding] = []
    if severity != Severity.OK:
        shas_by_week: dict = defaultdict(list)
        from .base import monday_of
        for c in ctx.commits:
            week = monday_of(c.date.date())
            if len(shas_by_week[week]) < 3:
                shas_by_week[week].append(c.sha)
        findings.append(
            Finding(
                check="trend",
                severity=severity,
                title=f"最近 {recent_w} 周活跃度{trend_word}",
                detail=(
                    "活跃度下降本身不代表项目出问题——可能是进入收尾、可能是考试周。"
                    "但它是一个值得确认的信号：如果是被动的（任务卡住、成员流失），"
                    "越早处理代价越小；如果是主动的（按计划收尾），记录下来即可。"
                ),
                advice=(
                    "对照 backlog 与里程碑确认是否为计划内收尾；"
                    "若是被动下降，用 cadence 检查项定位到具体成员，一对一确认。"
                ),
                evidence=[
                    Evidence(
                        label=f"最近 {len(weekly)} 周提交数",
                        detail=f"整体斜率 {slope:+.2f} 次/周，{p_label}；"
                               f"统计截止 {ctx.now.date()}（末尾零提交周已计入）",
                        lines=[
                            f"- {d.strftime('%Y-%m-%d')} 起那一周：{v} 次；"
                            + ("提交示例 " + ", ".join(shas_by_week[d])
                               if shas_by_week[d] else "无提交（零值证据）")
                            for (d, _), v in list(zip(weekly, values))[-12:]
                        ],
                        commit=ctx.commits[-1].sha,
                    )
                ],
            )
        )

    return [
        CheckResult(
            check="trend",
            title="活跃度趋势",
            severity=severity,
            summary=summary,
            order=8,
            stats={
                "weeks": len(weekly),
                "recent_mean": round(mean(recent), 2),
                "previous_mean": round(mean(prev), 2),
                "comparison_basis": baseline_label,
                "change": round(change, 4),
                "slope": round(slope, 4),
                "p_value": round(p_value, 4),
                "significant": significant,
                "analysis_end_date": str(ctx.now.date()),
                "trailing_zero_weeks": trailing_zero,
            },
            chart={
                "type": "line",
                "labels": labels,
                "values": [int(v) for v in values],
                "unit": "次/周",
            },
            findings=findings,
        )
    ]


def _empty(summary: str) -> CheckResult:
    return CheckResult(
        check="trend",
        title="活跃度趋势",
        severity=Severity.INFO,
        summary=summary,
        order=8,
    )
