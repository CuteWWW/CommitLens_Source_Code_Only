"""Machine-checkable audit of the 'finding -> evidence -> source' chain."""

from __future__ import annotations

from .diagnose import Diagnosis
from .model import Commit


def audit_evidence(diagnosis: Diagnosis, commits: list[Commit]) -> dict:
    shas = {c.sha for c in commits}
    failures = []
    total = 0
    supported = 0
    for check in diagnosis.results:
        for idx, finding in enumerate(check.findings):
            total += 1
            has_witness = False
            for ev in finding.evidence:
                if ev.commit and ev.commit in shas:
                    has_witness = True
                elif ev.commit:
                    failures.append(f"{check.check}[{idx}]: unknown commit {ev.commit}")
                elif ev.lines and any("零值证据" in line for line in ev.lines):
                    has_witness = True
            if has_witness:
                supported += 1
            else:
                failures.append(f"{check.check}[{idx}]: no valid commit witness")
    return {"findings": total, "traced": supported, "trace_rate": supported / total if total else 1.0,
            "failures": failures, "passed": not failures}
