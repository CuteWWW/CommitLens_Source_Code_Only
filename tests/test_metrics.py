"""指标层与诊断层测试。"""

from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

from commitlens.diagnose import diagnose, grade_of
from commitlens.metrics import run_all
from commitlens.metrics.cadence import run as cadence_run
from commitlens.metrics.ownership import bus_factor, run as ownership_run
from commitlens.metrics.message import _is_vague, rule_violations
from commitlens.metrics.size import run as size_run
from commitlens.metrics.trend import run as trend_run
from commitlens.config import Config
from commitlens.model import Commit, FileChange, Severity
from commitlens.stats import mann_kendall

from helpers import build_log, by_check, make_ctx, weekly_log


class TestMessageRules(unittest.TestCase):
    def setUp(self):
        self.cfg = Config().get

    def commit(self, subject, body="", churn=10):
        return Commit(
            sha="0" * 40, author="A", email="a@e.com",
            date=datetime.now(timezone.utc), subject=subject, body=body,
            files=[FileChange("a.py", churn, 0)],
        )

    def test_vague_words(self):
        self.assertTrue(_is_vague("update", ["update"]))
        self.assertTrue(_is_vague("Fix bug", ["fix"]))
        self.assertTrue(_is_vague("wip", ["wip"]))
        self.assertFalse(_is_vague("fix: avoid null pointer in parser", ["fix"]))
        self.assertFalse(_is_vague("feat: add retry", ["fix"]))

    def test_long_subject(self):
        c = self.commit("a" * 90)
        self.assertTrue(any("标题过长" in v for v in rule_violations(c, self.cfg)))

    def test_missing_type_prefix(self):
        cfg = Config({"message": {"require_type_prefix": True}}).get
        self.assertTrue(any("类型前缀" in v for v in rule_violations(self.commit("add parser"), cfg)))
        self.assertEqual(rule_violations(self.commit("feat: add parser"), cfg), [])

    def test_big_change_needs_body(self):
        c = self.commit("feat: rewrite engine", body="", churn=500)
        self.assertTrue(any("正文" in v for v in rule_violations(c, self.cfg)))
        c2 = self.commit("feat: rewrite engine", body="Because the old one was slow.", churn=500)
        self.assertEqual(rule_violations(c2, self.cfg), [])


class TestSize(unittest.TestCase):
    def test_giant_detection(self):
        files = [(1, 0, f"src/f{i}.py") for i in range(40)]
        log = build_log(("1" * 40, "A", "a@e.com", "2026-11-01T10:00:00+00:00",
                         "feat: big", "", files))
        results = size_run(make_ctx(log))
        giant = by_check(results, "giant")
        self.assertEqual(giant.severity, Severity.ALERT)
        self.assertEqual(giant.stats["count"], 1)


class TestOwnership(unittest.TestCase):
    def test_single_owner(self):
        records = []
        for i in range(5):
            records.append((f"{i:040x}", "Alice", "a@e.com", "2026-11-01T10:00:00+00:00",
                            f"feat: x{i}", "", [(1, 0, "core.py")]))
        records.append(("f" * 40, "Bob", "b@e.com", "2026-11-02T10:00:00+00:00",
                        "fix: y", "", [(1, 0, "core.py")]))
        results = ownership_run(make_ctx(build_log(*records)))
        own = by_check(results, "ownership")
        self.assertEqual(own.stats["single_owner_files"], 1)
        self.assertEqual(own.stats["bus_factor"], 1)

    def test_bus_factor_math(self):
        counts = {"a.py": Counter({"A": 8, "B": 2}), "b.py": Counter({"B": 5, "C": 5})}
        n, cum = bus_factor(counts, 0.8)
        # A=8(40%) B=15(75%) C=20(100%)，覆盖 80% 需要 3 个人
        self.assertEqual(n, 3)


class TestTrend(unittest.TestCase):
    def test_decline_is_detected(self):
        log = weekly_log([10, 11, 10, 9, 10, 10, 9, 10, 6, 5, 4, 3])
        trend = by_check(trend_run(make_ctx(log)), "trend")
        self.assertIn(trend.severity, (Severity.WARN, Severity.ALERT))
        self.assertLess(trend.stats["change"], -0.25)

    def test_flat_is_not_flagged(self):
        log = weekly_log([10, 10, 11, 10, 10, 9, 10, 11, 10, 10])
        trend = by_check(trend_run(make_ctx(log)), "trend")
        self.assertIn(trend.severity, (Severity.OK, Severity.INFO))

    def test_mann_kendall(self):
        self.assertLess(mann_kendall([10, 9, 8, 7, 6, 5, 4, 3])[2], 0.05)
        flat = mann_kendall([5, 6, 5, 6, 5, 6])
        self.assertIsNotNone(flat)
        self.assertGreater(flat[2], 0.5)


class TestCadence(unittest.TestCase):
    def test_dormant_member(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        new = (now - timedelta(days=1)).isoformat()
        records = [
            (f"{i:040x}", "Alice", "a@e.com", old, f"feat: x{i}", "", [(1, 0, "a.py")])
            for i in range(6)
        ]
        records += [
            ("f" * 40, "Bob", "b@e.com", new, "fix: y", "", [(1, 0, "a.py")]),
        ]
        results = cadence_run(make_ctx(build_log(*records)))
        dormant = by_check(results, "cadence_dormant")
        self.assertIn("Alice", dormant.stats["dormant"])

    def test_frequent_committer(self):
        now = datetime.now(timezone.utc)
        records = []
        for i in range(30):
            records.append((f"{i:040x}", "Fast", "f@e.com",
                            (now - timedelta(days=i % 10)).isoformat(),
                            f"feat: x{i}", "", [(1, 0, "a.py")]))
        for i in range(3):
            records.append((f"{i + 100:040x}", "Slow", "s@e.com",
                            (now - timedelta(days=i * 10)).isoformat(),
                            f"fix: y{i}", "", [(1, 0, "b.py")]))
        results = cadence_run(make_ctx(build_log(*records)))
        freq = by_check(results, "cadence_freq")
        self.assertIn("Fast", freq.stats["frequent"])


class TestDiagnosis(unittest.TestCase):
    def test_score_is_sum_of_deductions(self):
        log = weekly_log([10, 9, 8, 7, 6, 5, 4, 3])
        results = run_all(make_ctx(log))
        d = diagnose(results)
        self.assertEqual(len(results), 8)
        self.assertEqual(d.score, max(0, 100 - sum(x.points for x in d.deductions)))
        self.assertEqual(d.grade, grade_of(d.score))

    def test_every_check_has_summary(self):
        log = weekly_log([3, 4, 3, 5, 4, 3, 4, 5])
        for r in run_all(make_ctx(log)):
            self.assertTrue(r.summary)
            self.assertTrue(r.title)


if __name__ == "__main__":
    unittest.main()
