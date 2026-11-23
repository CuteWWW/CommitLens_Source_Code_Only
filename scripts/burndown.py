"""Create a dated burndown SVG from actual issue-tracker records, never guesses."""

from __future__ import annotations

import argparse
import csv
from html import escape
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    result = []
    for row in rows:
        if not row.get("issue_id", "").strip():
            continue
        if not row.get("github_issue_url", "").strip():
            raise ValueError(f"Missing verifiable issue URL: {row['issue_id']}")
        points = float(row["story_points"])
        planned = int(row["planned_week"])
        if not 2 <= planned <= 13 or points <= 0:
            raise ValueError(f"Invalid story points or planned week: {row['issue_id']}")
        closed = int(row["closed_week"]) if row.get("closed_week", "").strip() else None
        if closed is not None and not planned <= closed <= 13:
            raise ValueError(f"Invalid closure week: {row['issue_id']}")
        result.append({"id": row["issue_id"], "points": points,
                       "planned": planned, "closed": closed})
    return result


def make_svg(rows: list[dict]) -> str:
    width, height = 900, 420
    if not rows:
        return '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 420">
<rect width="900" height="420" fill="#f8fafc"/>
<text x="50" y="84" font-family="Arial" font-size="30" fill="#172554">CommitLens Burndown</text>
<text x="50" y="150" font-family="Arial" font-size="24" fill="#b45309">NO VERIFIED WORK ITEMS</text>
<text x="50" y="200" font-family="Arial" font-size="18" fill="#334155">Populate process/work_items_template.csv with real issue and PR records.</text>
<text x="50" y="230" font-family="Arial" font-size="18" fill="#334155">An empty chart is not evidence of three completed sprints.</text>
</svg>'''
    xs = [75 + i * 67 for i in range(12)]
    y_remaining = []
    for week in range(2, 14):
        planned = sum(row["points"] for row in rows if row["planned"] <= week)
        closed = sum(row["points"] for row in rows if row["closed"] is not None
                     and row["closed"] <= week)
        y_remaining.append(planned - closed)
    maximum = max(sum(r["points"] for r in rows), 1)
    ys = [350 - 260 * y / maximum for y in y_remaining]
    ideal = [350 - 260 * (1 - i / 11) for i in range(12)]
    # Actual progress is labeled with the chosen scope accounting convention.
    lines = [f'<polyline points="{" ".join(f"{x},{y:.1f}" for x, y in zip(xs, ys))}" '
             'fill="none" stroke="#0f766e" stroke-width="4"/>',
             f'<polyline points="{" ".join(f"{x},{y:.1f}" for x, y in zip(xs, ideal))}" '
             'fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="8 6"/>']
    for week, x, y, remain in zip(range(2, 14), xs, ys, y_remaining):
        lines.append(f'<circle cx="{x}" cy="{y:.1f}" r="4" fill="#0f766e">'
                     f'<title>Week {week}: {remain:g} story points remaining</title></circle>')
        lines.append(f'<text x="{x}" y="385" font-size="12" text-anchor="middle">W{week}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
<rect width="{width}" height="{height}" fill="#f8fafc"/>
<text x="50" y="38" font-family="Arial" font-size="25" fill="#172554">Actual Issue-Based Burndown (W2-W13)</text>
<text x="50" y="62" font-size="13" fill="#64748b">Scope added in a later week enters that week's backlog; closures require recorded issue evidence.</text>
<line x1="75" x2="812" y1="350" y2="350" stroke="#94a3b8"/>
<line x1="75" x2="75" y1="88" y2="350" stroke="#94a3b8"/>
<g font-family="Arial" fill="#475569">{''.join(lines)}</g>
<text x="75" y="414" font-size="12" fill="#0f766e">Solid: actual remaining points</text>
<text x="470" y="414" font-size="12" fill="#64748b">Dashed: illustrative ideal trajectory</text>
</svg>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=Path("process/work_items_template.csv"))
    ap.add_argument("--out", type=Path, default=Path("artifacts/burndown.svg"))
    args = ap.parse_args()
    rows = load_rows(args.input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(make_svg(rows), encoding="utf-8")
    print(f"Burndown: {args.out}; verified issue rows: {len(rows)}")


if __name__ == "__main__":
    main()
