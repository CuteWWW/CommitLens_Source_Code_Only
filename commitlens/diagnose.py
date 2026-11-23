"""诊断层：把 8 个指标结果变成「体检结论」。

设计原则（对应 NFR-2 可解释性）：
    分数不是模型算出来的，而是一条条扣分明细加出来的，
    每一条扣分都能在报告里指回到具体检查项和证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import CheckResult, Severity

PENALTY = {Severity.ALERT: 8, Severity.WARN: 4, Severity.INFO: 0, Severity.OK: 0}


@dataclass
class Deduction:
    check: str
    title: str
    severity: Severity
    points: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "title": self.title,
            "severity": self.severity.value,
            "points": self.points,
        }


@dataclass
class Diagnosis:
    results: list[CheckResult]
    score: int | None
    grade: str
    deductions: list[Deduction] = field(default_factory=list)
    headlines: list[CheckResult] = field(default_factory=list)
    scorable: bool = True

    @property
    def worst(self) -> Severity:
        if not self.results:
            return Severity.INFO
        return max((r.severity for r in self.results), key=lambda s: s.rank)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "scorable": self.scorable,
            "deductions": [d.to_dict() for d in self.deductions],
            "checks": [r.to_dict() for r in self.results],
        }


def grade_of(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "E"


def diagnose(
    results: list[CheckResult], *, n_commits: int | None = None, min_commits: int = 30,
) -> Diagnosis:
    deductions: list[Deduction] = []
    for r in results:
        points = PENALTY.get(r.severity, 0)
        if points:
            deductions.append(Deduction(r.check, r.title, r.severity, points))

    scorable = n_commits is None or n_commits >= min_commits
    score = max(0, 100 - sum(d.points for d in deductions)) if scorable else None

    # 头部结论：取最严重的几条，方便「七分钟演示」一眼看到重点
    ranked = sorted(results, key=lambda r: (-r.severity.rank, r.order))
    headlines = [r for r in ranked if r.severity.rank >= Severity.INFO.rank][:5]
    if not headlines:
        headlines = ranked[:3]

    return Diagnosis(
        results=results,
        score=score,
        grade=grade_of(score) if score is not None else "N/A",
        deductions=deductions,
        headlines=headlines,
        scorable=scorable,
    )
