"""Exactness and invalidation tests for fast append-only materialization."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commitlens.audit import audit_evidence
from commitlens.config import Config
from commitlens.diagnose import diagnose
from commitlens.metrics import run_all
from commitlens.rollup import (add_commits, load_state, make_context, new_state,
                               run_from_state, save_state)

from helpers import make_ctx, weekly_log


class TestMaterializedResults(unittest.TestCase):
    def verify_parity(self, ctx):
        state = new_state(ctx.config, ctx.now, {c.email: c.author for c in ctx.commits})
        # Build incrementally, not just in one large batch.
        mid = len(ctx.commits) // 2
        add_commits(state, ctx.commits[:mid], ctx.config)
        add_commits(state, ctx.commits[mid:], ctx.config)
        normal = run_all(ctx)
        compact = run_from_state(state, ctx.config, ctx.now)
        self.assertEqual(len(normal), len(compact))
        for a, b in zip(normal, compact):
            self.assertEqual(a.check, b.check)
            self.assertEqual(a.severity, b.severity, a.check)
            self.assertEqual(a.stats, b.stats, a.check)
        sample_ctx = make_context(state, ctx.config, ctx.now, "fixture")
        self.assertEqual(sample_ctx.commit_count, len(ctx.commits))
        self.assertTrue(audit_evidence(diagnose(compact), sample_ctx.commits)["passed"])

    def test_parity_long_decline_and_message_violations(self):
        self.verify_parity(make_ctx(weekly_log([15, 13, 10, 8, 7, 4, 2, 1])))

    def test_parity_stable_weekly_activity(self):
        self.verify_parity(make_ctx(weekly_log([10, 9, 10, 11, 12, 9, 8, 11])))

    def test_parity_after_long_zero_commit_tail(self):
        ctx = make_ctx(weekly_log([8] * 10))
        ctx.now += timedelta(weeks=8)
        self.verify_parity(ctx)

    def test_state_invalidates_on_day_or_configuration_change(self):
        now = datetime.now(timezone.utc)
        cfg = Config()
        state = new_state(cfg, now, {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json.gz"
            save_state(state, path)
            self.assertIsNotNone(load_state(path, cfg, now))
            self.assertIsNone(load_state(path, cfg, now + timedelta(days=1)))
            self.assertIsNone(load_state(path, Config({"trend": {"alpha": .01}}), now))


if __name__ == "__main__":
    unittest.main()
