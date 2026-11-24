"""输出层：把诊断结果渲染成一个自包含的 HTML 文件。

文件里没有外链、没有 JS、没有网络请求，双击就能看，可以直接发群里。
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from .. import __version__
from ..diagnose import Diagnosis
from ..model import SEVERITY_LABELS, CheckResult, Severity
from . import charts

SEV_COLOR = {
    "ok": "#16a34a",
    "info": "#2563eb",
    "warn": "#d97706",
    "alert": "#dc2626",
}

CSS = """
:root{--ok:#16a34a;--info:#2563eb;--warn:#d97706;--alert:#dc2626;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0}
*{box-sizing:border-box}
body{margin:0;background:#f6f7f9;color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  line-height:1.65;font-size:15px}
.wrap{max-width:1000px;margin:0 auto;padding:40px 24px 80px}
header{border-bottom:2px solid var(--ink);padding-bottom:20px;margin-bottom:24px}
.eyebrow{font-size:12px;letter-spacing:.16em;color:var(--muted);text-transform:uppercase}
h1{font-size:30px;margin:6px 0 4px;letter-spacing:-.01em}
.repo{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted);font-size:13px;word-break:break-all}
.chips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:#eef2ff;color:#3730a3;border-radius:999px;padding:3px 11px;font-size:12px;white-space:nowrap}
.verdict{display:flex;gap:32px;align-items:flex-start;background:#fff;border:1px solid var(--line);
  border-radius:12px;padding:26px;margin:26px 0}
.scorebox{text-align:center;min-width:130px;border-right:1px solid var(--line);padding-right:28px}
.scorebox .num{font-size:56px;font-weight:700;line-height:1}
.scorebox .grade{color:var(--muted);font-size:13px;margin-top:6px}
.headlines{flex:1;margin:0;padding-left:18px}
.headlines li{margin:6px 0}
.headlines .t{font-weight:600}
h2{font-size:19px;margin:38px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.card{background:#fff;border:1px solid var(--line);border-left:4px solid var(--muted);
  border-radius:8px;padding:14px 16px}
.card .ct{font-weight:600;font-size:14px;display:flex;justify-content:space-between;gap:8px}
.card .cs{color:var(--muted);font-size:13px;margin-top:6px}
.badge{font-size:11px;padding:1px 8px;border-radius:999px;color:#fff;white-space:nowrap;align-self:flex-start}
section.check{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin:18px 0}
section.check h2{margin-top:0;display:flex;align-items:center;gap:10px;border:none;padding:0}
.summary{font-size:15px;color:#1e293b;margin:4px 0 16px}
.chart-wrap{overflow-x:auto}
svg.chart{max-width:100%;height:auto}
svg .x{font-size:11px;fill:#64748b}
svg .y{font-size:11px;fill:#94a3b8}
svg .v{font-size:11px;fill:#334155}
svg .unit{font-size:11px;fill:#94a3b8}
table.data{width:100%;border-collapse:collapse;font-size:13.5px}
table.data th,table.data td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
table.data th{color:var(--muted);font-weight:600;font-size:12px}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.92em;background:#f1f5f9;
  padding:1px 5px;border-radius:4px;word-break:break-all}
.finding{border-top:1px dashed var(--line);padding-top:14px;margin-top:14px}
.finding h3{margin:0 0 6px;font-size:15.5px}
.finding p{margin:6px 0}
.advice{color:#065f46;background:#ecfdf5;border-left:3px solid #10b981;padding:8px 12px;border-radius:4px;font-size:14px}
details.ev{margin-top:10px}
details.ev summary{cursor:pointer;color:var(--info);font-size:13.5px;user-select:none}
.ev-body{margin:10px 0 0;padding:0;list-style:none}
.ev-body>li{border-left:2px solid var(--line);padding:6px 0 6px 12px;margin:8px 0}
.ev-body .el{font-weight:600;font-family:ui-monospace,Consolas,monospace;font-size:13px}
.ev-body .ed{color:var(--muted);font-size:13px;margin-left:6px}
.ev-body ul{margin:6px 0 0;padding-left:18px;color:#475569;font-size:12.5px}
.ev-body ul li{font-family:ui-monospace,Consolas,monospace;word-break:break-all}
.none{color:var(--ok);font-size:14px}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}
pre{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px;overflow-x:auto;font-size:12.5px;line-height:1.5}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;margin:18px 0 0}
nav.toc a{color:#334155;text-decoration:none;border-bottom:1px dashed #cbd5e1}
.banner{margin:18px 0 0;padding:12px 16px;border-radius:8px;background:#fffbeb;
  border:1px solid #fcd34d;color:#78350f;font-size:13.5px;line-height:1.6}
@media print{body{background:#fff}nav.toc{display:none}section.check{break-inside:avoid}}
"""


def render(ctx, diagnosis: Diagnosis, meta: dict[str, Any]) -> str:
    checks = diagnosis.results
    title = meta.get("title") or "Git Commit History Health Report"

    head = _header(ctx, meta, title)
    verdict = _verdict(diagnosis)
    nav = _nav(checks)
    cards = _cards(checks)
    deductions = _deductions(diagnosis)
    sections = "".join(_section(r) for r in checks)
    commit_index = _commit_index(ctx, diagnosis)
    footer = _footer(ctx, meta)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{head}
{nav}
{verdict}
<h2 id="scorecard">Health Check Results</h2>
<div class="cards">{cards}</div>
{deductions}
{sections}
{commit_index}
{footer}
</div>
</body>
</html>
"""


def _header(ctx, meta: dict[str, Any], title: str) -> str:
    rng = ctx.date_range
    date_range = f"{rng[0]} → {rng[1]}" if rng else "—"
    team = meta.get("team") or ""
    chips = [
        f"Commits: {ctx.commit_count:,}",
        f"Contributors: {len(ctx.authors)}",
        f"Date range: {date_range}",
        f"Window: {meta.get('window_days', 0)} days",
        f"Generated: {meta.get('generated_at', '')}",
        f"Config fingerprint: {meta.get('config_hash', '')}",
        f"v{__version__}",
    ]
    if team:
        chips.insert(0, f"Team: {team}")
    chip_html = "".join(f'<span class="chip">{escape(c)}</span>' for c in chips)

    min_commits = ctx.cfg("general", "min_commits")
    banner = ""
    if ctx.commit_count < min_commits:
        banner = (
            '<div class="banner">'
            f"<b>Insufficient sample size:</b> the selected window contains only "
            f"{ctx.commit_count} commits, below the {min_commits}-commit confidence threshold. "
            "Percentages and rankings may be noisy, so no A-E grade is assigned. Increase "
            "<code>window_days</code> or analyze a longer history before drawing conclusions."
            "</div>"
        )
    if not meta.get("show_authors", False):
        banner += (
            '<div class="banner">Privacy notice: contributors are shown as Dev numbers by '
            'default. Commit SHAs and local paths may still permit re-identification, so do '
            'not share reports that contain sensitive repository data.</div>'
        )

    return f"""<header>
  <div class="eyebrow">CommitLens · Commit History Health</div>
  <h1>{escape(title)}</h1>
  <div class="repo">{escape(ctx.repo)}</div>
  <div class="chips">{chip_html}</div>
</header>
{banner}"""


def _nav(checks: list[CheckResult]) -> str:
    items = "".join(
        f'<a href="#check-{escape(r.check)}">{r.order}. {escape(r.title)}</a>' for r in checks
    )
    return f'<nav class="toc">{items}</nav>'


def _verdict(d: Diagnosis) -> str:
    if d.deductions:
        items = "".join(
            f'<li><span class="t" style="color:{SEV_COLOR[x.severity.value]}">'
            f"{escape(SEVERITY_LABELS[x.severity.value])}</span> · {escape(x.title)}</li>"
            for x in sorted(d.deductions, key=lambda y: -y.points)
        )
    else:
        items = "<li>No material issues were found across the eight checks.</li>"

    color = SEV_COLOR.get(d.worst.value, "#0f172a")
    score_text = str(d.score) if d.scorable else "—"
    score_caption = (
        f"Overall score · Grade {escape(d.grade)}"
        if d.scorable else
        "Sample size is below the confidence threshold; no score is assigned"
    )
    return f"""<section class="verdict">
  <div class="scorebox">
    <div class="num" style="color:{color}">{score_text}</div>
    <div class="grade">{score_caption}</div>
  </div>
  <ul class="headlines">{items}</ul>
</section>"""


def _cards(checks: list[CheckResult]) -> str:
    out = []
    for r in checks:
        color = SEV_COLOR[r.severity.value]
        out.append(
            f'<div class="card" style="border-left-color:{color}">'
            f'<div class="ct"><span>{r.order}. {escape(r.title)}</span>'
            f'<span class="badge" style="background:{color}">{SEVERITY_LABELS[r.severity.value]}</span></div>'
            f'<div class="cs">{escape(r.summary)}</div></div>'
        )
    return "".join(out)


def _deductions(d: Diagnosis) -> str:
    if d.deductions:
        rows = "".join(
            f"<tr><td>{escape(x.title)}</td>"
            f'<td><span class="badge" style="background:{SEV_COLOR[x.severity.value]}">'
            f"{SEVERITY_LABELS[x.severity.value]}</span></td>"
            f"<td>-{x.points}</td></tr>"
            for x in sorted(d.deductions, key=lambda y: -y.points)
        )
        total = sum(x.points for x in d.deductions)
    else:
        rows = '<tr><td colspan="3">No deductions; all eight checks are within normal ranges.</td></tr>'
        total = 0

    return f"""<h2 id="deductions">Deduction Details</h2>
<table class="data"><thead><tr><th>Check</th><th>Level</th><th>Deduction</th></tr></thead>
<tbody>{rows}<tr><td><b>Total</b></td><td></td><td><b>-{total}</b></td></tr></tbody></table>
<p class="summary" style="font-size:13px;color:#64748b">
Scoring starts at 100 when the sample is sufficient. Each Alert deducts 8 points, each
Warning deducts 4 points, and Info items do not deduct points. Every deduction maps to a
finding shown above.</p>"""


def _section(r: CheckResult) -> str:
    color = SEV_COLOR[r.severity.value]
    body = charts.render(r.chart)

    if r.findings:
        findings = "".join(_finding(f) for f in r.findings)
    else:
        findings = '<p class="none">No issues requiring action were found for this check.</p>'

    return f"""<section class="check" id="check-{escape(r.check)}">
  <h2>{r.order}. {escape(r.title)}
    <span class="badge" style="background:{color}">{SEVERITY_LABELS[r.severity.value]}</span></h2>
  <p class="summary">{escape(r.summary)}</p>
  <div class="chart-wrap">{body}</div>
  {findings}
</section>"""


def _finding(f) -> str:
    color = SEV_COLOR[f.severity.value]
    parts = [
        f'<article class="finding">',
        f'<h3><span style="color:{color}">●</span> {escape(f.title)}</h3>',
    ]
    if f.detail:
        parts.append(f"<p>{escape(f.detail)}</p>")
    if f.advice:
        parts.append(f'<p class="advice"><b>Recommendation:</b> {escape(f.advice)}</p>')
    if f.evidence:
        parts.append(_evidence(f.evidence))
    parts.append("</article>")
    return "".join(parts)


def _evidence(items) -> str:
    rows = []
    for e in items:
        lines = ""
        if e.lines:
            lines = "<ul>" + "".join(f"<li>{escape(x)}</li>" for x in e.lines) + "</ul>"
        sha = (f' · <a href="#commit-{escape(e.commit)}"'
               f' title="View source commit">SHA {escape(e.commit[:12])}</a>') if e.commit else ""
        rows.append(
            f'<li><span class="el">{escape(e.label)}</span>'
            f'<span class="ed">{escape(e.detail)}{sha}</span>{lines}</li>'
        )
    label = f"View evidence ({len(items)})"
    return (
        f'<details class="ev"><summary>{label}</summary>'
        f'<ul class="ev-body">{"".join(rows)}</ul></details>'
    )


def _commit_index(ctx, diagnosis: Diagnosis) -> str:
    """Self-contained, offline drilldown into commit metadata and changed paths."""
    referenced = {e.commit for result in diagnosis.results for finding in result.findings
                  for e in finding.evidence if e.commit}
    if not referenced:
        return ""
    samples = {c.sha: c for c in ctx.commits if c.sha in referenced}
    rows = []
    for sha in sorted(samples):
        c = samples[sha]
        changes = ", ".join(c.paths[:10])
        if len(c.paths) > 10:
            changes += f" ... and {len(c.paths) - 10} more files"
        rows.append(
            f'<tr id="commit-{escape(sha)}"><td><code>{escape(sha)}</code></td>'
            f'<td>{escape(c.date.isoformat())}</td><td>{escape(c.author)}</td>'
            f'<td>{escape(c.subject)}</td><td>{escape(changes)}</td></tr>'
        )
    return ('<h2 id="commit-index">Source Commit Evidence Index</h2>'
            '<p class="summary">Select a SHA in a finding to jump to this index. '
            'Run <code>git show SHA</code> in the local repository for the complete diff.</p>'
            '<div class="chart-wrap"><table class="data"><thead><tr>'
            '<th>Full SHA</th><th>Date</th><th>Contributor</th><th>Subject</th><th>Changed files</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')


def _footer(ctx, meta: dict[str, Any]) -> str:
    cmd = meta.get("command", "")
    git_cmd = " ".join(meta.get("git_command", [])) or (
        "A saved log file was used; Git was not executed."
    )
    source = meta.get("source", "")
    config_toml = meta.get("config_toml", "")
    effective_config = meta.get("effective_config", "")
    data_sha = meta.get("data_sha256", "")
    git_version = meta.get("git_version", "Unknown")
    repo_revision = meta.get("repo_revision", "Unknown")
    config_source = meta.get("config_source", "Built-in defaults")
    identity_warnings = meta.get("identity_warnings", "None")
    return f"""<footer>
  <p><b>Reproduction command</b></p>
  <pre>{escape(cmd)}</pre>
  <p>Data source: {escape(source)}</p>
  <p>Git version: {escape(git_version)} · Repository HEAD: <code>{escape(repo_revision)}</code>
    · Source data SHA-256: <code>{escape(data_sha)}</code></p>
  <p>Configuration source: {escape(config_source)}</p>
  <p>Identity check: {escape(identity_warnings)}</p>
  <details><summary style="cursor:pointer">View collection command</summary><pre>{escape(git_cmd)}</pre></details>
  <details><summary style="cursor:pointer">View effective configuration</summary><pre>{escape(effective_config)}</pre></details>
  <details><summary style="cursor:pointer">View source configuration</summary><pre>{escape(config_toml)}</pre></details>
  <p>Interpretation limits: issue references are matched as text and are not verified against
     the issue tracker. File-change counts and contribution concentration do not measure actual
     code knowledge or contributor ability. Oversized commits are a scale signal and do not
     prove that multiple concerns were mixed. By default, only non-merge commits reachable from
     HEAD are analyzed. Use this report for process improvement, not individual performance
     evaluation.</p>
  <p>Generated by CommitLens v{__version__} at {escape(meta.get("generated_at", ""))} ·
     Config fingerprint {escape(meta.get("config_hash", ""))}</p>
</footer>"""
