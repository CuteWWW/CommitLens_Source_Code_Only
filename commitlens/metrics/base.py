"""指标层公共设施。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..config import Config
from ..model import CheckResult, Commit


@dataclass
class Context:
    """一次分析的全部输入。所有指标函数只读它，不碰 IO。"""

    commits: list[Commit]
    config: Config
    repo: str
    now: datetime
    git_command: list[str] = field(default_factory=list)
    # A compact incremental run carries complete aggregate totals separately
    # from the bounded set of source commits embedded in the HTML evidence.
    total_commits: int | None = None
    total_authors: list[str] | None = None
    first_last: tuple[date, date] | None = None

    def cfg(self, section: str, key: str) -> Any:
        return self.config.get(section, key)

    @property
    def authors(self) -> list[str]:
        if self.total_authors is not None:
            return self.total_authors
        return sorted({c.author for c in self.commits})

    @property
    def commit_count(self) -> int:
        return self.total_commits if self.total_commits is not None else len(self.commits)

    @property
    def date_range(self) -> tuple[date, date] | None:
        if self.first_last is not None:
            return self.first_last
        if not self.commits:
            return None
        return self.commits[0].date.date(), self.commits[-1].date.date()

    def weekly(self, weeks: int | None = None) -> list[tuple[date, int]]:
        """按 ISO 周（周一起）聚合提交数，空周补 0。"""
        return weekly_counts(self.commits, weeks, end_date=self.now.date())


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_counts(
    commits: list[Commit],
    weeks: int | None = None,
    end_date: date | None = None,
) -> list[tuple[date, int]]:
    if not commits:
        return []
    start = monday_of(commits[0].date.date())
    # The endpoint is the analysis date, not the most recent commit. Otherwise
    # a repository that has stopped for months appears healthy.
    end = monday_of(end_date or commits[-1].date.date())
    buckets: Counter[date] = Counter()
    for c in commits:
        buckets[monday_of(c.date.date())] += 1

    out: list[tuple[date, int]] = []
    cur = start
    while cur <= end:
        out.append((cur, buckets.get(cur, 0)))
        cur += timedelta(days=7)
    if weeks:
        out = out[-weeks:]
    return out


def by_author(commits: list[Commit]) -> dict[str, list[Commit]]:
    out: defaultdict[str, list[Commit]] = defaultdict(list)
    for c in commits:
        out[c.author].append(c)
    return dict(out)


def pct(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return part / whole


def fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def count_label(n: int, singular: str, plural: str | None = None) -> str:
    word = singular if n == 1 else (plural or singular + "s")
    return f"{n} {word}"


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# 每个模块注册一个 run(context) -> list[CheckResult]
CHECKS: list[Callable[[Context], list[CheckResult]]] = []


def register(fn: Callable[[Context], list[CheckResult]]) -> Callable[[Context], list[CheckResult]]:
    CHECKS.append(fn)
    return fn
