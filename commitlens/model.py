"""核心数据模型。

设计要点：finding（结论）与 evidence（证据）分离。
网页、JSON、报告都只是 finding 的不同渲染方式，加检查项不用改前端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Severity(str, Enum):
    OK = "ok"        # 正常
    INFO = "info"    # 提示，不算问题
    WARN = "warn"    # 需要关注
    ALERT = "alert"  # 明显问题

    @property
    def rank(self) -> int:
        return {"ok": 0, "info": 1, "warn": 2, "alert": 3}[self.value]


SEVERITY_CN = {
    "ok": "正常",
    "info": "提示",
    "warn": "关注",
    "alert": "问题",
}


@dataclass(frozen=True)
class FileChange:
    """一个提交里对单个文件的改动。"""

    path: str
    added: int
    deleted: int
    binary: bool = False

    @property
    def churn(self) -> int:
        return self.added + self.deleted


@dataclass
class Commit:
    """一次提交。"""

    sha: str
    author: str
    email: str
    date: datetime
    subject: str
    body: str = ""
    files: list[FileChange] = field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def churn(self) -> int:
        return sum(f.churn for f in self.files)

    @property
    def nfiles(self) -> int:
        return len(self.files)

    @property
    def message(self) -> str:
        return (self.subject + "\n\n" + self.body).strip()

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


@dataclass
class Evidence:
    """一条证据：能被人点开核对的最小事实。"""

    label: str                      # 例如 "commit 3f2a1b9"
    detail: str = ""                # 一句话说明
    lines: list[str] = field(default_factory=list)  # 详细内容，折叠展示
    commit: str | None = None       # 关联的 commit sha


@dataclass
class Finding:
    """一条结论。每条都必须能追溯回 evidence。"""

    check: str          # 属于哪个检查项
    severity: Severity
    title: str          # 一句话结论
    detail: str = ""    # 展开说明（为什么这是个问题）
    advice: str = ""    # 建议
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "advice": self.advice,
            "evidence": [
                {"label": e.label, "detail": e.detail, "lines": e.lines, "commit": e.commit}
                for e in self.evidence
            ],
        }


@dataclass
class CheckResult:
    """一个检查项的完整结果。"""

    check: str
    title: str
    severity: Severity
    summary: str                    # 结论句，显示在总览卡片上
    order: int = 0
    stats: dict[str, Any] = field(default_factory=dict)   # 原始数字，进 JSON
    chart: dict[str, Any] | None = None                   # 图表描述，见 report/charts.py
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "title": self.title,
            "severity": self.severity.value,
            "summary": self.summary,
            "stats": self.stats,
            "chart": self.chart,
            "findings": [f.to_dict() for f in self.findings],
        }
