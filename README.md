# CommitLens

把 Git 提交历史变成一份**团队开发健康度体检报告**：不只给数字，而是给结论、证据和行动建议。

```bash
python -m commitlens scan .          # 分析当前仓库，生成 report.html
```

打开生成的 `report.html` 就能看到八项检查的结果，每一结论都能点开看证据。

- **零依赖**：只用 Python 3.11+ 标准库，不需要 pip install
- **零联网**：默认不发出任何网络请求，报告是一个自包含的 HTML 文件
- **可复现**：报告页脚带采集命令、配置内容与配置指纹
- **SHA 证据链**：从诊断结论定位至源提交；生成时执行结构审计
- **增量分析**：`--incremental` 对安全的快进更新仅计算新增历史，遇到重写/配置变更回退全量
- **默认匿名显示**：作者标签默认为 `Dev-N`；需显式 `--show-authors` 才显示姓名

---

## 1. 快速开始

```bash
# 1. 分析当前目录的仓库
python -m commitlens scan .

# 2. 分析指定仓库，只取最近 90 天
python -m commitlens scan ../team-repo --since 90 --out team.html

# 3. 没有仓库也能先跑：用一份造出来的日志
python scripts/make_demo_repo.py --log demo.log
python -m commitlens scan --log demo.log --out demo.html
```

其它命令：

```bash
python -m commitlens checks    # 看八个检查项都查什么
python -m commitlens init      # 生成 commitlens.toml
python -m unittest discover -s tests -q    # 运行 49 个自动化测试
python scripts/coverage_report.py         # 主进程 Python 语句覆盖率
python scripts/run_evaluation.py          # 合成基线/敏感性实验

# 增量分析；请不要把 .commitlens-cache 传到公开仓库
python -m commitlens scan ../team-repo --since 0 --incremental --out report.html --json report.json

# 可复现的固定时间快照（不会取未来提交）
python -m commitlens scan ../team-repo --as-of 2026-11-19 --since 180 --out report.html

# 明确启用跨分支和 Merge；两者默认均不开启
python -m commitlens scan ../team-repo --all-branches --include-merges
```

测试命令只依赖 Python 标准库和本机 Git。Windows 上请确保 `git` 已在
`PATH` 中；测试会显式按 UTF-8 解码子进程输出，不依赖系统默认的 GBK/CP936。

接 CI 时用 `--fail-on warn`：出现「关注」及以上的问题时进程返回 1，可以卡住合并。

---

## 2. 八个检查项

| # | 检查项 | 回答的问题 | 主要指标 |
|---|---|---|---|
| 1 | 提交信息规范 | message 合不合规？ | 标题长度、类型前缀、空话词、issue 关联、大改动是否有正文 |
| 2 | 单次改动规模 | 一次是不是改太多？ | 文件数/行数的中位数与 P90，超阈值提交数 |
| 3 | 巨型提交 | 有没有"大爆炸"提交？ | ≥30 文件的提交占比，及它们吃掉的改动量 |
| 4 | 提交频率异常 | 谁提交过于频繁？ | 每人次/周 vs **其他人**的中位数（留一法） |
| 5 | 长期未提交 | 谁长期没动静？ | 距最后一次提交的天数，且此前是否活跃 |
| 6 | 热点文件 | 哪些文件最常被改？ | 改动次数、改动行数、参与人数 |
| 7 | 知识集中度 | 谁总改同一文件？风险多大？ | 单人占比 ≥80% 的文件数、**巴士因子** |
| 8 | 活跃度趋势 | 项目在降温吗？ | 近 4 周 vs 前 4 周均值、Mann-Kendall 趋势检验 |

诊断结果分四级：`正常 / 提示 / 关注 / 问题`。
综合评分 = 100 −（每个「问题」8 分 + 每个「关注」4 分），每一笔扣分都列在报告的「扣分明细」里，没有黑箱。

---

## 3. 代码结构

```
commitlens/
├── __main__.py           python -m commitlens 的入口
├── cli.py                命令行：scan / checks / init
├── config.py             commitlens.toml 加载（tomllib，无第三方依赖）
├── model.py              Commit / Finding / Evidence / CheckResult
├── ingest.py             采集层：git log → Commit 列表
├── identity.py           mailmap + alias + 重名消歧 + 默认 Dev 编号
├── cache.py              SHA/配置有效性校验、本地缓存与快速增量输入
├── rollup.py             精确物化统计状态、增量合并与证据保留
├── audit.py              Finding→SHA 的结构验证
├── stats.py              Mann-Kendall、线性斜率（纯函数，好测）
├── diagnose.py           诊断层：8 个结果 → 评分 + 头部结论
├── metrics/              指标层，一个模块一个主题
│   ├── base.py           Context、周聚合、分位数等公共工具
│   ├── message.py        检查项 1
│   ├── size.py           检查项 2、3
│   ├── cadence.py        检查项 4、5
│   ├── hotspot.py        检查项 6
│   ├── ownership.py      检查项 7（巴士因子）
│   └── trend.py          检查项 8
└── report/
    ├── charts.py         内联 SVG 图表（柱状 / 折线 / 表格）
    └── html.py           整份报告的渲染

scripts/
├── make_demo_repo.py     造合成历史（含"活跃度下降"场景，用于评估）
├── check_html.py         粗查 HTML 标签是否配对
├── benchmark.py          真实 Git 对象、合成历史的耗时测量
├── run_evaluation.py     合成固定阈值基线和敏感性试验
├── coverage_report.py    不依赖第三方模块的主进程覆盖率报告
├── build_issue_tracker.py  保留原问题描述的 52 项验收状态表
└── burndown.py           真实任务导入后生成燃尽图；空数据不伪造趋势

docs/adr/                 含 ADR-004～006 的性能、解释性和隐私决策
tests/                    49 个单元和真实本地 Git 集成测试（需要本地 Git）
process/                  Sprint、会议、人员贡献等真实证据填写模板
report/                   完整 LaTeX 正文与可重复构建的双栏 PDF 源码
deliverables/             样例报告、52 项更新表、展示网页、视频和双栏稿
```

数据流：

```
git log ──► ingest.parse_log_text ──► filter（忽略目录/机器人/时间窗）
        ──► metrics.run_all（8 个检查项）──► diagnose（评分 + 扣分）
        ──► report.html（结论 + 证据 + 图表）
```

关键设计：**finding（结论）与 evidence（证据）分离**。网页、JSON、报告都只是 finding 的不同渲染，所以加检查项不用改前端。**注意**：Git SHA 证据并不等同于对工程质量的因果证明。

---

## 4. 怎么加第 9 个检查项（给分组用）

1. 在 `commitlens/metrics/` 新建 `xxx.py`
2. 写 `run(ctx) -> list[CheckResult]`
3. 在 `metrics/__init__.py` 的 `MODULES` 里加上模块名
4. 在 `CHECK_TITLES` 里登记标题

```python
from ..model import CheckResult, Finding, Severity
from .base import Context, register


def run(ctx: Context) -> list[CheckResult]:
    n = len(ctx.commits)
    return [CheckResult(
        check="xxx", title="新检查项", severity=Severity.INFO,
        summary=f"共 {n} 个提交", order=9,
        stats={"n": n},
        chart={"type": "bar", "labels": ["A"], "values": [n], "unit": "次"},
        findings=[],
    )]
```

`chart` 支持三种：`bar` / `line` / `table`，见 `report/charts.py`。

---

## 5. 配置

`python -m commitlens init` 生成 `commitlens.toml`，所有阈值都在里面：

```toml
[general]
window_days = 180                 # 只分析最近 180 天
ignore_paths = ["node_modules/", "dist/", "*.lock"]

[message]
require_type_prefix = false       # 要不要强制 feat:/fix: 前缀
require_issue_ref = false         # 要不要强制关联 #123

[size]
max_files = 30                    # 超过 30 个文件算巨型提交
max_lines = 800

[cadence]
freq_multiplier = 3.0             # 高于其他人中位数 3 倍算异常
dormant_days = 21

[ownership]
single_owner_share = 0.8          # 单人占 80% 以上算知识集中

[trend]
recent_weeks = 4
alpha = 0.05
```

报告记录的是**真正生效的合并配置**、指纹、完整执行命令与来源哈希。默认 `.mailmap` 配合 `[identity].aliases` 可合并身份。`--show-authors` 是显式显示真实姓名的开关；默认使用匿名编号，缓存中的原始日志仍可能包含隐私信息，不应公开。

---

## 6. 给 8 人团队的切分建议

| 组 | 人 | 负责文件 | 第一周产出 |
|---|---|---|---|
| 采集与存储 | 2 | `ingest.py` `config.py` `cli.py` | 能读真仓库、忽略规则生效、`--since` 可用 |
| 指标与算法 | 2 | `metrics/*` `stats.py` | 8 个检查项全部出数 |
| 前端与报告 | 2 | `report/*` | HTML 报告好看、图表正确、证据可折叠 |
| 评估与文档 | 2 | `scripts/` `tests/` `docs/` | 合成场景、基线对比、报告章节 |

接口契约：`CheckResult` 和 `Finding` 两个 dataclass（`model.py`）。第一周定死，之后各组并行不会互相等。

---

## 7. 评估怎么跑

```bash
# 造一个"第 14 周开始下降"的合成历史，看趋势检测能不能抓到
python scripts/make_demo_repo.py --log decline.log --weeks 24 --decline-at 14
python -m commitlens scan --log decline.log --out decline.html
# 期望：第 8 项「活跃度趋势」判为「问题」，并给出从第几周开始下降

# 造一个"一直很平稳"的历史，看会不会误报
python scripts/make_demo_repo.py --log flat.log --weeks 24 --decline-at 99
```

真实仓库上的做法：跑工具 → 导出 JSON → 人工核对 30 条 finding（两人独立 + 仲裁）→ 算一致率 → 写进报告。

---

## 8. 已知限制

- 不读 issue / PR，所以"关联 issue"只做正则匹配，判断不了是否真的对应
- 不解析代码语义，tangled change 只到文件级，不到 hunk 级
- 默认只分析 HEAD 的可达历史、不统计 Merge；可选 `--all-branches` 和 `--include-merges`，需要显式确认统计口径
- 作者使用 Git `.mailmap`、自定义 alias、重名防碰撞；仍不能自动证明同一邮箱背后是否为同一真实人员
- 缓存针对本地安全快进追加优化；分支重写、切换统计日期或参数会失效并重新计算
- 最新性能验证来自 **50 万条合成提交构成的真实本地 Git 仓库**，不能外推为任意真实项目的性能保证
- 40 个批次合成异常实验中，混合 P95 方法与较强的固定 150 行基线**打平**，没有实证证明更准确
- 没有团队原始 Git 历史、3 个真实 Sprint 的会议和 PR Review、真人标注或用户研究；课程 PDF 的上述项目仍需真人凭证
- `report/main.tex` 提供完整章节和双栏预览；**非官方 IEEE TSE 类模板最终排版**，正式提交需更换课程要求的模板并校对人员材料

这些都是「未来工作」一节现成的素材，写清楚比藏着好。

---

## 9. 开发约定

- 提交前跑 `python -m unittest discover -s tests`，必须全绿
- 一个 PR ≤ 400 行，PR 描述写清「改了什么 / 怎么验证 / `Closes #N`」
- 新增指标必须带一个测试，否则不算完成
- `docs/adr/` 里记录每个非功能需求的架构选择与理由
