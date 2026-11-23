"""采集层测试：日志解析、重命名归一、忽略规则。

不需要装 git —— 这就是把「读 git」和「解析文本」拆开的好处。
"""

from __future__ import annotations

import unittest

from commitlens.ingest import filter_commits, normalize_path, parse_log_text

HEAD, FIELD, END = "\x01", "\x1f", "\x02"


def build_log(*records: tuple) -> str:
    """records: (sha, author, email, date, subject, body, [(added, deleted, path), ...])"""
    out = []
    for sha, author, email, date, subject, body, files in records:
        chunk = f"{HEAD}{sha}{FIELD}{author}{FIELD}{email}{FIELD}{date}{FIELD}{subject}{FIELD}{body}{END}"
        lines = [f"{a}\t{d}\t{p}" for a, d, p in files]
        out.append(chunk + "\n" + "\n".join(lines) + "\n")
    return "".join(out)


class TestParse(unittest.TestCase):
    def test_basic(self):
        text = build_log(
            ("a" * 40, "Zhang Wei", "z@e.com", "2026-11-01T10:00:00+08:00",
             "feat: add parser", "because reasons", [(10, 2, "src/a.py")]),
        )
        commits = parse_log_text(text)
        self.assertEqual(len(commits), 1)
        c = commits[0]
        self.assertEqual(c.author, "Zhang Wei")
        self.assertEqual(c.subject, "feat: add parser")
        self.assertEqual(c.body, "because reasons")
        self.assertEqual(c.nfiles, 1)
        self.assertEqual(c.churn, 12)
        self.assertEqual(c.date.year, 2026)

    def test_binary_and_rename(self):
        text = build_log(
            ("b" * 40, "A", "a@e.com", "2026-11-01T10:00:00+08:00", "x", "", [
                ("-", "-", "assets/logo.png"),
                (3, 1, "src/{old.py => new.py}/mod"),
            ]),
        )
        c = parse_log_text(text)[0]
        binary = [f for f in c.files if f.binary][0]
        self.assertEqual(binary.churn, 0)
        renamed = [f for f in c.files if not f.binary][0]
        self.assertEqual(renamed.path, "src/new.py/mod")

    def test_multiple_commits_sorted(self):
        text = build_log(
            ("c" * 40, "A", "a@e.com", "2026-11-02T10:00:00+08:00", "second", "", [(1, 0, "a.py")]),
            ("d" * 40, "A", "a@e.com", "2026-11-01T10:00:00+08:00", "first", "", [(1, 0, "a.py")]),
        )
        commits = parse_log_text(text)
        self.assertEqual([c.subject for c in commits], ["first", "second"])

    def test_normalize_path_variants(self):
        self.assertEqual(normalize_path("a/b.py"), "a/b.py")
        self.assertEqual(normalize_path("old.py => new.py"), "new.py")
        self.assertEqual(normalize_path("src/{a.py => b.py}"), "src/b.py")
        self.assertEqual(normalize_path("src/{a => b}/x.py"), "src/b/x.py")

    def test_ignore_paths(self):
        text = build_log(
            ("e" * 40, "A", "a@e.com", "2026-11-01T10:00:00+08:00", "x", "", [
                (1, 0, "src/a.py"),
                (1, 0, "node_modules/lib/index.js"),
                (1, 0, "package-lock.json"),
            ]),
        )
        commits = filter_commits(parse_log_text(text), ["node_modules/", "*.json"], [])
        self.assertEqual(commits[0].paths, ["src/a.py"])

    def test_commit_all_ignored_is_dropped(self):
        text = build_log(
            ("f" * 40, "A", "a@e.com", "2026-11-01T10:00:00+08:00", "x", "", [
                (1, 0, "dist/bundle.js"),
            ]),
        )
        self.assertEqual(filter_commits(parse_log_text(text), ["dist/"], []), [])

    def test_ignore_author(self):
        text = build_log(
            ("1" * 40, "dependabot[bot]", "bot@e.com", "2026-11-01T10:00:00+08:00", "bump", "", [
                (1, 0, "a.py"),
            ]),
        )
        self.assertEqual(
            filter_commits(parse_log_text(text), [], ["dependabot[bot]"]), []
        )


if __name__ == "__main__":
    unittest.main()
