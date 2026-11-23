"""Repeatable performance experiment on *synthetic* but real Git histories.

Creates commits using `git fast-import`, then measures the actual CLI with
both full and incremental analysis. It never claims to contain human work.

python scripts/benchmark.py --commits 50000 --work-dir /tmp/commitlens-bench
python scripts/benchmark.py --commits 500000 --work-dir /tmp/commitlens-bench-500k
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def make_repo(repo: Path, commits: int) -> float:
    if (repo / ".git").exists():
        raise RuntimeError(f"Refusing to overwrite existing Git repository: {repo}")
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "gc.auto", "0")
    git(repo, "config", "user.name", "Synthetic Benchmark")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    writer = subprocess.Popen(["git", "-C", str(repo), "fast-import", "--quiet"],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
    assert writer.stdin
    start = time.perf_counter()
    base = int((datetime.now(timezone.utc) - timedelta(days=170)).timestamp())
    try:
        writer.stdin.write(b"blob\nmark :1\ndata 6\nalpha\n"
                           b"blob\nmark :2\ndata 5\nbeta\n")
        for i in range(commits):
            epoch = base + (i * 169 * 86400 // max(commits, 1))
            author = i % 8
            message = f"feat: synthetic change {i}"
            entry = (
                f"commit refs/heads/main\n"
                f"author Synthetic-{author} <dev{author}@example.invalid> {epoch} +0000\n"
                f"committer Synthetic-{author} <dev{author}@example.invalid> {epoch} +0000\n"
                f"data {len(message)}\n{message}\n"
                f"M 100644 :{1 if i % 2 == 0 else 2} src/module.py\n"
            )
            writer.stdin.write(entry.encode())
        writer.stdin.close()
        stderr = writer.stderr.read() if writer.stderr else b""
        code = writer.wait(timeout=240)
        if code:
            raise RuntimeError(f"git fast-import failed: {stderr[-2000:]!r}")
    finally:
        if writer.poll() is None:
            writer.terminate()
            writer.wait()
    return time.perf_counter() - start


def scan(repo: Path, root: Path, *args: str) -> dict:
    report = root / "report.html"
    payload = root / "result.json"
    start = time.perf_counter()
    rss_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    cmd = [sys.executable, "-m", "commitlens", "scan", str(repo),
           "--since", "0", "--out", str(report), "--json", str(payload), *args]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=360)
    elapsed = time.perf_counter() - start
    if proc.returncode:
        raise RuntimeError(f"scan failed: {proc.stderr[-2000:]} {proc.stdout[-2000:]}")
    result = json.loads(payload.read_text(encoding="utf-8"))
    return {"elapsed_seconds": round(elapsed, 3),
            "peak_child_rss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "prior_peak_child_rss_kb": rss_before,
            "analysis": result.get("performance", {}),
            "commits_analyzed": result["summary"]["commits"],
            "evidence_audit": result["evidence_audit"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commits", type=int, default=10000)
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "benchmark.json")
    args = ap.parse_args()
    if args.commits < 1:
        raise ValueError("--commits must be positive")
    base = args.work_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)
    repo = base / "synthetic-git"
    if args.skip_build:
        created_in = None
        if not (repo / ".git").exists():
            raise FileNotFoundError(f"No benchmark repo: {repo}")
    else:
        created_in = make_repo(repo, args.commits)
    result = {"data_type": "synthetic history in a real local Git repository",
              "sample_count_target": args.commits, "repo": str(repo),
              "git_version": subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip(),
              "build_seconds": round(created_in, 3) if created_in is not None else None}

    print(f"Benchmarking full analysis on {args.commits:,} synthetic Git commits...", flush=True)
    result["full"] = scan(repo, base)
    print(f"Full: {result['full']['elapsed_seconds']:.3f}s", flush=True)
    result["incremental_warm"] = scan(repo, base, "--incremental")
    result["incremental_unchanged"] = scan(repo, base, "--incremental")
    # New local commit validates a delta update, not just an unchanged cache hit.
    target = repo / "src" / "module.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    current_head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
    target.write_text(f"gamma-{current_head[:12]}\n", encoding="utf-8")
    git(repo, "add", "src/module.py")
    git(repo, "commit", "-q", "-m", "fix: new synthetic delta commit")
    result["incremental_one_new_commit"] = scan(repo, base, "--incremental")
    print(f"Unchanged: {result['incremental_unchanged']['elapsed_seconds']:.3f}s; "
          f"1 new: {result['incremental_one_new_commit']['elapsed_seconds']:.3f}s", flush=True)
    result["targets"] = {
        "full_le_300s": result["full"]["elapsed_seconds"] <= 300,
        "incremental_unchanged_le_2s": result["incremental_unchanged"]["elapsed_seconds"] <= 2,
        "incremental_with_new_commit_le_2s": result["incremental_one_new_commit"]["elapsed_seconds"] <= 2,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results: {args.out}", flush=True)


if __name__ == "__main__":
    main()
