"""检查项 6：哪些文件被改得最频繁（热点文件）。"""

from __future__ import annotations

from collections import defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, count_label, fmt_pct, pct, register


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
        f"{len(stats)} files changed in total. The top {len(top)} hotspots account for "
        f"{fmt_pct(top_share)} of all changes; `{hottest_path}` changed "
        f"{hottest['changes']} times with "
        f"{count_label(len(hottest['authors']), 'contributor')}."
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
            title=(
                f"{count_label(len(top), 'file')} "
                f"{'accounts' if len(top) == 1 else 'account'} for "
                f"{fmt_pct(top_share)} of all changes"
            ),
            detail=(
                "Concentration in a small set of files is normal, because core modules are "
                "changed repeatedly. It also means these files carry a large share of review "
                "quality risk and are the most likely places for conflicts and regressions."
            ),
            advice=(
                "Strengthen tests and assign clear ownership for hotspot files. If changes "
                "to a file repeatedly require broad follow-up work, consider a structural "
                "refactor before adding more features."
            ),
            evidence=[
                Evidence(
                    label=p,
                    detail=(
                        f"Changed {s['changes']} times / {s['churn']:,} lines / "
                        f"{len(s['authors'])} contributor(s)"
                    ),
                    lines=[f"- Contributors: {', '.join(sorted(s['authors']))}"]
                    + [f"- Related commit SHA: {sha}" for sha in file_commits[p]],
                    commit=file_commits[p][0] if file_commits[p] else None,
                )
                for p, s in top[:5]
            ],
        )
    ] if top else []

    return [
        CheckResult(
            check="hotspot",
            title="Hotspot Files",
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
                "columns": ["File", "Changes", "Changed lines", "Contributors"],
                "rows": rows,
            },
            findings=findings,
        )
    ]


def _empty() -> CheckResult:
    return CheckResult(
        check="hotspot",
        title="Hotspot Files",
        severity=Severity.INFO,
        summary="No commits in the selected window; analysis unavailable",
        order=6,
    )
