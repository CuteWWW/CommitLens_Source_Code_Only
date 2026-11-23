"""测试用的小工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from commitlens.config import Config
from commitlens.ingest import filter_commits, parse_log_text
from commitlens.metrics import Context

HEAD, FIELD, END = "\x01", "\x1f", "\x02"


def build_log(*records) -> str:
    """records: (sha, author, email, date, subject, body, [(added, deleted, path), ...])"""
    out = []
    for sha, author, email, date, subject, body, files in records:
        chunk = f"{HEAD}{sha}{FIELD}{author}{FIELD}{email}{FIELD}{date}{FIELD}{subject}{FIELD}{body}{END}"
        lines = [f"{a}\t{d}\t{p}" for a, d, p in files]
        out.append(chunk + "\n" + "\n".join(lines) + "\n")
    return "".join(out)


def weekly_log(counts: list[int], start: datetime | None = None) -> str:
    """按「每周 N 个提交」造历史，用来测趋势检测。"""
    start = start or (datetime.now(timezone.utc) - timedelta(weeks=len(counts))).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    records = []
    i = 0
    for week, n in enumerate(counts):
        for k in range(n):
            i += 1
            day = start + timedelta(weeks=week, days=k % 5)
            records.append(
                (
                    f"{i:040x}",
                    "Dev A" if k % 2 else "Dev B",
                    "a@example.com",
                    day.isoformat(),
                    f"feat: change {i}",
                    "",
                    [(5, 1, f"src/mod{i % 5}.py")],
                )
            )
    return build_log(*records)


def make_ctx(log_text: str, overrides: dict | None = None, window_days: int = 0) -> Context:
    config = Config(overrides or {})
    config.data["general"]["window_days"] = window_days
    commits = parse_log_text(log_text)
    commits = filter_commits(
        commits,
        ignore_paths=config.get("general", "ignore_paths"),
        ignore_authors=config.get("general", "ignore_authors"),
        window_days=window_days,
    )
    return Context(
        commits=commits,
        config=config,
        repo="<test>",
        now=datetime.now(timezone.utc),
    )


def by_check(results, check: str):
    for r in results:
        if r.check == check:
            return r
    raise AssertionError(f"没有 {check} 这个检查项的结果")
