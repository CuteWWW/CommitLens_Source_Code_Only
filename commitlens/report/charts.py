"""把图表描述渲染成内联 SVG。

为什么不用 ECharts：NFR-3 要求默认离线、零外传。
内联 SVG 没有任何外部依赖，一个 HTML 文件拷到哪都能打开，
也省掉前端组一半的工作量。
"""

from __future__ import annotations

import math
from html import escape
from typing import Any

WIDTH = 720


def render(chart: dict[str, Any] | None) -> str:
    if not chart:
        return ""
    kind = chart.get("type")
    if kind == "bar":
        return bar_chart(chart)
    if kind == "line":
        return line_chart(chart)
    if kind == "table":
        return table(chart)
    return ""


def bar_chart(chart: dict[str, Any], width: int = WIDTH, height: int = 240) -> str:
    labels = [str(x) for x in chart.get("labels", [])]
    values = [float(v) for v in chart.get("values", [])]
    unit = chart.get("unit", "")
    if not labels:
        return ""

    pad_l, pad_r, pad_t, pad_b = 52, 14, 22, 58
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vmax = _nice_max(max(values) if values else 0)

    n = len(values)
    step = plot_w / max(n, 1)
    bar_w = min(step * 0.62, 66)
    rotate = n > 6 or any(len(l) > 6 for l in labels)

    parts = [_svg_open(width, height)]
    parts.append(_y_grid(pad_l, pad_t, plot_w, plot_h, vmax, 5))

    for i, (label, value) in enumerate(zip(labels, values)):
        h = plot_h * value / vmax
        x = pad_l + step * i + (step - bar_w) / 2
        y = pad_t + plot_h - h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 0):.1f}" '
            f'rx="3" fill="#3b82f6"><title>{escape(label)}: {_num(value)} {escape(unit)}</title></rect>'
        )
        if n <= 12 and h > 16:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" '
                f'class="v">{_num(value)}</text>'
            )
        cx = pad_l + step * i + step / 2
        ty = height - pad_b + 16
        if rotate:
            parts.append(
                f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="end" class="x" '
                f'transform="rotate(-28 {cx:.1f} {ty:.1f})">{escape(_clip(label, 14))}</text>'
            )
        else:
            parts.append(
                f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" class="x">'
                f'{escape(_clip(label, 12))}</text>'
            )

    parts.append(_axis_label(pad_l, 12, unit))
    parts.append("</svg>")
    return "".join(parts)


def line_chart(chart: dict[str, Any], width: int = WIDTH, height: int = 240) -> str:
    labels = [str(x) for x in chart.get("labels", [])]
    values = [float(v) for v in chart.get("values", [])]
    unit = chart.get("unit", "")
    if not labels:
        return ""

    pad_l, pad_r, pad_t, pad_b = 52, 14, 22, 46
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vmax = _nice_max(max(values) if values else 0)

    n = len(labels)
    step = plot_w / max(n - 1, 1)

    def px(i: int) -> float:
        return pad_l + step * i

    def py(v: float) -> float:
        return pad_t + plot_h - plot_h * v / vmax

    parts = [_svg_open(width, height)]
    parts.append(_y_grid(pad_l, pad_t, plot_w, plot_h, vmax, 5))

    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(values))
    area = f"{pad_l:.1f},{pad_t + plot_h:.1f} {pts} {px(n - 1):.1f},{pad_t + plot_h:.1f}"
    parts.append(f'<polygon points="{area}" fill="#3b82f6" opacity="0.10"/>')
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#3b82f6" stroke-width="2"/>')

    for i, v in enumerate(values):
        parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="3" fill="#1d4ed8"/>')

    every = max(1, math.ceil(n / 8))
    for i, label in enumerate(labels):
        if i % every and i != n - 1:
            continue
        parts.append(
            f'<text x="{px(i):.1f}" y="{height - pad_b + 18:.1f}" text-anchor="middle" class="x">'
            f"{escape(label)}</text>"
        )

    parts.append(_axis_label(pad_l, 12, unit))
    parts.append("</svg>")
    return "".join(parts)


def table(chart: dict[str, Any]) -> str:
    columns = [str(c) for c in chart.get("columns", [])]
    rows = chart.get("rows", [])
    if not columns:
        return ""
    head = "".join(f"<th>{escape(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_maybe_escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<table class="data"><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def _maybe_escape(cell: Any) -> str:
    """表格里允许出现少量内联标记（反引号代码），其余全部转义。"""
    text = escape(str(cell))
    if text.startswith("`") and text.endswith("`") and len(text) > 2:
        return "<code>" + text[1:-1] + "</code>"
    return text


def _svg_open(width: int, height: int) -> str:
    # 不写 xmlns：内联在 HTML 里不需要，也保证文件里没有任何 URL
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">'
    )


def _nice_max(v: float) -> float:
    """把坐标轴上限抬到 1/2/5×10^k，刻度才会是整数。"""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 5, 10):
        if v <= m * base:
            return float(m * base)
    return float(10 * base)


def _y_grid(pad_l: int, pad_t: int, plot_w: int, plot_h: int, vmax: float, ticks: int) -> str:
    out = []
    for i in range(ticks + 1):
        y = pad_t + plot_h * (1 - i / ticks)
        out.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="y">'
            f"{_num(vmax * i / ticks)}</text>"
        )
    return "".join(out)


def _axis_label(x: int, y: int, unit: str) -> str:
    if not unit:
        return ""
    return f'<text x="{x}" y="{y}" class="unit">{escape(unit)}</text>'


def _num(v: float) -> str:
    if abs(v - round(v)) < 0.05:
        return f"{int(round(v)):,}"
    return f"{v:,.1f}"


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
