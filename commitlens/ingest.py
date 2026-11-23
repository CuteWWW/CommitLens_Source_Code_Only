"""采集层：把 git 历史读成 Commit 列表。

分成两步，是为了可测试、可复现：
    1. read_log()      调用 git，拿到原始文本
    2. parse_log_text() 解析文本（不需要装 git 就能跑单元测试）

--log 参数可以吃一份已经保存下来的 git log 文本，
评估实验因此可以做到"同一份输入、可重复跑"。
"""

from __future__ import annotations

import subprocess
from datetime import datetime, time, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path

from .model import Commit, FileChange

HEAD = "\x01"
FIELD = "\x1f"
END = "\x02"

# 用 git 自己的 %xNN 转义，避免把控制字符塞进命令行参数
# %aN/%aE respect a repository's .mailmap; %an/%ae do not.
LOG_FORMAT = "%x01%H%x1f%aN%x1f%aE%x1f%aI%x1f%s%x1f%b%x02"


def build_git_command(
    repo: str | Path,
    since_days: int | None = None,
    until: str | None = None,
    *,
    all_branches: bool = False,
    include_merges: bool = False,
    exclude_rev: str | None = None,
) -> list[str]:
    cmd = [
        "git",
        "-C", str(repo),
        "-c", "core.quotepath=false",   # 中文路径不要被转义
        "log",
        "--numstat",
        "--date=iso-strict",
        f"--pretty=format:{LOG_FORMAT}",
    ]
    if include_merges:
        # Diff each merge against its first parent, avoiding duplicate per-parent rows.
        cmd.append("--diff-merges=first-parent")
    else:
        cmd.append("--no-merges")
    if all_branches:
        cmd.append("--all")
    if exclude_rev:
        if not all_branches:
            # A negative-only revision list does not imply HEAD as positive!
            cmd.append("HEAD")
        cmd.append(f"^{exclude_rev}")
    if since_days:
        # A calendar-day boundary makes same-day incremental caches correct.
        # `N days ago` drifts every second, silently expiring old commits while
        # a cache still appears current.
        cutoff = datetime.combine(datetime.now(timezone.utc).date() - timedelta(days=since_days),
                                  time.min, tzinfo=timezone.utc)
        cmd.append(f"--since={cutoff.isoformat()}")
    if until:
        cmd.append(f"--until={until}")
    return cmd


def read_log(
    repo: str | Path,
    since_days: int | None = None,
    *,
    until: str | None = None,
    all_branches: bool = False,
    include_merges: bool = False,
    exclude_rev: str | None = None,
) -> str:
    """执行 git log，返回原始文本。"""
    cmd = build_git_command(
        repo, since_days, until,
        all_branches=all_branches, include_merges=include_merges,
        exclude_rev=exclude_rev,
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:  # pragma: no cover - 环境问题
        raise RuntimeError(
            "找不到 git 命令。请先安装 Git 并确保 git 在 PATH 里，"
            "或者用 --log 指定一份已保存的 git log 文本。"
        ) from exc
    except PermissionError as exc:
        raise RuntimeError(f"没有权限执行 Git 或读取仓库：{exc}") from exc
    if proc.returncode != 0:
        # `git log` exits 128 in an initialized but as-yet-uncommitted repo.
        # This is valid input and must yield a low-confidence empty report.
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        valid_repo = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-dir"],
            capture_output=True, text=True, check=False,
        )
        if head.returncode != 0 and valid_repo.returncode == 0:
            return ""
        raise RuntimeError(f"git log 执行失败：{proc.stderr.strip()}")
    return proc.stdout


def normalize_path(path: str) -> str:
    """把 git 的重命名写法归一成新路径。

    'old.py => new.py'              -> 'new.py'
    'src/{a.py => b.py}/x'          -> 'src/b.py/x'
    '"a\\tb.py"'                    -> 'a\\tb.py'（去引号）
    """
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1].replace('\\"', '"').replace("\\t", "\t")

    if " => " not in path:
        return path

    if "{" in path and "}" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        new = mid.split(" => ")[-1].strip()
        return f"{pre}{new}{post}"

    return path.split(" => ")[-1].strip()


def parse_numstat(line: str) -> FileChange | None:
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    raw_add, raw_del = parts[0], parts[1]
    path = normalize_path("\t".join(parts[2:]))
    if not path:
        return None
    binary = raw_add == "-" or raw_del == "-"
    added = 0 if binary else _to_int(raw_add)
    deleted = 0 if binary else _to_int(raw_del)
    return FileChange(path=path, added=added, deleted=deleted, binary=binary)


def _to_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def parse_log_text(text: str) -> list[Commit]:
    """把 git log 文本解析成 Commit 列表。"""
    commits: list[Commit] = []
    for chunk in text.split(HEAD):
        if not chunk.strip():
            continue
        header, sep, rest = chunk.partition(END)
        if not sep:
            continue

        fields = header.split(FIELD, 5)
        if len(fields) < 6:
            continue
        sha, author, email, date_s, subject, body = fields

        try:
            date = datetime.fromisoformat(date_s.strip())
        except ValueError:
            continue
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)

        files: list[FileChange] = []
        for line in rest.splitlines():
            if not line.strip():
                continue
            fc = parse_numstat(line)
            if fc:
                files.append(fc)

        commits.append(
            Commit(
                sha=sha.strip(),
                author=author.strip(),
                email=email.strip(),
                date=date,
                subject=subject.strip(),
                body=body.strip(),
                files=files,
            )
        )
    commits.sort(key=lambda c: c.date)
    return commits


def filter_commits(
    commits: list[Commit],
    ignore_paths: list[str],
    ignore_authors: list[str],
    window_days: int = 0,
    now: datetime | None = None,
) -> list[Commit]:
    """按配置过滤：忽略目录、机器人、时间窗口。"""
    now = now or datetime.now(timezone.utc)
    cutoff = (datetime.combine(now.date() - timedelta(days=window_days),
                               time.min, tzinfo=timezone.utc) if window_days else None)

    out: list[Commit] = []
    for c in commits:
        if c.author in ignore_authors:
            continue
        if cutoff and c.date < cutoff:
            continue
        if c.date > now:
            continue

        kept = [f for f in c.files if not _ignored(f.path, ignore_paths)]
        if not kept:
            continue
        out.append(
            Commit(
                sha=c.sha,
                author=c.author,
                email=c.email,
                date=c.date,
                subject=c.subject,
                body=c.body,
                files=kept,
            )
        )
    return out


def _ignored(path: str, patterns: list[str]) -> bool:
    low = path.lower()
    for p in patterns:
        p_low = p.lower()
        if p_low.endswith("/"):
            if f"/{p_low}" in f"/{low}" or low.startswith(p_low):
                return True
        elif fnmatch(low, p_low) or fnmatch(low.split("/")[-1], p_low):
            return True
    return False
