# CommitLens

**English** | [简体中文](README.zh-CN.md)

CommitLens turns Git commit history into an evidence-backed team development health
report. It provides findings, source evidence, and actionable recommendations instead
of raw statistics alone.

```bash
python -m commitlens scan .
```

The command generates a self-contained `report.html`. The report interface, analysis,
findings, evidence labels, and recommendations are written in English. Repository data
such as commit subjects, author names, and file paths is preserved exactly as stored in
Git.

## Features

- **No runtime dependencies:** CommitLens uses only the Python 3.11+ standard library.
- **Offline by default:** Generated reports make no network requests.
- **Reproducible:** The footer records the collection command, effective configuration,
  source hash, configuration fingerprint, and repository revision.
- **Traceable evidence:** Findings link back to source commits, and the generated output
  is checked for evidence completeness.
- **Incremental analysis:** `--incremental` reuses validated local state when history
  advances safely and rebuilds when rewrites or configuration changes are detected.
- **Privacy-conscious defaults:** Contributors are labeled `Dev-01`, `Dev-02`, and so on
  unless `--show-authors` is explicitly enabled.

## Quick Start

Run CommitLens from the project root:

```bash
# Analyze the current repository
python -m commitlens scan .

# Analyze another repository and limit the window to 90 days
python -m commitlens scan ../team-repo --since 90 --out team-report.html

# Generate a demo from synthetic Git history
python scripts/make_demo_repo.py --log demo.log
python -m commitlens scan --log demo.log --out demo.html
```

To use the `commitlens` command outside the source directory:

```bash
python -m pip install -e .
commitlens scan /path/to/repository
```

## Commands

```bash
python -m commitlens checks
python -m commitlens init
python -m unittest discover -s tests -q
python scripts/coverage_report.py
python scripts/run_evaluation.py
```

Useful scan options:

```bash
# Write both HTML and machine-readable JSON
python -m commitlens scan ../team-repo --out report.html --json report.json

# Reuse validated local state for a safe incremental update
python -m commitlens scan ../team-repo --since 0 --incremental

# Analyze a fixed historical snapshot
python -m commitlens scan ../team-repo --as-of 2026-11-19 --since 180

# Include all local branches and merge commits explicitly
python -m commitlens scan ../team-repo --all-branches --include-merges

# Fail CI when a Warning or Alert is found
python -m commitlens scan ../team-repo --fail-on warn
```

Tests require Python and a local Git executable. On Windows, the test harness explicitly
decodes subprocess output as UTF-8 and does not depend on the system GBK/CP936 setting.

## Checks

| # | Check | Question | Main signals |
|---|---|---|---|
| 1 | Commit Message Quality | Do commit messages explain changes? | Subject length, type prefixes, vague wording, issue references, explanation bodies |
| 2 | Change Size | Are individual commits too large? | Median and P90 files/lines, commits over threshold |
| 3 | Oversized Commits | Are there unusually large commits? | Threshold breaches and share of changed lines |
| 4 | Commit Cadence | Is anyone committing unusually often? | Per-contributor rate versus the leave-one-out peer median |
| 5 | Dormant Contributors | Has anyone been inactive? | Days since the last commit and prior activity |
| 6 | Hotspot Files | Which files change most often? | Change count, changed lines, contributor count |
| 7 | Knowledge Concentration | Is change activity concentrated in one contributor? | Single-contributor file share and a historical activity proxy |
| 8 | Activity Trend | Is project activity declining? | Recent-versus-previous periods and Mann-Kendall trend test |

Severity levels are `OK`, `Info`, `Warning`, and `Alert`.

When the sample is large enough, scoring starts at 100. Each `Alert` deducts 8 points,
each `Warning` deducts 4 points, and `Info` items do not affect the score. Every deduction
is listed in the report's **Deduction Details** section.

Reports explicitly distinguish scale indicators from semantic conclusions. For example,
an oversized commit does not prove that unrelated concerns were mixed, and file-change
concentration does not prove who understands the code.

## Repository Layout

```text
commitlens/
├── __main__.py           python -m commitlens entry point
├── cli.py                scan / checks / init commands
├── config.py             commitlens.toml loading via tomllib
├── model.py              Commit / Finding / Evidence / CheckResult
├── ingest.py             Git log collection and parsing
├── identity.py           mailmap, aliases, duplicate names, pseudonyms
├── cache.py              Cache validation and incremental input
├── rollup.py             Materialized metrics and incremental merging
├── audit.py              Finding-to-source evidence validation
├── stats.py              Mann-Kendall and linear trend helpers
├── diagnose.py           Results -> score and headline findings
├── metrics/              One module per metric area
└── report/               Inline SVG charts and HTML rendering

scripts/                  Demo, evaluation, coverage, benchmark, and reporting tools
tests/                    50 unit and local-Git integration tests
.github/                  Issue templates and CI workflow
```

This source-only distribution does not include the research paper, presentation,
artifacts, process templates, or external evidence that are referenced by the original
course project.

## Data Flow

```text
git log
  -> ingest.parse_log_text
  -> path/author/time filters
  -> metrics.run_all
  -> diagnose
  -> HTML + JSON
```

Findings and evidence are separate data structures. HTML and JSON are different
renderings of the same findings, so new checks can be added without rewriting the
front end.

## Add a Ninth Check

1. Create `commitlens/metrics/example.py`.
2. Implement `run(ctx) -> list[CheckResult]`.
3. Add the module to `MODULES` in `commitlens/metrics/__init__.py`.
4. Register its display title in `CHECK_TITLES`.

```python
from ..model import CheckResult, Severity
from .base import Context


def run(ctx: Context) -> list[CheckResult]:
    n = len(ctx.commits)
    return [
        CheckResult(
            check="example",
            title="Example Check",
            severity=Severity.INFO,
            summary=f"{n} commits analyzed",
            order=9,
            stats={"n": n},
            chart={
                "type": "bar",
                "labels": ["Commits"],
                "values": [n],
                "unit": "commits",
            },
            findings=[],
        )
    ]
```

Supported chart types are `bar`, `line`, and `table`.

## Configuration

Run `python -m commitlens init` to generate `commitlens.toml`.

```toml
[general]
window_days = 180
ignore_paths = ["node_modules/", "dist/", "*.lock"]

[message]
require_type_prefix = false
require_issue_ref = false

[size]
max_files = 30
max_lines = 800

[cadence]
freq_multiplier = 3.0
dormant_days = 21

[ownership]
single_owner_share = 0.8

[trend]
recent_weeks = 4
alpha = 0.05
```

The report records the effective merged configuration and its fingerprint. Git
`.mailmap` data and `[identity].aliases` can merge identities. Author names remain
pseudonymous unless `--show-authors` is set.

Do not publish `.commitlens-cache` or reports containing sensitive repository data.
The cache may contain raw commit metadata even when report authors are pseudonymized.

## Evaluation

```bash
# Synthetic activity decline beginning in week 14
python scripts/make_demo_repo.py --log decline.log --weeks 24 --decline-at 14
python -m commitlens scan --log decline.log --out decline.html

# Stable synthetic history
python scripts/make_demo_repo.py --log flat.log --weeks 24 --decline-at 99
python -m commitlens scan --log flat.log --out flat.html
```

For a real repository, export JSON, independently review a sample of findings, measure
agreement, and record the review process with the result.

## Known Limitations

- Issues and pull requests are not read; issue references are text matches only.
- Code semantics are not analyzed; changes are evaluated at the file level.
- By default, only non-merge commits reachable from `HEAD` are counted.
- Git identities can be normalized, but aliases cannot prove that two identities are the
  same real person.
- Cache reuse is designed for safe fast-forward history. Rewrites and scope changes
  trigger a full rebuild.
- Performance results come from synthetic local Git histories and are not a guarantee
  for every production repository.
- The project does not include human-labeled anomaly data, participant studies, or
  verified team Sprint evidence.

## Development

- Run `python -m unittest discover -s tests` before committing.
- Keep pull requests focused; the project guideline is at most 400 changed lines.
- Include a test for every new metric.
- Document behavior changes and configuration changes with the code change.
