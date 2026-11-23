"""检查项 1：Commit message 是否规范。"""

from __future__ import annotations

import re
from collections import Counter

from ..model import CheckResult, Commit, Evidence, Finding, Severity
from .base import Context, fmt_pct, register

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
        issues.append(f"标题过长（{len(subject)} > {max_len} 字符）")
    if len(subject) < min_len:
        issues.append(f"标题过短（{len(subject)} < {min_len} 字符）")

    if cfg("message", "require_type_prefix"):
        match = TYPE_RE.match(subject)
        allowed = {str(t).lower() for t in cfg("message", "type_prefixes")}
        if not match:
            issues.append("缺少类型前缀（如 feat: / fix:）")
        elif match.group(1).lower() not in allowed:
            issues.append(f"类型前缀 {match.group(1)!r} 不在允许列表中")

    if _is_vague(subject, cfg("message", "vague_words")):
        issues.append("内容空泛，看不出改了什么")

    if cfg("message", "require_issue_ref") and not ISSUE_RE.search(commit.message):
        issues.append("没有关联 issue 编号")

    need_body = cfg("message", "require_body_churn")
    if need_body and commit.churn >= need_body and not body:
        issues.append(f"改动 {commit.churn} 行却没有正文说明")

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
                rule_counter[item.split("（")[0]] += 1

    ratio = len(offenders) / total
    severity = _severity(ratio)
    summary = (
        f"{fmt_pct(ratio)} 的提交不符合规范（{len(offenders)}/{total}）"
        f"，最常见问题：{rule_counter.most_common(1)[0][0] if rule_counter else '无'}"
    )

    findings: list[Finding] = []
    if severity != Severity.OK:
        offenders.sort(key=lambda x: x[0].churn, reverse=True)
        evidence = [
            Evidence(
                label=f"commit {c.short_sha}",
                detail=f"「{c.subject}」改动 {c.nfiles} 个文件 / {c.churn} 行",
                lines=["- " + v for v in violations],
                commit=c.sha,
            )
            for c, violations in offenders[:5]
        ]
        findings.append(
            Finding(
                check="message",
                severity=severity,
                title=f"{len(offenders)} 个提交的信息无法说明改了什么",
                detail=(
                    "提交信息是评审者理解改动的第一入口。标题空泛、长度失控或缺少"
                    "背景说明，会迫使人去读 diff 才能判断这个提交在干什么。"
                ),
                advice=(
                    "约定标题格式（建议 `<type>: <做了什么>`，不超过 72 字符）；"
                    "改动超过 200 行时在正文写清「为什么改」；"
                    "团队统一后可用 require_type_prefix 在提交前拦截。"
                ),
                evidence=evidence,
            )
        )

    chart = {
        "type": "bar",
        "labels": [k for k, _ in rule_counter.most_common(6)] or ["无违规"],
        "values": [v for _, v in rule_counter.most_common(6)] or [0],
        "unit": "次",
    }

    return [
        CheckResult(
            check="message",
            title="提交信息规范",
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
        title="提交信息规范",
        severity=Severity.INFO,
        summary="窗口内没有提交，无法评估",
        order=1,
    )
