"""CommitLens —— Git 提交历史健康度诊断工具。

模块划分（对应报告里的三层架构）：
    ingest      采集层：git log -> Commit 对象
    metrics     指标层：8 个检查项
    diagnose    诊断层：指标 -> finding（结论 + 证据 + 建议）
    report      输出层：静态 HTML / JSON
"""

__version__ = "0.2.0"
