"""检查项 6：哪些文件被改得最频繁（热点文件）。"""

from __future__ import annotations

from collections import defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, fmt_pct, pct, register


def file_stats(ctx: Context) -> dict[str, dict]:
    """每个文件的改动次数、改动行数、涉及作者。"""
    stats: dict[str, dict] = defaultdict(lambda: {"changes": 0, "churn": 0, "authors": set()})
    for c in ctx.commits:
        for f in c.files:
            s = stats[f.path]
            s["changes"] += 1
            s["churn"] += f.churn
            s["authors"].add(c.author)
    return stats


def run(ctx: Context) -> list[CheckResult]:
    if not ctx.commits:
        return [_empty()]

    stats = file_stats(ctx)
    total_changes = sum(s["changes"] for s in stats.values()) or 1
    top_n = ctx.cfg("hotspot", "top_n")
    min_changes = ctx.cfg("hotspot", "min_changes")

    ranked = sorted(stats.items(), key=lambda kv: (kv[1]["changes"], kv[1]["churn"]), reverse=True)
    top = [(p, s) for p, s in ranked if s["changes"] >= min_changes][:top_n]
    top_share = sum(s["changes"] for _, s in top) / total_changes

    hottest_path, hottest = ranked[0] if ranked else ("-", {"changes": 0, "authors": set()})
    severity = Severity.WARN if pct(hottest["changes"], total_changes) >= 0.4 else Severity.INFO
    summary = (
        f"共 {len(stats)} 个文件被改动；最热的 {len(top)} 个文件承担了 "
        f"{fmt_pct(top_share)} 的改动，其中 `{hottest_path}` 被改 {hottest['changes']} 次"
        f"（{len(hottest['authors'])} 人参与）"
    )

    rows = [
        [f"`{p}`", str(s["changes"]), f"{s['churn']:,}", str(len(s["authors"]))]
        for p, s in top
    ]

    # Preserve commit-level witnesses for every aggregate reported to users.
    file_commits: dict[str, list[str]] = defaultdict(list)
    watched = {p for p, _ in top[:5]}
    for c in reversed(ctx.commits):
        for f in c.files:
            if f.path in watched and len(file_commits[f.path]) < 8:
                file_commits[f.path].append(c.sha)

    findings = [
        Finding(
            check="hotspot",
            severity=severity,
            title=f"{len(top)} 个文件承担了 {fmt_pct(top_share)} 的改动",
            detail=(
                "改动集中在少数文件上是正常现象（核心模块本来就会被反复修改），"
                "但它意味着两件事：这些文件的评审质量决定了整个项目的质量；"
                "以及它们是最容易产生冲突和回归的地方。"
            ),
            advice=(
                "给热点文件配更强的测试与更明确的 owner；"
                "如果某文件每次改动都要大范围返工，考虑先做一次结构性重构。"
            ),
            evidence=[
                Evidence(
                    label=p,
                    detail=f"被改 {s['changes']} 次 / {s['churn']:,} 行 / {len(s['authors'])} 人参与",
                    lines=[f"- 参与人：{'、'.join(sorted(s['authors']))}"]
                    + [f"- 关联提交 SHA：{sha}" for sha in file_commits[p]],
                    commit=file_commits[p][0] if file_commits[p] else None,
                )
                for p, s in top[:5]
            ],
        )
    ] if top else []

    return [
        CheckResult(
            check="hotspot",
            title="热点文件",
            severity=severity,
            summary=summary,
            order=6,
            stats={
                "n_files": len(stats),
                "top_share": round(top_share, 4),
                "top": [{"path": p, "changes": s["changes"], "authors": len(s["authors"])} for p, s in top],
            },
            chart={
                "type": "table",
                "columns": ["文件", "改动次数", "改动行数", "参与人数"],
                "rows": rows,
            },
            findings=findings,
        )
    ]


def _empty() -> CheckResult:
    return CheckResult(
        check="hotspot",
        title="热点文件",
        severity=Severity.INFO,
        summary="窗口内没有提交，无法评估",
        order=6,
    )
