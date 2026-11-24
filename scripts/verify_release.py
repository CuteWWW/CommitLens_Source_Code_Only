"""Final machine-verifiable checks across the repaired project artifacts.

External human/team/course milestones are explicitly marked as unverified;
the script never treats the presence of a template as human evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class OfflineReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.outgoing = []
        self.counts: Counter[str] = Counter()

    def handle_starttag(self, tag, attrs):
        self.counts[tag] += 1
        attributes = dict(attrs)
        if tag in ('script', 'iframe', 'link'):
            self.outgoing.append(f"externally capable tag {tag}")
        if tag in ('img', 'video', 'audio', 'source') and attributes.get('src', '').startswith(('http://', 'https://')):
            self.outgoing.append(f"remote media {tag}")
        if tag == 'style' and attributes.get('src'):
            self.outgoing.append('external stylesheet')
        for key in ('href', 'src'):
            value = attributes.get(key, '')
            if isinstance(value, str) and value.startswith(('http://', 'https://', '//')):
                self.outgoing.append(f"remote resource: {value[:100]}")


def verify(*, run_tests: bool) -> dict:
    checks = []

    def add(name, ok, detail, scope='machine'):
        checks.append({'name': name, 'passed': bool(ok), 'detail': detail, 'scope': scope})

    issue_html = (ROOT / 'deliverables/CommitLens_52_Issues_Updated.html').read_text(encoding='utf-8')
    numbers = [int(n) for n in re.findall(r'<tr data-p="P[012]" data-k="[^"]+"><th scope="row">(\d+)</th>', issue_html)]
    statuses = Counter(re.findall(r'<td class="status status-(done|partial|pending)">', issue_html))
    add('52 unique original issues', sorted(numbers) == list(range(1, 53)), str(numbers))
    add('52 updated evidence-based statuses', statuses == {'done': 23, 'partial': 18, 'pending': 11}, str(dict(statuses)))
    evidence_links = re.findall(r'<td><a href="\.\./([^\"]+)">', issue_html)
    missing = [value for value in evidence_links if not (ROOT / value).is_file()]
    add('all 52 evidence links exist', len(evidence_links) == 52 and not missing, f"links={len(evidence_links)}, missing={missing}")

    report = ROOT / 'deliverables/sample_report.html'
    html_parser = OfflineReportParser()
    html_parser.feed(report.read_text(encoding='utf-8'))
    add('sample HTML self-contained and static', not html_parser.outgoing,
        {'violations': html_parser.outgoing, 'svg_count': html_parser.counts['svg']})
    payload = json.loads((ROOT / 'deliverables/sample_report.json').read_text(encoding='utf-8'))
    results = payload['diagnosis']['checks']
    audit = payload.get('evidence_audit', {})
    add('eight checks in example diagnosis', len(results) == 8, f"check_count={len(results)}")
    add('example SHA witnesses structurally verified', audit.get('passed') and audit.get('trace_rate') == 1.0,
        audit)
    add('fixed source time and provenance in sample',
        '2026-11-19' in report.read_text(encoding='utf-8') and payload.get('provenance'),
        'fixed report and provenance structure present')

    coverage = json.loads((ROOT / 'artifacts/coverage.json').read_text(encoding='utf-8'))
    add('test suite and main-process statement coverage recorded',
        coverage['tests_passed'] and coverage['tests_run'] >= 50 and coverage['coverage'] >= .70,
        {'tests': coverage['tests_run'], 'main_process_coverage': coverage['coverage'],
         'coverage_scope': coverage['method']})
    if run_tests:
        proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        add('regression suite rerun', proc.returncode == 0, (proc.stdout + proc.stderr)[-1200:])

    performance = json.loads((ROOT / 'artifacts/benchmark_500k.json').read_text(encoding='utf-8'))
    add('500k synthetic Git history meets measured NFR budgets',
        performance.get('sample_count_target') == 500000 and all(performance['targets'].values()),
        performance['targets'])
    comeback = json.loads((ROOT / 'artifacts/benchmark_50k_recheck.json').read_text(encoding='utf-8'))
    add('new-author 50k recheck meets append target',
        comeback['targets']['incremental_with_new_commit_le_2s'],
        comeback['incremental_one_new_commit']['elapsed_seconds'])

    evaluation = json.loads((ROOT / 'artifacts/evaluation.json').read_text(encoding='utf-8'))
    baselines = evaluation['outliers']['baselines']
    add('honest synthetic comparison: strong fixed rule ties hybrid',
        evaluation['synthetic_only'] and baselines['fixed_lines_150']['precision'] ==
        baselines['hybrid_p95']['precision'] and baselines['fixed_lines_150']['recall'] ==
        baselines['hybrid_p95']['recall'], {'synthetic_only': evaluation['synthetic_only'],
                                          'fixed_150': baselines['fixed_lines_150'],
                                          'hybrid': baselines['hybrid_p95']})

    pdf = ROOT / 'deliverables/CommitLens_TSE_Draft_Preview.pdf'
    if pdf.is_file():
        proc = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True, timeout=10)
        match = re.search(r'^Pages:\s*(\d+)', proc.stdout, re.M)
        pages = int(match.group(1)) if match else 0
        add('report PDF opens and has substantive chapters', proc.returncode == 0 and pages >= 10,
            f"pages={pages}, format=2-column article preview, NOT IEEE TSE official class")
    else:
        add('report PDF exists', False, 'No preview PDF found')
    chapters = [ROOT / 'report/sections' / f'{number:02d}_{name}.tex' for number, name in
                enumerate(('introduction', 'related_work', 'preliminaries', 'solution',
                           'software_process', 'evaluation', 'conclusion', 'bios_contributions'), 1)]
    add('all eight report source sections exist', all(f.exists() for f in chapters),
        [f.name for f in chapters])
    add('presentation and demo video included',
        all((ROOT / f'deliverables/{name}').exists() for name in
            ('CommitLens_Presentation_7min.html', 'CommitLens_Demo_Captioned.mp4')),
        '12-slide HTML presentation and captioned programmatic MP4')
    video_proc = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                 '-of', 'default=noprint_wrappers=1:nokey=1',
                                 str(ROOT / 'deliverables/CommitLens_Demo_Captioned.mp4')],
                                capture_output=True, text=True, timeout=15)
    duration = float(video_proc.stdout.strip()) if video_proc.returncode == 0 else -1
    add('demo video actual duration matches storyboard', 194.5 <= duration <= 195.5,
        f"{duration:.2f} seconds (generated captions; not a live screencast)")

    process_files = ('SPRINT_EVIDENCE_GUIDE.md', 'weekly_meetings_template.csv',
                     'team_contributions_template.csv', 'risk_register.md',
                     'AI_USE_AND_HUMAN_REVIEW.md', 'work_items_template.csv')
    add('truthful process evidence collection templates included',
        all((ROOT / 'process' / path).is_file() for path in process_files), process_files)
    for missing_evidence in (
        'original team GitHub repository history and real pull requests',
        'three verified human sprints and weekly standing meetings',
        'actual burndown with dated issue completion data',
        'real participant identities, registration, hours and reflections',
        'independently human-labeled real anomalies and developer user study',
        'official IEEE Transactions on Software Engineering template and submission'):
        add(missing_evidence, False, 'Not present in original project archive; cannot be generated honestly',
            scope='external_pending')

    failures = [x for x in checks if x['scope'] == 'machine' and not x['passed']]
    return {'machine_passed': not failures, 'machine_check_count': sum(x['scope'] == 'machine' for x in checks),
            'machine_failure_count': len(failures),
            'external_pending_count': sum(x['scope'] == 'external_pending' for x in checks),
            'issue_status': dict(statuses), 'checks': checks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-tests', action='store_true')
    ap.add_argument('--out', type=Path, default=ROOT / 'artifacts/acceptance_check.json')
    args = ap.parse_args()
    result = verify(run_tests=args.run_tests)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"Machine checks: {result['machine_check_count'] - result['machine_failure_count']}/"
          f"{result['machine_check_count']} passed. Real-world evidence gates remain: "
          f"{result['external_pending_count']}. Results: {args.out}")
    for check in result['checks']:
        if check['scope'] == 'machine' and not check['passed']:
            print(f"FAIL: {check['name']}: {check['detail']}")
    sys.exit(0 if result['machine_passed'] else 1)


if __name__ == '__main__':
    main()
