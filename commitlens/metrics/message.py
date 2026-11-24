"""检查项 1：Commit message 是否规范。"""

from __future__ import annotations

import re
from collections import Counter

from ..model import CheckResult, Commit, Evidence, Finding, Severity
from .base import Context, count_label, fmt_pct, register

ISSUE_RE = re.compile(r"(#\d+|\b[A-Z][A-Z0-9]+-\d+\b)")
TYPE_RE = re.compile(r"^([a-z]+)(\([^)]+\))?!?:\s")


def rule_violations(commit: Commit, cfg) -> list[str]:
    """返回这个 commit 违反了哪些规则（空列表表示合规）。"""
    issues: list[str] = []
    subject = commit.subject.strip()
    body = commit.body.strip()

    max_len = cfg("message", "max_subject_length")
    min_len = cfg("message", "min_subject_length")
    if len(subject) > max_len:
        issues.append(f"Subject too long ({len(subject)} > {max_len} characters)")
    if len(subject) < min_len:
        issues.append(f"Subject too short ({len(subject)} < {min_len} characters)")

    if cfg("message", "require_type_prefix"):
        match = TYPE_RE.match(subject)
        allowed = {str(t).lower() for t in cfg("message", "type_prefixes")}
        if not match:
            issues.append("Missing type prefix (for example, feat: or fix:)")
        elif match.group(1).lower() not in allowed:
            issues.append(f"Type prefix {match.group(1)!r} is not in the allowed list")

    if _is_vague(subject, cfg("message", "vague_words")):
        issues.append("Vague subject; it does not explain what changed")

    if cfg("message", "require_issue_ref") and not ISSUE_RE.search(commit.message):
        issues.append("No linked issue reference")

    need_body = cfg("message", "require_body_churn")
    if need_body and commit.churn >= need_body and not body:
        issues.append(f"{commit.churn} changed lines without an explanatory body")

    return issues


def _is_vague(subject: str, vague_words: list[str]) -> bool:
    word = re.sub(r"[\s\.\!\:；;,，。]+$", "", subject.strip().lower())
    if not word:
        return True
    if word in {w.lower() for w in vague_words}:
        return True
    # "fix bug", "update code" 这类：以空话词开头且不超过 3 个词
    tokens = word.split()
    if tokens and tokens[0] in {w.lower() for w in vague_words} and len(tokens) <= 3:
        return True
    return False


def run(ctx: Context) -> list[CheckResult]:
    commits = ctx.commits
    total = len(commits)
    if not total:
        return [_empty()]

    offenders: list[tuple[Commit, list[str]]] = []
    rule_counter: Counter[str] = Counter()
    for c in commits:
        v = rule_violations(c, ctx.cfg)
        if v:
            offenders.append((c, v))
            for item in v:
                rule_counter[item.split(" (", 1)[0]] += 1

    ratio = len(offenders) / total
    severity = _severity(ratio)
    summary = (
        f"{fmt_pct(ratio)} of commits do not meet the message rules "
        f"({len(offenders)}/{total}); most common issue: "
        f"{rule_counter.most_common(1)[0][0] if rule_counter else 'none'}"
    )

    findings: list[Finding] = []
    if severity != Severity.OK:
        offenders.sort(key=lambda x: x[0].churn, reverse=True)
        evidence = [
            Evidence(
                label=f"commit {c.short_sha}",
                detail=f"\"{c.subject}\" changed {c.nfiles} files / {c.churn} lines",
                lines=["- " + v for v in violations],
                commit=c.sha,
            )
            for c, violations in offenders[:5]
        ]
        findings.append(
            Finding(
                check="message",
                severity=severity,
                title=(
                    f"{count_label(len(offenders), 'commit')} "
                    f"{'does' if len(offenders) == 1 else 'do'} not explain what changed"
                ),
                detail=(
                    "Commit messages are the first place reviewers look to understand a change. "
                    "Vague, badly sized, or unexplained subjects force reviewers to inspect the "
                    "diff before they can determine the purpose of the commit."
                ),
                advice=(
                    "Use a consistent subject format such as `<type>: <what changed>` and keep "
                    "it under 72 characters. For changes above 200 lines, explain why the change "
                    "is needed in the body. Teams can enforce prefixes with require_type_prefix."
                ),
                evidence=evidence,
            )
        )

    chart = {
        "type": "bar",
        "labels": [k for k, _ in rule_counter.most_common(6)] or ["No violations"],
        "values": [v for _, v in rule_counter.most_common(6)] or [0],
        "unit": "occurrences",
    }

    return [
        CheckResult(
            check="message",
            title="Commit Message Quality",
            severity=severity,
            summary=summary,
            order=1,
            stats={
                "total": total,
                "offenders": len(offenders),
                "ratio": round(ratio, 4),
                "by_rule": dict(rule_counter),
            },
            chart=chart,
            findings=findings,
        )
    ]


def _severity(ratio: float) -> Severity:
    if ratio >= 0.4:
        return Severity.ALERT
    if ratio >= 0.2:
        return Severity.WARN
    if ratio > 0:
        return Severity.INFO
    return Severity.OK


def _empty() -> CheckResult:
    return CheckResult(
        check="message",
        title="Commit Message Quality",
        severity=Severity.INFO,
        summary="No commits in the selected window; analysis unavailable",
        order=1,
    )
