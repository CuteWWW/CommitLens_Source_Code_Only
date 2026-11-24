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
from .model import Severity
from .report.html import render as render_html
from .rollup import (add_commits, include_new_pseudonymous_authors, load_state,
                     make_context, new_state, run_from_state, save_state, state_path)

REPO_ROOT = Path(__file__).resolve().parent.parent

ICON = {"ok": "[OK]", "info": "[--]", "warn": "[! ]", "alert": "[X!]"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="commitlens",
        description=(
            "Git history health diagnosis: 8 checks -> findings, evidence, and recommendations"
        ),
    )
    p.add_argument("--version", action="version", version=f"commitlens {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Analyze a repository and generate a report")
    s.add_argument("repo", nargs="?", default=".", help="Repository path (default: current directory)")
    s.add_argument("--config", help=f"Configuration file (default: ./{CONFIG_FILENAME})")
    s.add_argument("--log", help="Analyze a saved git log without running Git")
    s.add_argument("--since", type=int, help="Analyze only the last N days")
    s.add_argument("--as-of", help="Fixed ISO date or datetime for a reproducible analysis")
    s.add_argument("--until", help="Git history cutoff date")
    s.add_argument("--all-branches", action="store_true", help="Include commits from all local branches")
    s.add_argument("--include-merges", action="store_true", help="Include merge commits")
    s.add_argument("--incremental", action="store_true", help="Reuse validated local cache")
    s.add_argument("--cache-dir", help="Incremental cache directory")
    s.add_argument("--show-authors", action="store_true", help="Show Git author names")
    s.add_argument("--out", default="report.html", help="HTML report path")
    s.add_argument("--json", dest="json_out", help="Optional JSON output path")
    s.add_argument("--title", help="Report title")
    s.add_argument("--team", help="Team name displayed in the report header")
    s.add_argument(
        "--fail-on",
        choices=["none", "warn", "alert"],
        default="none",
        help="Return a non-zero exit code at or above this severity (for CI)",
    )

    sub.add_parser("checks", help="List the eight checks")
    i = sub.add_parser("init", help="Generate a default configuration")
    i.add_argument("--force", action="store_true", help="Overwrite an existing configuration")
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
        print(f"{path} already exists. Use --force to overwrite it.")
        return 1
    write_default_config(path)
    print(f"Created {path}. Adjust thresholds as needed and run scan again.")
    return 0


def cmd_scan(args) -> int:
    started = time.perf_counter()
    repo = Path(args.repo).expanduser()
    if args.since is not None and args.since < 0:
        print("--since cannot be negative", file=sys.stderr)
        return 2
    try:
        now = _parse_as_of(args.as_of) if args.as_of else datetime.now(timezone.utc)
    except ValueError as exc:
        print(f"Invalid --as-of date: {exc}", file=sys.stderr)
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
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
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
            print("--log cannot be combined with Git collection options", file=sys.stderr)
            return 2
        try:
            text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Failed to read log file: {exc}", file=sys.stderr)
            return 2
        source = f"Log file: {Path(args.log).resolve()}"
    else:
        if not repo.exists():
            print(f"Path does not exist: {repo}", file=sys.stderr)
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
            print(f"Failed to collect repository data: {exc}", file=sys.stderr)
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
            print(f"CommitLens: cached report hit, HEAD {revision[:12]} unchanged; report: {out}")
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
            "Incremental update: reused validated identity mappings; anonymous new contributors "
            "were assigned new labels, and unknown identities in author-name mode triggered a "
            "full rebuild."
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
            print(f"Identity mapping failed: {exc}", file=sys.stderr)
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
            f"{identity_audit.identities} validated identities; "
            f"{identity_audit.alias_merges} alias merges; "
            f"duplicate names with different emails: "
            f"{', '.join(identity_audit.duplicate_names) or 'none'}; "
            f"email renames: {identity_audit.renamed_emails}."
        )

    diagnosis = diagnose(results, n_commits=ctx.commit_count,
                         min_commits=config.get("general", "min_commits"))
    evidence_audit = audit_evidence(diagnosis, ctx.commits)
    if not evidence_audit["passed"]:
        print("Evidence audit failed: " + "; ".join(evidence_audit["failures"]),
              file=sys.stderr)
        return 3
    analyzed_at = time.perf_counter()

    config_text = (
        config_path.read_text(encoding="utf-8")
        if config_path else
        "(No configuration file was used.)"
    )
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
        "config_source": str(config_path.resolve()) if config_path else "Built-in defaults",
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
    span = f"{rng[0]} -> {rng[1]}" if rng else "No data"
    print(f"\nCommitLens v{__version__}")
    print(f"Repository: {ctx.repo}")
    print(
        f"Commits: {ctx.commit_count} · Contributors: {len(ctx.authors)} · {span} · "
        f"Window: {ctx.config.get('general', 'window_days')} days"
    )
    score = diagnosis.score if diagnosis.scorable else "Not scored"
    print(f"\nOverall score: {score} ({diagnosis.grade})\n")
    min_commits = ctx.config.get("general", "min_commits")
    if ctx.commit_count < min_commits:
        print(
            f"  !! Warning: only {ctx.commit_count} commits are in the window "
            f"(minimum: {min_commits}); percentages and rankings may be noisy.\n"
            "     Confirm history with git rev-list --count HEAD or increase --since.\n"
        )
    for r in diagnosis.results:
        mark = ICON.get(r.severity.value, "·")
        print(f"  {mark} {r.order}. {r.title}  {r.summary}")
    print(f"\nReport: {out}")
    if json_out:
        print(f"JSON: {json_out}")


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
        "message": "Subject length, type prefixes, vague wording, issue links, and change bodies",
        "size": "Distribution of changed files and lines per commit",
        "giant": "Oversized commits and their share of all changed lines",
        "cadence_freq": "Contributors whose commit frequency is well above the team median",
        "cadence_dormant": "Contributors with no recent commits",
        "hotspot": "Most frequently changed files and their contributor coverage",
        "ownership": "Files with concentrated change activity",
        "trend": "Whether recent activity has declined significantly",
    }
    print("CommitLens checks:\n")
    for i, (key, title) in enumerate(CHECK_TITLES.items(), 1):
        print(f"  {i}. {_pad(title, 14)}{desc.get(key, '')}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
