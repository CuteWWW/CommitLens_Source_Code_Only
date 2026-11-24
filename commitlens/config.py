"""Configuration loading.

Uses the standard-library tomllib module (Python 3.11+). All thresholds are
configurable so teams can adapt the analysis without changing code.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "commitlens.toml"

DEFAULTS: dict[str, Any] = {
    "general": {
        # Analyze the most recent N days; 0 means full history.
        "window_days": 180,
        # Generated files, dependencies, and lock files distort the statistics.
        "ignore_paths": [
            "node_modules/",
            "vendor/",
            "dist/",
            "build/",
            "*.lock",
            "package-lock.json",
            "*.min.js",
            "*.min.css",
            "*.svg",
            "*.png",
            "*.jpg",
        ],
        # Ignore automated contributors.
        "ignore_authors": ["dependabot[bot]", "renovate[bot]"],
        # Below this count, percentages and rankings are too noisy to score.
        "min_commits": 30,
    },
    "identity": {
        # Email aliases not covered by the repository's .mailmap.
        # Example: "old@company.com" = "new@company.com"
        "aliases": {},
    },
    "message": {
        "max_subject_length": 72,
        "min_subject_length": 8,
        "require_type_prefix": False,
        "type_prefixes": [
            "feat", "fix", "docs", "style", "refactor",
            "test", "chore", "perf", "build", "ci", "revert",
        ],
        # Subjects that do not explain what changed.
        "vague_words": [
            "update", "updates", "fix", "fixes", "wip", "minor",
            "change", "changes", "modify", "misc", "stuff", "tmp",
            "fix bug", "bug fix",
        ],
        "require_issue_ref": False,
        # Large changes require a body that explains why they were made.
        "require_body_churn": 200,
    },
    "size": {
        # Commits above this file count are considered oversized.
        "max_files": 30,
        # Commits above this changed-line count are considered oversized.
        "max_lines": 800,
        # Use a percentile threshold as a relative outlier guard.
        "giant_percentile": 95,
        "top_n": 5,
    },
    "cadence": {
        # Multiple of the team median considered unusually frequent.
        "freq_multiplier": 3.0,
        "min_commits_for_freq": 8,
        # Days without a commit before a contributor is considered dormant.
        "dormant_days": 21,
        # Minimum prior commits before dormancy is evaluated.
        "min_commits_for_dormant": 5,
    },
    "hotspot": {
        "top_n": 10,
        "min_changes": 3,
    },
    "ownership": {
        # Share of file changes from one contributor considered concentrated.
        "single_owner_share": 0.8,
        "min_changes": 5,
        # Share of historical changes covered by the bus-factor proxy.
        "bus_factor_coverage": 0.8,
        "shared_min_authors": 3,
        "top_n": 10,
    },
    "trend": {
        "recent_weeks": 4,
        # Statistical significance threshold.
        "alpha": 0.05,
        # Percentage drop considered materially significant.
        "drop_pct": 0.25,
        "min_weeks": 8,
    },
    "report": {
        "title": "Git Commit History Health Report",
        # Team name displayed in the report header.
        "team": "",
    },
}

DEFAULT_TOML = """# CommitLens configuration. Change thresholds and rerun scan.
# The configuration is fingerprinted and included in the report footer.

[general]
window_days = 180
ignore_paths = ["node_modules/", "vendor/", "dist/", "build/", "*.lock", "*.min.js"]
ignore_authors = ["dependabot[bot]", "renovate[bot]"]
min_commits = 30

[message]
max_subject_length = 72
min_subject_length = 8
require_type_prefix = false
require_issue_ref = false
require_body_churn = 200
type_prefixes = ["feat", "fix", "docs", "style", "refactor", "test", "chore", "perf", "build", "ci", "revert"]

[size]
max_files = 30
max_lines = 800
giant_percentile = 95

[identity]
# Git automatically reads .mailmap; list extra email aliases here if needed.
# aliases = { "old@example.org" = "new@example.org" }

[cadence]
freq_multiplier = 3.0
dormant_days = 21

[hotspot]
top_n = 10

[ownership]
single_owner_share = 0.8
min_changes = 5
bus_factor_coverage = 0.8

[trend]
recent_weeks = 4
alpha = 0.05
drop_pct = 0.25

[report]
title = "Git Commit History Health Report"
team = ""
"""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """嵌套字典 + 类型化取值，够用且好懂。"""

    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.data = _deep_merge(DEFAULTS, data or {})
        self.path = path
        self._validate()

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if path is None:
            default = Path(CONFIG_FILENAME)
            path = default if default.exists() else None
        if path is None:
            return cls()
        p = Path(path)
        with p.open("rb") as f:
            raw = tomllib.load(f)
        return cls(raw, path=p)

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name, {}))

    def get(self, section: str, key: str) -> Any:
        return self.data.get(section, {}).get(key, DEFAULTS.get(section, {}).get(key))

    @property
    def fingerprint(self) -> str:
        """配置指纹，写进报告页脚：结果不同时能判断是不是改了阈值。"""
        blob = json.dumps(self.data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:8]

    def _validate(self) -> None:
        """Catch invalid thresholds before producing misleading output."""
        for section, key, low, high in (
            ("general", "window_days", 0, 365000),
            ("general", "min_commits", 1, 1000000),
            ("size", "giant_percentile", 0, 100),
            ("cadence", "freq_multiplier", 1, 1000),
            ("trend", "alpha", 0, 1),
            ("trend", "drop_pct", 0, 1),
            ("trend", "recent_weeks", 1, 5200),
            ("trend", "min_weeks", 4, 5200),
            ("ownership", "bus_factor_coverage", 0, 1),
            ("ownership", "single_owner_share", 0, 1),
        ):
            value = self.get(section, key)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not low <= value <= high):
                raise ValueError(f"Invalid {section}.{key}: {value!r}; expected {low}..{high}")
        aliases = self.get("identity", "aliases")
        if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or not k or not v
            for k, v in aliases.items()
        ):
            raise ValueError("identity.aliases must map nonempty email strings to emails")


def write_default_config(path: str | Path) -> Path:
    p = Path(path)
    p.write_text(DEFAULT_TOML, encoding="utf-8")
    return p
