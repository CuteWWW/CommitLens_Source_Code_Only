"""Render an honest, captioned end-to-end CommitLens demo video from real outputs.

All figures are read from generated artifacts. Frames are programmatic
visualizations, not pretended recordings of a human operating a browser.
Requires Pillow and ffmpeg only for producing the optional MP4 deliverable;
CommitLens itself continues to use Python's standard library exclusively.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 1280, 720
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
NAVY = (6, 23, 42)
PAPER = (17, 45, 69)
WHITE = (240, 250, 255)
MUTED = (166, 197, 221)
MINT = (111, 229, 184)
GOLD = (255, 208, 117)


def f(size: int, mono: bool = False):
    return ImageFont.truetype(MONO if mono else FONT, size)


def wrap(draw, text: str, font, width: int) -> list[str]:
    lines = []
    for chunk in text.split("\n"):
        if not chunk.strip():
            lines.append("")
            continue
        current = ""
        for word in chunk.split():
            candidate = (current + " " + word).strip()
            if draw.textlength(candidate, font=font) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def text_block(draw, value, x, y, font, color=MUTED, width=1040, step=None):
    line_height = step or int(font.size * 1.55)
    for line in wrap(draw, value, font, width):
        draw.text((x, y), line, font=font, fill=color)
        y += line_height
    return y


def canvas(index, total, title, kicker):
    im = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((40, 36, WIDTH - 40, HEIGHT - 45), 25, fill=PAPER,
                           outline=(43, 85, 110), width=2)
    draw.rounded_rectangle((64, 66, 267, 104), 13, fill=(21, 84, 85))
    draw.text((81, 73), "COMMITLENS DEMO", fill=MINT, font=f(17))
    draw.text((78, 122), kicker.upper(), fill=GOLD, font=f(18))
    text_block(draw, title, 77, 161, f(40), WHITE, 1080, 52)
    draw.line((78, 242, 1195, 242), fill=(56, 98, 120), width=2)
    draw.text((82, 639), f"{index+1:02d} / {total:02d}", fill=MINT, font=f(18))
    draw.text((925, 639), "Synthetic data; real tool output", fill=MUTED, font=f(15))
    return im, draw


def card(draw, x, y, w, h, title, body, accent=MINT):
    draw.rounded_rectangle((x, y, x + w, y + h), 18, fill=(9, 31, 50),
                           outline=(51, 91, 111), width=2)
    draw.text((x + 24, y + 22), title, fill=accent, font=f(25))
    text_block(draw, body, x + 24, y + 70, f(18), MUTED, w - 48, 28)


def draw_scene(scene, index, total):
    im, draw = canvas(index, total, scene["title"], scene["kicker"])
    style = scene["style"]
    if style == "intro":
        text_block(draw, scene["body"], 85, 290, f(30), WHITE, 1030, 48)
        card(draw, 82, 462, 520, 140, "LOCAL + EVIDENCE-FIRST",
             "One command, eight diagnostic checks, traceable commit SHAs.")
        card(draw, 626, 462, 530, 140, "NOT A HUMAN TEAM RECORD",
             "Demo Git history is generated; sprint participation is unverified.", GOLD)
    elif style == "code":
        draw.rounded_rectangle((80, 269, 1152, 588), 17, fill=(2, 15, 27))
        for n, line in enumerate(scene["body"].split("\n")):
            draw.text((112, 303 + 42 * n), line, fill=(195, 224, 243), font=f(22, True))
    elif style == "two":
        card(draw, 82, 274, 519, 310, *scene["left"])
        card(draw, 620, 274, 533, 310, *scene["right"], accent=GOLD)
    elif style == "stats":
        values = scene["values"]
        x = 83
        for val, lab in values:
            draw.rounded_rectangle((x, 291, x + 335, 533), 17, fill=(6, 29, 47),
                                   outline=(35, 92, 110), width=2)
            draw.text((x + 26, 322), str(val), fill=MINT, font=f(52))
            text_block(draw, str(lab), x + 27, 410, f(19), MUTED, 290, 28)
            x += 363
        text_block(draw, scene.get("foot", ""), 85, 560, f(17), GOLD, 1050)
    elif style == "list":
        y = 282
        for heading, detail in scene["items"]:
            draw.ellipse((86, y + 9, 101, y + 24), fill=MINT)
            draw.text((121, y), heading, fill=WHITE, font=f(24))
            y = text_block(draw, detail, 123, y + 39, f(18), MUTED, 1015, 27) + 19
    return im


def build_scenes():
    payload = json.loads((ROOT / "deliverables" / "sample_report.json").read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "artifacts" / "benchmark_500k.json").read_text(encoding="utf-8"))
    coverage = json.loads((ROOT / "artifacts" / "coverage.json").read_text(encoding="utf-8"))
    checks = payload["diagnosis"]["checks"]
    trend = next(x for x in checks if x["check"] == "trend")
    messages = next(x for x in checks if x["check"] == "message")
    sample_sha = messages["findings"][0]["evidence"][0]["commit"]
    count = payload["summary"]["commits"]
    return [
        {"title": "CommitLens: Git -> evidence -> action", "kicker": "1 / Product overview", "style": "intro", "duration": 17,
         "body": "An offline software-engineering diagnostic tool for developers.\nNot a productivity grade and not a predictor of code quality."},
        {"title": "Step 1: create a reproducible input", "kicker": "2 / Synthetic data generation", "style": "code", "duration": 22,
         "body": "python scripts/make_demo_repo.py \\\n  --log artifacts/demo_synthetic.log \\\n  --weeks 24 --decline-at 14 --seed 7\n\n# Output: deterministic saved synthetic Git log"},
        {"title": "Step 2: run the complete scan", "kicker": "3 / Actual CLI invocation", "style": "code", "duration": 25,
         "body": "python -m commitlens scan \\\n  --log artifacts/demo_synthetic.log \\\n  --as-of 2026-11-19 \\\n  --out deliverables/sample_report.html \\\n  --json deliverables/sample_report.json\n\n# Python standard library; no network service"},
        {"title": "Step 3: examine the generated diagnosis", "kicker": "4 / Output numbers", "style": "stats", "duration": 27,
         "values": [(count, "commits read from the demo log"),
                    (len(checks), "independent check results"),
                    (payload["diagnosis"]["grade"], "illustrative synthetic-history grade")],
         "foot": f"Message-rule offenders: {messages['stats']['offenders']} / {count}; trend: {trend['severity']}"},
        {"title": "Step 4: drill down to exact SHA", "kicker": "5 / Evidence trail", "style": "two", "duration": 25,
         "left": ("Evidence record", f"Example commit: {sample_sha[:12]}\nFull SHA: {sample_sha}\nRule violations and original paths appear in report HTML."),
         "right": ("Reproduction metadata", "Report includes full input-data SHA-256, effective config JSON, Git scope and exact scan command. Sample input is a saved synthetic log.")},
        {"title": "Step 5: catch stopped development", "kicker": "6 / Correctness regression", "style": "two", "duration": 23,
         "left": ("Old error", "When weeks after the final commit were dropped, a stalled repository could falsely look stable."),
         "right": ("New behavior", f"Tail zero weeks are included through the as-of date. Demo trend: {trend['summary'][:150]}")},
        {"title": "Step 6: append to a large Git history", "kicker": "7 / Performance evidence", "style": "stats", "duration": 28,
         "values": [(f"{bench['full']['elapsed_seconds']}s", "full 500k Git-history scan"),
                    (f"{bench['incremental_unchanged']['elapsed_seconds']}s", "unchanged report reused"),
                    (f"{bench['incremental_one_new_commit']['elapsed_seconds']}s", "one new commit; exact rollup update")],
         "foot": "Generated by real local git-fast-import, NOT an industrial repository."},
        {"title": "Step 7: tests, process and honest limits", "kicker": "8 / Engineering handoff", "style": "list", "duration": 28,
         "items": [(f"{coverage['tests_run']} automated tests + coverage report",
                    f"All tests green in recorded run. Main-process statement-line coverage {coverage['coverage']:.1%}."),
                   ("Three-sprint evidence collection workflow",
                    "Issues -> PR -> tests -> review -> closure; weekly minutes and actual burndown require genuine student records."),
                   ("Real-world validation pending",
                    "No fabricated user study, independent labels, human reviews or Canvas registration. Synthetic baseline ties a tuned fixed rule.") ]},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "deliverables" / "CommitLens_Demo_Captioned.mp4")
    args = ap.parse_args()
    scenes = build_scenes()
    with tempfile.TemporaryDirectory(prefix="commitlens-video-") as tmpdir:
        directory = Path(tmpdir)
        lines = []
        for index, scene in enumerate(scenes):
            path = directory / f"frame_{index:02d}.png"
            draw_scene(scene, index, len(scenes)).save(path, optimize=True)
            lines.extend([f"file '{path}'", f"duration {scene['duration']}"])
        # concat demuxer applies the final duration only with a duplicate final frame.
        lines.append(f"file '{directory / f'frame_{len(scenes)-1:02d}.png'}'")
        manifest = directory / "frames.ffconcat"
        manifest.write_text("ffconcat version 1.0\n" + "\n".join(lines) + "\n", encoding="utf-8")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-safe", "0",
               "-f", "concat", "-i", str(manifest), "-vf", "fps=12,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
               "-t", str(sum(scene["duration"] for scene in scenes)),
               "-movflags", "+faststart", str(args.out)]
        subprocess.run(cmd, check=True)
    seconds = sum(s["duration"] for s in scenes)
    print(f"Video created: {args.out}, {seconds} seconds, {len(scenes)} captioned scenes, no voiceover")


if __name__ == "__main__":
    main()
