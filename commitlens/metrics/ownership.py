"""检查项 7：谁经常改同一文件 / 知识是否过度集中。"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..model import CheckResult, Evidence, Finding, Severity
from .base import Context, fmt_pct, pct, register


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
        f"在 {len(eligible)} 个被改动 ≥{min_changes} 次的文件中，{len(single_owner)} 个"
        f"（{fmt_pct(ratio_single)}）有 {share_threshold:.0%} 以上的改动来自同一人；"
        f"贡献活动集中度代理 = {bf} 人（{bf} 人累计覆盖 {coverage:.0%} 的历史文件修改次数，"
        "不能据此推断真实知识覆盖）"
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
                title=f"{len(single_owner)} 个文件的修改活动集中于同一人（活动代理 {bf}）",
                detail=(
                    "此指标仅统计作者修改过文件的次数，属于贡献活动代理。"
                    "不能单凭这些记录判断谁掌握代码知识、谁能够接手或谁离开会造成知识流失。"
                    "可用它识别值得人工核实的潜在交接点。"
                ),
                advice=(
                    "对单人垄断的热点文件做一次结对评审或轮换 owner；"
                    "补上该模块的文档与测试，让第二个人能在半天内接手。"
                ),
                evidence=[
                    Evidence(
                        label=p,
                        detail=(
                            f"{share:.0%} 的改动来自 {counter.most_common(1)[0][0]}"
                            f"（{counter.most_common(1)[0][1]} 次 / 共 {counter.total()} 次，"
                            f"{len(counter)} 人参与）"
                        ),
                        lines=[f"- {a}: {n} 次" for a, n in counter.most_common(6)]
                        + [f"- 关联提交 SHA：{sha}" for sha in file_commits[p]],
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
                title=f"{len(shared)} 个文件被 {shared_min} 人以上反复修改，属于协作密集区",
                detail=(
                    "多人反复改同一个文件，通常意味着它是团队的公共交叉点："
                    "容易产生合并冲突，也说明这块的设计可能不够清晰。"
                ),
                advice="把公共交叉点拆成职责更单一的模块；冲突频繁的文件在评审时优先安排原作者参与。",
                evidence=[
                    Evidence(
                        label=p,
                        detail=f"{counter.total()} 次改动，{len(counter)} 人参与",
                        lines=[f"- {a}: {n} 次" for a, n in counter.most_common(6)]
                        + [f"- 关联提交 SHA：{sha}" for sha in file_commits[p]],
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
            title="知识集中度",
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
                "columns": ["文件", "主导者", "占比", "改动次数", "参与人数"],
                "rows": rows,
            },
            findings=findings,
        )
    ]


def _empty() -> CheckResult:
    return CheckResult(
        check="ownership",
        title="知识集中度",
        severity=Severity.INFO,
        summary="窗口内没有提交，无法评估",
        order=7,
    )
