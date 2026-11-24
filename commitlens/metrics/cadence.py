"""检查项 4、5：提交节奏异常（过于频繁 / 长期未提交）。"""

from __future__ import annotations

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, by_author, count_label, fmt_pct, median, register


def _weeks_span(ctx: Context) -> float:
    rng = ctx.date_range
    if not rng:
        return 1.0
    days = (rng[1] - rng[0]).days + 1
    return max(days / 7.0, 1.0)


def _others_median(rates: dict[str, float], author: str) -> float:
    """留一法：拿「其他人」的中位数做参照，两个人也能比。"""
    others = [r for a, r in rates.items() if a != author]
    return median(others) if others else 0.0


def run(ctx: Context) -> list[CheckResult]:
    if not ctx.commits:
        return [
            _empty("cadence_freq", "Commit Cadence", 4),
            _empty("cadence_dormant", "Dormant Contributors", 5),
        ]

    per_author = by_author(ctx.commits)
    weeks = _weeks_span(ctx)
    total = len(ctx.commits)
    n_people = len(per_author)
    counts = {a: len(cs) for a, cs in per_author.items()}
    rates = {a: counts[a] / weeks for a in per_author}
    med = median(list(rates.values()))
    mean_count = total / max(n_people, 1)
    multiplier = ctx.cfg("cadence", "freq_multiplier")
    min_commits = ctx.cfg("cadence", "min_commits_for_freq")

    # 只有「明显高于其他人」且「绝对频率也不低」才算异常。
    # 两个门槛都必要：窗口很长时，其他人的中位数会被偶尔提交的人稀释成 0，
    # 只比倍数的话，一个每周提交一次的人也会被判成"异常"。
    frequent = [
        (a, rates[a], counts[a])
        for a in rates
        if n_people >= 2
        and counts[a] >= min_commits
        and rates[a] >= 1.0
        and rates[a] > _others_median(rates, a) * multiplier
    ]
    frequent.sort(key=lambda x: x[1], reverse=True)

    # ---------- 检查项 4：提交频率 ----------
    freq_sev = (
        Severity.WARN if len(frequent) >= 3 else
        Severity.INFO if frequent else Severity.OK
    )
    freq_summary = (
        f"{total} commits from {count_label(n_people, 'contributor')} in the selected window, "
        f"averaging {mean_count:.1f} commits per contributor"
        + (
            f"; {len(frequent)} contributors commit substantially more often than their peers"
            if frequent else "; no contributor has an unusual commit frequency"
        )
    )

    freq_findings: list[Finding] = []
    if frequent:
        freq_findings.append(
            Finding(
                check="cadence_freq",
                severity=freq_sev,
                title=(
                    f"{count_label(len(frequent), 'contributor')} "
                    f"{'commits' if len(frequent) == 1 else 'commit'} substantially more often "
                    "than the team median"
                ),
                detail=(
                    "A high commit rate is not necessarily a problem: the contributor may be "
                    "handling more work, or the history may use commits that are too granular. "
                    "This metric is observational and must not be used for individual performance "
                    "evaluation."
                ),
                advice=(
                    "If commits are too granular, agree that each commit should represent one "
                    "independently understandable change. If work is unevenly assigned, review "
                    "the backlog allocation."
                ),
                evidence=[
                    Evidence(
                        label=a,
                        detail=(
                            f"{count} commits over {weeks:.0f} weeks (~{r:.2f} per week; "
                            f"peers' median: {_others_median(rates, a):.2f})"
                        ),
                        commit=max(per_author[a], key=lambda c: c.date).sha,
                    )
                    for a, r, count in frequent[:5]
                ],
            )
        )

    # 用绝对提交数画图：长窗口下"次/周"会小到看不出差异
    top_authors = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    freq_chart = {
        "type": "bar",
        "labels": [a for a, _ in top_authors],
        "values": [c for _, c in top_authors],
        "unit": "commits",
    }

    freq_result = CheckResult(
        check="cadence_freq",
        title="Commit Cadence",
        severity=freq_sev,
        summary=freq_summary,
        order=4,
        stats={"weeks": round(weeks, 1), "median_per_week": round(med, 2),
               "frequent": [a for a, _, _ in frequent]},
        chart=freq_chart,
        findings=freq_findings,
    )

    # ---------- 检查项 5：长期未提交 ----------
    dormant_days = ctx.cfg("cadence", "dormant_days")
    min_for_dormant = ctx.cfg("cadence", "min_commits_for_dormant")

    dormant = []
    for a, cs in per_author.items():
        last = max(c.date for c in cs)
        gap = max((ctx.now - last).days, 0)
        if gap >= dormant_days and len(cs) >= min_for_dormant:
            dormant.append((a, gap, len(cs), last, cs))
    dormant.sort(key=lambda x: x[1], reverse=True)

    ratio_dormant = len(dormant) / max(len(per_author), 1)
    dormant_sev = (
        Severity.ALERT if ratio_dormant >= 0.5 else
        Severity.WARN if dormant else Severity.OK
    )
    if dormant:
        who = ", ".join(a for a, *_ in dormant[:3])
        dormant_summary = (
            f"{count_label(len(dormant), 'contributor')} "
            f"{'has' if len(dormant) == 1 else 'have'} not committed for at least "
            f"{dormant_days} days "
            f"({who}), representing {fmt_pct(ratio_dormant)} of contributors"
        )
    else:
        dormant_summary = f"No contributor has been inactive for more than {dormant_days} days"

    dormant_findings: list[Finding] = []
    if dormant:
        dormant_findings.append(
            Finding(
                check="cadence_dormant",
                severity=dormant_sev,
                title=(
                    f"{count_label(len(dormant), 'contributor')} "
                    f"{'has' if len(dormant) == 1 else 'have'} been inactive for at least "
                    f"{dormant_days} days"
                ),
                detail=(
                    "A long period without commits usually means one of three things: the work "
                    "is complete, the contributor is blocked, or the contributor has left the "
                    "team. Check this weekly so that hidden blockers are found early."
                ),
                advice=(
                    "Check in directly to determine whether the contributor is blocked. Assign "
                    "more work if the backlog is empty, or adjust ownership promptly if the "
                    "contributor is no longer involved."
                ),
                evidence=[
                    Evidence(
                        label=a,
                        detail=f"Last commit {last.date()} ({gap} days ago); {count} commits before that",
                        commit=max(cs, key=lambda c: c.date).sha,
                    )
                    for a, gap, count, last, cs in dormant[:6]
                ],
            )
        )

    dormant_chart = {
        "type": "bar",
        "labels": [a for a, *_ in dormant] or ["None"],
        "values": [g for _, g, *_ in dormant] or [0],
        "unit": "days since last commit",
    }

    dormant_result = CheckResult(
        check="cadence_dormant",
        title="Dormant Contributors",
        severity=dormant_sev,
        summary=dormant_summary,
        order=5,
        stats={"dormant": [a for a, *_ in dormant], "threshold_days": dormant_days},
        chart=dormant_chart,
        findings=dormant_findings,
    )

    return [freq_result, dormant_result]


def _empty(check: str, title: str, order: int) -> CheckResult:
    return CheckResult(
        check=check,
        title=title,
        severity=Severity.INFO,
        summary="No commits in the selected window; analysis unavailable",
        order=order,
    )
