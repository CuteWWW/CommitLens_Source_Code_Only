"""Regression tests linked to the original 52-issue audit: 1-14, 16-20."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commitlens.audit import audit_evidence
from commitlens.config import Config
from commitlens.diagnose import diagnose
from commitlens.identity import normalize_identities, pseudonymize_authors
from commitlens.ingest import parse_log_text
from commitlens.metrics import run_all
from commitlens.metrics.cadence import run as cadence_run
from commitlens.metrics.message import rule_violations
from commitlens.metrics.size import run as size_run
from commitlens.metrics.trend import run as trend_run
from commitlens.model import Commit, FileChange, Severity

from helpers import build_log, by_check, make_ctx, weekly_log


class TestIssueRegressions(unittest.TestCase):
    def test_01_trailing_zero_commit_weeks_included(self):
        log = weekly_log([10] * 8)
        ctx = make_ctx(log, window_days=0)
        ctx.now = ctx.commits[-1].date + timedelta(weeks=8)
        weekly = ctx.weekly()
        self.assertGreaterEqual(len(weekly), 16)
        self.assertEqual([n for _, n in weekly[-7:]], [0] * 7)
        trend = by_check(trend_run(ctx), "trend")
        self.assertEqual(trend.severity, Severity.ALERT)
        self.assertLess(trend.stats["change"], -0.25)

    def test_02_one_developer_never_marked_hyperactive(self):
        now = datetime.now(timezone.utc)
        records = [
            (f"{i:040x}", "Solo", "solo@example.org", (now - timedelta(days=i % 4)).isoformat(),
             f"feat: revision {i}", "", [(1, 0, "solo.py")])
            for i in range(50)
        ]
        result = by_check(cadence_run(make_ctx(build_log(*records))), "cadence_freq")
        self.assertEqual(result.stats["frequent"], [])
        self.assertEqual(result.severity, Severity.OK)

    def test_03_prefix_whitelist_enforced(self):
        c = Commit("f" * 40, "A", "a@example.org", datetime.now(timezone.utc),
                   "xyz: add a feature", files=[FileChange("a.py", 4, 0)])
        cfg = Config({"message": {"require_type_prefix": True,
                                   "type_prefixes": ["feat", "fix"]}}).get
        self.assertTrue(any("not in the allowed list" in x for x in rule_violations(c, cfg)))
        c.subject = "feat: add a feature"
        self.assertFalse(any("type prefix" in x.lower() for x in rule_violations(c, cfg)))

    def test_04_percentile_changes_giant_detection(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [(f"{i:040x}", "A", "a@ex.org", now,
                    f"feat: revision {i}", "", [(10 if i != 29 else 600, 0, "f.py")])
                   for i in range(30)]
        override = {"size": {"max_files": 999, "max_lines": 9999,
                             "giant_percentile": 95}}
        result = by_check(size_run(make_ctx(build_log(*records), override)), "giant")
        self.assertEqual(result.stats["count"], 1)
        override["size"]["giant_percentile"] = 100
        result = by_check(size_run(make_ctx(build_log(*records), override)), "giant")
        self.assertEqual(result.stats["count"], 0)

    def test_08_same_name_different_emails_and_alias_merges(self):
        records = [
            ("1" * 40, "Wei", "wei@a.org", "2026-11-01T00:00:00+00:00", "x", "", [(1, 0, "a")]),
            ("2" * 40, "Wei", "wei@b.org", "2026-11-02T00:00:00+00:00", "x", "", [(1, 0, "a")]),
            ("3" * 40, "Wei New", "new@a.org", "2026-11-03T00:00:00+00:00", "x", "", [(1, 0, "a")]),
        ]
        commits = parse_log_text(build_log(*records))
        audit = normalize_identities(commits, {"wei@a.org": "new@a.org"})
        self.assertEqual(audit.identities, 2)
        self.assertEqual(audit.alias_merges, 1)
        self.assertEqual(commits[0].author, commits[2].author)
        self.assertNotEqual(commits[1].author, commits[2].author)
        pseudonymize_authors(commits)
        self.assertEqual(len({c.author for c in commits}), 2)
        self.assertTrue(all(c.email == "redacted" for c in commits))

    def test_09_low_sample_cannot_be_graded(self):
        ctx = make_ctx(weekly_log([1, 1, 1]))
        diagnosis = diagnose(run_all(ctx), n_commits=len(ctx.commits), min_commits=30)
        self.assertIsNone(diagnosis.score)
        self.assertEqual(diagnosis.grade, "N/A")
        self.assertFalse(diagnosis.scorable)

    def test_10_17_every_finding_has_valid_original_sha(self):
        ctx = make_ctx(weekly_log([10, 9, 8, 7, 6, 5, 4, 3]))
        diagnosis = diagnose(run_all(ctx))
        outcome = audit_evidence(diagnosis, ctx.commits)
        self.assertTrue(outcome["passed"], outcome["failures"])
        self.assertEqual(outcome["trace_rate"], 1.0)

    def test_11_small_p_values_not_rounded_to_zero(self):
        ctx = make_ctx(weekly_log([20, 19, 18, 17, 14, 11, 7, 3, 2, 1]))
        result = by_check(trend_run(ctx), "trend")
        self.assertIn("p < 0.001", result.summary)
        self.assertNotIn("p = 0.000", result.summary)

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            Config({"trend": {"alpha": -1}})
        with self.assertRaises(ValueError):
            normalize_identities([Commit("0" * 40, "A", "a", datetime.now(timezone.utc), "x")],
                                 {"a": "b", "b": "a"})


if __name__ == "__main__":
    unittest.main()
