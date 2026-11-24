"""检查项 8：项目活跃度是否在下降。"""

from __future__ import annotations

from collections import defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from ..stats import change_pct, linear_slope, mann_kendall
from .base import Context, count_label, mean, register


def run(ctx: Context) -> list[CheckResult]:
    weekly = ctx.weekly()
    if not weekly:
        return [_empty("No commits in the selected window; analysis unavailable")]

    min_weeks = ctx.cfg("trend", "min_weeks")
    if len(weekly) < min_weeks:
        return [
            CheckResult(
                check="trend",
                title="Activity Trend",
                severity=Severity.INFO,
                summary=(
                    f"The selected window contains only {len(weekly)} weeks of data "
                    f"(minimum: {min_weeks}); trend analysis is unavailable"
                ),
                order=8,
                stats={"weeks": len(weekly)},
                chart={
                    "type": "line",
                    "labels": [d.strftime("%m-%d") for d, _ in weekly],
                    "values": [v for _, v in weekly],
                    "unit": "commits/week",
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
    baseline_label = f"the previous {len(prev)} consecutive weeks"
    if all(v == 0 for v in recent) and not any(prev):
        # When both adjacent windows are zero, a 0% 'change' falsely implies
        # stability. Compare with the most recent active weeks, and label the
        # non-adjacent reference unambiguously.
        active = [v for v in values[:-recent_w] if v > 0][-recent_w:]
        if active:
            prev = active
            baseline_label = f"the previous {len(active)} active weeks"
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
        trend_word = f"changed from {base:.1f} to {now:.1f} commits per week"
    elif change < 0:
        trend_word = f"down {abs(change):.0%}"
    elif change > 0:
        trend_word = f"up {change:.0%}"
    else:
        trend_word = "roughly flat"

    summary = (
        f"The last {recent_w} weeks averaged {mean(recent):.1f} commits per week, while "
        f"{baseline_label} averaged {mean(prev):.1f}; activity is {trend_word}."
    )
    p_label = "p < 0.001" if p_value < 0.001 else f"p = {p_value:.3f}"
    if significant:
        summary += f" Mann-Kendall trend test: {p_label}; statistically significant."
    else:
        summary += " The trend is not statistically significant and may reflect normal variation."
    if trailing_zero:
        summary += (
            f" As of the analysis date, there "
            f"{'has' if trailing_zero == 1 else 'have'} been "
            f"{count_label(trailing_zero, 'consecutive zero-commit week')}."
        )

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
                title=f"Activity over the last {recent_w} weeks is {trend_word}",
                detail=(
                    "A decline in activity does not prove that the project has a problem. It "
                    "may reflect planned completion or a temporary slowdown. It is still worth "
                    "confirming: passive declines caused by blockers or contributor loss become "
                    "more expensive to resolve over time."
                ),
                advice=(
                    "Compare the backlog and milestones to determine whether the decline is "
                    "planned. If it is not, use the cadence checks to identify affected "
                    "contributors and follow up directly."
                ),
                evidence=[
                    Evidence(
                        label=f"Weekly commits over the last {len(weekly)} weeks",
                        detail=(
                            f"Overall slope: {slope:+.2f} commits/week; {p_label}; "
                            f"analysis cutoff: {ctx.now.date()} "
                            "(trailing zero-commit weeks included)"
                        ),
                        lines=[
                            f"- Week starting {d.strftime('%Y-%m-%d')}: {v} commits; "
                            + ("example commits: " + ", ".join(shas_by_week[d])
                               if shas_by_week[d] else "no commits (zero-value evidence)")
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
            title="Activity Trend",
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
                "unit": "commits/week",
            },
            findings=findings,
        )
    ]


def _empty(summary: str) -> CheckResult:
    return CheckResult(
        check="trend",
        title="Activity Trend",
        severity=Severity.INFO,
        summary=summary,
        order=8,
    )
