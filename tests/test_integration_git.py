"""End-to-end tests against real *local* Git repositories (never remote).

The Git histories are test fixtures created during the test, not evidence of
historical human-team work, and must never be described as such.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from commitlens.cache import fetch_git_log
from commitlens.ingest import parse_log_text, read_log

PROJECT = Path(__file__).resolve().parent.parent
SUBPROCESS_TEXT = {"encoding": "utf-8", "errors": "replace"}


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture Maintainer")
        self.git("config", "user.email", "fixture@example.org")

    def git(self, *args: str, env: dict | None = None, check: bool = True):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, check=check,
                              env={**os.environ, **(env or {})},
                              **SUBPROCESS_TEXT)

    def commit(self, message: str, path: str = "src/main.py", *,
               data: bytes | None = None, name: str = "Fixture Maintainer",
               email: str = "fixture@example.org"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            target.write_text(f"# {message}\n", encoding="utf-8")
        else:
            target.write_bytes(data)
        self.git("add", "-A")
        stamp = datetime.now(timezone.utc).isoformat()
        self.git("-c", f"user.name={name}", "-c", f"user.email={email}",
                 "commit", "-q", "-m", message,
                 env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})

    def scan(self, *args, return_code=0):
        report = self.base / "report.html"
        payload = self.base / "out.json"
        proc = subprocess.run(
            [sys.executable, "-m", "commitlens", "scan", str(self.repo),
             "--out", str(report), "--json", str(payload), *args],
            cwd=PROJECT, capture_output=True, check=False,
            **SUBPROCESS_TEXT,
        )
        details = (proc.stderr or "") + (proc.stdout or "")
        self.assertEqual(proc.returncode, return_code, details)
        self.assertTrue(report.is_file())
        self.assertTrue(payload.is_file())
        return report.read_text(encoding="utf-8"), json.loads(payload.read_text(encoding="utf-8")), proc


class TestLocalGitIntegration(GitFixture):
    def test_20_empty_repo_is_valid_not_a_crash(self):
        report, payload, _ = self.scan()
        self.assertEqual(payload["summary"]["commits"], 0)
        self.assertIsNone(payload["summary"]["score"])
        self.assertIn("样本不足，暂不评分", report)

    def test_20_rename_and_binary_file(self):
        self.commit("feat: initialize module", "src/old.py")
        self.git("mv", "src/old.py", "src/new.py")
        self.git("commit", "-q", "-m", "refactor: rename module")
        self.commit("feat: add binary payload", "data/payload.bin",
                    data=b"\x00\xff\x01\x00\x00\xff" * 16)
        parsed = parse_log_text(read_log(self.repo))
        self.assertTrue(any("src/new.py" in c.paths for c in parsed))
        self.assertTrue(any(f.binary for c in parsed for f in c.files))
        report, payload, _ = self.scan()
        self.assertEqual(payload["summary"]["commits"], 3)
        self.assertNotIn("<script", report)

    def test_05_06_actual_config_and_reproduction_args(self):
        (self.repo / "commitlens.toml").write_text(
            "[general]\nwindow_days = 0\nmin_commits = 1\n"
            "[message]\nrequire_type_prefix = true\ntype_prefixes = ['feat']\n",
            encoding="utf-8")
        self.commit("weird: unexplained", "src/a.py")
        report, payload, _ = self.scan("--as-of", datetime.now(timezone.utc).date().isoformat(),
                                      "--team", "Fixture Team", "--show-authors")
        self.assertIn("Fixture Team", report)
        self.assertIn("--show-authors", report)
        self.assertIn("--as-of", report)
        self.assertIn("type_prefixes", report)
        self.assertIn("&#x27;feat&#x27;", report)
        self.assertIn("identity", report)
        self.assertEqual(payload["effective_config"]["general"]["window_days"], 0)
        self.assertEqual(payload["diagnosis"]["checks"][0]["stats"]["offenders"], 1)
        self.assertTrue(payload["evidence_audit"]["passed"])

    def test_08_mailmap_git_canonicalization(self):
        self.commit("feat: initial commit", name="Original Name", email="old@example.org")
        (self.repo / ".mailmap").write_text(
            "Canonical Name <canonical@example.org> Original Name <old@example.org>\n",
            encoding="utf-8")
        self.git("add", ".mailmap")
        self.git("commit", "-q", "-m", "docs: normalize maintainer identity")
        parsed = parse_log_text(read_log(self.repo))
        self.assertIn("Canonical Name", [c.author for c in parsed])
        self.assertIn("canonical@example.org", [c.email for c in parsed])

    def test_13_branch_and_merge_policy_is_explicit(self):
        self.commit("feat: base")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("feat: experiment", "feature.py")
        self.git("checkout", "-q", "main")
        self.commit("fix: mainline", "main.py")
        self.git("merge", "--no-ff", "-m", "Merge feature", "feature")
        main_only = parse_log_text(read_log(self.repo))
        merges = parse_log_text(read_log(self.repo, include_merges=True))
        self.assertEqual(len(main_only), 3)
        self.assertEqual(len(merges), 4)
        self.assertTrue(any("Merge feature" in c.subject for c in merges))
        self.scan("--include-merges", "--all-branches")

    def test_16_incremental_new_commits_and_unchanged_head(self):
        self.commit("feat: first")
        cached_dir = self.base / "cache"
        a = fetch_git_log(self.repo, cached_dir, since_days=None, until=None,
                          all_branches=False, include_merges=False)
        self.assertEqual(a.mode, "full")
        self.assertEqual(len(parse_log_text(a.text)), 1)
        b = fetch_git_log(self.repo, cached_dir, since_days=None, until=None,
                          all_branches=False, include_merges=False)
        self.assertEqual(b.mode, "unchanged")
        self.commit("feat: second", "second.py")
        c = fetch_git_log(self.repo, cached_dir, since_days=None, until=None,
                          all_branches=False, include_merges=False)
        self.assertEqual(c.mode, "delta")
        self.assertEqual(len(parse_log_text(c.text)), 2)

        _, payload, _ = self.scan("--incremental", "--cache-dir", str(cached_dir))
        self.assertEqual(payload["summary"]["commits"], 2)
        _, _, proc = self.scan("--incremental", "--cache-dir", str(cached_dir))
        self.assertIn("缓存报告命中", proc.stdout)

    def test_16_fast_delta_stats_match_full_recalculation(self):
        for i in range(35):
            self.commit(f"feat: generated fixture {i}", f"src/m{i % 5}.py")
        self.scan("--incremental", "--show-authors")
        self.commit("fix: follow-up change", "src/m0.py")
        _, delta_json, _ = self.scan("--incremental", "--show-authors")
        self.assertEqual(delta_json["performance"]["cache_input_mode"], "delta")
        _, full_json, _ = self.scan("--show-authors")
        self.assertEqual(delta_json["summary"], full_json["summary"])
        for from_delta, from_full in zip(
            delta_json["diagnosis"]["checks"], full_json["diagnosis"]["checks"]
        ):
            self.assertEqual(from_delta["check"], from_full["check"])
            self.assertEqual(from_delta["severity"], from_full["severity"])
            self.assertEqual(from_delta["stats"], from_full["stats"])
        self.assertTrue(delta_json["evidence_audit"]["passed"])

    def test_16_delta_accepts_new_member_without_stale_pseudonyms(self):
        for i in range(35):
            self.commit(f"feat: fixture baseline {i}", f"src/m{i % 5}.py")
        self.scan("--incremental")
        self.commit("feat: new contributor", "new_member.py",
                    name="Another Developer", email="aaa@example.org")
        _, fast_json, _ = self.scan("--incremental")
        self.assertEqual(fast_json["performance"]["cache_input_mode"], "delta")
        self.assertEqual(fast_json["summary"]["authors"], 2)
        _, full_json, _ = self.scan()
        self.assertEqual(fast_json["summary"], full_json["summary"])
        for fast, full in zip(fast_json["diagnosis"]["checks"],
                              full_json["diagnosis"]["checks"]):
            self.assertEqual(fast["stats"], full["stats"], fast["check"])

    def test_20_missing_repository_path_fails_cleanly(self):
        missing = self.base / "nonexistent"
        proc = subprocess.run(
            [sys.executable, "-m", "commitlens", "scan", str(missing)],
            cwd=PROJECT, capture_output=True, check=False,
            **SUBPROCESS_TEXT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("路径不存在", proc.stderr or "")

    def test_20_permission_denied_is_a_readable_error(self):
        # Deterministic fault injection: chmod is unreliable in CI running as
        # root, so exercise the real local-repo call with Git execution denied.
        with patch('commitlens.ingest.subprocess.run',
                   side_effect=PermissionError('EACCES: cannot execute git')):
            with self.assertRaisesRegex(RuntimeError, '没有权限'):
                read_log(self.repo)

    def test_18_generated_html_has_no_network_dependencies(self):
        self.commit("feat: add local report")
        report, payload, _ = self.scan()
        self.assertNotIn("<script", report)
        self.assertNotIn("src=\"http", report)
        self.assertNotIn("href=\"http", report)
        self.assertTrue(payload["evidence_audit"]["passed"])


if __name__ == "__main__":
    unittest.main()
