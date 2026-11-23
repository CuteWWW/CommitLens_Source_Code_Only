"""Update the supplied 52-issue HTML with verifiable repair statuses.

The imported issue statements and remedies are preserved. Project-history,
human-review and real-data gaps remain open rather than being fabricated.
"""

from __future__ import annotations

import argparse
import html
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Status, evidence path (relative to project root), and a precise qualification.
STATUS = {
    1: ("已修复", "tests/test_regressions.py", "补齐最后提交后至截止日的零提交周"),
    2: ("已修复", "tests/test_regressions.py", "单成员仓库不进行相对频率比较"),
    3: ("已修复", "commitlens/metrics/message.py", "真正校验 type_prefixes 白名单"),
    4: ("已修复", "commitlens/metrics/size.py", "分位数参与判定，并有小样本限制"),
    5: ("已修复", "commitlens/cli.py", "统一有效配置、展示、哈希"),
    6: ("已修复", "commitlens/report/html.py", "完整命令、配置、来源哈希和 Git 版本"),
    7: ("已修复", "commitlens/metrics/ownership.py", "明确是历史修改集中度代理，不冒充真实知识"),
    8: ("已修复", "commitlens/identity.py", "支持 mailmap、身份别名和重名区分"),
    9: ("已修复", "commitlens/diagnose.py", "样本不足标 N/A、不给 A/B/C 等级"),
    10: ("已修复", "commitlens/audit.py", "证据索引下钻至可定位 SHA"),
    11: ("已修复", "commitlens/metrics/trend.py", "极小 p 值显示为 p < 0.001"),
    12: ("已修复", "report/sections/04_solution.tex", "明确仅字符串正则关联，未验证远程 Issue"),
    13: ("已修复", "tests/test_integration_git.py", "显式增加 all-branches/include-merges 策略和测试"),
    14: ("已修复", "docs/evaluation_contract.md", "明确规模不是语义复杂度，属于诚实限制"),
    15: ("部分完成", "artifacts/benchmark_500k.json", "50 万真实本地 Git 对象由合成脚本生成；独立真实仓库未测"),
    16: ("部分完成", "artifacts/benchmark_500k.json", "增量 1.243s 通过合成实测；真实大型仓库性能待验"),
    17: ("部分完成", "commitlens/audit.py", "自动化机器 SHA 链接审核已做；人工逐条核查未做"),
    18: ("部分完成", "docs/adr/0006-offline-privacy.md", "离线静态资源检查已做；操作系统断网端到端尚未验证"),
    19: ("已修复", "artifacts/coverage.json", "新增可复运行覆盖率工具和分支测试"),
    20: ("已修复", "tests/test_integration_git.py", "本地真实 Git 集成 + 边界输入测试"),
    21: ("部分完成", ".github/workflows/ci.yml", "CI 配置就绪；没有真实上游 PR Status Check 证据"),
    22: ("部分完成", "artifacts/evaluation.json", "内部固定阈值基线已比较；外部工具同仓库定量测试缺失"),
    23: ("部分完成", "artifacts/evaluation.json", "合成消融已完成且与强固定阈值打平，不能宣称领先"),
    24: ("外部证据待补", "docs/evaluation_contract.md", "需真实开发者统计视图/解释视图任务对照"),
    25: ("已修复", "docs/evaluation_contract.md", "已冻结本次修复实验的范围、指标、基线和停止规则"),
    26: ("部分完成", "deliverables/sample_report.json", "合成样本可复现；尚无独立真实仓库冻结快照"),
    27: ("外部证据待补", "docs/evaluation_contract.md", "需人类独立标注的真实异常 Commit 数据集"),
    28: ("外部证据待补", "docs/evaluation_contract.md", "真实数据 precision/recall/FPR 尚不存在"),
    29: ("已修复", "docs/requirements_and_acceptance.md", "可执行验收案例、步骤、预期和来源完整列出"),
    30: ("部分完成", "artifacts/benchmark_500k.json", "多规模耗时与阶段记录已做；复杂真实仓库分布待测"),
    31: ("已修复", "artifacts/evaluation.json", "系统扫描多个规模/所有权/休眠阈值"),
    32: ("外部证据待补", "docs/evaluation_contract.md", "真实用户研究、完成时间和反馈尚未进行"),
    33: ("已修复", "report/sections/06_evaluation.tex", "公开固定规则打平的负面结果、偏差与局限"),
    34: ("外部证据待补", "process/team_registration_and_evidence.md", "原始 Git 历史和正式项目 URL 未提供"),
    35: ("外部证据待补", "process/SPRINT_EVIDENCE_GUIDE.md", "有三个 Sprint 计划与证据模板，不代表已举行"),
    36: ("外部证据待补", "process/weekly_meetings_template.csv", "周会真实日期/出席/会议记录未提供"),
    37: ("外部证据待补", "artifacts/burndown.svg", "生成器可用，但空数据图明确标示未核实"),
    38: ("已修复", "docs/requirements_and_acceptance.md", "11 个 FR story/验收标准/代码测试映射"),
    39: ("部分完成", ".github/pull_request_template.md", "模板就绪；真实 Issue→PR→Review→Close 待连接"),
    40: ("外部证据待补", "process/AI_USE_AND_HUMAN_REVIEW.md", "没有真实开发者审查意见及 PR 记录"),
    41: ("部分完成", "process/AI_USE_AND_HUMAN_REVIEW.md", "AI 使用和复核流程已记录；真人签字尚未有"),
    42: ("已修复", "process/technical_debt_register.md", "技术债登记和缓解优先级已提供；需团队后续维护"),
    43: ("部分完成", "artifacts/coverage.json", "当前覆盖率和代码改进可验证；不能冒充历史 Sprint 2 活动"),
    44: ("部分完成", "tests/test_regressions.py", "Bug 修复闭环有自动测试；历史 Sprint 3/PR 闭环未核实"),
    45: ("部分完成", "process/risk_register.md", "风险登记、Stop Rules 与拟定角色已提供；真实责任人未指派"),
    46: ("部分完成", "process/roadmap_and_scope.md", "依赖与范围计划已做，实际周度状态变更待真实追踪"),
    47: ("外部证据待补", "process/team_registration_and_evidence.md", "真实注册、项目编号、组长、教师/TA 确认未提供"),
    48: ("外部证据待补", "process/team_contributions_template.csv", "8 人工时、PR 和实际相对贡献不能代填"),
    49: ("已修复", "deliverables/CommitLens_Presentation_7min.html", "完成带计时提示的 7 分钟 HTML 演示幻灯片"),
    50: ("部分完成", "deliverables/CommitLens_Demo_Captioned.mp4", "完成真实工具结果支撑的配字幕视频；非真实屏录/真人讲解"),
    51: ("部分完成", "deliverables/CommitLens_TSE_Draft_Preview.pdf", "内容章节与预览稿齐；官方 IEEE 模板和真人内容待补"),
    52: ("部分完成", "report/sections/08_bios_contributions.tex", "Sprint 过程稿和 8 个姓名栏齐；真实 Bio/Reflection 未提供"),
}


def update_html(source: str) -> tuple[str, Counter]:
    counts: Counter = Counter()
    pattern = re.compile(r'<tr data-p="P[012]" data-k="[^"]+"><th scope="row">(\d+)</th>.*?</tr>')

    def replace_row(m: re.Match) -> str:
        number = int(m.group(1))
        status, path, note = STATUS[number]
        counts[status] += 1
        row = m.group(0)
        row = re.sub(r'<td class="status">.*?</td>',
                     f'<td class="status status-{["done", "partial", "pending"][["已修复", "部分完成", "外部证据待补"].index(status)]}">{status}</td>', row)
        evidence = '<td><a href="../' + html.escape(path, quote=True) + '">' + html.escape(path) + \
                   '</a><small class="qualification">' + html.escape(note) + '</small></td>'
        return row.replace('</tr>', evidence + '</tr>')

    result = pattern.sub(replace_row, source)
    if sum(counts.values()) != 52 or len(STATUS) != 52:
        raise RuntimeError(f"Expected all 52 issue rows; got {sum(counts.values())}")
    result = result.replace('项目问题清单（52项）', '项目整改复核（52项）')
    result = result.replace('CommitLens 项目全部问题清单', 'CommitLens 52 项整改 · 最终复核')
    result = result.replace(
        '依据课程 PDF、项目 RAR 与重新运行的源码测试整理。共 52 项，保留原始优先级、分类、问题描述、当前状态及修复要求。',
        '逐条对照原始问题表、运行结果与课程 PDF。状态是截至 2026-11-19 的证据判断；缺少真人记录的项目保持未完成。')
    result = result.replace(
        '<div class="stat"><strong>52</strong><span>问题及待验证事项</span></div>',
        f'<div class="stat"><strong>52</strong><span>全部原始问题</span></div>')
    result = result.replace(
        '<div class="stat red"><strong>15</strong><span>P0 · 优先处理</span></div>',
        f'<div class="stat"><strong>{counts["已修复"]}</strong><span>已修复 / 已完成文档性要求</span></div>')
    result = result.replace(
        '<div class="stat amber"><strong>32</strong><span>P1 · 提交前完成</span></div>',
        f'<div class="stat amber"><strong>{counts["部分完成"]}</strong><span>部分完成 · 仍有验收条件</span></div>')
    result = result.replace(
        '<div class="stat blue"><strong>5</strong><span>P2 · 建议完善</span></div>',
        f'<div class="stat red"><strong>{counts["外部证据待补"]}</strong><span>需真实外部资料</span></div>')
    result = result.replace('当前状态</th><th scope="col">应如何解决</th>',
                            '整改状态</th><th scope="col">原始整改要求</th><th scope="col">实现证据 / 剩余条件</th>')
    result = result.replace('原始当前状态没有保留', '原始当前状态没有保留')
    result = result.replace(
        '状态说明：“已复现”表示有具体复现场景；“未见 / RAR 未包含”表示当前提交材料没有对应证据，不代表其他位置一定没有。P0/P1/P2 是项目整改优先级，而非教师官方评分。',
        '验收语义：已修复＝当前源码、测试或明确局限说明满足该项可验证的整改要求；部分完成＝已有交付物但仍欠独立验证或特定形式；外部证据待补＝无法从所给压缩包推导真实团队、GitHub、会议、人类标签或用户结果。P0/P1/P2 保留原表，不代表官方评分。')
    result = result.replace('"当前状态", "应如何解决"',
                            '"整改状态", "原始整改要求", "实现证据 / 剩余条件"')
    result = result.replace(
        '<div class="footer">说明：此表为 2026 年 11 月 19 日的项目整改快照。可使用顶部筛选、打印和 CSV 导出功能；不应据此补造历史会议、人员工作或评估结果。</div>',
        '<div class="footer">本次状态通过 <code>python scripts/build_issue_tracker.py</code> 可再生成。表内链接相对于解压后的项目目录；配套 <code>docs/FINAL_PDF_COMPLIANCE_CHECK.md</code> 是课程 PDF 验收矩阵。不能把合成实验、计划模板、AI 编码输出冒充真人完成记录。</div>')
    result = result.replace('    .status{color:#43556a;white-space:nowrap}',
                            '    .status{color:#43556a;white-space:nowrap;font-weight:650}'
                            ' .status-done{color:#13764a}.status-partial{color:#93600e}'
                            ' .status-pending{color:#b83c43}'
                            ' .qualification{display:block;color:#607086;margin-top:4px;line-height:1.4}'
                            ' tbody a{color:#1a608e;overflow-wrap:anywhere}')
    result = result.replace('      th:nth-child(6){width:37%}', '      th:nth-child(6){width:25%}\n      th:nth-child(7){width:24%}')
    result = result.replace('table{width:100%;border-collapse:collapse;min-width:1060px}',
                            'table{width:100%;border-collapse:collapse;min-width:1320px}')
    return result, counts


def main():
    ap = argparse.ArgumentParser()
    packaged_source = ROOT / 'references' / 'CommitLens_Original_52_Issues.html'
    uploaded_source = ROOT.parent.parent / 'upload' / 'CommitLens_CS5351_52_Issues(1).html'
    ap.add_argument('--source', type=Path,
                    default=packaged_source if packaged_source.is_file() else uploaded_source)
    ap.add_argument('--out', type=Path, default=ROOT / 'deliverables' /
                    'CommitLens_52_Issues_Updated.html')
    args = ap.parse_args()
    content, counts = update_html(args.source.read_text(encoding='utf-8'))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding='utf-8')
    print(f"Wrote {args.out}; 52 items: {dict(counts)}")


if __name__ == '__main__':
    main()
