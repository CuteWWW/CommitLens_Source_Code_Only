"""Standard-library-only statement-line coverage approximation for unittest.

Counts executable Python lines using code-object line tables and sys.settrace.
Subprocess code is not instrumented; integration tests are still run by this
script, but lines executed only in child processes are undercounted.
"""

from __future__ import annotations

import argparse
import json
import sys
import trace
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "commitlens"


def runnable_lines(code: types.CodeType) -> set[int]:
    found = {number for _, _, number in code.co_lines() if number is not None}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            found |= runnable_lines(const)
    return found


def run_tests():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/coverage.json")
    ap.add_argument("--minimum", type=float, default=0)
    args = ap.parse_args()

    tracer = trace.Trace(count=True, trace=False)
    result = tracer.runfunc(run_tests)
    counted = tracer.results().counts
    hits: dict[Path, set[int]] = {}
    for (path, line), n in counted.items():
        file = Path(path).resolve()
        if n > 0 and (SRC == file.parent or SRC in file.parents):
            hits.setdefault(file, set()).add(line)

    files = []
    total_executable = total_hit = 0
    for path in sorted(SRC.rglob("*.py")):
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        executable = runnable_lines(code)
        covered = executable & hits.get(path.resolve(), set())
        total_executable += len(executable)
        total_hit += len(covered)
        files.append({"file": str(path.relative_to(ROOT)), "executable_lines": len(executable),
                      "covered_lines": len(covered),
                      "coverage": round(covered and len(covered) / len(executable) or 0.0, 4)
                      if executable else 1.0})

    ratio = total_hit / total_executable if total_executable else 1.0
    output = {"method": "trace + compiled executable-line table (main process only)",
              "tests_run": result.testsRun, "tests_passed": result.wasSuccessful(),
              "covered_lines": total_hit, "executable_lines": total_executable,
              "coverage": round(ratio, 4), "files": files}
    target = ROOT / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Tests: {result.testsRun}, passed={result.wasSuccessful()}; "
          f"main-process statement coverage {ratio:.1%} ({total_hit}/{total_executable})")
    print(f"JSON: {target}")
    if not result.wasSuccessful() or ratio < args.minimum:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
