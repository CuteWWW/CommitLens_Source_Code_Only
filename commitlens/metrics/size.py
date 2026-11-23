"""检查项 2、3：单次改动规模 / 巨型提交。"""

from __future__ import annotations

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, fmt_pct, pct, percentile, register

BUCKETS = [
    ("1 个文件", lambda n: n == 1),
    ("2-3 个", lambda n: 2 <= n <= 3),
    ("4-9 个", lambda n: 4 <= n <= 9),
    ("10-29 个", lambda n: 10 <= n <= 29),
    ("30-99 个", lambda n: 30 <= n <= 99),
    ("100+ 个", lambda n: n >= 100),
]


def run(ctx: Context) -> list[CheckResult]:
    commits = ctx.commits
    if not commits:
        return [_empty("size", "单次改动规模", 2), _empty("giant", "巨型提交", 3)]

    files = [float(c.nfiles) for c in commits]
    churns = [float(c.churn) for c in commits]
    total_churn = sum(churns) or 1.0

    max_files = ctx.cfg("size", "max_files")
    max_lines = ctx.cfg("size", "max_lines")
    top_n = ctx.cfg("size", "top_n")
    rel_q = ctx.cfg("size", "giant_percentile")
    # A percentile alone always flags the largest commit. Require an actual
    # outlier and enough observations to make the tail estimate meaningful.
    use_relative = len(commits) >= 20 and 0 < rel_q < 100
    p_files = percentile(files, rel_q)
    p_lines = percentile(churns, rel_q)
    m_files = percentile(files, 50)
    m_lines = percentile(churns, 50)

    def is_relative_outlier(c) -> bool:
        return use_relative and (
            (c.nfiles > p_files and c.nfiles >= max(4, 3 * m_files))
            or (c.churn > p_lines and c.churn >= max(100, 3 * m_lines))
        )

    wide = [c for c in commits if c.nfiles > max_files]
    giant = [c for c in commits if c.nfiles >= max_files or c.churn >= max_lines
             or is_relative_outlier(c)]
    giant_churn = sum(c.churn for c in giant) / total_churn

    # ---------- 检查项 2：改动规模分布 ----------
    size_sev = (
        Severity.WARN if len(wide) / len(commits) >= 0.1 else
        Severity.INFO if wide else Severity.OK
    )
    size_summary = (
        f"提交改动中位数 {percentile(files, 50):.0f} 个文件、"
        f"{percentile(churns, 50):.0f} 行；P90 为 {percentile(files, 90):.0f} 个文件，"
        f"{len(wide)} 个提交改了超过 {max_files} 个文件"
    )

    size_findings: list[Finding] = []
    if wide:
        wide.sort(key=lambda c: c.nfiles, reverse=True)
        size_findings.append(
            Finding(
                check="size",
                severity=size_sev,
                title=f"{len(wide)} 个提交改动文件数超过阈值（{max_files} 个）",
                detail=(
                    "单个提交横跨太多文件时，评审者很难在有限时间内判断改动是否安全，"
                    "回滚时也只能整块撤回。大规模改动本身不是错误，但需要拆成"
                    "「可独立理解、可独立回滚」的若干提交。"
                ),
                advice="按功能点拆分提交；纯格式化/重命名单独一个提交；依赖升级单独一个提交。",
                evidence=[
                    Evidence(
                        label=f"commit {c.short_sha}",
                        detail=f"{c.nfiles} 个文件 / {c.churn} 行 · {c.author} ·「{c.subject}」",
                        lines=[f"- {p}" for p in c.paths[:12]]
                        + ([f"- …… 还有 {c.nfiles - 12} 个文件"] if c.nfiles > 12 else []),
                        commit=c.sha,
                    )
                    for c in wide[:top_n]
                ],
            )
        )

    bucket_counts = [sum(1 for n in files if pred(int(n))) for _, pred in BUCKETS]
    size_chart = {
        "type": "bar",
        "labels": [name for name, _ in BUCKETS],
        "values": bucket_counts,
        "unit": "个提交",
    }

    size_result = CheckResult(
        check="size",
        title="单次改动规模",
        severity=size_sev,
        summary=size_summary,
        order=2,
        stats={
            "median_files": percentile(files, 50),
            "p90_files": percentile(files, 90),
            "median_churn": percentile(churns, 50),
            "p95_churn": percentile(churns, 95),
            "over_max_files": len(wide),
            "max_files": max_files,
        },
        chart=size_chart,
        findings=size_findings,
    )

    # ---------- 检查项 3：巨型提交 ----------
    giant_sev = (
        Severity.ALERT if giant_churn >= 0.30 else
        Severity.WARN if giant_churn >= 0.15 else
        Severity.INFO if giant else Severity.OK
    )
    giant_summary = (
        f"{len(giant)} 个规模异常提交（≥ {max_files} 文件、≥ {max_lines} 行"
        f"或 P{rel_q:g} 极端离群），"
        f"占 {fmt_pct(len(giant) / len(commits))} 的提交数、"
        f"却吃掉 {fmt_pct(giant_churn)} 的代码改动量"
    )

    giant_findings: list[Finding] = []
    if giant:
        giant.sort(key=lambda c: c.churn, reverse=True)
        giant_findings.append(
            Finding(
                check="giant",
                severity=giant_sev,
                title=f"{len(giant)} 个巨型提交吃掉了 {fmt_pct(giant_churn)} 的代码变更量",
                detail=(
                    "大规模修改可能提高评审难度，但规模并不能证明代码质量差，"
                    "也不能判断是否混合了多个关注点。请结合 diff 和变更目的人工复核。"
                ),
                advice=(
                    "把大改动拆成「准备型改动 + 功能改动 + 清理型改动」；"
                    "确实无法拆分的（如批量重命名）在提交信息里标明，评审时跳过逐行检查。"
                ),
                evidence=[
                    Evidence(
                        label=f"commit {c.short_sha}",
                        detail=(
                            f"{c.nfiles} 个文件 / {c.churn} 行"
                            f"（占全项目改动 {pct(c.churn, total_churn) * 100:.1f}%）"
                            f" · {c.author}"
                        ),
                        lines=[f"- {p}" for p in c.paths[:12]]
                        + ([f"- …… 还有 {c.nfiles - 12} 个文件"] if c.nfiles > 12 else []),
                        commit=c.sha,
                    )
                    for c in giant[:top_n]
                ],
            )
        )

    giant_chart = {
        "type": "bar",
        "labels": [c.short_sha for c in giant[:8]] or ["无"],
        "values": [c.churn for c in giant[:8]] or [0],
        "unit": "行改动",
    }

    giant_result = CheckResult(
        check="giant",
        title="巨型提交",
        severity=giant_sev,
        summary=giant_summary,
        order=3,
        stats={
            "count": len(giant),
            "ratio": round(len(giant) / len(commits), 4),
            "churn_share": round(giant_churn, 4),
            "max_files": max_files,
            "max_lines": max_lines,
            "giant_percentile": rel_q,
            "percentile_files_threshold": round(p_files, 2),
            "percentile_churn_threshold": round(p_lines, 2),
            "relative_enabled": use_relative,
        },
        chart=giant_chart,
        findings=giant_findings,
    )

    return [size_result, giant_result]


def _empty(check: str, title: str, order: int) -> CheckResult:
    return CheckResult(
        check=check,
        title=title,
        severity=Severity.INFO,
        summary="窗口内没有提交，无法评估",
        order=order,
    )
