"""配置加载。

用标准库 tomllib（Python 3.11+），不引入 PyYAML，减少安装麻烦。
所有阈值都从配置来，对应 NFR「可配置」：不写死数字，换团队改配置即可。
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
        # 只分析最近 N 天；设成 0 表示全历史
        "window_days": 180,
        # 这些路径不参与分析（生成物、依赖、锁文件会把统计带偏）
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
        # 排除的机器人账号
        "ignore_authors": ["dependabot[bot]", "renovate[bot]"],
        # 低于这个提交数，百分比和排名基本是噪声，报告会给出提示
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
        # 一句话说不清改了什么的词
        "vague_words": [
            "update", "updates", "fix", "fixes", "wip", "minor",
            "change", "changes", "modify", "misc", "stuff", "tmp",
            "修改", "更新", "提交", "测试", "fix bug", "bug fix",
        ],
        "require_issue_ref": False,
        # 大改动必须写正文解释为什么
        "require_body_churn": 200,
    },
    "size": {
        # 超过这个文件数算"巨型提交"
        "max_files": 30,
        # 超过这个增删行数算"巨型提交"
        "max_lines": 800,
        # 同时用分位数兜底（P95），避免大团队阈值失真
        "giant_percentile": 95,
        "top_n": 5,
    },
    "cadence": {
        # 高于团队中位数的多少倍算"过于频繁"
        "freq_multiplier": 3.0,
        "min_commits_for_freq": 8,
        # 多少天没提交算"长期未提交"
        "dormant_days": 21,
        # 判断空窗前，此人至少要有多少次提交
        "min_commits_for_dormant": 5,
    },
    "hotspot": {
        "top_n": 10,
        "min_changes": 3,
    },
    "ownership": {
        # 单人改动占比超过多少算"单人垄断"
        "single_owner_share": 0.8,
        "min_changes": 5,
        # 巴士因子：覆盖多少比例的改动
        "bus_factor_coverage": 0.8,
        "shared_min_authors": 3,
        "top_n": 10,
    },
    "trend": {
        "recent_weeks": 4,
        # 统计显著性阈值
        "alpha": 0.05,
        # 降幅超过多少百分比算"明显下降"
        "drop_pct": 0.25,
        "min_weeks": 8,
    },
    "report": {
        "title": "Git 提交历史健康度报告",
        # 团队名，显示在页头
        "team": "",
    },
}

DEFAULT_TOML = """# CommitLens 配置。所有阈值都可以改，改完重新 scan 即可。
# 这份文件本身会进报告页脚（哈希），保证结果可复现。

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
title = "Git 提交历史健康度报告"
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
