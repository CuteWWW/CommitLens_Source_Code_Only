"""检查项 7：谁经常改同一文件 / 知识是否过度集中。"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, count_label, fmt_pct, pct, register


def author_file_counts(ctx: Context) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for c in ctx.commits:
        for f in c.files:
            out[f.path][c.author] += 1
    return dict(out)


def bus_factor(counts: dict[str, Counter], coverage: float) -> tuple[int, list[tuple[str, float]]]:
    """覆盖 coverage 比例的改动，最少需要几个作者。

    返回 (人数, [(作者, 累计占比), ...])。
    """
    totals = Counter()
    for per_author in counts.values():
        for author, n in per_author.items():
            totals[author] += n
    grand = sum(totals.values())
    if not grand:
        return 0, []

    ranked = totals.most_common()
    cum = 0.0
    out: list[tuple[str, float]] = []
    for author, n in ranked:
        cum += n
        share = cum / grand
        out.append((author, share))
        if share >= coverage:
            break
    return len(out), out


def run(ctx: Context) -> list[CheckResult]:
    if not ctx.commits:
        return [_empty()]

    counts = author_file_counts(ctx)
    min_changes = ctx.cfg("ownership", "min_changes")
    share_threshold = ctx.cfg("ownership", "single_owner_share")
    coverage = ctx.cfg("ownership", "bus_factor_coverage")
    shared_min = ctx.cfg("ownership", "shared_min_authors")
    top_n = ctx.cfg("ownership", "top_n")

    eligible = {p: c for p, c in counts.items() if sum(c.values()) >= min_changes}
    single_owner: list[tuple[str, Counter, float]] = []
    shared: list[tuple[str, Counter]] = []

    for path, counter in eligible.items():
        total = sum(counter.values())
        top_author, top_count = counter.most_common(1)[0]
        share = top_count / total
        if share >= share_threshold:
            single_owner.append((path, counter, share))
        elif len(counter) >= shared_min:
            shared.append((path, counter))

    single_owner.sort(key=lambda x: (x[1].total(), x[2]), reverse=True)
    shared.sort(key=lambda x: len(x[1]), reverse=True)

    bf, cum = bus_factor(counts, coverage)
    ratio_single = pct(len(single_owner), max(len(eligible), 1))

    severity = (
        Severity.ALERT if ratio_single >= 0.30 else
        Severity.WARN if len(single_owner) >= 3 else
        Severity.INFO if single_owner else Severity.OK
    )
    summary = (
        f"Of {len(eligible)} files changed at least {min_changes} times, "
        f"{len(single_owner)} ({fmt_pct(ratio_single)}) have at least "
        f"{share_threshold:.0%} of their changes from one contributor. "
        f"Contribution-concentration proxy = {bf}: the smallest contributor group covering "
        f"{coverage:.0%} of historical file changes. This does not measure actual knowledge."
    )

    watched = {p for p, _, _ in single_owner[:5]} | {p for p, _ in shared[:5]}
    file_commits: dict[str, list[str]] = defaultdict(list)
    for c in reversed(ctx.commits):
        for f in c.files:
            if f.path in watched and len(file_commits[f.path]) < 8:
                file_commits[f.path].append(c.sha)

    findings: list[Finding] = []
    if single_owner:
        findings.append(
            Finding(
                check="ownership",
                severity=severity,
                title=(
                    f"Changes to {count_label(len(single_owner), 'file')} are concentrated in "
                    f"one contributor (activity proxy: {bf})"
                ),
                detail=(
                    "This metric counts how often contributors changed each file. It is an "
                    "activity proxy, not proof of who understands the code or who can take over. "
                    "Use it to identify handoff risks that deserve human review."
                ),
                advice=(
                    "Run a paired review or rotate ownership for hotspots dominated by one "
                    "contributor. Add documentation and tests so a second contributor can take "
                    "over within half a day."
                ),
                evidence=[
                    Evidence(
                        label=p,
                        detail=(
                            f"{share:.0%} of changes came from {counter.most_common(1)[0][0]} "
                            f"({counter.most_common(1)[0][1]} of {counter.total()} changes; "
                            f"{len(counter)} contributor(s))"
                        ),
                        lines=[f"- {a}: {n} changes" for a, n in counter.most_common(6)]
                        + [f"- Related commit SHA: {sha}" for sha in file_commits[p]],
                        commit=file_commits[p][0] if file_commits[p] else None,
                    )
                    for p, counter, share in single_owner[:5]
                ],
            )
        )

    if shared:
        findings.append(
            Finding(
                check="ownership",
                severity=Severity.INFO,
                title=(
                    f"{count_label(len(shared), 'file')} "
                    f"{'was' if len(shared) == 1 else 'were'} repeatedly changed by at least "
                    f"{shared_min} contributors"
                ),
                detail=(
                    "Files changed repeatedly by multiple contributors are shared coordination "
                    "points. They are prone to merge conflicts and may indicate an unclear module "
                    "boundary."
                ),
                advice=(
                    "Split shared coordination points into modules with narrower responsibilities. "
                    "Include recent authors in reviews for files with frequent conflicts."
                ),
                evidence=[
                    Evidence(
                        label=p,
                        detail=f"{counter.total()} changes from {len(counter)} contributor(s)",
                        lines=[f"- {a}: {n} changes" for a, n in counter.most_common(6)]
                        + [f"- Related commit SHA: {sha}" for sha in file_commits[p]],
                        commit=file_commits[p][0] if file_commits[p] else None,
                    )
                    for p, counter in shared[:5]
                ],
            )
        )

    rows = [
        [f"`{p}`", c.most_common(1)[0][0], f"{s:.0%}", str(c.total()), str(len(c))]
        for p, c, s in single_owner[:top_n]
    ]

    return [
        CheckResult(
            check="ownership",
            title="Knowledge Concentration",
            severity=severity,
            summary=summary,
            order=7,
            stats={
                "eligible_files": len(eligible),
                "single_owner_files": len(single_owner),
                "ratio": round(ratio_single, 4),
                "bus_factor": bf,
                "bus_factor_interpretation": "historical file-change concentration proxy; not verified knowledge or expertise",
                "bus_factor_cumulative": [(a, round(s, 3)) for a, s in cum],
                "shared_files": [p for p, _ in shared[:10]],
            },
            chart={
                "type": "table",
                "columns": ["File", "Dominant contributor", "Share", "Changes", "Contributors"],
                "rows": rows,
            },
            findings=findings,
        )
    ]


def _empty() -> CheckResult:
    return CheckResult(
        check="ownership",
        title="Knowledge Concentration",
        severity=Severity.INFO,
        summary="No commits in the selected window; analysis unavailable",
        order=7,
    )
