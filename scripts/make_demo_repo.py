"""造数据：生成一份合成的 git log，或一个真实的 git 仓库。

两个用途：
    1. 没有真实仓库时，先跑通工具（--log）
    2. 评估实验里的「合成下降场景」：已知第 N 周开始下滑，
       看 trend 检查项能不能抓到（对应报告 Evaluation 一节）

用法：
    python scripts/make_demo_repo.py --log demo.log
    python scripts/make_demo_repo.py --log decline.log --weeks 24 --decline-at 14
    python scripts/make_demo_repo.py --repo ../demo-repo --commits 200
"""

from __future__ import annotations

import argparse
import hashlib
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEAD, FIELD, END = "\x01", "\x1f", "\x02"

AUTHORS = [
    ("Zhang Wei", "zhangw@example.com"),
    ("Li Na", "lin@example.com"),
    ("Wang Fang", "wangf@example.com"),
    ("Chen Jie", "chenj@example.com"),
    ("Liu Yang", "liuy@example.com"),
    ("Zhao Min", "zhaom@example.com"),
]

GOOD = [
    "feat: add retry to upload task",
    "fix: avoid null pointer when config is empty",
    "refactor: extract parser from service layer",
    "docs: explain why cache is invalidated on logout",
    "test: cover empty repository case",
    "perf: batch insert instead of per-row commit",
]

VAGUE = ["update", "fix bug", "wip", "minor changes", "修改", "update code", "fix"]

MODULES = ["src/core", "src/api", "src/ui", "src/utils", "tests", "docs"]
FILES = {
    "src/core": ["engine.py", "parser.py", "lexer.py", "scheduler.py"],
    "src/api": ["routes.py", "schemas.py", "auth.py"],
    "src/ui": ["views.py", "components.py", "theme.py"],
    "src/utils": ["io.py", "time.py", "strings.py"],
    "tests": ["test_engine.py", "test_api.py", "test_utils.py"],
    "docs": ["README.md", "design.md"],
}


def build_log(
    weeks: int = 20,
    decline_at: int = 14,
    seed: int = 7,
    start: datetime | None = None,
) -> str:
    rng = random.Random(seed)
    start = start or (datetime.now(timezone.utc) - timedelta(weeks=weeks))
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)

    chunks: list[str] = []
    index = 0
    for week in range(weeks):
        # 活跃度：decline_at 之后线性下降
        if week < decline_at:
            per_week = rng.randint(9, 14)
        else:
            remain = weeks - decline_at
            factor = 1.0 - 0.75 * (week - decline_at + 1) / max(remain, 1)
            per_week = max(2, int(rng.randint(9, 14) * factor))

        for _ in range(per_week):
            index += 1
            author, email = rng.choice(AUTHORS[: 5 if week < decline_at else 3])
            day = start + timedelta(weeks=week, days=rng.randint(0, 4),
                                    hours=rng.randint(0, 8), minutes=rng.randint(0, 59))

            # 5% 巨型提交，混合多个模块
            if rng.random() < 0.05:
                paths = [f"{m}/{f}" for m in rng.sample(MODULES, 4) for f in FILES[m][:8]]
                subject = rng.choice(["update", "big refactor", "misc changes"])
                body = ""
            else:
                module = rng.choice(MODULES)
                count = rng.randint(1, 3)
                paths = [f"{module}/{f}" for f in rng.sample(FILES[module], min(count, len(FILES[module])))]
                if rng.random() < 0.35:
                    subject = rng.choice(VAGUE)
                    body = ""
                else:
                    subject = rng.choice(GOOD)
                    body = "Because the previous behaviour broke on empty input." if rng.random() < 0.4 else ""

            sha = hashlib.sha1(f"{seed}-{index}".encode()).hexdigest()
            chunk = (
                f"{HEAD}{sha}{FIELD}{author}{FIELD}{email}{FIELD}"
                f"{day.isoformat()}{FIELD}{subject}{FIELD}{body}{END}"
            )
            lines = []
            for p in paths:
                added = rng.randint(0, 120)
                deleted = rng.randint(0, 60)
                lines.append(f"{added}\t{deleted}\t{p}")
            chunks.append(chunk + "\n" + "\n".join(lines) + "\n")

    return "".join(chunks)


def write_log(path: Path, **kwargs) -> None:
    path.write_text(build_log(**kwargs), encoding="utf-8")


def build_repo(path: Path, weeks: int = 20, seed: int = 7) -> None:
    """生成一个真的 git 仓库（需要本机装了 git）。"""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=path, check=True)

    rng = random.Random(seed)
    start = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    for i in range(weeks * 8):
        day = start + timedelta(days=i // 2)
        rel = f"src/module{i % 5}/file{i % 17}.py"
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# revision {i}\nVALUE = {i}\n", encoding="utf-8")
        stamp = day.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env_stamp = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        msg = rng.choice(GOOD + VAGUE)
        subprocess.run(
            ["git", "commit", "-q", "-m", msg], cwd=path, check=True, env=env_stamp
        )
    print(f"已生成仓库：{path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成演示用的 git 历史")
    ap.add_argument("--log", help="输出一份 git log 文本")
    ap.add_argument("--repo", help="输出一个真实的 git 仓库（需要 git）")
    ap.add_argument("--weeks", type=int, default=20)
    ap.add_argument("--decline-at", type=int, default=14, help="从第几周开始活跃度下降")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    if args.log:
        write_log(Path(args.log), weeks=args.weeks, decline_at=args.decline_at, seed=args.seed)
        print(f"已生成日志：{args.log}（{args.weeks} 周，第 {args.decline_at} 周起下降）")
    if args.repo:
        build_repo(Path(args.repo), weeks=args.weeks, seed=args.seed)


if __name__ == "__main__":
    main()
