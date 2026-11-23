"""检查项 4、5：提交节奏异常（过于频繁 / 长期未提交）。"""

from __future__ import annotations

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, by_author, fmt_pct, median, register


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
            _empty("cadence_freq", "提交频率异常", 4),
            _empty("cadence_dormant", "长期未提交", 5),
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
        f"窗口内 {total} 次提交、{n_people} 人参与，人均 {mean_count:.1f} 次"
        + (
            f"；{len(frequent)} 人提交频率显著高于其他人"
            if frequent else "；没有人的提交频率明显偏离团队"
        )
    )

    freq_findings: list[Finding] = []
    if frequent:
        freq_findings.append(
            Finding(
                check="cadence_freq",
                severity=freq_sev,
                title=f"{len(frequent)} 人的提交频率显著高于团队中位数",
                detail=(
                    "提交频率高不必然是坏事：可能承担了更多任务，也可能是提交粒度过细"
                    "（每改一行就提交一次），后者会让历史难以阅读。这个指标只用于观察，"
                    "不应作为个人绩效依据。"
                ),
                advice=(
                    "如果是粒度过细，约定「一次提交对应一个可独立理解的改动」；"
                    "如果是任务分配不均，检查一下 backlog 的分配。"
                ),
                evidence=[
                    Evidence(
                        label=a,
                        detail=(
                            f"{count} 次提交 / {weeks:.0f} 周 ≈ {r:.2f} 次每周"
                            f"（其他人中位数 {_others_median(rates, a):.2f}）"
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
        "unit": "次",
    }

    freq_result = CheckResult(
        check="cadence_freq",
        title="提交频率异常",
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
        who = "、".join(a for a, *_ in dormant[:3])
        dormant_summary = (
            f"{len(dormant)} 人已连续 ≥{dormant_days} 天没有提交（{who}），"
            f"占 {fmt_pct(ratio_dormant)} 的贡献者"
        )
    else:
        dormant_summary = f"没有成员超过 {dormant_days} 天未提交"

    dormant_findings: list[Finding] = []
    if dormant:
        dormant_findings.append(
            Finding(
                check="cadence_dormant",
                severity=dormant_sev,
                title=f"{len(dormant)} 人已连续 {dormant_days} 天以上没有提交",
                detail=(
                    "长期沉默的成员通常意味着三件事之一：任务已完成、任务被卡住、"
                    "或已经脱离团队。越晚发现越难补救，所以这条要按周看而不是按学期看。"
                ),
                advice="一对一确认是否被阻塞；如果是任务空了，从 backlog 里补；如果是退出，及时调整分工。",
                evidence=[
                    Evidence(
                        label=a,
                        detail=f"最后提交 {last.date()}（距今 {gap} 天），此前共 {count} 次",
                        commit=max(cs, key=lambda c: c.date).sha,
                    )
                    for a, gap, count, last, cs in dormant[:6]
                ],
            )
        )

    dormant_chart = {
        "type": "bar",
        "labels": [a for a, *_ in dormant] or ["无"],
        "values": [g for _, g, *_ in dormant] or [0],
        "unit": "天未提交",
    }

    dormant_result = CheckResult(
        check="cadence_dormant",
        title="长期未提交",
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
        summary="窗口内没有提交，无法评估",
        order=order,
    )
