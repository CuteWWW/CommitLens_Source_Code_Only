"""输出层测试：结构完整、内容转义。"""

from __future__ import annotations

import unittest
from datetime import datetime

from commitlens.diagnose import diagnose
from commitlens.metrics import run_all
from commitlens.report.html import render as render_html

from helpers import build_log, make_ctx, weekly_log

META = {
    "title": "Test Report",
    "team": "Panda",
    "generated_at": "2026-11-17 10:00",
    "config_hash": "deadbeef",
    "window_days": 180,
    "source": "<test>",
    "git_command": ["git", "log"],
    "config_toml": "[general]\nwindow_days = 180\n",
    "command": "python -m commitlens scan .",
}


def build_html(log: str) -> str:
    ctx = make_ctx(log)
    return render_html(ctx, diagnose(run_all(ctx)), META)


class TestReport(unittest.TestCase):
    def test_structure(self):
        html = build_html(weekly_log([5, 6, 4, 7, 5, 6, 4, 5, 3, 2]))
        self.assertIn("<!doctype html>", html)
        self.assertEqual(html.count('<section class="check"'), 8)
        self.assertEqual(html.count('class="card"'), 8)
        self.assertIn("Overall score", html)
        self.assertIn("Deduction Details", html)
        self.assertIn("Test Report", html)
        self.assertIn("<svg", html)
        self.assertNotRegex(html, r"[\u4e00-\u9fff]")

    def test_no_external_assets(self):
        html = build_html(weekly_log([5, 6, 4, 7, 5, 6, 4, 5]))
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<script", html)

    def test_escaping(self):
        # 标题过长 => 进入证据列表 => 必须被转义
        payload = "<script>alert(1)</script>" + "x" * 60
        log = build_log(
            ("1" * 40, "A", "a@e.com", "2026-11-01T10:00:00+00:00",
             payload, "", [(1, 0, "a.py")]),
        )
        html = build_html(log)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_evidence_is_collapsible(self):
        html = build_html(weekly_log([10, 9, 8, 7, 6, 5, 4, 3]))
        self.assertIn("<details", html)
        self.assertIn("View evidence", html)


if __name__ == "__main__":
    unittest.main()
