"""命令行入口。

    python -m commitlens scan . --out report.html
    python -m commitlens scan . --log sample.log --json result.json
    python -m commitlens checks
    python -m commitlens init
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .audit import audit_evidence
from .cache import can_reuse_render, fetch_git_log, git_head, store_render_checkpoint
from .config import CONFIG_FILENAME, Config, write_default_config
from .diagnose import diagnose
from .identity import normalize_identities, pseudonymize_authors
from .ingest import build_git_command, filter_commits, parse_log_text, read_log
from .metrics import CHECK_TITLES, Context, run_all
from .model import SEVERITY_CN, Severity
from .report.html import render as render_html
from .rollup import (add_commits, include_new_pseudonymous_authors, load_state,
                     make_context, new_state, run_from_state, save_state, state_path)

REPO_ROOT = Path(__file__).resolve().parent.parent

ICON = {"ok": "[OK]", "info": "[--]", "warn": "[! ]", "alert": "[X!]"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="commitlens",
        description="Git 提交历史健康度诊断：8 个检查项 → 结论 + 证据 + 建议",
    )
    p.add_argument("--version", action="version", version=f"commitlens {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="分析一个仓库并生成报告")
    s.add_argument("repo", nargs="?", default=".", help="仓库路径，默认当前目录")
    s.add_argument("--config", help=f"配置文件，默认找 ./{CONFIG_FILENAME}")
    s.add_argument("--log", help="直接从一份 git log 文本分析（不需要 git）")
    s.add_argument("--since", type=int, help="只分析最近 N 天，覆盖配置里的 window_days")
    s.add_argument("--as-of", help="固定分析截止时间（ISO 日期或 datetime），用于复验历史快照")
    s.add_argument("--until", help="Git 的历史截止日期（仅适用于仓库采集）")
    s.add_argument("--all-branches", action="store_true", help="采集全部本地分支引用的可达提交")
    s.add_argument("--include-merges", action="store_true", help="包括合并提交，比较第一父提交")
    s.add_argument("--incremental", action="store_true", help="使用本地校验缓存，前进的 HEAD 只采集新增提交")
    s.add_argument("--cache-dir", help="增量分析缓存目录（默认输出目录下的 .commitlens-cache）")
    s.add_argument("--show-authors", action="store_true", help="显式展示 Git 作者姓名，默认显示 Dev 编号")
    s.add_argument("--out", default="report.html", help="HTML 报告输出路径")
    s.add_argument("--json", dest="json_out", help="另外输出一份 JSON")
    s.add_argument("--title", help="报告标题")
    s.add_argument("--team", help="团队名，显示在页头")
    s.add_argument(
        "--fail-on",
        choices=["none", "warn", "alert"],
        default="none",
        help="出现该等级及以上的问题时返回非零退出码，便于接 CI",
    )

    sub.add_parser("checks", help="列出 8 个检查项")
    i = sub.add_parser("init", help="生成一份默认配置")
    i.add_argument("--force", action="store_true", help="覆盖已存在的配置")
    return p


def _use_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，会吞掉中文和符号。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.cmd == "checks":
        print_checks()
        return 0
    if args.cmd == "init":
        return cmd_init(args)
    return cmd_scan(args)


def cmd_init(args) -> int:
    path = Path(CONFIG_FILENAME)
    if path.exists() and not args.force:
        print(f"{path} 已存在，加 --force 覆盖。")
        return 1
    write_default_config(path)
    print(f"已生成 {path}，按需修改阈值后重新运行 scan。")
    return 0


def cmd_scan(args) -> int:
    started = time.perf_counter()
    repo = Path(args.repo).expanduser()
    if args.since is not None and args.since < 0:
        print("--since 不可为负数", file=sys.stderr)
        return 2
    try:
        now = _parse_as_of(args.as_of) if args.as_of else datetime.now(timezone.utc)
    except ValueError as exc:
        print(f"--as-of 日期无效：{exc}", file=sys.stderr)
        return 2

    if args.config:
        config_path = Path(args.config).expanduser()
    elif (repo / CONFIG_FILENAME).is_file() and not args.log:
        config_path = repo / CONFIG_FILENAME
    elif Path(CONFIG_FILENAME).is_file():
        config_path = Path(CONFIG_FILENAME)
    else:
        config_path = None
    try:
        config = Config.load(config_path)
    except (ValueError, OSError) as exc:
        print(f"配置加载失败：{exc}", file=sys.stderr)
        return 2
    window = args.since if args.since is not None else config.get("general", "window_days")
    config.data["general"]["window_days"] = window

    out = Path(args.out).expanduser()
    json_path = Path(args.json_out).expanduser() if args.json_out else None
    # shell-escaped and fully specified for manual reproduction.
    command_args = ["python", "-m", "commitlens", "scan", args.repo]
    for flag, value in (
        ("--config", args.config), ("--log", args.log), ("--since", args.since),
        ("--as-of", args.as_of), ("--until", args.until), ("--cache-dir", args.cache_dir),
        ("--out", args.out), ("--json", args.json_out), ("--title", args.title),
        ("--team", args.team),
    ):
        if value is not None:
            command_args.extend([flag, str(value)])
    for flag, enabled in (
        ("--all-branches", args.all_branches), ("--include-merges", args.include_merges),
        ("--incremental", args.incremental), ("--show-authors", args.show_authors),
    ):
        if enabled:
            command_args.append(flag)
    command_args.extend(["--fail-on", args.fail_on])

    cached = None
    cutoff_for_git = window if not args.as_of else None
    git_command = []
    revision = "not available (saved log)"
    git_version = "not available (saved log)"

    if args.log:
        if args.all_branches or args.include_merges or args.until:
            print("--log 已是固定输入，不能同时使用 Git 采集选项", file=sys.stderr)
            return 2
        try:
            text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"日志读取失败：{exc}", file=sys.stderr)
            return 2
        source = f"日志文件 {Path(args.log).resolve()}"
    else:
        if not repo.exists():
            print(f"路径不存在：{repo}", file=sys.stderr)
            return 2
        try:
            if args.incremental:
                cached = fetch_git_log(
                    repo, Path(args.cache_dir) if args.cache_dir else out.parent / ".commitlens-cache",
                    since_days=cutoff_for_git or None, until=args.until,
                    all_branches=args.all_branches, include_merges=args.include_merges,
                )
                text = cached.text
                revision = cached.head
            else:
                text = read_log(
                    repo, since_days=cutoff_for_git or None, until=args.until,
                    all_branches=args.all_branches, include_merges=args.include_merges,
                )
                revision = git_head(repo)
            source = str(repo.resolve())
            git_command = build_git_command(
                repo, since_days=cutoff_for_git or None, until=args.until,
                all_branches=args.all_branches, include_merges=args.include_merges,
            )
            git_version = subprocess.run(["git", "--version"], capture_output=True,
                                         text=True, check=False).stdout.strip()
        except (RuntimeError, OSError) as exc:
            print(f"读取失败：{exc}", file=sys.stderr)
            return 2

    report_spec = {
        "tool_version": __version__,
        "pipeline_hash": _pipeline_hash(),
        "data_hash": hashlib.sha256(text.encode()).hexdigest(),
        "config_hash": config.fingerprint,
        "date": now.date().isoformat(),
        "exact_as_of": now.isoformat() if args.as_of else None,
        "repro_command": command_args,
        "out": str(out.resolve()),
        "json_out": str(json_path.resolve()) if json_path else None,
    }
    if cached:
        severity_rank = can_reuse_render(cached, report_spec, out, json_path)
        if severity_rank is not None:
            print(f"CommitLens：缓存报告命中，HEAD {revision[:12]} 未变化；报告 {out}")
            return _exit_for_severity(severity_rank, args.fail_on)

    collected_at = time.perf_counter()
    rollup_file = state_path(cached.metadata_file) if cached else None
    rollup = (load_state(rollup_file, config, now)
              if cached and cached.mode == "delta" and rollup_file else None)
    if rollup and rollup.get("head") != cached.base_head:
        rollup = None
    fast_delta = False
    identity_audit = None
    if rollup and cached and cached.delta_text is not None:
        # Only the delta is parsed. Missing or renamed author identities or a
        # changed .mailmap require a full rebuild for correct historical labels.
        new_commits = filter_commits(
            parse_log_text(cached.delta_text),
            ignore_paths=config.get("general", "ignore_paths"),
            ignore_authors=config.get("general", "ignore_authors"),
            window_days=window or 0, now=now,
        )
        normalize_identities(new_commits, config.get("identity", "aliases"))
        if not args.show_authors and not any(".mailmap" in c.paths for c in new_commits):
            include_new_pseudonymous_authors(rollup, {c.email for c in new_commits})
        labels = rollup["author_labels"]
        compatible = all(c.email in labels and
                         (not args.show_authors or c.author == labels[c.email])
                         and ".mailmap" not in c.paths for c in new_commits)
        if compatible:
            for c in new_commits:
                c.author = labels[c.email]
                if not args.show_authors:
                    c.email = "redacted"
            add_commits(rollup, new_commits, config)
            rollup["head"] = cached.head
            fast_delta = True

    if fast_delta:
        ctx = make_context(rollup, config, now,
                           str(repo.resolve()) if not args.log else args.log)
        ctx.git_command = git_command
        parsed_at = time.perf_counter()
        results = run_from_state(rollup, config, now)
        identity_message = (
            "增量更新：复用已核实的身份映射；匿名新成员会安全重编号，"
            "显式实名模式遇到未知身份则回退全量"
        )
    else:
        commits = parse_log_text(text)
        commits = filter_commits(
            commits,
            ignore_paths=config.get("general", "ignore_paths"),
            ignore_authors=config.get("general", "ignore_authors"),
            window_days=window or 0,
            now=now,
        )
        try:
            identity_audit = normalize_identities(commits, config.get("identity", "aliases"))
        except ValueError as exc:
            print(f"身份映射失败：{exc}", file=sys.stderr)
            return 2
        if not args.show_authors:
            email_order = sorted({c.email for c in commits})
            author_labels = {email: f"Dev-{i:02d}" for i, email in enumerate(email_order, 1)}
            pseudonymize_authors(commits)
        else:
            author_labels = {c.email: c.author for c in commits}
        parsed_at = time.perf_counter()
        ctx = Context(
            commits=commits,
            config=config,
            repo=str(repo.resolve()) if not args.log else args.log,
            now=now,
            git_command=git_command,
        )
        results = run_all(ctx)
        if cached:
            rollup = new_state(config, now, author_labels)
            add_commits(rollup, commits, config)
            rollup["head"] = cached.head
        identity_message = (
            f"{identity_audit.identities} 位独立身份；别名归并 {identity_audit.alias_merges} 次；"
            f"同名不同邮箱 {', '.join(identity_audit.duplicate_names) or '无'}；"
            f"同邮箱改名 {identity_audit.renamed_emails} 次"
        )

    diagnosis = diagnose(results, n_commits=ctx.commit_count,
                         min_commits=config.get("general", "min_commits"))
    evidence_audit = audit_evidence(diagnosis, ctx.commits)
    if not evidence_audit["passed"]:
        print("证据完整性验收失败：" + "; ".join(evidence_audit["failures"]),
              file=sys.stderr)
        return 3
    analyzed_at = time.perf_counter()

    config_text = config_path.read_text(encoding="utf-8") if config_path else "（未使用配置文件）"
    meta = {
        "title": args.title or config.get("report", "title"),
        "team": args.team if args.team is not None else config.get("report", "team"),
        "generated_at": now.isoformat(),
        "config_hash": config.fingerprint,
        "window_days": window,
        "source": source,
        "git_command": git_command,
        "config_toml": config_text,
        "effective_config": json.dumps(config.data, ensure_ascii=False, sort_keys=True, indent=2),
        "config_source": str(config_path.resolve()) if config_path else "内置默认值",
        "command": shlex.join(command_args),
        "data_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "git_version": git_version,
        "repo_revision": revision,
        "show_authors": args.show_authors,
        "identity_warnings": identity_message,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(ctx, diagnosis, meta), encoding="utf-8")
    rendered_at = time.perf_counter()
    performance = {
        "collect_ms": round((collected_at - started) * 1000, 2),
        "parse_filter_identity_ms": round((parsed_at - collected_at) * 1000, 2),
        "metrics_diagnosis_audit_ms": round((analyzed_at - parsed_at) * 1000, 2),
        "html_ms": round((rendered_at - analyzed_at) * 1000, 2),
        "until_report_written_ms": round((rendered_at - started) * 1000, 2),
        "cache_input_mode": cached.mode if cached else "disabled",
    }

    if json_path:
        payload = {
            "tool": "commitlens",
            "version": __version__,
            "generated_at": meta["generated_at"],
            "config_hash": meta["config_hash"],
            "effective_config": config.data,
            "provenance": {
                "source": source, "repo_head": revision, "git_version": git_version,
                "git_command": git_command, "reproduction_command": meta["command"],
                "data_sha256": meta["data_sha256"], "analysis_as_of": now.isoformat(),
            },
            "evidence_audit": evidence_audit,
            "performance": performance,
            "repo": ctx.repo,
            "summary": {
                "commits": ctx.commit_count,
                "authors": len(ctx.authors),
                "window_days": window,
                "score": diagnosis.score,
                "grade": diagnosis.grade,
            },
            "diagnosis": diagnosis.to_dict(),
        }
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if cached:
        if rollup_file and rollup:
            save_state(rollup, rollup_file)
        store_render_checkpoint(cached, report_spec, out, json_path, diagnosis.worst.rank)

    print_summary(ctx, diagnosis, out, args.json_out)

    return _exit_for_severity(diagnosis.worst.rank, args.fail_on)


def print_summary(ctx, diagnosis, out: Path, json_out: str | None) -> None:
    rng = ctx.date_range
    span = f"{rng[0]} → {rng[1]}" if rng else "无数据"
    print(f"\nCommitLens v{__version__}")
    print(f"仓库：{ctx.repo}")
    print(f"提交 {ctx.commit_count} · 贡献者 {len(ctx.authors)} · {span} · 窗口 {ctx.config.get('general', 'window_days')} 天")
    score = diagnosis.score if diagnosis.scorable else "暂不评分"
    print(f"\n综合评分 {score}（{diagnosis.grade}）\n")
    min_commits = ctx.config.get("general", "min_commits")
    if ctx.commit_count < min_commits:
        print(
            f"  !! 警告：窗口内只有 {ctx.commit_count} 个提交（< {min_commits}），"
            "百分比和排名可能只是噪声。\n"
            "     先用 git rev-list --count HEAD 确认仓库历史是否完整，"
            "或加大 --since。\n"
        )
    for r in diagnosis.results:
        mark = ICON.get(r.severity.value, "·")
        print(f"  {mark} {r.order}. {r.title}  {r.summary}")
    print(f"\n报告：{out}")
    if json_out:
        print(f"JSON：{json_out}")


def _parse_as_of(raw: str) -> datetime:
    if len(raw) == 10:
        return datetime.fromisoformat(raw + "T23:59:59+00:00")
    date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def _exit_for_severity(severity_rank: int, fail_on: str) -> int:
    threshold = {"none": 99, "warn": Severity.WARN.rank,
                 "alert": Severity.ALERT.rank}[fail_on]
    return int(severity_rank >= threshold)


def _pipeline_hash() -> str:
    """Invalidate rendered-cache hits when implementation code changes."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _disp_width(s: str) -> int:
    """按显示宽度算：中文算两格，终端对齐才好看。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _disp_width(s))


def print_checks() -> None:
    desc = {
        "message": "标题长度、类型前缀、空话、issue 关联、大改动是否有正文",
        "size": "单次改动的文件数与行数分布",
        "giant": "超过阈值的巨型提交，及其占全部改动的比例",
        "cadence_freq": "谁的提交频率显著高于团队中位数",
        "cadence_dormant": "谁已连续多天没有提交",
        "hotspot": "哪些文件被改得最频繁、涉及多少人",
        "ownership": "哪些文件的知识集中在一个人身上（巴士因子）",
        "trend": "最近几周的活跃度是否显著下降",
    }
    print("CommitLens 的 8 个检查项：\n")
    for i, (key, title) in enumerate(CHECK_TITLES.items(), 1):
        print(f"  {i}. {_pad(title, 14)}{desc.get(key, '')}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
