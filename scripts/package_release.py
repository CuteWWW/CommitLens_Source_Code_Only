"""Create and verify a self-contained, non-destructive repaired project ZIP.

Preserves all non-generated project sources and the original HTML/PDF/RAR
references. Excludes local caches, TeX helper artifacts, and generated
500k-commit benchmark repositories, which are reproducible from a script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = 'CommitLens_CS5351_Repaired_v0.2.0'
EXCLUDE_SUFFIXES = {'.pyc', '.pyo', '.fmt', '.aux', '.out', '.toc', '.synctex.gz'}
EXCLUDE_DIRS = {'__pycache__', '.git', '.commitlens-cache', '.venv', '.pytest_cache',
                '.mypy_cache', '.ruff_cache', 'build', 'dist'}
EXCLUDE_ARTIFACTS = {'report/pdflatex.log', 'report/qa_first.png', 'report/qa_last.png',
                     'report/qa_last_final.png', 'report/texsys.aux',
                     'deliverables/missfont.log'}


def add_file(archive: zipfile.ZipFile, manifest: list[dict], file: Path, arcname: str):
    archive.write(file, arcname)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest.append({'path': arcname, 'size_bytes': file.stat().st_size, 'sha256': digest})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path,
                    default=ROOT.parent.parent / 'CommitLens_CS5351_Repaired_Full_Package.zip')
    ap.add_argument('--upload-dir', type=Path, default=ROOT.parent.parent / 'upload')
    args = ap.parse_args()
    output = args.out.resolve()
    if ROOT == output.parent or ROOT in output.parents:
        raise ValueError('Do not package recursively into a project subdirectory')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=7, allowZip64=True) as archive:
        for file in sorted(ROOT.rglob('*')):
            if not file.is_file():
                continue
            relative = file.relative_to(ROOT)
            if any(part in EXCLUDE_DIRS for part in relative.parts):
                continue
            if file.suffix in EXCLUDE_SUFFIXES:
                continue
            if relative.as_posix() in EXCLUDE_ARTIFACTS:
                continue
            if relative.parent == Path('.') and relative.name in (
                'demo.html', 'demo.log', 'report.html', 'result.json'):
                continue  # older generated reports, replaced by deliverables/sample_report
            add_file(archive, manifest, file,
                     f'{ARCHIVE_ROOT}/{relative.as_posix()}')
        references = [
            (args.upload_dir / 'CommitLens_CS5351_52_Issues(1).html',
             'references/CommitLens_Original_52_Issues.html'),
            (args.upload_dir / 'project_5351(1).rar',
             'references/Original_Project_5351.rar'),
        ]
        existing_paths = {item['path'] for item in manifest}
        for source, name in references:
            if source.is_file() and f'{ARCHIVE_ROOT}/{name}' not in existing_paths:
                add_file(archive, manifest, source, f'{ARCHIVE_ROOT}/{name}')
            elif not (ROOT / name).is_file():
                raise FileNotFoundError(f'Original archive/reference missing: {source}')
        record = {'name': ARCHIVE_ROOT, 'version': '0.2.0',
                  'issue_counts': {'repaired': 23, 'partial': 18, 'external_evidence': 11},
                  'measurements': {'tests': 49, 'main_process_statement_coverage': .745,
                                   'synthetic_500k_full_s': 20.379,
                                   'synthetic_500k_new_s': 1.243},
                  'warning': 'Human sprints, team identity, real datasets and official IEEE formatting unverified',
                  'files': manifest}
        archive.writestr(f'{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json',
                         json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    with zipfile.ZipFile(output, 'r') as check:
        corrupted = check.testzip()
        if corrupted:
            raise RuntimeError(f'Archive CRC failure in {corrupted}')
        names = set(check.namelist())
        for required in ('commitlens/cli.py', 'deliverables/CommitLens_TSE_Draft_Preview.pdf',
                         'deliverables/CommitLens_52_Issues_Updated.html',
                         'deliverables/CommitLens_Demo_Captioned.mp4',
                         'references/CommitLens_Original_52_Issues.html',
                         'report/main.tex', 'docs/FINAL_PDF_COMPLIANCE_CHECK.md',
                         'scripts/verify_release.py', 'PACKAGE_MANIFEST.json'):
            if f'{ARCHIVE_ROOT}/{required}' not in names:
                raise RuntimeError(f'Required release artifact missing: {required}')
    print(f'ZIP verified: {output} ({output.stat().st_size:,} bytes, '
          f'{len(manifest)} files + manifest, ZIP CRC clean)')


if __name__ == '__main__':
    main()
