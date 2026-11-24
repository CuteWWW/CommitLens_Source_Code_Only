"""检查项 2、3：单次改动规模 / 巨型提交。"""

from __future__ import annotations

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, count_label, fmt_pct, pct, percentile, register

BUCKETS = [
    ("1 file", lambda n: n == 1),
    ("2-3 files", lambda n: 2 <= n <= 3),
    ("4-9 files", lambda n: 4 <= n <= 9),
    ("10-29 files", lambda n: 10 <= n <= 29),
    ("30-99 files", lambda n: 30 <= n <= 99),
    ("100+ files", lambda n: n >= 100),
]


def run(ctx: Context) -> list[CheckResult]:
    commits = ctx.commits
    if not commits:
        return [_empty("size", "Change Size", 2), _empty("giant", "Oversized Commits", 3)]

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
        f"Median commit size is {percentile(files, 50):.0f} files and "
        f"{percentile(churns, 50):.0f} lines; P90 is {percentile(files, 90):.0f} files. "
        f"{count_label(len(wide), 'commit')} changed more than {max_files} files."
    )

    size_findings: list[Finding] = []
    if wide:
        wide.sort(key=lambda c: c.nfiles, reverse=True)
        size_findings.append(
            Finding(
                check="size",
                severity=size_sev,
                title=(
                    f"{count_label(len(wide), 'commit')} "
                    f"{'exceeds' if len(wide) == 1 else 'exceed'} "
                    f"the file-count threshold ({max_files})"
                ),
                detail=(
                    "When one commit spans too many files, reviewers cannot easily determine "
                    "whether the change is safe, and rollback can only revert the entire batch. "
                    "A large change is not inherently wrong, but it should be split into commits "
                    "that can be understood and reverted independently."
                ),
                advice=(
                    "Split commits by functional change. Keep formatting-only changes, renames, "
                    "and dependency upgrades in separate commits."
                ),
                evidence=[
                    Evidence(
                        label=f"commit {c.short_sha}",
                        detail=(
                            f"Files changed: {c.nfiles} / Lines changed: {c.churn} · "
                            f"{c.author} · \"{c.subject}\""
                        ),
                        lines=[f"- {p}" for p in c.paths[:12]]
                        + ([f"- ... and {c.nfiles - 12} more files"] if c.nfiles > 12 else []),
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
        "unit": "commits",
    }

    size_result = CheckResult(
        check="size",
        title="Change Size",
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
        f"Oversized commit signals: {len(giant)} "
        f"(>= {max_files} files, >= {max_lines} lines, or above the P{rel_q:g} tail). "
        f"{'It accounts' if len(giant) == 1 else 'They account'} for "
        f"{fmt_pct(len(giant) / len(commits))} of commits and "
        f"{fmt_pct(giant_churn)} of all changed lines."
    )

    giant_findings: list[Finding] = []
    if giant:
        giant.sort(key=lambda c: c.churn, reverse=True)
        giant_findings.append(
            Finding(
                check="giant",
                severity=giant_sev,
                title=(
                    f"Oversized commits: {len(giant)} accounting for "
                    f"{fmt_pct(giant_churn)} of changed lines"
                ),
                detail=(
                    "Large changes can make review harder, but size alone does not prove poor "
                    "code quality or confirm that unrelated concerns were mixed together. "
                    "Review the diff and the stated purpose before drawing conclusions."
                ),
                advice=(
                    "Split large changes into preparation, functional, and cleanup commits. "
                    "For changes that cannot be split, such as bulk renames, state that in the "
                    "message and use a review strategy that does not require line-by-line checks."
                ),
                evidence=[
                    Evidence(
                        label=f"commit {c.short_sha}",
                        detail=(
                            f"{c.nfiles} files / {c.churn} lines "
                            f"({pct(c.churn, total_churn) * 100:.1f}% of all changed lines)"
                            f" · {c.author}"
                        ),
                        lines=[f"- {p}" for p in c.paths[:12]]
                        + ([f"- ... and {c.nfiles - 12} more files"] if c.nfiles > 12 else []),
                        commit=c.sha,
                    )
                    for c in giant[:top_n]
                ],
            )
        )

    giant_chart = {
        "type": "bar",
        "labels": [c.short_sha for c in giant[:8]] or ["None"],
        "values": [c.churn for c in giant[:8]] or [0],
        "unit": "changed lines",
    }

    giant_result = CheckResult(
        check="giant",
        title="Oversized Commits",
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
        summary="No commits in the selected window; analysis unavailable",
        order=order,
    )
